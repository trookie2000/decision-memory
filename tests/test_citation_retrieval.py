"""Post-review sources remain real, bounded, labelled, and exactly replayable."""
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from decision_memory.citation_retrieval import RETRIEVAL_POLICY, augment_evidence
from decision_memory.client import Config, ModelClient
from decision_memory.evaluation import digest
from decision_memory.grounded import (
    LEGACY_PROTOCOL, V1_LOCAL_POLICY, V1_LOCAL_PROMPT, decode_payload, extract_grounded, review_payload, signature,
)
from decision_memory.run_report import render_result_html
from decision_memory.sources import index_materials
from decision_memory.storage import read_json, render_markdown, result_digest, save_result, validate_saved
from http_fixture import completion, model_server


def sample():
    return index_materials('**用户：**\n保持现有排序职责。',
        '排序由引擎完成。\n同时透传实时当前出价给引擎。\n其它材料不涉及本条。',
        'design', 'RETRIEVE', 'dialogue.md', 'artifact.md')


def payload():
    return {'items': [{'conclusion': {'text': '排序由引擎完成，并透传实时当前出价。', 'evidence': ['A00001']},
                      'reason': {'text': '未说明', 'evidence': []}, 'boundary': {'text': '未说明', 'evidence': []}}],
            'open_questions': []}


def final_payload():
    return {**payload(), 'review_notes': ['保留真实陈述。'], 'field_checks': [
        {'item_index': 1, 'field': name, 'status': 'supported' if name == 'conclusion' else 'unstated',
         'notes': '按本字段核对。'} for name in ('conclusion', 'reason', 'boundary')]}


