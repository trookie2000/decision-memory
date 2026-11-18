"""Keep directory inputs and manifest/audit inputs compatible after integration."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from decision_memory.client import ModelOutputError
from decision_memory.evaluation import digest
from decision_memory.official_eval import validated_inputs
from decision_memory.pipeline import checked_call, extract
from decision_memory.requirements import assemble_requirement
from decision_memory.sources import discover_requirements, make_sample
from decision_memory.storage import result_digest
from decision_memory.task_delivery import task_packet
from test_optimizations import FakeClient, UNSTATED


class MergeCompatibilityTests(unittest.TestCase):
    def test_manifest_metadata_does_not_occupy_directory_node_stages(self):
        node = make_sample('保留缓存', '保留缓存', 'analysis')
        sample = assemble_requirement('REQ-merge', [('analysis', 'analysis', node)])
        self.assertEqual(sample.input_bytes, len('保留缓存'.encode()) * 2)
        self.assertEqual(sample.node_stages, {})
        self.assertEqual(sample.nodes[0]['id'], 'analysis')
        self.assertEqual(sample.node_order('D.analysis.00001'), 0)

    def test_directory_predictions_keep_scoring_provenance_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'REQ-merge/上下文').mkdir(parents=True)
            (root / 'REQ-merge/产物').mkdir()
            (root / 'REQ-merge/上下文/需求确认记录.md').write_text('**用户：**\n保留缓存')
            (root / 'REQ-merge/产物/需求分析.md').write_text('保留缓存')
            sample = discover_requirements(root)[0]
            payload = {'items': [{'conclusion': {'text': '保留缓存', 'evidence': [
                {'source_id': 'D1-00002', 'quote': '保留缓存'}]}, 'reason': UNSTATED, 'boundary': UNSTATED}],
                'open_questions': []}
            prediction = extract(sample, FakeClient([payload]), 'baseline')
            prediction['result_digest'] = result_digest(prediction)
            grouped, sources, bindings = validated_inputs(root, [prediction], 'requirement')
            self.assertEqual(list(grouped), ['REQ-merge'])
            self.assertTrue(any(row['source_id'] == 'D1-00002' for row in sources['REQ-merge']))
            self.assertEqual(bindings[0]['prediction_digest'], digest(prediction))
            forged = {**prediction, 'input_fingerprint': 'wrong'}
            forged['result_digest'] = result_digest(forged)
            with self.assertRaises(ValueError):
                validated_inputs(root, [forged], 'requirement')

    def test_legacy_scoring_packet_still_requires_original_prediction_digest(self):
        prediction = {'stage': 'requirement', 'sample_id': 'REQ-merge', 'items': [], 'open_questions': []}
        payload = {'nodes': [], 'prediction': prediction}
        identity = {'task_id': 'T', 'sample_id': 'REQ-merge', 'role': 'scorer', 'round': 1,
                    'input_version': digest(payload), 'prediction_digest': digest(prediction)}
        op = {'identity': identity, 'nonce': 'n', 'document': {}}
        self.assertEqual(task_packet(op, 'requirement', payload, 'fixture')['payload'], payload)
        op['identity'] = {**identity, 'prediction_digest': 'wrong'}
        with self.assertRaises(ValueError):
            task_packet(op, 'requirement', payload, 'fixture')

    def test_nonrepairable_model_errors_never_reach_retry_or_conservative_repair(self):
        client, repair = Mock(), Mock()
        client.complete.side_effect = ModelOutputError('credential echo rejected', protocol_repair_allowed=False)
        with self.assertRaises(ModelOutputError):
            checked_call(client, 'system', 'user', lambda payload: payload, repair)
        self.assertEqual(client.complete.call_count, 1)
        repair.assert_not_called()
