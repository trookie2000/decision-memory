import copy
import unittest

from decision_memory.requirement_flow import summarize_comparisons


class ComparisonSummaryTests(unittest.TestCase):
    def test_no_overall_quality_ratio_until_both_methods_finish_the_whole_batch(self):
        values = {'complete': True, 'decisions': {'correct': 1, 'reviewed': 1,
                    'citation_supported': 1, 'citation_reviewed': 1},
                  'reference_coverage': {'decision': {'total': 1, 'full': 1},
                                         'uncertainty': {'total': 1, 'full': 1}}}
        pending = copy.deepcopy(values)
        pending['complete'] = False
        metrics = {'calls': 1, 'http_attempts': 1, 'seconds': 1,
                   'prompt_tokens': 10, 'completion_tokens': 5, 'estimated_cost': None,
                   'usage_complete': True, 'cost_unit': None}
        score = {'kind': 'requirement-comparison', 'complete': False,
                 'methods': {'A': 'whole', 'B': 'per_node'}, 'variants': {'A': values, 'B': pending},
                 'metrics': {'whole': metrics, 'per_node': metrics}}
        summary = summarize_comparisons([{'score': score}], 1)
        self.assertEqual(summary['fully_scored_cases'], 0)
        for row in summary['methods'].values():
            for name in ('precision', 'citation_support', 'decision_coverage', 'uncertainty_coverage'):
                self.assertIsNone(row[name], name)
            self.assertEqual(row['metrics']['calls'], 1)
            self.assertEqual(row['reference_counts']['decision']['full'], 1)


if __name__ == '__main__':
    unittest.main()
