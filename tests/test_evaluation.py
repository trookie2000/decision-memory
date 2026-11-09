import unittest

from decision_memory.evaluation import make_worksheet, score_worksheet
from test_validation import valid_payload


class EvaluationTests(unittest.TestCase):
    def worksheet(self):
        result = valid_payload()
        result['items'][0]['id'] = 'K001'
        result['sample_id'] = 'example-analysis'
        return make_worksheet(result, [{'id': 'R1', 'text': '门槛120元'}, {'id': 'R2', 'text': '仅本次活动'}])

    def test_unreviewed_items_do_not_become_perfect_scores(self):
        score = score_worksheet(self.worksheet())
        self.assertIsNone(score['precision_reviewed'])
        self.assertIsNone(score['citation_support_reviewed'])
        self.assertIsNone(score['recall'])
        self.assertEqual(score['pending_items'], 1)
        self.assertFalse(score['complete'])

    def test_coverage_counts_unique_reference_ids(self):
        worksheet = self.worksheet()
        worksheet['judgments'][0].update(correct=True, citation_support=True, matches=['R1'])
        score = score_worksheet(worksheet)
        self.assertEqual(score['recall'], .5)
        self.assertEqual(score['precision_reviewed'], 1)
        self.assertTrue(score['complete'])

    def test_rejects_unknown_or_duplicate_judgments(self):
        worksheet = self.worksheet()
        worksheet['judgments'][0].update(correct=True, matches=['unknown'])
        with self.assertRaises(ValueError):
            score_worksheet(worksheet)
        worksheet = self.worksheet()
        worksheet['judgments'].append(worksheet['judgments'][0])
        with self.assertRaises(ValueError):
            score_worksheet(worksheet)


if __name__ == '__main__':
    unittest.main()
