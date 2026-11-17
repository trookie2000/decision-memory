"""Audit contracts over hand-written source fragments, not model-quality claims."""
import copy
import unittest

from decision_memory.evidence_spans import locate_unique_quote
from decision_memory.requirement_audit import build_coverage_catalog, validate_evidence_audit
from decision_memory.requirement_grounding import validate_grounding_review
from decision_memory.requirement_memory import compile_memory
from decision_memory.requirements import assemble_requirement
from decision_memory.sources import index_materials


def sample(dialogue='**用户：**\n仅商品详情缓存改造；周五上线。',
           artifact='# 技术方案\n仅商品详情缓存改造；周五上线。'):
    return assemble_requirement('AUDIT', [('N1', 'design', index_materials(
        dialogue, artifact, 'design', 'N1', 'dialogue.md', 'artifact.md'))])


def ref(source, quote):
    return {'source_id': source, 'quote': quote}


def field(text='未说明', refs=None):
    return {'text': text, 'evidence': refs or []}


def event(refs=None):
    return {'id': 'E1', 'knowledge_key': 'detail-cache', 'node_id': 'N1', 'operation': 'add',
            'previous': None, 'item': {'kind': 'scope',
                'conclusion': field('仅商品详情缓存改造', refs or [
                    ref('D.N1.00002', '仅商品详情缓存改造'), ref('A.N1.00002', '仅商品详情缓存改造')]),
                'reason': field(), 'boundary': field()}}


def grounding(compiled, context, checks=None):
    return validate_grounding_review({'fields': [
        {'knowledge_key': row['knowledge_key'], 'field': name,
         'status': 'unstated' if compiled['items'][index][name]['text'] == '未说明' else 'supported',
         'notes': '逐字段核对原文。'}
        for index, row in enumerate(compiled['memory_index']) for name in ('conclusion', 'reason', 'boundary')],
        'reason_checks': checks or []}, compiled, context)


def target(source, quote):
    return {'type': 'item_field', 'knowledge_key': 'detail-cache', 'field': 'conclusion',
            'evidence': ref(source, quote)}


def coverage(unit, disposition='current', targets=None, semantic='full', exclusion=None):
    return {'unit_id': unit, 'disposition': disposition, 'semantic_coverage': semantic,
            'targets': targets or [], 'exclusion_reason': exclusion, 'notes': '核对原文业务范围。'}


def audit_reply():
    return {'policy': 'evidence-v1', 'evidence_focus': [], 'admission_checks': [
        {'knowledge_key': 'detail-cache', 'basis': 'user_direct',
         'direct': [ref('D.N1.00002', '仅商品详情缓存改造')], 'proposal': [], 'confirmation': [],
         'notes': '用户明确限制为商品详情。'}], 'user_attribution_checks': [],
        'coverage_checks': [
            coverage('U001', targets=[target('D.N1.00002', '仅商品详情缓存改造')]),
            coverage('U002', targets=[target('A.N1.00002', '仅商品详情缓存改造')]),
            coverage('U003', 'non_reusable', semantic='not_applicable', exclusion='schedule')]}


class EvidenceSpanTests(unittest.TestCase):
    def test_unicode_offsets_preserve_bom_crlf_and_full_source_hash(self):
        context = sample('\ufeff**用户：**\r\n😀仅商品详情。\r\n', '仅商品详情。')
        span = locate_unique_quote(context, ref('D.N1.00002', '仅商品'))
        self.assertEqual((span['start'], span['end']), (1, 4))
        self.assertEqual(span['line'], 2)
        self.assertEqual(span['role'], 'user')
        self.assertEqual(span['file_sha256'], context.file_hashes['dialogue.md'])

    def test_repeated_or_missing_quotes_require_disambiguation(self):
        context = sample('**用户：**\n只改详情，只改详情。', '只改详情。')
        for quote in ('只改详情', '不是原文'):
            with self.subTest(quote=quote), self.assertRaises(ValueError):
                locate_unique_quote(context, ref('D.N1.00002', quote))
        self.assertEqual(locate_unique_quote(context, ref('D.N1.00002', '只改详情，只改详情'))['end'], 9)