class CitationRetrievalTests(unittest.TestCase):
    def test_two_http_calls_and_labelled_sources_survive_saved_replay(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with model_server([(200, completion(payload())), (200, completion(final_payload()))]) as (url, requests):
                result = extract_grounded(sample(), ModelClient(Config(endpoint=url, model='fixture',
                    api_key_env='', retries=0)), root / 'audit')
            self.assertEqual(len(requests), 2)
            candidate = json.loads(requests[1]['messages'][1]['content'].split('\n候选 ', 1)[1].split('\nreview_hints ', 1)[0])
            self.assertEqual(candidate['items'][0]['conclusion']['evidence'], ['A00001'])
            field = result['items'][0]['conclusion']
            self.assertEqual(field['text'], payload()['items'][0]['conclusion']['text'])
            self.assertEqual([r['source_id'] for r in field['evidence']], ['A00001', 'A00002'])
            self.assertEqual(field['evidence_retrieval']['declared_source_ids'], ['A00001'])
            self.assertEqual(field['evidence_retrieval']['added_source_ids'], ['A00002'])
            self.assertEqual(result['grounding_proof']['citation_retrieval_policy_digest'], digest(RETRIEVAL_POLICY))
            self.assertFalse(result['grounding_proof']['source_retrieval_semantic_certification'])
            self.assertIn('程序检索补充引文', render_markdown(result))
            self.assertIn('程序检索补充引文', render_result_html(result, sample()))
            self.assertIn('程序补充引文的语义支持仍待独立确认', render_markdown({**result, 'scope': 'requirement', 'nodes': []}))
            save_result(root / 'result.json', result, sample=sample())
            validate_saved(read_json(root / 'result.json'), sample())
            for kind in ('reference', 'provenance'):
                changed = copy.deepcopy(result)
                value = changed['items'][0]['conclusion']
                if kind == 'reference':
                    value['evidence'].pop()
                else:
                    value['evidence_retrieval']['matches'][0]['anchors'] = ['不存在']
                changed['result_digest'] = result_digest(changed)
                with self.assertRaises(ValueError):
                    validate_saved(changed, sample())

            legacy = {**result, **review_payload(final_payload(), sample(), include_retrieval=False,
                                                 policy=V1_LOCAL_POLICY)}
            legacy.pop('input_contract')
            legacy.update(extraction_protocol=LEGACY_PROTOCOL, version='0.2.0', prompt_hash=V1_LOCAL_PROMPT,
                          source_columns=list(V1_LOCAL_POLICY['columns']))
            legacy['run_signature'] = signature(sample(), legacy['requested_config'], include_retrieval=False,
                                                protocol=LEGACY_PROTOCOL, version='0.2.0', prompt=V1_LOCAL_PROMPT)
            legacy['result_digest'] = result_digest(legacy)
            validate_saved(legacy, sample())

    def test_unstated_questions_and_uncertain_model_verdicts_are_preserved(self):
        raw = final_payload()
        raw['field_checks'][0]['status'] = 'unsupported'
        raw['open_questions'] = [{'text': '下一步是否扩展仍待确认？', 'evidence': ['D00002']}]
        reviewed = review_payload(raw, sample())
        self.assertEqual(reviewed['grounding_proof']['status'], 'needs_review')
        self.assertEqual(reviewed['items'][0]['reason'], {'text': '未说明', 'evidence': []})
        self.assertNotIn('evidence_retrieval', reviewed['open_questions'][0])

    def test_no_extra_source_bypasses_user_attribution_or_uses_agent_proposals(self):
        raw = payload(); raw['items'][0]['conclusion']['text'] = '用户要求排序由引擎完成，并透传实时当前出价。'
        with self.assertRaisesRegex(ValueError, '用户'):
            review_payload({**final_payload(), **raw}, sample())
        context = index_materials('**Agent：**\n同时透传实时当前出价给引擎。', '排序由引擎完成。', 'design', 'AGENT', 'd', 'a')
        checked = decode_payload(payload(), context, enforce_bindings=False)
        self.assertNotIn('evidence_retrieval', augment_evidence(checked, context)['items'][0]['conclusion'])

    def test_source_count_and_total_bytes_are_bounded_without_partial_mutation(self):
        context = index_materials('**用户：**\n保留原有职责。',
            '\n'.join('原始资料' + str(i) for i in range(16)) + '\n同时透传实时当前出价给引擎。',
            'design', 'LIMIT', 'd', 'a')
        raw = payload(); raw['items'][0]['conclusion']['evidence'] = ['A' + str(i).zfill(5) for i in range(1, 17)]
        normalized = decode_payload(raw, context, enforce_bindings=False)
        completed = augment_evidence(normalized, context)
        self.assertEqual(completed['items'][0]['conclusion']['evidence'], normalized['items'][0]['conclusion']['evidence'])
        normalized = decode_payload(payload(), sample(), enforce_bindings=False)
        before = copy.deepcopy(normalized)
        with self.assertRaisesRegex(ValueError, '8 MB'):
            augment_evidence(normalized, sample(), max_bytes=len(sample().catalog['A00001'].text.encode()) + 1)
        self.assertEqual(normalized, before)

    def test_flow_header_and_retrieval_provenance_compose_in_saved_replay(self):
        context = index_materials('**用户：**\n保持原职责。',
            '```\nReadCacheFlow\n  -> 排序由引擎完成。\n```\n同时透传实时当前出价给引擎。',
            'design', 'COMPOSE', 'd', 'a')
        raw = payload()
        raw['items'][0]['conclusion'] = {'text': 'ReadCacheFlow 排序由引擎完成，并透传实时当前出价。', 'evidence': ['A00003']}
        final = {**final_payload(), **raw}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with model_server([(200, completion(raw)), (200, completion(final))]) as (url, _):
                result = extract_grounded(context, ModelClient(Config(endpoint=url, model='fixture',
                    api_key_env='', retries=0)), root / 'audit')
            field = result['items'][0]['conclusion']
            self.assertEqual(field['evidence_context']['declared_source_ids'], ['A00003'])
            self.assertEqual(field['evidence_retrieval']['declared_source_ids'], ['A00003', 'A00002'])
            self.assertEqual(field['evidence_retrieval']['added_source_ids'], ['A00005'])
            save_result(root / 'result.json', result, sample=context)
            validate_saved(read_json(root / 'result.json'), context)

    def test_source_ranking_is_stable_across_hash_seeds(self):
        script = """import json
from decision_memory.citation_retrieval import RetrievalIndex
catalog={str(i):{'text':t,'role':r} for i,(t,r) in enumerate([
('排序由引擎完成。','artifact'),('透传实时当前出价及活动价格。','artifact'),
('活动价格与当前出价分开提供。','user'),('当前出价保持实时。','artifact')])}
field={'text':'排序由引擎完成，并透传实时当前出价及活动价格。',
       'evidence':[{'source_id':'0','quote':'排序由引擎完成。'}]}
print(json.dumps(RetrievalIndex(catalog).select(field),ensure_ascii=False))
"""
        outputs = [subprocess.check_output([sys.executable, '-B', '-c', script],
            cwd=Path(__file__).resolve().parents[1], env={**os.environ, 'PYTHONHASHSEED': seed}) for seed in ('1', '12', '123')]
        self.assertTrue(all(output == outputs[0] for output in outputs))


if __name__ == '__main__':
    unittest.main()
