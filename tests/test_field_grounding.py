"""Necessary implementation-symbol checks do not certify business semantics."""
import copy
import unittest

from decision_memory.field_grounding import field_evidence_candidates, validate_literal_grounding
from decision_memory.requirement_memory import compile_memory
from decision_memory.requirements import assemble_requirement
from decision_memory.sources import index_materials


def context(later=False):
    nodes = [('N1', '**用户：**\n仅详情入口需要同步。\n**AGENT：**\n通过 mergeFieldUnit 同步。',
              'patchWidgetMap 只处理详情。\nmergeFieldUnit(field, dataSetFieldList);')]
    if later:
        nodes[0] = ('N1', '**用户：**\n仅详情入口需要同步。', 'patchWidgetMap 只处理详情。')
        nodes.append(('N2', '**用户：**\n后续新要求。', 'mergeFieldUnit(field, dataSetFieldList);'))
    return assemble_requirement('FIELDS', [(node, 'step', index_materials(d, a, 'step', node,
        node + '-dialogue.md', node + '-artifact.md')) for node, d, a in nodes])


def compiled(ctx, complete=False):
    refs = [{'source_id': 'A.N1.00001', 'quote': 'patchWidgetMap 只处理详情。'}]
    if complete:
        refs.append({'source_id': 'A.N1.00002', 'quote': 'mergeFieldUnit(field, dataSetFieldList);'})
    payload = {'events': [{'id': 'E1', 'knowledge_key': 'detail-unit', 'node_id': 'N1', 'operation': 'add',
        'previous': None, 'item': {'kind': 'decision',
            'conclusion': {'text': 'patchWidgetMap 使用 mergeFieldUnit 同步。', 'evidence': refs},
            'reason': {'text': '未说明', 'evidence': []}, 'boundary': {'text': '未说明', 'evidence': []}}}],
        'open_questions': []}
    return compile_memory(payload, ctx)


class FieldGroundingTests(unittest.TestCase):
    def test_missing_symbols_are_not_borrowed_from_other_fields(self):
        ctx = context()
        value = compiled(ctx)
        value['items'][0]['reason'] = {'text': '已有 mergeFieldUnit。', 'evidence': [
            {'source_id': 'A.N1.00002', 'quote': 'mergeFieldUnit(field, dataSetFieldList);'}]}
        with self.assertRaisesRegex(ValueError, 'detail-unit.*conclusion.*mergeFieldUnit'):
            validate_literal_grounding(value)

    def test_complete_binding_preserves_field_identity_without_claiming_semantic_truth(self):
        proof = validate_literal_grounding(compiled(context(), complete=True))
        self.assertEqual(proof['policy'], 'source-symbols-v1')
        conclusion = next(row for row in proof['fields'] if row['field'] == 'conclusion')
        self.assertEqual(conclusion['knowledge_key'], 'detail-unit')
        self.assertEqual(conclusion['symbols'], [
            {'symbol': 'mergeFieldUnit', 'source_ids': ['A.N1.00002']},
            {'symbol': 'patchWidgetMap', 'source_ids': ['A.N1.00001']}])
        self.assertNotIn('semantic_verified', proof)

    def test_plain_protocol_terms_dates_and_business_quantities_are_not_code_symbols(self):
        value = compiled(context())
        value['items'][0]['conclusion']['text'] = 'HTTP/JSON 请求仅限本期30条，2026.11.17发布 v0.2。'
        proof = validate_literal_grounding(value)
        self.assertEqual(proof['fields'][0]['symbols'], [])

    def test_qualified_identifiers_and_package_paths_need_their_own_evidence(self):
        for symbol in ('config.unitVO', '@org/dashboard-kit', 'src/widget/actions.ts'):
            with self.subTest(symbol=symbol), self.assertRaises(ValueError):
                value = compiled(context(), complete=True)
                value['items'][0]['conclusion']['text'] = '实现使用 ' + symbol
                validate_literal_grounding(value)

    def test_longer_identifier_or_path_cannot_support_a_shorter_symbol(self):
        for symbol, quoted in (('mergeFieldUnit', 'mergeFieldUnitLegacy'),
                               ('config.unitVO', 'config.unitVOExtra'),
                               ('@org/dashboard-kit', '@org/dashboard-kit-extra'),
                               ('src/widget/actions.ts', 'src/widget/actions.ts.map')):
            with self.subTest(symbol=symbol):
                value = compiled(context())
                value['items'][0]['conclusion'] = {'text': '仅调用 ' + symbol,
                    'evidence': [{'source_id': 'A.N1.00001', 'quote': '仅调用 ' + quoted + '。'}]}
                with self.assertRaisesRegex(ValueError, '字段自身引文'):
                    validate_literal_grounding(value)
                suggestions = field_evidence_candidates(value, context())
                self.assertEqual(suggestions[0]['missing_symbols'], [symbol])

    def test_pascal_case_and_acronym_prefixed_symbols_are_checked(self):
        for symbol in ('MergeFieldUnit', 'WidgetFactory', 'HTTPWidget'):
            with self.subTest(symbol=symbol):
                value = compiled(context())
                value['items'][0]['conclusion']['text'] = '通过 ' + symbol + ' 改造'
                with self.assertRaisesRegex(ValueError, symbol):
                    validate_literal_grounding(value)

    def test_retrieval_is_bounded_stable_and_does_not_mutate_inputs(self):
        ctx = context()
        value = compiled(ctx)
        untouched = copy.deepcopy(value)
        suggestions = field_evidence_candidates(value, ctx, limit=2)
        self.assertEqual(suggestions, field_evidence_candidates(value, ctx, limit=2))
        row = next(row for row in suggestions if row['field'] == 'conclusion')
        self.assertEqual(row['missing_symbols'], ['mergeFieldUnit'])
        self.assertLessEqual(len(row['suggested_sources']), 2)
        self.assertTrue(any('mergeFieldUnit' in source['text'] for source in row['suggested_sources']))
        self.assertEqual(value, untouched)

    def test_retrieval_does_not_suggest_future_nodes_for_current_knowledge(self):
        ctx = context(later=True)
        rows = field_evidence_candidates(compiled(ctx), ctx)
        for row in rows:
            self.assertFalse(any('.N2.' in source['reference_id'] for source in row['suggested_sources']))


if __name__ == '__main__':
    unittest.main()
