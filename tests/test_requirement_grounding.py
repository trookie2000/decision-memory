"""Regression cases for missing rationale evidence and partial unknown reasons."""
import copy
import unittest

from decision_memory.requirement_pipeline import extract_requirement, validate_requirement_saved
from decision_memory.requirement_grounding import reason_obligations
from decision_memory.requirements import assemble_requirement
from decision_memory.sources import index_materials
from decision_memory.storage import render_markdown, result_digest
from test_requirement_pipeline import Client


def field(text='未说明', source=None, quote=None):
    return {'text': text, 'evidence': [] if source is None else [{'source_id': source, 'quote': quote}]}


def threshold_sample():
    return assemble_requirement('DEMO-ALERT', [
        ('N1', 'analysis', index_materials('**用户：**\n预警阈值定为 80 件。',
          '预警阈值 80 件：原因未说明。', 'analysis', 'N1', 'n1-dialogue.md', 'n1-artifact.md')),
        ('N2', 'design', index_materials('**用户：**\n80 件太高，改成 25 件。',
          '预警阈值 25 件：具体取值原因未说明。', 'design', 'N2', 'n2-dialogue.md', 'n2-artifact.md')),
        ('N3', 'coding', index_materials('**用户：**\n保持 25 件阈值。',
          '预警阈值为 25 件。', 'coding', 'N3', 'n3-dialogue.md', 'n3-artifact.md')),
    ])


def threshold_events(repaired=False):
    first = {'id': 'E1', 'knowledge_key': 'threshold', 'node_id': 'N1', 'operation': 'add',
             'previous': None, 'item': {'kind': 'metric',
             'conclusion': field('预警阈值为 80 件', 'D.N1.00002', '预警阈值定为 80 件'),
             'reason': field(), 'boundary': field()}}
    reason = field('80 件太高', 'D.N2.00002', '80 件太高')
    if repaired:
        reason['text'] = '80 件太高，所以降低阈值；为何恰好选 25 件未说明。'
        reason['evidence'].append({'source_id': 'A.N2.00001', 'quote': '具体取值原因未说明'})
    second = {'id': 'E2', 'knowledge_key': 'threshold', 'node_id': 'N2', 'operation': 'update',
              'previous': 'E1', 'item': {'kind': 'metric',
              'conclusion': field('预警阈值为 25 件', 'D.N2.00002', '改成 25 件'),
              'reason': reason, 'boundary': field()}}
    return [first, second]


def threshold_review():
    return {'events': threshold_events(True), 'open_questions': [], 'review_notes': ['补充具体取值仍未知。'],
            'grounding_review': {
                'fields': [
                    {'knowledge_key': 'threshold', 'field': 'conclusion', 'status': 'supported',
                     'notes': '用户明确将阈值调整到 25 件。'},
                    {'knowledge_key': 'threshold', 'field': 'reason', 'status': 'partial',
                     'notes': '降低阈值的原因已知，具体数值的选择依据仍未知。'},
                    {'knowledge_key': 'threshold', 'field': 'boundary', 'status': 'unstated',
                     'notes': '没有另行确认适用边界。'}],
                'reason_checks': [
                    {'source_id': 'A.N1.00001', 'quote': '预警阈值 80 件：原因未说明。',
                     'status': 'superseded', 'knowledge_key': None,
                     'unknown_text': '', 'evidence': [{'source_id': 'D.N2.00002', 'quote': '改成 25 件'}],
                     'notes': '80 件规则已由后续节点替换。'},
                    {'source_id': 'A.N2.00001', 'quote': '预警阈值 25 件：具体取值原因未说明。',
                     'status': 'retained', 'knowledge_key': 'threshold',
                     'unknown_text': '为何恰好选 25 件未说明',
                     'evidence': [{'source_id': 'A.N2.00001', 'quote': '具体取值原因未说明'}],
                     'notes': '取值理由未知保留在当前理由字段中。'}]}}


