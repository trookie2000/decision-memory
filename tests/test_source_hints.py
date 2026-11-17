"""Source retrieval guides review, but cannot certify or repair business claims."""
import json
import tempfile
import unittest
from pathlib import Path

from decision_memory.client import Config, ModelClient
from decision_memory.grounded import decode_payload, extract_grounded
from decision_memory.source_hints import build_review_hints
from decision_memory.sources import index_materials
from http_fixture import completion, model_server


def payload():
    return {'items': [{'conclusion': {'text': '沿用 CacheStore。', 'evidence': ['A00001']},
                      'reason': {'text': '未说明', 'evidence': []},
                      'boundary': {'text': '未说明', 'evidence': []}}], 'open_questions': []}


def sample():
    return index_materials('**用户：**\n沿用 CacheStore，避免重复存储。',
                           '仅缓存详情。\n失败保留旧值，不阻塞主链路。', 'design', 'FAKE', 'd.md', 'a.md')


class SourceHintTests(unittest.TestCase):
    def test_missing_symbol_and_unreferenced_rule_offer_real_scoped_ids(self):
        context = sample()
        first = decode_payload(payload(), context, enforce_bindings=False)
        hints = build_review_hints(first, context)
        field = hints['fields'][0]
        self.assertIn('CacheStore', field['missing_symbols'])
        self.assertIn('D00002', field['candidate_source_ids'])
        self.assertIn('A00002', hints['unreferenced_artifact_candidates'])
        self.assertEqual(hints['full_source_count'], len(context.catalog))
        for row in hints['fields']:
            self.assertTrue(set(row['candidate_source_ids']) <= set(context.catalog))

    def test_candidate_defect_reaches_review_without_extra_extraction_call(self):
        fixed = payload(); fixed['items'][0]['conclusion']['evidence'] = ['D00002']
        fixed['review_notes'] = ['修正本字段来源，不删除可复用规则。']
        fixed['field_checks'] = [{'item_index': 1, 'field': name,
            'status': 'supported' if name == 'conclusion' else 'unstated', 'notes': '核对自身来源。'}
            for name in ('conclusion', 'reason', 'boundary')]
        with tempfile.TemporaryDirectory() as temporary:
            with model_server([(200, completion(payload())), (200, completion(fixed))]) as (url, requests):
                result = extract_grounded(sample(), ModelClient(Config(endpoint=url, model='fixture',
                    api_key_env='', retries=0)), Path(temporary) / 'run')
            self.assertEqual(len(requests), 2)
            self.assertIn('review_hints', requests[1]['messages'][1]['content'])
            self.assertIn('missing_symbols', requests[1]['messages'][1]['content'])
            self.assertEqual(result['items'][0]['conclusion']['evidence'][0]['source_id'], 'D00002')

    def test_retrieval_does_not_make_unsupported_final_symbol_pass(self):
        with self.assertRaises(ValueError):
            decode_payload(payload(), sample())
        first = decode_payload(payload(), sample(), enforce_bindings=False)
        self.assertIn('CacheStore', build_review_hints(first, sample())['fields'][0]['missing_symbols'])
        self.assertEqual(first['items'][0]['conclusion']['evidence'][0]['source_id'], 'A00001')


if __name__ == '__main__':
    unittest.main()
