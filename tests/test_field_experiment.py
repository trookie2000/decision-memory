"""Frozen sources, blind judging and bounded calls precede any improvement claim."""
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from decision_memory.client import Config, ModelError
from decision_memory.field_experiment import check_freeze, freeze, load_credentials, run, summarize
from decision_memory.field_verification import FIELDS, UNKNOWN
from decision_memory.pipeline import extract
from decision_memory.sources import discover_requirements
from decision_memory.storage import read_json, save_result
from http_fixture import completion, model_server
from test_optimizations import FakeClient
import test_optimizations as fixtures


class FrozenFieldExperimentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.dataset = fixtures.OfficialEvalTests().dataset(self.root)
        self.sample = discover_requirements(self.dataset, 'REQ-one')[0]
        self.item = {'conclusion': {'text': '甲方负责排序', 'evidence': [{'source_id': 'A2-00001', 'quote': '甲方负责排序'}]},
                     'reason': UNKNOWN, 'boundary': UNKNOWN}
        self.base = extract(self.sample, FakeClient([{'items': [self.item], 'open_questions': []}]), 'baseline')
        self.predictions = self.root / 'predictions'
        save_result(self.predictions / 'base.json', self.base)
        self.output = self.root / 'frozen'

    def config(self, url='https://example.invalid/chat/completions', env=''):
        return Config(endpoint=url, model='fixture', api_key_env=env, retries=0)

    def plan(self, url='https://example.invalid/chat/completions', **kwargs):
        return freeze(self.dataset, self.predictions, self.config(url), self.config(url), self.output, **kwargs)

    def responses(self):
        questions = {'questions': [{'item_id': 'K001', 'field': field, 'questions': ['排序职责及其依据和范围是什么？']}
                                   for field in FIELDS]}
        answers = {'answers': [{'item_id': 'K001', 'field': field,
                               'status': 'answered' if field == 'conclusion' else 'unstated', **self.item[field]}
                              for field in FIELDS]}
        checks = {'field_checks': [{'item_id': 'K001', 'field': field,
            'original_support': 'supported' if field == 'conclusion' else 'unstated',
            'action': 'keep', 'value': self.base['items'][0][field], 'notes': 'fixture same evidence'} for field in FIELDS]}
        judgment = {'items': [{'item_id': 'requirement:K001', 'verdict': 'valid', 'matches': ['K1'],
                               'citation_support': True, 'notes': 'fictional fixture'}], 'positives': [
                        {'ref_id': 'K1', 'coverage': 'full', 'items': ['requirement:K001'], 'notes': 'fixture'},
                        {'ref_id': 'K2', 'coverage': 'missing', 'items': [], 'notes': 'fixture'}]}
        return [(200, completion(value)) for value in (questions, answers, checks, judgment, judgment)]

    def test_prepare_copies_and_binds_sources_original_predictions_references_configs_and_code(self):
        plan = self.plan()
        self.assertEqual(len(plan['cases']), 1)
        self.assertEqual(read_json(self.output / 'base/01.json'), self.base)
        self.assertEqual(plan['baseline'], 'frozen_existing_prediction_not_new_extraction')
        self.assertIn('field_verification.py', plan['implementation'])
        self.assertEqual(read_json(self.output / 'state.json')['status'], 'frozen')
        check_freeze(self.output)

    def test_artifact_only_baseline_is_not_claimed_as_a_same_material_field_comparison(self):
        base = extract(self.sample, FakeClient([{'items': [self.item], 'open_questions': []}]), 'artifact-only')
        save_result(self.root / 'artifact-predictions/base.json', base)
        with self.assertRaisesRegex(ValueError, '仅产物'):
            freeze(self.dataset, self.root / 'artifact-predictions', self.config(), self.config(), self.output)
        self.assertFalse(self.output.exists())

    def test_dataset_reference_prediction_or_implementation_tamper_fails_before_running(self):
        self.plan()
        original = (self.output / 'references.json').read_text()
        (self.output / 'references.json').write_text(original + '\n')
        with self.assertRaisesRegex(ValueError, '冻结'):
            run(self.output)
        self.assertEqual(read_json(self.output / 'state.json')['status'], 'frozen')

    def test_changed_source_signature_refuses_reuse_without_modifying_real_source(self):
        self.plan()
        with patch('decision_memory.field_experiment.implementation', return_value={}):
            with self.assertRaisesRegex(ValueError, '实现已改变'):
                check_freeze(self.output)

    def test_local_http_pair_has_blind_judges_and_does_not_claim_equal_metrics_are_improvement(self):
        with model_server(self.responses()) as (url, requests):
            self.plan(url)
            summary = run(self.output)
        self.assertEqual(len(requests), 5)
        self.assertEqual(summary['complete_pairs'], 1)
        self.assertFalse(summary['improvement_demonstrated'])
        self.assertEqual(summary['all_case_deltas']['recall'], 0)
        for request in requests[-2:]:
            data = json.loads(request['messages'][1]['content'])
            self.assertEqual(set(data), {'positives', 'negatives', 'items', 'source_materials'})
            self.assertNotIn('verification', json.dumps(data))
            self.assertNotIn('verifier_config', json.dumps(data))
        for request in requests[:3]:
            self.assertNotIn('"positives"', request['messages'][1]['content'])
        self.assertEqual(read_json(self.output / 'calls.json')['calls'][-1]['status'], 'completed')
        self.assertTrue((self.output / 'report.md').exists())
        with self.assertRaisesRegex(ValueError, '已启动'):
            run(self.output)

    def test_budget_stops_before_extra_requests_and_failed_case_remains_in_denominator(self):
        with model_server(self.responses()[:2]) as (url, requests):
            self.plan(url, max_calls=2)
            summary = run(self.output)
        self.assertEqual(len(requests), 2)
        self.assertEqual(summary['total_cases'], 1)
        self.assertEqual(summary['complete_pairs'], 0)
        self.assertIsNone(summary['all_case_deltas'])
        self.assertFalse(summary['improvement_demonstrated'])
        self.assertEqual(read_json(self.output / 'state.json')['status'], 'incomplete')
        self.assertTrue((self.output / 'raw/002-response.json').exists())

    def test_partial_publication_is_not_an_improvement_even_with_good_surviving_items(self):
        plan = self.plan()
        summary = summarize(plan, [{'id': 'REQ-one', 'status': 'partial', 'held_items': 1}], [])
        self.assertEqual(summary['complete_pairs'], 0)
        self.assertFalse(summary['improvement_demonstrated'])
        self.assertIsNone(summary['paired_metrics']['verified'])

    def test_missing_credentials_fail_preflight_without_consuming_the_once_only_state(self):
        freeze(self.dataset, self.predictions, self.config(env='FIELD_FIXTURE_MISSING_KEY'), self.config(), self.output)
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(ModelError):
            run(self.output)
        self.assertEqual(read_json(self.output / 'state.json')['status'], 'frozen')

    def test_credential_file_only_reads_requested_literal_names(self):
        path = self.root / 'fixture.env'
        path.write_text('export FIELD_FIXTURE_KEY="fixture value"\nUNRELATED=do-not-read\n')
        with patch.dict(os.environ, {}, clear=True):
            load_credentials(path, [self.config(env='FIELD_FIXTURE_KEY')])
            self.assertEqual(os.environ['FIELD_FIXTURE_KEY'], 'fixture value')
            self.assertNotIn('UNRELATED', os.environ)

    def test_credential_file_never_executes_shell_expansion(self):
        path = self.root / 'fixture.env'
        path.write_text('FIELD_FIXTURE_KEY="$(touch should-not-exist)"\n')
        with self.assertRaisesRegex(ValueError, '不执行命令'):
            load_credentials(path, [self.config(env='FIELD_FIXTURE_KEY')])