class RequirementGroundingTests(unittest.TestCase):
    def run_review(self, review):
        initial = {'events': threshold_events(), 'open_questions': []}
        return extract_requirement(threshold_sample(), Client([initial, review, copy.deepcopy(review)]), 'verified')

    def test_verified_rejects_a_rewrite_without_field_review(self):
        review = threshold_review()
        review.pop('grounding_review')
        with self.assertRaisesRegex(ValueError, '复核'):
            self.run_review(review)

    def test_partial_reason_keeps_known_explanation_and_exact_value_unknown(self):
        initial = {'events': threshold_events(), 'open_questions': []}
        client = Client([initial, threshold_review()])
        result = extract_requirement(threshold_sample(), client, 'verified')
        reason = result['items'][0]['reason']
        self.assertEqual(reason['text'], '80 件太高，所以降低阈值；为何恰好选 25 件未说明。')
        self.assertEqual([row['source_id'] for row in reason['evidence']], ['D.N2.00002', 'A.N2.00001'])
        self.assertEqual(result['metrics']['calls'], 2)
        self.assertEqual(result['checks']['field_grounding'], 'model_reviewed')
        self.assertEqual(result['schema_version'], 2)
        self.assertEqual(result['grounding_review']['reason_checks'][0]['status'], 'superseded')
        markdown = render_markdown(result)
        self.assertIn('为何恰好选 25 件未说明', markdown)
        self.assertIn('A.N1.00001', markdown)

    def test_explicit_unknown_cannot_be_claimed_retained_when_omitted_from_reason(self):
        review = threshold_review()
        review['events'] = threshold_events(False)
        with self.assertRaisesRegex(ValueError, '未知'):
            self.run_review(review)

    def test_every_explicit_unknown_source_requires_a_disposition(self):
        review = threshold_review()
        review['grounding_review']['reason_checks'].pop()
        with self.assertRaisesRegex(ValueError, '未知|清单'):
            self.run_review(review)

    def test_missing_duplicate_and_unsupported_field_verdicts_are_rejected(self):
        for change in ('missing', 'duplicate', 'unsupported'):
            with self.subTest(change=change):
                review = threshold_review()
                rows = review['grounding_review']['fields']
                if change == 'missing':
                    rows.pop()
                elif change == 'duplicate':
                    rows.append(copy.deepcopy(rows[0]))
                else:
                    rows[1]['status'] = 'unsupported'
                with self.assertRaisesRegex(ValueError, '字段|复核'):
                    self.run_review(review)

    def test_unknown_cannot_be_dismissed_using_its_original_node(self):
        review = threshold_review()
        review['grounding_review']['reason_checks'][0]['evidence'] = [
            {'source_id': 'A.N1.00001', 'quote': '原因未说明'}]
        with self.assertRaisesRegex(ValueError, '后续|更晚'):
            self.run_review(review)

    def test_retained_label_cannot_replace_actual_unknown_text_and_quote(self):
        review = threshold_review()
        review['events'] = threshold_events(False)
        review['events'][-1]['item']['reason']['evidence'].append(
            {'source_id': 'A.N2.00001', 'quote': '预警阈值 25 件'})
        check = review['grounding_review']['reason_checks'][1]
        check['unknown_text'] = '80 件太高'
        check['evidence'][0]['quote'] = '预警阈值 25 件'
        with self.assertRaisesRegex(ValueError, '未知'):
            self.run_review(review)

    def test_retained_gap_requires_the_unknown_part_of_the_citation(self):
        review = threshold_review()
        check = review['grounding_review']['reason_checks'][1]
        check['evidence'][0]['quote'] = '预警阈值 25 件'
        review['events'][-1]['item']['reason']['evidence'][1]['quote'] = '预警阈值 25 件'
        with self.assertRaisesRegex(ValueError, '未知|声明'):
            self.run_review(review)

    def test_an_unrelated_gap_on_the_same_line_can_be_out_of_scope(self):
        sample = assemble_requirement('DEMO-MIXED', [('N1', 'design', index_materials(
            '**用户：**\n阈值固定为 25 件。周五上线的原因未说明。', '阈值固定为 25 件。',
            'design', 'N1', 'dialogue.md', 'artifact.md'))])
        event = threshold_events()[0]
        event['item']['conclusion'] = field('阈值固定为 25 件', 'D.N1.00002', '阈值固定为 25 件')
        review = {'events': [event], 'open_questions': [], 'review_notes': [], 'grounding_review': {
            'fields': [{'knowledge_key': 'threshold', 'field': name, 'status': status, 'notes': '已核对。'}
                for name, status in [('conclusion', 'supported'), ('reason', 'unstated'), ('boundary', 'unstated')]],
            'reason_checks': [{'source_id': 'D.N1.00002', 'quote': '周五上线的原因未说明。',
                'status': 'out_of_scope', 'knowledge_key': None,
                'unknown_text': '', 'evidence': [{'source_id': 'D.N1.00002', 'quote': '周五上线的原因未说明'}],
                'notes': '上线日期属于排期，按提取规则排除。'}]}}
        result = extract_requirement(sample, Client([
            {'events': [event], 'open_questions': []}, review, copy.deepcopy(review)]), 'verified')
        self.assertEqual(result['items'][0]['conclusion']['text'], '阈值固定为 25 件')
        self.assertEqual(result['grounding_review']['reason_checks'][0]['status'], 'out_of_scope')

    def test_two_gaps_on_one_line_can_have_different_dispositions(self):
        sample = assemble_requirement('DEMO-TWO-GAPS', [('N1', 'design', index_materials(
            '**用户：**\n阈值固定为 25 件，取值原因未说明。周五上线的原因未说明。', '阈值固定为 25 件。',
            'design', 'N1', 'dialogue.md', 'artifact.md'))])
        event = threshold_events()[0]
        event['item']['conclusion'] = field('阈值固定为 25 件', 'D.N1.00002', '阈值固定为 25 件')
        review = {'events': [event], 'open_questions': [], 'review_notes': [], 'grounding_review': {
            'fields': [{'knowledge_key': 'threshold', 'field': name, 'status': status, 'notes': '已核对。'}
                for name, status in [('conclusion', 'supported'), ('reason', 'unstated'), ('boundary', 'unstated')]],
            'reason_checks': [
                {'source_id': 'D.N1.00002', 'quote': '阈值固定为 25 件，取值原因未说明。',
                 'status': 'retained', 'knowledge_key': 'threshold', 'unknown_text': '未说明',
                 'evidence': [{'source_id': 'D.N1.00002', 'quote': '取值原因未说明'}],
                 'notes': '阈值选定但理由未知。'},
                {'source_id': 'D.N1.00002', 'quote': '周五上线的原因未说明。',
                 'status': 'out_of_scope', 'knowledge_key': None, 'unknown_text': '',
                 'evidence': [{'source_id': 'D.N1.00002', 'quote': '周五上线的原因未说明'}],
                 'notes': '排期不作为业务知识。'}]}}
        result = extract_requirement(sample, Client([{'events': [event], 'open_questions': []}, review]), 'verified')
        self.assertEqual([row['status'] for row in result['grounding_review']['reason_checks']],
                         ['retained', 'out_of_scope'])
        self.assertEqual(result['items'][0]['reason'], {'text': '未说明', 'evidence': []})
        self.assertEqual(result['open_questions'], [])

    def test_unknown_disposition_cannot_fabricate_evidence(self):
        review = threshold_review()
        review['grounding_review']['reason_checks'][1]['evidence'][0]['quote'] = '模型猜出的理由'
        with self.assertRaisesRegex(ValueError, '引文'):
            self.run_review(review)

    def test_review_binds_current_fields_even_when_latest_event_is_merge(self):
        review = threshold_review()
        review['events'].append({'id': 'E3', 'knowledge_key': 'threshold', 'node_id': 'N3',
                                 'operation': 'merge', 'previous': 'E2', 'evidence': {
                                     'conclusion': [{'source_id': 'D.N3.00002', 'quote': '保持 25 件阈值'}]}})
        result = self.run_review(review)
        self.assertEqual(result['memory_index'][0]['event_id'], 'E3')
        self.assertEqual(result['grounding_review']['fields'][1]['status'], 'partial')
        self.assertEqual(len(result['items'][0]['conclusion']['evidence']), 2)

    def test_merge_can_add_a_later_source_for_the_same_still_unknown_reason(self):
        sample = threshold_sample()
        last = index_materials('**用户：**\n保持 25 件阈值。', '预警阈值 25 件：取值原因未说明。',
                               'coding', 'N3', 'n3-dialogue.md', 'n3-artifact.md')
        # Rebuild from literal source text, not from any prediction or reference answer.
        sample = assemble_requirement('DEMO-ALERT', [
            ('N1', 'analysis', index_materials('**用户：**\n预警阈值定为 80 件。',
                '预警阈值 80 件：原因未说明。', 'analysis', 'N1', 'n1-dialogue.md', 'n1-artifact.md')),
            ('N2', 'design', index_materials('**用户：**\n80 件太高，改成 25 件。',
                '预警阈值 25 件：具体取值原因未说明。', 'design', 'N2', 'n2-dialogue.md', 'n2-artifact.md')),
            ('N3', 'coding', last)])
        review = threshold_review()
        review['events'].append({'id': 'E3', 'knowledge_key': 'threshold', 'node_id': 'N3',
            'operation': 'merge', 'previous': 'E2', 'evidence': {
                'conclusion': [{'source_id': 'D.N3.00002', 'quote': '保持 25 件阈值'}],
                'reason': [{'source_id': 'A.N3.00001', 'quote': '取值原因未说明'}]}})
        review['grounding_review']['reason_checks'].append({
            'source_id': 'A.N3.00001', 'quote': '预警阈值 25 件：取值原因未说明。',
            'status': 'retained', 'knowledge_key': 'threshold',
            'unknown_text': '为何恰好选 25 件未说明',
            'evidence': [{'source_id': 'A.N3.00001', 'quote': '取值原因未说明'}],
            'notes': '当前 merge 只增加仍未知的同一理由来源。'})
        initial = {'events': threshold_events(), 'open_questions': []}
        result = extract_requirement(sample, Client([initial, review, copy.deepcopy(review)]), 'verified')
        self.assertEqual(result['memory_index'][0]['event_id'], 'E3')
        self.assertEqual(result['grounding_review']['reason_checks'][-1]['source_id'], 'A.N3.00001')

    def test_reason_section_headings_are_not_individual_unknown_assertions(self):
        sample = assemble_requirement('DEMO-HEADER', [('N1', 'design', index_materials(
            '**用户：**\n阈值已经确认。', '## 原因未说明的决定\n阈值为 25 件：原因未说明。',
            'design', 'N1', 'dialogue.md', 'artifact.md'))])
        self.assertEqual(reason_obligations(sample), [
            {'source_id': 'A.N1.00002', 'quote': '阈值为 25 件：原因未说明。'}])

    def test_invalid_field_status_is_rejected_as_a_repairable_validation_error(self):
        review = threshold_review()
        review['grounding_review']['fields'][0]['status'] = []
        with self.assertRaises(ValueError):
            self.run_review(review)

    def test_one_bad_review_can_be_repaired_without_repeating_extraction(self):
        bad = threshold_review()
        bad['grounding_review']['fields'][1]['status'] = 'unsupported'
        initial = {'events': threshold_events(), 'open_questions': []}
        client = Client([initial, bad, threshold_review()])
        result = extract_requirement(threshold_sample(), client, 'verified')
        self.assertEqual(result['metrics']['calls'], 3)
        self.assertEqual(result['items'][0]['reason']['text'],
                         '80 件太高，所以降低阈值；为何恰好选 25 件未说明。')

    def test_new_saved_verified_result_cannot_drop_review_even_after_rehash(self):
        sample = threshold_sample()
        result = self.run_review(threshold_review())
        result['result_digest'] = result_digest(result)
        validate_requirement_saved(result, sample)
        broken = copy.deepcopy(result)
        broken.pop('grounding_review')
        broken['result_digest'] = result_digest(broken)
        with self.assertRaisesRegex(ValueError, '复核'):
            validate_requirement_saved(broken, sample)

    def test_saved_review_cannot_claim_it_was_not_model_reviewed(self):
        result = self.run_review(threshold_review())
        result['checks']['model_review'] = False
        result['result_digest'] = result_digest(result)
        with self.assertRaisesRegex(ValueError, '复核'):
            validate_requirement_saved(result, threshold_sample())

    def test_unknown_reason_citation_is_replaced_with_evidence_for_the_known_reason(self):
        sample = assemble_requirement('DEMO-AUTH', [('N1', 'design', index_materials(
            '**用户：**\n访问判断交给导出服务，因为它便于维护细粒度业务规则。\n本期采用字段级校验。',
            '表级改为字段级：原因未说明。', 'design', 'N1', 'dialogue.md', 'artifact.md'))])
        event = {'id': 'E1', 'knowledge_key': 'field-check', 'node_id': 'N1', 'operation': 'add',
            'previous': None, 'item': {'kind': 'decision',
            'conclusion': field('本期由导出服务执行字段级访问校验', 'D.N1.00003', '本期采用字段级校验'),
            'reason': field('导出服务便于维护细粒度业务规则', 'A.N1.00001', '原因未说明'),
            'boundary': field()}}
        event['item']['conclusion']['evidence'].append(
            {'source_id': 'D.N1.00002', 'quote': '访问判断交给导出服务'})
        repaired = copy.deepcopy(event)
        repaired['item']['reason'] = field('导出服务便于维护细粒度业务规则；表级改为字段级的原因未说明。',
            'D.N1.00002', '因为它便于维护细粒度业务规则')
        repaired['item']['reason']['evidence'].append(
            {'source_id': 'A.N1.00001', 'quote': '表级改为字段级：原因未说明'})
        review = {'events': [repaired], 'open_questions': [], 'review_notes': ['纠正理由的引用。'],
            'grounding_review': {'fields': [
                {'knowledge_key': 'field-check', 'field': name, 'status': status, 'notes': '已核对对应原文。'}
                for name, status in [('conclusion', 'supported'), ('reason', 'partial'), ('boundary', 'unstated')]],
                'reason_checks': [{'source_id': 'A.N1.00001', 'quote': '表级改为字段级：原因未说明。',
                    'status': 'retained',
                    'knowledge_key': 'field-check', 'unknown_text': '表级改为字段级的原因未说明',
                    'evidence': [{'source_id': 'A.N1.00001', 'quote': '表级改为字段级：原因未说明'}],
                    'notes': '服务职责的理由不等于改变校验粒度的理由。'}]}}
        client = Client([{'events': [event], 'open_questions': []}, review])
        result = extract_requirement(sample, client, 'verified')
        reason = result['items'][0]['reason']
        self.assertEqual(reason['evidence'][0]['source_id'], 'D.N1.00002')
        self.assertEqual(reason['evidence'][0]['quote'], '因为它便于维护细粒度业务规则')
        self.assertEqual(reason['evidence'][1]['quote'], '表级改为字段级：原因未说明')
        self.assertEqual(result['metrics']['calls'], 2)

    def test_fully_unstated_reason_keeps_empty_evidence_and_a_separate_gap_source(self):
        sample = assemble_requirement('DEMO-UNKNOWN', [('N1', 'design', index_materials(
            '**用户：**\n阈值固定为 25 件。', '阈值 25 件：原因未说明。',
            'design', 'N1', 'dialogue.md', 'artifact.md'))])
        event = threshold_events()[0]
        event['item']['conclusion'] = field('阈值固定为 25 件', 'D.N1.00002', '阈值固定为 25 件')
        review = {'events': [event], 'open_questions': [], 'review_notes': [], 'grounding_review': {
            'fields': [{'knowledge_key': 'threshold', 'field': name, 'status': status, 'notes': '逐字段核对。'}
                for name, status in [('conclusion', 'supported'), ('reason', 'unstated'), ('boundary', 'unstated')]],
            'reason_checks': [{'source_id': 'A.N1.00001', 'quote': '阈值 25 件：原因未说明。',
                'status': 'retained', 'knowledge_key': 'threshold',
                'unknown_text': '未说明', 'evidence': [{'source_id': 'A.N1.00001', 'quote': '原因未说明'}],
                'notes': '原文没有说明阈值原因。'}]}}
        result = extract_requirement(sample, Client([{'events': [event], 'open_questions': []}, review]), 'verified')
        self.assertEqual(result['items'][0]['reason'], {'text': '未说明', 'evidence': []})
        self.assertEqual(result['grounding_review']['reason_checks'][0]['evidence'][0]['file'], 'artifact.md')

    def test_baseline_artifact_only_and_legacy_do_not_claim_semantic_review(self):
        sample = threshold_sample()
        for mode, source, quote in [('baseline', 'D.N1.00002', '预警阈值定为 80 件'),
                                    ('artifact-only', 'A.N1.00001', '预警阈值 80 件')]:
            with self.subTest(mode=mode):
                event = threshold_events()[0]
                event['item']['conclusion'] = field('预警阈值为 80 件', source, quote)
                client = Client([{'events': [event], 'open_questions': []}])
                result = extract_requirement(sample, client, mode)
                self.assertEqual(result['checks']['field_grounding'], 'not_reviewed')
                self.assertEqual(result['metrics']['calls'], 1)
                result['result_digest'] = result_digest(result)
                validate_requirement_saved(result, sample)
                legacy = copy.deepcopy(result)
                legacy['schema_version'] = 1
                legacy.pop('grounding_review', None)
                legacy['checks'].pop('field_grounding')
                legacy['result_digest'] = result_digest(legacy)
                validate_requirement_saved(legacy, sample)


if __name__ == '__main__':
    unittest.main()
