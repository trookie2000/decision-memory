import copy
import unittest

from decision_memory.sources import make_sample
from decision_memory.validation import validate_result


def valid_payload():
    return {'items': [{
        'conclusion': {'text': '门槛为 120 元', 'evidence': [{'source_id': 'D00001', 'quote': '门槛改成 120 元'}]},
        'reason': {'text': '未说明', 'evidence': []},
        'boundary': {'text': '本次活动', 'evidence': [{'source_id': 'A00001', 'quote': '本次活动'}]},
    }], 'open_questions': []}


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.sample = make_sample('门槛改成 120 元', '本次活动门槛为 120 元', 'analysis')

    def test_resolves_real_source_locations(self):
        result = validate_result(valid_payload(), self.sample)
        evidence = result['items'][0]['conclusion']['evidence'][0]
        self.assertEqual(evidence['line'], 1)
        self.assertEqual(evidence['file'], 'dialogue.md')
        self.assertEqual(result['items'][0]['id'], 'K001')

    def test_rejects_fabricated_source_or_quote(self):
        for reference in ({'source_id': 'D99999', 'quote': '门槛'}, {'source_id': 'D00001', 'quote': '100 元'}, {'source_id': 'D00001', 'quote': ''}):
            payload = valid_payload()
            payload['items'][0]['conclusion']['evidence'] = [reference]
            with self.subTest(reference=reference), self.assertRaises(ValueError):
                validate_result(payload, self.sample)

    def test_reason_cannot_be_filled_without_evidence(self):
        payload = valid_payload()
        payload['items'][0]['reason']['text'] = '为了提升转化率'
        with self.assertRaisesRegex(ValueError, '证据'):
            validate_result(payload, self.sample)

    def test_rejects_non_object_and_empty_conclusion(self):
        with self.assertRaises(ValueError):
            validate_result([], self.sample)
        payload = valid_payload()
        payload['items'][0]['conclusion'] = {'text': '未说明', 'evidence': []}
        with self.assertRaises(ValueError):
            validate_result(payload, self.sample)

    def test_duplicate_entries_do_not_inflate_output(self):
        payload = valid_payload()
        payload['items'].append(copy.deepcopy(payload['items'][0]))
        with self.assertRaisesRegex(ValueError, '重复'):
            validate_result(payload, self.sample)


if __name__ == '__main__':
    unittest.main()
