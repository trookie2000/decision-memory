import unittest

from decision_memory.grounded import MAX_REFS, review_payload
from decision_memory.grounded_atoms import MissingAtomEvidence
from decision_memory.literal_repair import plan_literal_repair
from decision_memory.sources import index_materials


class LiteralRepairTests(unittest.TestCase):
    def setUp(self):
        self.sample = index_materials(
            'User: Previously the intake cutoff was 40 minutes.\n'
            'User: Observed intake takes 52 minutes.\n'
            'User: Shift intake cutoff to 60 minutes.\n'
            'User: Parking validation lasts 40 minutes.',
            'Intake cutoff is now 60 minutes.', 'design', 'INTAKE', 'talk.md', 'result.md')
        self.review = {'items': [{
            'conclusion': {'text': '材料接收截止从 40 分钟调整为 60 分钟。', 'evidence': ['D00001', 'D00003']},
            'reason': {'text': '原 40 分钟不足，实测接收需要 52 分钟。', 'evidence': ['D00002']},
            'boundary': {'text': '未说明', 'evidence': []},
        }], 'open_questions': [], 'review_notes': [], 'field_checks': [
            {'item_index': 1, 'field': field, 'status': status, 'notes': '按字段核查。'}
            for field, status in [('conclusion', 'supported'), ('reason', 'unsupported'), ('boundary', 'unstated')]
        ]}

    def test_repairs_reuse_source_coordinates_and_preserve_the_unresolved_status(self):
        with self.assertRaises(MissingAtomEvidence) as caught:
            review_payload(self.review, self.sample)
        _, user, apply = plan_literal_repair(self.review, caught.exception, self.sample, max_refs=MAX_REFS)
        selection = {'item_id': 'K001', 'field': 'reason', 'source_ids': ['D00001']}
        merged = apply({'additions': [selection]})
        self.assertNotIn('Parking validation', user)
        self.assertEqual(self.review['items'][0]['reason']['evidence'], ['D00002'])
        self.assertEqual(merged['items'][0]['conclusion'], self.review['items'][0]['conclusion'])
        self.assertEqual(merged['items'][0]['reason']['evidence'], ['D00002', 'D00001'])
        self.assertEqual(review_payload(merged, self.sample)['grounding_proof']['status'], 'needs_review')
        for invalid in ({**selection, 'field': 'conclusion'}, {**selection, 'source_ids': ['D00004']},
                        {**selection, 'source_ids': ['D00001', 'D00001']}, {**selection, 'text': '修改答案'}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                apply({'additions': [invalid]})

    def test_equal_quantity_from_another_rule_cannot_rescue_the_field(self):
        item = self.review['items'][0]
        item['conclusion'] = {'text': '材料接收截止为 60 分钟。', 'evidence': ['D00003', 'D00004']}
        with self.assertRaises(MissingAtomEvidence) as caught:
            review_payload(self.review, self.sample)
        with self.assertRaisesRegex(ValueError, '同条目'):
            plan_literal_repair(self.review, caught.exception, self.sample, max_refs=MAX_REFS)
