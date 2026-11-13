import copy
import unittest

from decision_memory.evaluation import digest, make_worksheet, score_worksheet
from decision_memory.review_v2 import align_question_judgments, apply_review_v2
from test_validation import valid_payload


class ReviewV2Tests(unittest.TestCase):
    def setUp(self):
        self.result = {**valid_payload(), 'sample_id': 'fixture'}
        self.result['items'][0]['id'] = 'K001'
        self.result['open_questions'] = [{'text': '间隔的理由未说明',
                                          'evidence': [{'source_id': 'D00002', 'quote': '未说明'}]}]
        self.references = [{'id': 'R1', 'text': '有效决定'}, {'id': 'R2', 'text': '间隔的理由未说明'}]
        self.policy = {'schema_version': 1, 'references_digest': digest(self.references), 'assignments': [
            {'reference_id': 'R1', 'kind': 'decision', 'rationale': '有效决定的检查点'},
            {'reference_id': 'R2', 'kind': 'uncertainty', 'rationale': '不能把未知理由作为已生效决定'}]}

    def worksheet(self):
        return make_worksheet(self.result, self.references, 'agent', schema_version=2, reference_policy=self.policy)

    def reviewed(self):
        return apply_review_v2(self.worksheet(),
            judgments=[{'item_id': 'K001', 'correct': True, 'citation_support': True,
                        'matches': ['R1'], 'notes': '离线 fixture'}],
            open_question_judgments=[{'question_id': 'Q001', 'correct': True, 'citation_support': True,
                                     'matches': ['R2'], 'notes': '离线 fixture'}],
            reference_judgments=[
                {'reference_id': 'R1', 'coverage': 'full', 'supported_by': [
                    {'target': 'item', 'id': 'K001', 'field': 'conclusion'}], 'notes': '完整覆盖有效决定'},
                {'reference_id': 'R2', 'coverage': 'full', 'supported_by': [
                    {'target': 'open_question', 'id': 'Q001'}], 'notes': '独立保留未知项'}])

    def test_unreviewed_and_empty_outputs_do_not_produce_perfect_scores(self):
        score = score_worksheet(self.worksheet())
        self.assertIsNone(score['decisions']['precision_reviewed'])
        self.assertIsNone(score['uncertainties']['precision_reviewed'])
        self.assertIsNone(score['reference_coverage']['decision']['coverage'])
        self.assertFalse(score['complete'])
        self.result['items'] = []
        self.result['open_questions'] = []
        score = score_worksheet(self.worksheet())
        self.assertFalse(score['complete'])
        self.assertIsNone(score['reference_coverage']['uncertainty']['coverage'])

    def test_question_review_is_separate_from_decision_accuracy(self):
        score = score_worksheet(self.reviewed())
        self.assertEqual(score['decisions']['outputs'], 1)
        self.assertEqual(score['uncertainties']['outputs'], 1)
        self.assertEqual(score['reference_coverage']['decision']['coverage'], 1)
        self.assertEqual(score['reference_coverage']['uncertainty']['coverage'], 1)
        self.assertTrue(score['complete'])
        self.assertEqual(score['human_review'], 'pending')
        self.assertIsNone(score['official_score'])
        self.assertNotIn('recall', score)

    def test_matches_alone_never_imply_full_reference_coverage(self):
        worksheet = self.worksheet()
        judgments = self.reviewed()['judgments']
        score = score_worksheet(apply_review_v2(worksheet, judgments=judgments))
        self.assertEqual(score['reference_coverage']['decision']['pending'], 1)
        self.assertIsNone(score['reference_coverage']['decision']['coverage'])

    def test_partial_composite_match_does_not_count_as_full(self):
        worksheet = self.reviewed()
        worksheet['reference_judgments'][0]['coverage'] = 'partial'
        worksheet['reference_judgments'][0]['notes'] = '仅覆盖参考的前半部分'
        score = score_worksheet(worksheet)
        self.assertEqual(score['reference_coverage']['decision']['partial'], 1)
        self.assertEqual(score['reference_coverage']['decision']['coverage'], 0)

    def test_uncertainty_cannot_cover_effective_decision(self):
        worksheet = self.reviewed()
        worksheet['open_question_judgments'][0]['matches'].append('R1')
        with self.assertRaisesRegex(ValueError, '已生效'):
            score_worksheet(worksheet)

    def test_known_missing_reason_may_be_checked_on_reason_field(self):
        worksheet = self.reviewed()
        worksheet['judgments'][0]['matches'].append('R2')
        worksheet['reference_judgments'][1]['supported_by'] = [{'target': 'item', 'id': 'K001', 'field': 'reason'}]
        self.assertEqual(score_worksheet(worksheet)['reference_coverage']['uncertainty']['full'], 1)
        worksheet['reference_judgments'][1]['supported_by'][0]['field'] = 'conclusion'
        with self.assertRaisesRegex(ValueError, '未知信息'):
            score_worksheet(worksheet)

    def test_predicted_questions_gold_and_policy_are_immutable(self):
        for field in ('items', 'open_questions', 'references', 'reference_policy'):
            worksheet = self.reviewed()
            worksheet[field] = []
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, '改动'):
                score_worksheet(worksheet)

    def test_incorrect_or_unsupported_output_cannot_support_coverage(self):
        for field in ('correct', 'citation_support'):
            worksheet = self.reviewed()
            worksheet['judgments'][0][field] = False
            with self.subTest(field=field), self.assertRaises(ValueError):
                score_worksheet(worksheet)

    def test_reference_and_question_judgments_must_be_one_to_one(self):
        for field in ('reference_judgments', 'open_question_judgments'):
            worksheet = self.reviewed()
            worksheet[field].append(copy.deepcopy(worksheet[field][0]))
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, '重复'):
                score_worksheet(worksheet)

    def test_unsupported_unknown_and_duplicate_links_are_rejected(self):
        for replacement in ([{'target': 'item', 'id': 'K999', 'field': 'conclusion'}],
                            [{'target': 'item', 'id': 'K001', 'field': 'conclusion'}] * 2, []):
            worksheet = self.reviewed()
            worksheet['reference_judgments'][0]['supported_by'] = replacement
            with self.subTest(replacement=replacement), self.assertRaises(ValueError):
                score_worksheet(worksheet)

    def test_question_import_is_exact_and_keeps_unreviewed_questions_null(self):
        self.result['open_questions'].append({'text': '另一未决项', 'evidence': []})
        rows = [{'text': '间隔的理由未说明', 'correct': True, 'citation_support': True,
                 'matches': ['R2'], 'notes': '逐字匹配已有员工评分'}]
        aligned = align_question_judgments(self.result, rows)
        self.assertEqual(aligned[0]['question_id'], 'Q001')
        self.assertIsNone(aligned[1]['correct'])
        rows[0]['text'] = '意思相似但并非逐字原文'
        with self.assertRaisesRegex(ValueError, '不存在'):
            align_question_judgments(self.result, rows)

    def test_policy_requires_every_reference_and_matching_digest(self):
        self.policy['assignments'].pop()
        with self.assertRaisesRegex(ValueError, '一一对应'):
            self.worksheet()

    def test_legacy_score_is_still_exactly_legacy(self):
        legacy = make_worksheet(self.result, self.references, 'agent')
        legacy['judgments'][0].update(correct=True, citation_support=True, matches=['R1'], notes='fixture')
        before = copy.deepcopy(legacy)
        score = score_worksheet(legacy)
        self.assertEqual(score['recall'], .5)
        self.assertEqual(legacy, before)
        self.assertNotIn('uncertainties', score)


if __name__ == '__main__':
    unittest.main()
