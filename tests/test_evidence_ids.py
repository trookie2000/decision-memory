"""Source-reference codecs copy immutable original text, not model quotations."""
import copy
import json
import unittest

from decision_memory.evidence_ids import (decode_event_stream, decode_review_reply, encode_event_stream,
                                         gap_catalog, reference_catalog, source_packet)
from decision_memory.requirement_memory import compile_memory
from decision_memory.requirements import assemble_requirement
from decision_memory.sources import index_materials
from test_requirement_audit import sample


def first_reply():
    return {'events': [{'id': 'E1', 'knowledge_key': 'detail-cache', 'node_id': 'N1', 'operation': 'add',
        'previous': None, 'item': {'kind': 'scope',
            'conclusion': {'text': '仅商品详情缓存改造', 'evidence': ['D.N1.00002#C001', 'A.N1.00002#C001']},
            'reason': {'text': '未说明', 'evidence': []}, 'boundary': {'text': '未说明', 'evidence': []}}}],
        'open_questions': []}


def final_reply():
    value = first_reply()
    value.update(review_notes=[], grounding_review={'fields': [], 'reason_checks': []}, evidence_audit={
        'policy': 'evidence-v1', 'evidence_focus': [], 'admission_checks': [
            {'knowledge_key': 'detail-cache', 'basis': 'user_direct', 'direct': ['D.N1.00002#C001'],
             'proposal': [], 'confirmation': [], 'notes': '用户明确限制范围。'}], 'user_attribution_checks': [],
        'coverage_checks': [{'unit_id': 'U001', 'disposition': 'current', 'semantic_coverage': 'full',
            'targets': [{'type': 'item_field', 'knowledge_key': 'detail-cache', 'field': 'conclusion',
                         'evidence': 'D.N1.00002#C001'}], 'exclusion_reason': None, 'notes': '业务范围。'}]})
    return value