class RequirementAuditTests(unittest.TestCase):
    def validate(self, value=None, context=None, events=None, questions=None, checks=None):
        context = context or sample()
        compiled = compile_memory({'events': events or [event()], 'open_questions': questions or []}, context)
        proof = grounding(compiled, context, checks)
        return validate_evidence_audit(value if value is not None else audit_reply(), compiled, proof, context)

    def test_catalog_splits_same_line_business_and_schedule(self):
        rows = build_coverage_catalog(sample())
        self.assertEqual([(r['unit_id'], r['source_id'], r['quote']) for r in rows], [
            ('U001', 'D.N1.00002', '仅商品详情缓存改造；'),
            ('U002', 'A.N1.00002', '仅商品详情缓存改造；'),
            ('U003', 'A.N1.00002', '周五上线。')])
        self.assertEqual([(r['start'], r['end']) for r in rows], [(0, 10), (0, 10), (10, 15)])
        result = self.validate()
        self.assertEqual(result['audit_status'], 'passed')
        self.assertEqual(result['unit_catalog'], rows)

    def test_candidate_limit_refuses_overflow_without_truncation(self):
        context = sample('**用户：**\n对。', '\n'.join('约束%d。' % n for n in range(257)))
        with self.assertRaises(ValueError):
            build_coverage_catalog(context)

    def test_unknown_role_is_not_user_direct_even_when_text_looks_like_a_rule(self):
        context = sample('仅商品详情缓存改造；周五上线。')
        value = audit_reply()
        value['admission_checks'][0]['direct'][0]['source_id'] = 'D.N1.00001'
        value['coverage_checks'][0]['targets'][0]['evidence']['source_id'] = 'D.N1.00001'
        current = event([ref('D.N1.00001', '仅商品详情缓存改造'), ref('A.N1.00002', '仅商品详情缓存改造')])
        with self.assertRaises(ValueError):
            self.validate(value, context, [current])

    def test_missing_duplicate_and_unlinked_candidates_fail(self):
        variants = []
        missing = audit_reply(); missing['coverage_checks'].pop(); variants.append(missing)
        duplicate = audit_reply(); duplicate['coverage_checks'].append(copy.deepcopy(duplicate['coverage_checks'][0])); variants.append(duplicate)
        unlinked = audit_reply(); unlinked['coverage_checks'][0]['targets'][0]['evidence'] = ref('A.N1.00002', '仅商品详情缓存改造'); variants.append(unlinked)
        for value in variants:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.validate(value)

    def test_partial_semantics_is_not_passed_as_complete_coverage(self):
        value = audit_reply()
        value['coverage_checks'][0]['semantic_coverage'] = 'partial'
        result = self.validate(value)
        self.assertEqual(result['audit_status'], 'needs_review')
        self.assertEqual(result['coverage_checks'][0]['disposition'], 'needs_review')

    def test_numeric_only_quote_has_position_but_requires_semantic_review(self):
        context = sample('**用户：**\n必须限制为100条。', '必须限制为100条。')
        current = event([ref('D.N1.00002', '100条'), ref('A.N1.00001', '100条')])
        value = audit_reply()
        value['admission_checks'][0]['direct'] = [ref('D.N1.00002', '100条')]
        value['coverage_checks'] = [coverage('U001', targets=[target('D.N1.00002', '100条')], semantic='partial'),
                                    coverage('U002', targets=[target('A.N1.00001', '100条')], semantic='unclear')]
        self.assertEqual(self.validate(value, context, [current])['audit_status'], 'needs_review')

    def test_exclusion_checks_all_current_evidence_not_just_declared_targets(self):
        value = audit_reply()
        value['coverage_checks'][0] = coverage('U001', 'non_reusable', semantic='not_applicable', exclusion='format')
        with self.assertRaises(ValueError):
            self.validate(value)

    def test_focus_separates_disambiguation_context_from_schedule(self):
        quote = '仅商品详情缓存改造；周五上线。'
        current = event([ref('D.N1.00002', quote), ref('A.N1.00002', quote)])
        value = audit_reply()
        for row in value['coverage_checks'][:2]:
            row['targets'][0]['evidence']['quote'] = quote
        value['admission_checks'][0]['direct'][0]['quote'] = quote
        with self.assertRaises(ValueError):
            self.validate(value, events=[current])
        value['evidence_focus'] = [
            {'knowledge_key': 'detail-cache', 'field': 'conclusion', **ref(source, quote),
             'focus_quote': '仅商品详情缓存改造'} for source in ('D.N1.00002', 'A.N1.00002')]
        result = self.validate(value, events=[current])
        self.assertEqual(result['audit_status'], 'passed')
        self.assertEqual(result['evidence_spans'][0]['focus_end'], 9)

    def test_punctuation_only_focus_cannot_hide_all_actual_business_evidence(self):
        quote = '仅商品详情缓存改造；周五上线。'
        current = event([ref('D.N1.00002', quote), ref('A.N1.00002', quote)])
        value = audit_reply()
        value['admission_checks'][0]['direct'][0]['quote'] = quote
        value['evidence_focus'] = [{'knowledge_key': 'detail-cache', 'field': 'conclusion',
            **ref(source, quote), 'focus_quote': '；'} for source in ('D.N1.00002', 'A.N1.00002')]
        value['coverage_checks'] = [coverage(unit, 'non_reusable', semantic='not_applicable', exclusion='format')
                                    for unit in ('U001', 'U002', 'U003')]
        with self.assertRaises(ValueError):
            self.validate(value, events=[current])

    def test_confirmed_proposal_preserves_both_roles_and_order(self):
        context = sample('**AGENT：**\n建议按变更刷新缓存。\n**用户：**\n对。', '按变更刷新缓存。')
        current = event([ref('D.N1.00002', '建议按变更刷新缓存'), ref('D.N1.00004', '对'), ref('A.N1.00001', '按变更刷新缓存')])
        current['item']['conclusion']['text'] = '按变更刷新缓存'
        value = {'policy': 'evidence-v1', 'evidence_focus': [], 'admission_checks': [
            {'knowledge_key': 'detail-cache', 'basis': 'confirmed_proposal', 'direct': [],
             'proposal': [ref('D.N1.00002', '建议按变更刷新缓存')],
             'confirmation': [ref('D.N1.00004', '对')], 'notes': '用户确认上一条提议。'}],
            'user_attribution_checks': [], 'coverage_checks': [
                coverage('U001', targets=[target('A.N1.00001', '按变更刷新缓存')])]}
        self.assertEqual(self.validate(value, context, [current])['audit_status'], 'passed')
        value['admission_checks'][0].update(basis='user_direct', direct=[ref('D.N1.00004', '对')], proposal=[], confirmation=[])
        with self.assertRaises(ValueError):
            self.validate(value, context, [current])

    def test_confirmation_cannot_precede_the_proposal(self):
        context = sample('**用户：**\n对。\n**AGENT：**\n建议按变更刷新缓存。', '按变更刷新缓存。')
        current = event([ref('D.N1.00004', '建议按变更刷新缓存'), ref('D.N1.00002', '对'), ref('A.N1.00001', '按变更刷新缓存')])
        value = audit_reply()
        value['admission_checks'][0].update(basis='confirmed_proposal', direct=[],
            proposal=[ref('D.N1.00004', '建议按变更刷新缓存')], confirmation=[ref('D.N1.00002', '对')])
        value['coverage_checks'] = [coverage('U001', targets=[target('A.N1.00001', '按变更刷新缓存')])]
        with self.assertRaises(ValueError):
            self.validate(value, context, [current])

    def test_role_header_does_not_disguise_a_short_confirmation_as_a_direct_rule(self):
        context = sample('**用户：**对。', '按变更刷新缓存。')
        current = event([ref('D.N1.00001', '**用户：**对。'), ref('A.N1.00001', '按变更刷新缓存')])
        value = audit_reply()
        value['admission_checks'][0]['direct'] = [ref('D.N1.00001', '**用户：**对。')]
        value['coverage_checks'] = [coverage('U001', targets=[target('A.N1.00001', '按变更刷新缓存')])]
        with self.assertRaises(ValueError):
            self.validate(value, context, [current])

    def test_explicit_user_attribution_requires_user_evidence_in_that_field(self):
        current = event()
        current['item']['reason'] = field('用户要求限定改造范围', [ref('A.N1.00002', '仅商品详情缓存改造')])
        value = audit_reply()
        with self.assertRaises(ValueError):
            self.validate(value, events=[current])
        value['user_attribution_checks'] = [{'knowledge_key': 'detail-cache', 'field': 'reason',
            'evidence': [ref('A.N1.00002', '仅商品详情缓存改造')], 'notes': '用户要求。'}]
        with self.assertRaises(ValueError):
            self.validate(value, events=[current])

    def test_structure_errors_are_not_hidden_by_needs_review(self):
        value = audit_reply()
        value['admission_checks'][0]['basis'] = 'needs_review'
        value['coverage_checks'].pop()
        with self.assertRaises(ValueError):
            self.validate(value)
        value['coverage_checks'].append(coverage('U003', 'non_reusable', semantic='not_applicable', exclusion='schedule'))
        self.assertEqual(self.validate(value)['audit_status'], 'needs_review')

    def test_saved_audit_rejects_internal_catalog_or_binding_tampering(self):
        context = sample()
        compiled = compile_memory({'events': [event()], 'open_questions': []}, context)
        proof = grounding(compiled, context)
        saved = self.validate()
        self.assertEqual(validate_evidence_audit(saved, compiled, proof, context), saved)
        for key in ('unit_catalog', 'input_digest', 'evidence_spans'):
            changed = copy.deepcopy(saved)
            changed[key] = [] if isinstance(changed[key], list) else 'forged'
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_evidence_audit(changed, compiled, proof, context)

    def test_history_targets_require_real_superseded_or_invalidated_events(self):
        context = assemble_requirement('AUDIT', [
            (name, 'design', index_materials('**用户：**\n' + text, text, 'design', name,
                                            name + '-dialogue.md', name + '-artifact.md'))
            for name, text in [('N1', '必须限制100条。'), ('N2', '取消100条限制。')]])
        current = event([ref('D.N1.00002', '必须限制100条'), ref('A.N1.00001', '必须限制100条')])
        cancel = {'id': 'E2', 'knowledge_key': 'detail-cache', 'node_id': 'N2', 'operation': 'invalidate',
                  'previous': 'E1', 'note': '取消原限制', 'evidence': [
                      ref('D.N2.00002', '取消100条限制'), ref('A.N2.00001', '取消100条限制')]}
        compiled = compile_memory({'events': [current, cancel], 'open_questions': []}, context)
        rows = []
        for unit, source, quote, eid, field_name in [
                ('U001', 'D.N1.00002', '必须限制100条', 'E1', 'conclusion'),
                ('U002', 'A.N1.00001', '必须限制100条', 'E1', 'conclusion'),
                ('U003', 'D.N2.00002', '取消100条限制', 'E2', 'note'),
                ('U004', 'A.N2.00001', '取消100条限制', 'E2', 'note')]:
            rows.append(coverage(unit, 'history', targets=[{'type': 'history_event', 'event_id': eid,
                'field': field_name, 'evidence': ref(source, quote)}]))
        value = {'policy': 'evidence-v1', 'evidence_focus': [], 'admission_checks': [],
                 'user_attribution_checks': [], 'coverage_checks': rows}
        proof = grounding(compiled, context)
        self.assertEqual(validate_evidence_audit(value, compiled, proof, context)['audit_status'], 'passed')
        value['coverage_checks'][0]['targets'][0]['event_id'] = 'E9'
        with self.assertRaises(ValueError):
            validate_evidence_audit(value, compiled, proof, context)

    def test_pending_target_is_bound_to_the_actual_question_evidence(self):
        context = sample('**用户：**\n报警范围待确认。', '报警范围待确认。')
        question = field('报警范围待确认', [ref('D.N1.00002', '报警范围待确认'), ref('A.N1.00001', '报警范围待确认')])
        compiled = compile_memory({'events': [], 'open_questions': [question]}, context)
        value = {'policy': 'evidence-v1', 'evidence_focus': [], 'admission_checks': [],
                 'user_attribution_checks': [], 'coverage_checks': [
                     coverage(unit, 'pending', targets=[{'type': 'open_question', 'question_id': 'Q001',
                        'evidence': ref(source, '报警范围待确认')}])
                     for unit, source in [('U001', 'D.N1.00002'), ('U002', 'A.N1.00001')]]}
        proof = grounding(compiled, context)
        self.assertEqual(validate_evidence_audit(value, compiled, proof, context)['audit_status'], 'passed')
        value['coverage_checks'][0]['targets'][0]['question_id'] = 'Q002'
        with self.assertRaises(ValueError):
            validate_evidence_audit(value, compiled, proof, context)

    def test_reason_unknown_links_to_retained_check_when_reason_has_no_evidence(self):
        context = sample('**用户：**\n必须限制100条；理由未说明。', '必须限制100条；理由未说明。')
        current = event([ref('D.N1.00002', '必须限制100条'), ref('A.N1.00001', '必须限制100条')])
        checks = [{'source_id': source, 'quote': '理由未说明。', 'status': 'retained',
                   'knowledge_key': 'detail-cache', 'unknown_text': '未说明',
                   'evidence': [ref(source, '理由未说明')], 'notes': '原文未提供理由。'}
                  for source in ('D.N1.00002', 'A.N1.00001')]
        value = audit_reply()
        value['admission_checks'][0]['direct'] = [ref('D.N1.00002', '必须限制100条')]
        value['coverage_checks'] = []
        for business_unit, gap_unit, source in [('U001', 'U002', 'D.N1.00002'), ('U003', 'U004', 'A.N1.00001')]:
            value['coverage_checks'].append(coverage(business_unit, targets=[target(source, '必须限制100条')]))
            value['coverage_checks'].append(coverage(gap_unit, 'pending', targets=[{
                'type': 'reason_check', 'source_id': source, 'quote': '理由未说明。',
                'evidence': ref(source, '理由未说明')}]))
        self.assertEqual(self.validate(value, context, [current], checks=checks)['audit_status'], 'passed')


if __name__ == '__main__':
    unittest.main()
