"""Reject scores whose claimed coverage has no valid, grounded prediction."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from decision_memory.official_eval import metrics, validate_judgment
from decision_memory import official_eval
from decision_memory.pipeline import extract
from decision_memory.sources import discover_samples
from decision_memory.storage import result_digest, save_result
import test_optimizations as fixtures

FakeClient, UNSTATED = fixtures.FakeClient, fixtures.UNSTATED


class JudgmentIntegrityTests(unittest.TestCase):
    def payload(self):
        return {
            'items': [
                {'item_id': 'design:K001', 'verdict': 'valid', 'matches': ['R1'],
                 'citation_support': True, 'notes': '结论与边界都有原文依据。'},
                {'item_id': 'design:K002', 'verdict': 'unsupported', 'matches': [],
                 'citation_support': False, 'notes': '原文没有这一断言。'}],
            'positives': [
                {'ref_id': 'R1', 'coverage': 'full', 'items': ['design:K001'], 'notes': '完整表达规则及边界。'},
                {'ref_id': 'R2', 'coverage': 'missing', 'items': [], 'notes': '未找到对应预测。'}]}

    def validate(self, payload):
        return validate_judgment(payload, {'design:K001', 'design:K002'}, {'R1', 'R2'})

    def test_accepts_bidirectional_grounded_coverage(self):
        result = self.validate(self.payload())
        self.assertEqual(result['positives'][0]['items'], ['design:K001'])

    def test_full_coverage_cannot_point_to_missing_or_invalid_prediction(self):
        for supporters in (['does-not-exist'], ['design:K002'], []):
            with self.subTest(supporters=supporters), self.assertRaises(ValueError):
                payload = self.payload()
                payload['positives'][0]['items'] = supporters
                self.validate(payload)

    def test_coverage_requires_citation_support(self):
        payload = self.payload()
        payload['items'][0]['citation_support'] = False
        with self.assertRaises(ValueError):
            self.validate(payload)

    def test_matches_and_reference_support_must_agree_both_ways(self):
        for change in ('missing_match', 'missing_support', 'missing_with_support'):
            with self.subTest(change=change), self.assertRaises(ValueError):
                payload = self.payload()
                if change == 'missing_match':
                    payload['items'][0]['matches'] = []
                elif change == 'missing_support':
                    payload['items'][0]['matches'].append('R2')
                else:
                    payload['positives'][1]['items'] = ['design:K001']
                self.validate(payload)

    def test_duplicate_judgments_and_duplicate_links_are_rejected(self):
        for change in ('item', 'reference', 'match', 'support'):
            with self.subTest(change=change), self.assertRaises(ValueError):
                payload = self.payload()
                if change == 'item':
                    payload['items'].append(copy.deepcopy(payload['items'][0]))
                elif change == 'reference':
                    payload['positives'].append(copy.deepcopy(payload['positives'][0]))
                elif change == 'match':
                    payload['items'][0]['matches'].append('R1')
                else:
                    payload['positives'][0]['items'].append('design:K001')
                self.validate(payload)

    def test_empty_or_missing_explanations_are_rejected(self):
        for section in ('items', 'positives'):
            with self.subTest(section=section), self.assertRaises(ValueError):
                payload = self.payload()
                payload[section][0]['notes'] = ' '
                self.validate(payload)

    def test_strict_recall_determines_projected_points(self):
        judgment = {
            'items': [{'item_id': f'I{i}', 'verdict': 'valid', 'citation_support': True} for i in range(20)],
            'positives': [{'ref_id': f'R{i}', 'coverage': 'full' if i < 17 else 'partial'} for i in range(20)]}
        result = metrics([judgment])
        self.assertEqual(result['recall'], 0.85)
        self.assertEqual(result['recall_strict'], 0.85)
        self.assertEqual(result['recall_weighted'], 0.925)
        self.assertEqual(result['projected_points']['recall'], 16)


class OfficialPredictionIntegrityTests(unittest.TestCase):
    def predictions(self, directory):
        dataset = fixtures.OfficialEvalTests().dataset(directory)
        output = Path(directory) / 'predictions'
        output.mkdir()
        for sample in discover_samples(dataset, requirement_id='REQ-one'):
            items = []
            if sample.stage == 'design':
                items = [{'conclusion': {'text': '甲方负责排序', 'evidence': [
                    {'source_id': 'A00001', 'quote': '甲方负责排序'}]}, 'reason': UNSTATED, 'boundary': UNSTATED}]
            prediction = extract(sample, FakeClient([{'items': items, 'open_questions': []}]), 'baseline')
            save_result(output / (sample.sample_id + '.json'), prediction)
        return dataset, output

    def judgment(self, prefix='design'):
        key = prefix + ':K001'
        return {'items': [{'item_id': key, 'verdict': 'valid', 'matches': ['K1'],
                           'citation_support': True, 'notes': '合成评分回复，仅验证程序。'}],
                'positives': [{'ref_id': 'K1', 'coverage': 'full', 'items': [key], 'notes': '规则完整。'},
                              {'ref_id': 'K2', 'coverage': 'missing', 'items': [], 'notes': '没有门槛预测。'}]}

    def test_forged_quotes_fail_before_any_judging_even_after_rehash(self):
        with tempfile.TemporaryDirectory() as directory:
            dataset, output = self.predictions(directory)
            path = output / 'REQ-one-design.json'
            prediction = json.loads(path.read_text())
            prediction['items'][0]['conclusion']['evidence'][0]['quote'] = '原文没有这句话'
            prediction['result_digest'] = result_digest(prediction)
            path.write_text(json.dumps(prediction))
            judge = FakeClient([])
            with self.assertRaises(ValueError):
                official_eval.evaluate(dataset, output, judge)
            self.assertEqual(judge.prompts, [])

    def test_judge_receives_actual_uncited_source_context_and_input_bindings(self):
        with tempfile.TemporaryDirectory() as directory:
            dataset, output = self.predictions(directory)
            judge = FakeClient([self.judgment()])
            report = official_eval.evaluate(dataset, output, judge)
        request = json.loads(judge.prompts[0][1])
        self.assertTrue(any('失败时不改变候选' in row['text'] for row in request['source_materials']))
        self.assertEqual(len(report['prediction_bindings']), 2)
        self.assertEqual(report['human_review'], 'pending')
        self.assertIsNone(report['official_score'])

    def test_requirement_scope_rejects_missing_stage_before_judging(self):
        with tempfile.TemporaryDirectory() as directory:
            dataset, output = self.predictions(directory)
            (output / 'REQ-one-analysis.json').unlink()
            judge = FakeClient([])
            with self.assertRaises(ValueError):
                official_eval.evaluate(dataset, output, judge)
            self.assertEqual(judge.prompts, [])

    def test_requirement_result_uses_full_requirement_id_and_cannot_mix_granularity(self):
        from decision_memory.requirements import discover_requirements
        from decision_memory.requirement_pipeline import extract_requirement
        with tempfile.TemporaryDirectory() as directory:
            dataset, output = self.predictions(directory)
            sample = discover_requirements(dataset, 'REQ-one')[0]
            event = {'id': 'E1', 'knowledge_key': 'sorting', 'node_id': 'design', 'operation': 'add',
                     'previous': None, 'item': {'conclusion': {'text': '甲方负责排序', 'evidence': [
                         {'source_id': 'A.design.00001', 'quote': '甲方负责排序'}]},
                         'reason': UNSTATED, 'boundary': UNSTATED}}
            result = extract_requirement(sample, FakeClient([{'events': [event], 'open_questions': []}]), 'baseline')
            # save_result also verifies the ordinary JSON/Markdown output path.
            save_result(output / 'requirement.json', result)
            judge = FakeClient([])
            with self.assertRaises(ValueError):
                official_eval.evaluate(dataset, output, judge)
            self.assertEqual(judge.prompts, [])
            (output / 'REQ-one-analysis.json').unlink()
            (output / 'REQ-one-design.json').unlink()
            report = official_eval.evaluate(dataset, output, FakeClient([self.judgment('requirement')]))
        self.assertEqual(list(report['requirements']), ['REQ-one'])
        self.assertEqual(report['total']['recall'], 0.5)


if __name__ == '__main__':
    unittest.main()
