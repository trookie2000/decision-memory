"""Regression cases for current knowledge versus evidenced decision history."""

import copy
import unittest

from decision_memory.memory import reconcile
from decision_memory.pipeline import extract, repair_decisions, validate_decisions
from decision_memory.sources import requirement_from_documents
from decision_memory.storage import render_markdown, result_digest, validate_saved
from decision_memory.validation import validate_result
from test_optimizations import FakeClient


UNSTATED = {'text': '未说明', 'evidence': []}


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.sample = requirement_from_documents('REQ-regression', [
            {'kind': 'D', 'node': 1, 'label': '需求分析', 'name': '上下文/需求确认记录.md',
             'text': '**AGENT：**\n少于10件触发。\n**用户：**\n本期少于10件触发。\n实时曲线后续再定。'},
            {'kind': 'A', 'node': 1, 'label': '需求分析', 'name': '产物/需求分析.md',
             'text': '本期少于10件触发。'},
            {'kind': 'D', 'node': 2, 'label': '技术设计', 'name': '上下文/技术设计确认记录.md',
             'text': '**用户：**\n本期改为少于5件触发，10件太高。\n实时曲线本期不做。\n原来少于10件，现在少于5件。\n接口批量上限为20。'},
            {'kind': 'A', 'node': 2, 'label': '技术设计', 'name': '产物/技术方案.md',
             'text': '本期少于5件触发。\n接口批量上限为30。'},
            {'kind': 'A', 'node': 9, 'label': '补充材料：口径', 'name': '产物/口径说明.md',
             'text': '**用户：**\n少于20件触发。\n浏览pv一次计一次。'},
        ])

    def ref(self, key, quote=None):
        return {'source_id': key, 'quote': quote or self.sample.catalog[key].text}

    def decision(self, status, key, topic='阈值', scope='本期', quote=None, **extra):
        return {'topic': topic, 'scope': scope, 'status': status,
                'text': quote or self.sample.catalog[key].text,
                'evidence': [self.ref(key, quote)], **extra}

    def item(self, key, text=None, quote=None, **fields):
        return {'conclusion': {'text': text or self.sample.catalog[key].text,
                               'evidence': [self.ref(key, quote)]},
                'reason': copy.deepcopy(UNSTATED), 'boundary': copy.deepcopy(UNSTATED), **fields}

    def run_memory(self, items, decisions, questions=None):
        return reconcile(validate_result({'items': items, 'open_questions': questions or []}, self.sample),
                         validate_decisions({'decisions': decisions}, self.sample), self.sample)[0]

    def test_earlier_artifact_expires_and_history_retains_both_sides(self):
        result = self.run_memory([self.item('A1-00001'), self.item('A2-00001')], [
            self.decision('superseded', 'A1-00001'), self.decision('active', 'D2-00002')])
        self.assertEqual([item['conclusion']['text'] for item in result['items']], ['本期少于5件触发。'])
        self.assertEqual(result['items'][0]['id'], 'K001')
        history = result['decision_history'][0]
        self.assertEqual((history['source_nodes'], history['resolved_at_node']), ([1], 2))
        self.assertTrue(history['previously_confirmed'])
        self.assertEqual(history['reason'], UNSTATED)
        self.assertEqual(history['resolution_evidence'][0]['source_id'], 'D2-00002')

    def test_larger_source_id_does_not_make_supplement_a_later_decision(self):
        result = self.run_memory([self.item('A1-00001'), self.item('A9-00002')], [
            self.decision('superseded', 'A1-00001'), self.decision('active', 'A9-00002')])
        self.assertEqual([item['conclusion']['text'] for item in result['items']], ['本期少于10件触发。'])
        self.assertEqual(result['decision_history'], [])
        self.assertEqual([entry['kind'] for entry in result['review_queue']],
                         ['unverified_transition', 'candidate_requires_review'])
        self.assertEqual(result['open_questions'], [])

    def test_non_conflicting_supplementary_definition_remains_usable(self):
        result = self.run_memory([self.item('A9-00003')], [self.decision('active', 'A9-00003', topic='浏览口径')])
        self.assertEqual(len(result['items']), 1)
        self.assertEqual(result['review_queue'], [])

    def test_different_scopes_are_not_treated_as_replacements(self):
        result = self.run_memory([self.item('A1-00001'), self.item('A2-00001')], [
            self.decision('superseded', 'A1-00001', scope='回测'),
            self.decision('active', 'D2-00002', scope='线上')])
        self.assertEqual(len(result['items']), 2)
        self.assertEqual(result['decision_history'], [])
        self.assertEqual(result['review_queue'][0]['kind'], 'unverified_transition')

    def test_two_fragments_on_one_line_do_not_retire_the_current_fact(self):
        result = self.run_memory([
            self.item('D2-00004', text='少于10件', quote='原来少于10件'),
            self.item('D2-00004', text='少于5件', quote='现在少于5件')], [
            self.decision('superseded', 'D2-00004', quote='原来少于10件'),
            self.decision('active', 'D2-00004', quote='现在少于5件')])
        self.assertEqual([item['conclusion']['text'] for item in result['items']], ['少于5件'])

    def test_change_item_can_cite_old_and_new_values(self):
        item = self.item('A1-00001', text='从10件改为5件')
        item['conclusion']['evidence'].append(self.ref('D2-00002'))
        result = self.run_memory([item], [self.decision('superseded', 'A1-00001'),
                                        self.decision('active', 'D2-00002')])
        self.assertEqual(len(result['items']), 1)

    def test_same_stage_user_artifact_conflict_goes_to_review_queue(self):
        conflict = self.decision('conflict', 'D2-00005', topic='接口批量上限')
        conflict['evidence'].append(self.ref('A2-00002'))
        result = self.run_memory([self.item('D2-00005')], [conflict])
        self.assertEqual(result['items'], [])
        self.assertEqual(result['open_questions'], [])
        self.assertEqual(result['review_queue'][0]['kind'], 'source_conflict')

    def test_conflict_queue_is_rendered_and_replayed_as_a_separate_audit_record(self):
        conflict = self.decision('conflict', 'D2-00005', topic='接口批量上限')
        conflict['evidence'].append(self.ref('A2-00002'))
        payload = {'items': [self.item('D2-00005')], 'open_questions': []}
        client = FakeClient([{'decisions': [conflict]}, payload, {**payload, 'review_notes': []}])
        result = extract(self.sample, client)
        result['result_digest'] = result_digest(result)
        self.assertTrue(result['checks']['requires_review'])
        self.assertEqual(result['open_questions'], [])
        self.assertIn('程序核查队列', render_markdown(result))
        validate_saved(result, self.sample)
        forged = copy.deepcopy(result)
        forged['review_queue'][0]['evidence'][0]['line'] = 999
        forged['result_digest'] = result_digest(forged)
        with self.assertRaises(ValueError):
            validate_saved(forged, self.sample)

    def test_trace_repair_is_disclosed_in_output_checks(self):
        invalid = self.decision('active', 'A2-00001', reason={
            'text': '没有原文的原因', 'evidence': [self.ref('A2-00001', '伪造引文')]})
        trace = {'decisions': [invalid]}
        payload = {'items': [self.item('A2-00001')], 'open_questions': []}
        client = FakeClient([trace, trace, payload, {**payload, 'review_notes': []}])
        result = extract(self.sample, client)
        self.assertEqual(len(client.prompts), 4)
        self.assertTrue(result['checks']['citation_repairs'])
        self.assertEqual(result['decisions'][0]['reason'], UNSTATED)
        self.assertTrue(any('决策轨迹' in note and '保守修复' in note for note in result['review_notes']))

    def test_agent_proposal_is_not_confirmed_just_by_being_the_last_message(self):
        result = self.run_memory([self.item('A1-00001')], [
            self.decision('superseded', 'A1-00001'), self.decision('active', 'D1-00002')])
        self.assertEqual(len(result['items']), 1)
        self.assertEqual(result['decision_history'], [])

    def test_pending_question_disappears_after_cited_resolution(self):
        decision = self.decision('pending', 'D1-00005', topic='实时曲线',
                                 resolution_evidence=[self.ref('D2-00003')])
        question = {'text': '实时曲线待确认', 'evidence': [self.ref('D1-00005')]}
        result = self.run_memory([self.item('A2-00001')], [decision], [question])
        self.assertEqual(result['open_questions'], [])
        self.assertEqual(result['decision_history'][0]['status'], 'resolved')

    def test_direct_rejection_can_close_a_proposal_without_an_active_replacement(self):
        decision = self.decision('superseded', 'D1-00002',
                                 resolution_evidence=[self.ref('D2-00002')])
        result = self.run_memory([self.item('D1-00002')], [decision])
        self.assertEqual(result['items'], [])
        self.assertIsNone(result['decision_history'][0]['previously_confirmed'])

    def test_trace_repair_never_fabricates_reason_or_resolution(self):
        invalid = self.decision('superseded', 'A1-00001',
                                resolution_evidence=[self.ref('D2-00002', '伪造引文')],
                                reason={'text': '伪造理由', 'evidence': [self.ref('D2-00002', '不存在')]})
        repaired = repair_decisions({'decisions': [invalid]}, self.sample)[0]
        self.assertEqual(repaired['resolution_evidence'], [])
        self.assertEqual(repaired['reason'], UNSTATED)

    def test_verified_pipeline_persists_and_revalidates_auditable_transitions(self):
        decisions = {'decisions': [self.decision('superseded', 'A1-00001'), self.decision('active', 'D2-00002')]}
        payload = {'items': [self.item('A1-00001'), self.item('A2-00001')], 'open_questions': []}
        client = FakeClient([decisions, payload, {**payload, 'review_notes': []}])
        result = extract(self.sample, client)
        result['result_digest'] = result_digest(result)
        self.assertEqual(len(client.prompts), 3)
        self.assertEqual(len(result['items']), 1)
        self.assertEqual(len(result['input_documents']), 5)
        self.assertFalse(result['checks']['requires_review'])
        validate_saved(result, self.sample)
        self.assertIn('决策历史', render_markdown(result))
        forged = copy.deepcopy(result)
        forged['decision_history'][0]['resolution_evidence'][0]['line'] = 999
        forged['result_digest'] = result_digest(forged)
        with self.assertRaises(ValueError):
            validate_saved(forged, self.sample)


if __name__ == '__main__':
    unittest.main()