class EvidenceIdTests(unittest.TestCase):
    def test_columnar_packet_roundtrips_every_source_field_and_unknown_role(self):
        context = sample()
        packet = json.loads(source_packet(context))
        self.assertEqual(packet['source_format'], 'columnar-v1')
        self.assertEqual(packet['source_columns'],
                         ['source_id', 'node_id', 'node_order', 'file', 'line', 'role', 'message', 'text'])
        rows = [dict(zip(packet['source_columns'], values)) for values in packet['sources']]
        user = next(row for row in rows if row['source_id'] == 'D.N1.00002')
        self.assertEqual(user, {'source_id': 'D.N1.00002', 'node_id': 'N1', 'node_order': 0,
            'file': 'dialogue.md', 'line': 2, 'role': 'user', 'message': 1,
            'text': '仅商品详情缓存改造；周五上线。'})
        for row in rows:
            original = context.catalog[row['source_id']]
            self.assertEqual([row[k] for k in ('file', 'line', 'role', 'message', 'text')],
                             [original.file, original.line, original.role, original.message, original.text])
        unknown = json.loads(source_packet(sample('没有角色标签的事实。', '最终事实。')))
        self.assertIsNone(next(row for row in unknown['sources'] if row[0] == 'D.N1.00001')[5])

    def test_refs_fill_exact_whole_lines_or_unique_clauses_without_mutating_reply(self):
        context = sample()
        catalog = reference_catalog(context)
        self.assertEqual(catalog['D.N1.00002'],
                         {'source_id': 'D.N1.00002', 'quote': '仅商品详情缓存改造；周五上线。'})
        self.assertEqual(catalog['D.N1.00002#C001'],
                         {'source_id': 'D.N1.00002', 'quote': '仅商品详情缓存改造；'})
        raw = first_reply()
        original = copy.deepcopy(raw)
        compiled = compile_memory(decode_event_stream(raw, context), context)
        self.assertEqual(compiled['items'][0]['conclusion']['evidence'][0]['quote'], '仅商品详情缓存改造；')
        self.assertEqual(raw, original)
        encoded = encode_event_stream(compiled, context)
        self.assertEqual(encoded['events'][0]['item']['conclusion']['evidence'],
                         ['D.N1.00002#C001', 'A.N1.00002#C001'])
        self.assertEqual(compile_memory(decode_event_stream(encoded, context), context), compiled)

    def test_ambiguous_clauses_are_not_silently_assigned_to_the_first_occurrence(self):
        context = sample('**用户：**\n只改详情；只改详情；周五上线。', '最终产物。')
        catalog = reference_catalog(context)
        self.assertNotIn('D.N1.00002#C001', catalog)
        self.assertNotIn('D.N1.00002#C002', catalog)
        self.assertIn('D.N1.00002', catalog)
        self.assertEqual(catalog['D.N1.00002#C003']['quote'], '周五上线。')

    def test_fabricated_duplicate_or_object_style_references_are_rejected(self):
        for refs in (['D.N1.99999'], [' D.N1.00002'], ['D.N1.00002', 'D.N1.00002'],
                     [{'source_id': 'D.N1.00002', 'quote': '模型手写'}], [1], 'D.N1.00002'):
            with self.subTest(refs=refs), self.assertRaises(ValueError):
                raw = first_reply()
                raw['events'][0]['item']['conclusion']['evidence'] = refs
                decode_event_stream(raw, sample())

    def test_all_event_operations_and_node_order_survive_the_codec(self):
        nodes = [('N1', '详情最多重试3次。'), ('N2', '详情不重试。'),
                 ('N3', '保留详情不重试。'), ('N4', '取消详情重试规则，等待新方案。')]
        context = assemble_requirement('TIMELINE', [(node, 'step', index_materials(
            '**用户：**\n' + text, text, 'step', node, node + '-d.md', node + '-a.md')) for node, text in nodes])
        first = first_reply()['events'][0]
        first['item']['conclusion'] = {'text': '详情最多重试3次', 'evidence': ['D.N1.00002']}
        second = copy.deepcopy(first)
        second.update(id='E2', node_id='N2', operation='update', previous='E1')
        second['item']['conclusion'] = {'text': '详情不重试', 'evidence': ['D.N2.00002']}
        merged = {'id': 'E3', 'knowledge_key': 'detail-cache', 'node_id': 'N3', 'operation': 'merge',
                  'previous': 'E2', 'evidence': {'conclusion': ['D.N3.00002']}}
        removed = {'id': 'E4', 'knowledge_key': 'detail-cache', 'node_id': 'N4', 'operation': 'invalidate',
                   'previous': 'E3', 'note': '旧规则已取消', 'evidence': ['D.N4.00002']}
        raw = {'events': [first, second, merged, removed], 'open_questions': []}
        compiled = compile_memory(decode_event_stream(raw, context), context)
        self.assertEqual(compiled['items'], [])
        self.assertEqual([row['operation'] for row in compiled['events']], ['add', 'update', 'merge', 'invalidate'])
        self.assertEqual(compile_memory(decode_event_stream(encode_event_stream(compiled, context), context), context), compiled)
        wrong = copy.deepcopy(raw)
        wrong['events'][0]['item']['conclusion']['evidence'] = ['D.N4.00002']
        with self.assertRaisesRegex(ValueError, '未来'):
            compile_memory(decode_event_stream(wrong, context), context)

    def test_review_refs_expand_without_accepting_model_written_focus(self):
        raw = final_reply()
        stream, proof, audit = decode_review_reply(raw, sample())
        self.assertEqual(audit['admission_checks'][0]['direct'][0]['quote'], '仅商品详情缓存改造；')
        self.assertEqual(audit['coverage_checks'][0]['targets'][0]['evidence']['source_id'], 'D.N1.00002')
        self.assertEqual(proof['reason_checks'], [])
        self.assertIn('events', stream)
        raw['evidence_audit']['evidence_focus'] = [{'source_id': 'D.N1.00002', 'focus_quote': '猜测'}]
        with self.assertRaises(ValueError):
            decode_review_reply(raw, sample())

    def test_gap_ids_bind_exact_source_identity_and_cannot_be_forged(self):
        context = sample('**用户：**\n门槛改为30件，具体取值原因未说明。', '门槛改为30件。')
        self.assertEqual(gap_catalog(context), [{'gap_id': 'G001', 'source_id': 'D.N1.00002',
                                                'quote': '门槛改为30件，具体取值原因未说明。'}])
        raw = final_reply()
        raw['events'][0]['item']['conclusion'] = {'text': '门槛改为30件', 'evidence': ['D.N1.00002']}
        raw['grounding_review']['reason_checks'] = [{'gap_id': 'G001', 'status': 'retained',
            'knowledge_key': 'detail-cache', 'unknown_text': '未说明', 'evidence': ['D.N1.00002'], 'notes': '保留未知。'}]
        raw['evidence_audit']['admission_checks'][0]['direct'] = ['D.N1.00002']
        raw['evidence_audit']['coverage_checks'] = []
        _, proof, _ = decode_review_reply(raw, context)
        check = proof['reason_checks'][0]
        self.assertEqual((check['source_id'], check['quote']),
                         ('D.N1.00002', '门槛改为30件，具体取值原因未说明。'))
        self.assertNotIn('gap_id', check)
        for change in ({'gap_id': 'G999'}, {'source_id': 'A.N1.00001'}, {'quote': '模型伪造声明'}):
            bad = copy.deepcopy(raw)
            bad['grounding_review']['reason_checks'][0].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                decode_review_reply(bad, context)


if __name__ == '__main__':
    unittest.main()
