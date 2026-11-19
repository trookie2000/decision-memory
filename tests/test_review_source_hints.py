import unittest

from decision_memory.requirements import assemble_requirement
from decision_memory.source_hints import build_review_hints
from decision_memory.sources import make_sample


class ReviewSourceHintTests(unittest.TestCase):
    def test_field_witnesses_locate_peer_evidence_without_borrowing_it(self):
        from decision_memory.source_hints import field_witnesses

        sample = make_sample(
            '用户：使用蓝色隔板，避免冷藏柜串温。\n'
            '用户：新款只适用于夜间收货。\n'
            '用户：仅延迟拆掉旧标签。',
            '采用蓝色隔板。', 'design',
        )
        draft = {'items': [{
            'conclusion': {'text': '采用蓝色隔板，只用于夜间收货。', 'evidence': [
                {'source_id': 'D00001', 'quote': sample.catalog['D00001'].text},
            ]},
            'reason': {'text': '未说明', 'evidence': []},
            'boundary': {'text': '只用于夜间收货。', 'evidence': [
                {'source_id': 'D00002', 'quote': sample.catalog['D00002'].text},
            ]},
        }]}
        hints = {hint['field']: hint['candidate_source_ids'] for hint in field_witnesses(draft, sample)}
        self.assertEqual(hints['conclusion'], ['D00002'])
        self.assertEqual(hints['reason'], ['D00001'])
        self.assertEqual(draft['items'][0]['reason']['evidence'], [])
        self.assertNotIn('boundary', hints)

    def test_bounded_turn_candidates_leave_room_for_later_revisions(self):
        sample = make_sample(
            '**王工（值班）** 库存复核。\n旧梯会卡住托盘。\n灰度期间生效。\n'
            '仅核对破损。\n仅导出账目。\n仅小组签名。\n'
            '**许工（审核）** 库存复核取消，改为抽查。',
            '本期改为库存抽查。', 'design',
        )
        answer = {'items': [{'conclusion': {'text': '开展库存复核。', 'evidence': [
            {'source_id': 'D00001', 'quote': '库存复核。'},
        ]}, 'reason': {'text': '未说明', 'evidence': []},
            'boundary': {'text': '未说明', 'evidence': []}}], 'open_questions': []}

        hints = {row['field']: row for row in build_review_hints(answer, sample)['fields']}

        self.assertIn('D00002', hints['reason']['candidate_source_ids'])
        self.assertIn('D00003', hints['boundary']['candidate_source_ids'])
        self.assertIn('D00007', hints['boundary']['later_related_source_ids'])

    def test_a_scope_and_reason_can_continue_a_different_field_in_the_same_turn(self):
        sample = make_sample(
            '**赵师傅（库管）**　货箱进复核区。\n'
            '原因是旧托盘会卡住电梯。\n'
            '仅适用特殊规格。\n'
            '**9:30 · Assistant** 建议货箱不用复核。',
            '货箱转入复核区。', 'design',
        )
        answer = {'items': [{'conclusion': {'text': '将货箱转入复核区。', 'evidence': [
            {'source_id': 'D00001', 'quote': '货箱进复核区。'},
            {'source_id': 'A00001', 'quote': '货箱转入复核区。'},
        ]}, 'reason': {'text': '未说明', 'evidence': []},
            'boundary': {'text': '未说明', 'evidence': []}}], 'open_questions': []}

        hints = {row['field']: row['candidate_source_ids'] for row in build_review_hints(answer, sample)['fields']}

        self.assertEqual(hints['reason'][0], 'D00002')
        self.assertEqual(hints['boundary'][0], 'D00003')

    def test_cross_language_field_hints_reuse_related_original_artifact_lines(self):
        sample = make_sample(
            '**Nora (Maintainer)** The relay is used for credential rotation. '
            'Offline workstations use a signed checklist.\n',
            '# Relay and credential rotation\n'
            'Credential rotation uses the relay.\n'
            'Offline workstations instead use the signed checklist.\n'
            'The catering calendar lists the next staff meal.\n', 'design',
        )
        answer = {'items': [{'conclusion': {'text': '轮换凭证时使用中继。', 'evidence': [
            {'source_id': 'A00002', 'quote': 'Credential rotation uses the relay.'},
            {'source_id': 'A00003', 'quote': 'Offline workstations instead use the signed checklist.'},
        ]}, 'reason': {'text': '未说明', 'evidence': []},
            'boundary': {'text': '离线工作站使用已签署的清单。', 'evidence': [
                {'source_id': 'D00001', 'quote': 'Offline workstations use a signed checklist.'},
            ]}}], 'open_questions': []}

        hints = {row['field']: row for row in build_review_hints(answer, sample)['fields']}

        self.assertEqual(hints['boundary']['linked_artifact_source_ids'], ['A00003', 'A00002'])
        self.assertNotIn('linked_artifact_source_ids', hints['reason'])
        answer['items'][0]['boundary']['text'] = 'Offline workstations use a signed checklist.'
        hints = {row['field']: row for row in build_review_hints(answer, sample)['fields']}
        self.assertNotIn('linked_artifact_source_ids', hints['boundary'])

    def test_scope_review_points_to_each_condition_even_on_an_already_cited_line(self):
        line = '用户：如果网关断开就登记，若备线失效就转人工。'
        sample = make_sample(line + '\n助手：如果太忙，跳过登记。\n> 若为示例，跳过核验。',
                             '除非存在已核验的工单，否则须留存交接记录。', 'design')
        answer = {'items': [{'conclusion': {'text': '网关断开就登记。', 'evidence': [
            {'source_id': 'D00001', 'quote': line},
        ]}, 'reason': {'text': '未说明', 'evidence': []},
            'boundary': {'text': '未说明', 'evidence': []}}], 'open_questions': []}

        scope = build_review_hints(answer, sample).get('scope_review', {})
        markers = {(sid, kind, sample.catalog[sid].text[start:end])
                   for sid, start, end, kind in scope.get('source_markers', [])}
        self.assertIn(('D00001', 'condition', '如果'), markers)
        self.assertIn(('D00001', 'condition', '若'), markers)
        self.assertIn(('A00001', 'exception', '除非'), markers)
        self.assertFalse(any(sid in {'D00002', 'D00003'} for sid, _, _ in markers))

    def test_scope_intensification_warning_uses_the_fields_own_citations(self):
        artifact = '橙色也用于标记测温记录。\n二级权限仅供复核员使用。\n例如这次抽查留存一份照片。'
        sample = make_sample('用户：按当前方案执行。', artifact, 'design')
        answer = {'items': [{'conclusion': {'text': '二级权限仅供复核员使用。', 'evidence': [
            {'source_id': 'A00002', 'quote': '二级权限仅供复核员使用。'},
        ]}, 'reason': {'text': '橙色只用于标记测温记录。', 'evidence': [
            {'source_id': 'A00001', 'quote': '橙色也用于标记测温记录。'},
        ]}, 'boundary': {'text': '所有抽查都须留存照片。', 'evidence': [
            {'source_id': 'A00003', 'quote': '例如这次抽查留存一份照片。'},
        ]}}], 'open_questions': []}

        fields = build_review_hints(answer, sample).get('scope_review', {}).get('quantifier_checks', [])
        self.assertEqual([(row['item_index'], row['field'], row['quantifier'], row['source_ids']) for row in fields],
                         [(1, 'reason', 'exclusive', ['A00001']), (1, 'boundary', 'universal', ['A00003'])])

    def test_retention_cites_earlier_related_definition_across_language(self):
        initial = make_sample('User: Witnessed torque measurements use the calibrated spring adapter.',
                              'Receipts use a logbook.\nTorque measurements are filed by tool label.',
                              'analysis', dialogue_name='initial.md', artifact_name='initial-art.md')
        final = make_sample('User: Keep the witnessed torque measurements. Register only the new tool labels.',
                            'Witnessed torque measurements still apply.\nCheck the maintenance calendar.',
                            'design', dialogue_name='final.md', artifact_name='final-art.md')
        sample = assemble_requirement('REGISTRY', [('draft', 'analysis', initial), ('final', 'design', final)],
                                      [('D', 'notes.md', 'Witnessed torque measurements also use a laser.')])
        answer = {'items': [{'conclusion': {'text': '沿用见证测量扭矩的要求。', 'evidence': [
            {'source_id': 'D.final.00001', 'quote': final.catalog['D00001'].text},
        ]}, 'reason': {'text': '未说明', 'evidence': []},
            'boundary': {'text': '未说明', 'evidence': []}}], 'open_questions': []}

        scope = build_review_hints(answer, sample).get('scope_review', {})
        rules = scope.get('retained_rules', [])
        self.assertEqual(len(rules), 1)
        [retention] = rules
        self.assertEqual(retention['item_index'], 1)
        self.assertEqual(retention['retention_source_ids'], ['D.final.00001'])
        self.assertEqual(retention['earlier_source_ids'][0], 'D.draft.00001')
        self.assertNotIn('A.draft.00001', retention['earlier_source_ids'])
        self.assertFalse(any('supplement' in sid for sid in retention['earlier_source_ids']))

    def test_parallel_conditions_and_exceptions_point_to_separate_spans(self):
        line = '用户：当日志中断时登记，当备线失效时转人工。除模拟工单外要签字，除归档工单外要复核。'
        sample = make_sample(line, '# 方案', 'design')
        scope = build_review_hints({'items': [], 'open_questions': []}, sample).get('scope_review', {})
        spans = [(line[start:end], kind) for sid, start, end, kind in scope.get('source_markers', [])]
        self.assertEqual(spans, [('当日志中断时', 'condition'), ('当备线失效时', 'condition'),
                                 ('除模拟工单外', 'exception'), ('除归档工单外', 'exception')])

    def test_artifact_code_and_examples_do_not_use_scope_marker_slots(self):
        fence = chr(96) * 3
        artifact = f'{fence}python\nif debug: keep all rows\n{fence}\n \tif code: keep all rows\n\n' \
                   '> 例如文档格式。\nIf this is a quote, ignore.\n\nIf the logger is disconnected, write a slip.'
        sample = make_sample('# Meeting notes', artifact, 'design')
        scope = build_review_hints({'items': [], 'open_questions': []}, sample).get('scope_review', {})
        self.assertEqual([(sid, kind) for sid, _, _, kind in scope.get('source_markers', [])],
                         [('A00009', 'condition')])

    def test_an_unrelated_exclusive_clause_does_not_hide_an_additive_warning(self):
        line = '橙色不仅用于标记测温记录，还用于核对交接。清理权限仅供管理员使用。'
        sample = make_sample('# 交流记录', line, 'design')
        answer = {'items': [{'conclusion': {'text': '橙色只用于标记测温记录。', 'evidence': [
            {'source_id': 'A00001', 'quote': line},
        ]}, 'reason': {'text': '未说明', 'evidence': []},
            'boundary': {'text': '未说明', 'evidence': []}}], 'open_questions': []}

        checks = build_review_hints(answer, sample).get('scope_review', {}).get('quantifier_checks', [])
        self.assertEqual([(row['quantifier'], row['source_ids']) for row in checks],
                         [('exclusive', ['A00001'])])

    def test_a_later_definition_cannot_match_an_earlier_unrelated_retention_anchor(self):
        stages = [('User: Keep the brass gasket.', 'first'), ('User: Install the brass gasket.', 'middle'),
                  ('User: Keep the zebra harness.', 'last')]
        nodes = [(node, 'design', make_sample(line, '# Notes', 'design', dialogue_name=f'{node}.md'))
                 for line, node in stages]
        sample = assemble_requirement('MAINTAIN', nodes)
        answer = {'items': [{'conclusion': {'text': '沿用垫圈与线束。', 'evidence': [
            {'source_id': 'D.first.00001', 'quote': stages[0][0]},
            {'source_id': 'D.last.00001', 'quote': stages[2][0]},
        ]}, 'reason': {'text': '未说明', 'evidence': []},
            'boundary': {'text': '未说明', 'evidence': []}}], 'open_questions': []}

        scope = build_review_hints(answer, sample).get('scope_review', {})
        self.assertEqual(scope.get('retained_rules', []), [])

    def test_deadline_forms_keep_the_event_and_permanent_test_prerequisites_stay_in_scope(self):
        sample = make_sample('用户：接单后三个工作日内回复。\n用户：正式流程只要完成演练就可上线。\n'
                             'User: Respond within three working days of receipt.\n'
                             'User: All sign-ins require a one-time password.', '# 流程', 'design')
        answer = {'items': [
            {'conclusion': {'text': '完成演练后可上线。', 'evidence': [
                {'source_id': 'D00002', 'quote': sample.catalog['D00002'].text},
            ]}, 'reason': {'text': '未说明', 'evidence': []},
                'boundary': {'text': '未说明', 'evidence': []}},
            {'conclusion': {'text': '登录要使用一次性密码。', 'evidence': [
                {'source_id': 'D00004', 'quote': sample.catalog['D00004'].text},
            ]}, 'reason': {'text': '未说明', 'evidence': []},
                'boundary': {'text': '未说明', 'evidence': []}},
        ], 'open_questions': []}
        focused = build_review_hints(answer, sample)['condition_checks']
        self.assertEqual([row['source_id'] for row in focused['deadline_clauses']], ['D00001', 'D00003'])
        self.assertEqual(focused['one_off_items'], [])

    def test_cutoff_hints_distinguish_inclusive_and_strict_deadlines(self):
        sample = make_sample('User: Use meter files received by publication.\n'
                             'Assistant: Could we change that to strictly before 14:15?',
                             'Courier bags must arrive strictly before 14:15.\n'
                             'This checklist is compiled by operations.', 'design')
        hints = build_review_hints({'items': [], 'open_questions': []}, sample)['condition_checks']
        spans = [(row['source_id'], row['relation'], sample.catalog[row['source_id']].text[row['start']:row['end']])
                 for row in hints['cutoff_boundaries']]
        self.assertEqual(spans, [('D00001', 'inclusive', 'by publication'),
                                 ('A00001', 'exclusive', 'strictly before 14:15')])

    def test_no_waiver_cannot_silently_turn_into_no_approval_required(self):
        quotes = ('持卡不能绕过审批。', '即使拿到通行证，也不能省掉审批。', 'A badge does not automatically waive approval.')
        for quote in quotes:
            with self.subTest(quote=quote):
                sample = make_sample('用户：' + quote, '# 记录', 'design')
                answer = {'items': [{'conclusion': {'text': '持卡人无须审批。', 'evidence': [
                    {'source_id': 'D00001', 'quote': sample.catalog['D00001'].text},
                ]}, 'reason': {'text': '未说明', 'evidence': []},
                    'boundary': {'text': '未说明', 'evidence': []}}], 'open_questions': []}
                checks = build_review_hints(answer, sample).get('condition_checks', {})
                self.assertEqual(checks.get('scope_overclaims', []),
                                 [{'item_index': 1, 'field': 'conclusion', 'source_ids': ['D00001']}])

    def test_one_off_calendar_event_with_a_named_day_is_a_review_candidate(self):
        sample = make_sample('用户：彩排时可以用试用贴纸。那只是周三联调，正式界面别带上。', '# 说明', 'design')
        item = {'conclusion': {'text': '使用试用贴纸。', 'evidence': [
            {'source_id': 'D00001', 'quote': sample.catalog['D00001'].text},
        ]}, 'reason': {'text': '未说明', 'evidence': []},
            'boundary': {'text': '未说明', 'evidence': []}}
        focused = build_review_hints({'items': [item], 'open_questions': []}, sample).get('condition_checks', {})
        self.assertEqual(focused.get('one_off_items', []), [{'item_index': 1, 'source_ids': ['D00001']}])

    def test_a_pending_statement_with_a_negative_word_is_not_a_mixed_decision(self):
        material = make_sample('User: The new logo is not confirmed, still pending.\n'
                               '用户：是否不采用临时方案尚待确认。\n'
                               '用户：标题先别定，正式版不做试用贴纸。', '# 导航', 'design')
        focused = build_review_hints({'items': [], 'open_questions': []}, material).get('condition_checks', {})
        self.assertEqual(focused.get('mixed_decision_sources', []), ['D00003'])
