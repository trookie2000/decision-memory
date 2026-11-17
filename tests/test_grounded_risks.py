"""Real evidence gaps: omitted old values, constants, and unexamined unknowns."""
import tempfile
import unittest
from pathlib import Path

from decision_memory.client import Config, ModelClient
from decision_memory.grounded import decode_payload, extract_grounded
from decision_memory.sources import index_materials
from decision_memory.source_hints import build_review_hints
from http_fixture import completion, model_server


def sample():
    return index_materials('**用户：**\n原先门槛是 40 元。\n改为 120 元；成功后存 48 小时。',
        '# 当前方案\n默认 120 元，成功后存 2 天。\n使用 NOTICE_FREQUENCY_KEY。\n'
        '因为要避免重复通知，使用用户维度。\n仅用于用户通知。', 'design', 'RISK', 'd.md', 'a.md')


def payload(text, ids):
    return {'items': [{'conclusion': {'text': text, 'evidence': ids},
                      'reason': {'text': '未说明', 'evidence': []},
                      'boundary': {'text': '未说明', 'evidence': []}}], 'open_questions': []}


class GroundedRiskTests(unittest.TestCase):
    def test_old_numeric_value_and_uppercase_constant_need_their_own_source(self):
        for text in ('门槛从 40 元改为 120 元。', '默认使用 NOTICE_FREQUENCY_KEY。'):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, '40|NOTICE_FREQUENCY_KEY'):
                decode_payload(payload(text, ['A00002']), sample())
        value = decode_payload(payload('门槛从 40 元改为 120 元。', ['D00002', 'A00002']), sample())
        self.assertIn('40', value['items'][0]['conclusion']['text'])

    def test_explicit_time_equivalence_and_formatted_numbers_are_not_false_gaps(self):
        decode_payload(payload('成功后保留 48 小时。', ['A00002']), sample())
        other = index_materials('**用户：**\n保留原有统计。', '观测 12,345 条。', 'design', 'N', 'd', 'a')
        decode_payload(payload('观测 12345 条。', ['A00001']), other)
        with self.assertRaises(ValueError):
            decode_payload(payload('观测 1234 条。', ['A00001']), other)

    def test_large_numbers_do_not_become_equal_after_decimal_context_rounding(self):
        actual = '123456789012345678901234567891'
        invented = '123456789012345678901234567899'
        other = index_materials('**用户：**\n保留原值。', '值 ' + actual, 'design', 'BIG', 'd', 'a')
        with self.assertRaises(ValueError):
            decode_payload(payload('值 ' + invented, ['A00001']), other)

    def test_unstated_fields_and_questions_receive_targeted_current_source_hints(self):
        result = decode_payload(payload('使用用户维度避免重复通知。', ['A00004']), sample())
        result['open_questions'] = [{'text': '用户通知是否只针对当前用户？',
                                    'evidence': [{'source_id': 'D00002', 'quote': '原先门槛是 40 元。'}]}]
        hints = build_review_hints(result, sample())
        by_field = {row['field']: row for row in hints['fields']}
        self.assertEqual(set(by_field), {'conclusion', 'reason', 'boundary'})
        self.assertTrue(by_field['reason']['candidate_source_ids'])
        self.assertIn('A00004', by_field['reason']['candidate_source_ids'])
        self.assertIn('A00005', by_field['boundary']['candidate_source_ids'])
        self.assertTrue(by_field['reason']['unstated_requires_check'])
        self.assertEqual(hints['questions'][0]['question_index'], 1)
        self.assertIn('A00005', hints['questions'][0]['later_related_source_ids'])

    def test_candidate_value_gap_reaches_the_same_two_call_review(self):
        first = payload('门槛从 40 元改为 120 元。', ['A00002'])
        final = payload('门槛从 40 元改为 120 元。', ['D00002', 'A00002'])
        final.update(review_notes=['补回原门槛的自身来源。'], field_checks=[
            {'item_index': 1, 'field': name, 'status': 'supported' if name == 'conclusion' else 'unstated',
             'notes': '根据本字段来源核对。'} for name in ('conclusion', 'reason', 'boundary')])
        with tempfile.TemporaryDirectory() as temporary:
            with model_server([(200, completion(first)), (200, completion(final))]) as (url, requests):
                result = extract_grounded(sample(), ModelClient(Config(endpoint=url, model='fixture',
                    api_key_env='', retries=0)), Path(temporary) / 'audit')
            self.assertEqual(len(requests), 2)
            self.assertIn('missing_atoms', requests[1]['messages'][1]['content'])
            self.assertIn('unstated_requires_check', requests[1]['messages'][1]['content'])
            self.assertIn('later_related_source_ids', requests[1]['messages'][1]['content'])
            self.assertIn('atoms', result['grounding_proof']['literal_bindings'])


if __name__ == '__main__':
    unittest.main()
