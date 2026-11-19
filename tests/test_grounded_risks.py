"""Real evidence gaps: omitted old values, constants, and unexamined unknowns."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from decision_memory.client import Config, ModelClient
from decision_memory.grounded import MAX_ITEMS, MAX_QUESTIONS, decode_payload, extract_grounded, review_payload
from decision_memory.sources import index_materials
from decision_memory.source_hints import build_review_hints
from http_fixture import completion, model_server


def sample():
    return index_materials('**用户：**\n原先门槛是 40 元。\n改为 120 元；成功后存 48 小时。',
        '# 当前方案\n默认 120 元，成功后存 2 天。\n使用 NOTICE_FREQUENCY_KEY。\n'
        '因为要避免重复通知，使用用户维度。\n仅用于用户通知。', 'design', 'RISK', 'd.md', 'a.md')


def payload(text, ids):
    return {'items': [{'conclusion': {'text': text, 'evidence': ids},
                      'reason': {'text': '未说明', 'evidence': []},
                      'boundary': {'text': '未说明', 'evidence': []}}], 'open_questions': []}


class GroundedRiskTests(unittest.TestCase):
    def test_old_numeric_value_and_uppercase_constant_need_their_own_source(self):
        for text in ('门槛从 40 元改为 120 元。', '默认使用 NOTICE_FREQUENCY_KEY。'):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, '40|NOTICE_FREQUENCY_KEY'):
                decode_payload(payload(text, ['A00002']), sample())
        value = decode_payload(payload('门槛从 40 元改为 120 元。', ['D00002', 'A00002']), sample())
        self.assertIn('40', value['items'][0]['conclusion']['text'])

    def test_explicit_time_equivalence_and_formatted_numbers_are_not_false_gaps(self):
        decode_payload(payload('成功后保留 48 小时。', ['A00002']), sample())
        other = index_materials('**用户：**\n保留原有统计。', '观测 12,345 条。', 'design', 'N', 'd', 'a')
        decode_payload(payload('观测 12345 条。', ['A00001']), other)
        with self.assertRaises(ValueError):
            decode_payload(payload('观测 1234 条。', ['A00001']), other)

    def test_explicit_duration_requires_a_matching_time_quantity(self):
        material = index_materials('**用户：**\n短路等待 10 秒。\n最长等待 600 秒。\n门槛是 10 条。',
                                   '沿用等待设置。', 'design', 'TIME', 'dialogue.md', 'design.md')
        for key in ('D00002', 'D00004'):
            with self.subTest(source=key), self.assertRaisesRegex(ValueError, '10 分钟'):
                decode_payload(payload('最长等待 10 分钟。', [key]), material)
        decoded = decode_payload(payload('最长等待 10 分钟。', ['D00003']), material)
        self.assertIn('10 分钟', decoded['items'][0]['conclusion']['text'])

    def test_rejected_written_duration_in_a_reason_needs_its_own_proposal_citation(self):
        material = index_materials('Assistant: A seven-calendar-day threshold is an option.\n'
                                   'User: Skip that threshold. Route signed forms to the desk.',
                                   'Signed forms go to the desk.', 'design', 'FORM', 'd.md', 'a.md')
        answer = payload('签收单交柜台办理。', ['D00001', 'D00002'])
        answer['items'][0]['reason'] = {'text': '七天门槛未采纳。', 'evidence': ['D00002']}
        with self.assertRaisesRegex(ValueError, 'K001.reason: 七天'):
            decode_payload(answer, material)

        draft = decode_payload(answer, material, enforce_bindings=False)
        reason = next(row for row in build_review_hints(draft, material)['fields'] if row['field'] == 'reason')
        self.assertEqual(reason['missing_atoms'], [{'text': '七天', 'candidate_source_ids': ['D00001']}])
        answer['items'][0]['reason']['evidence'].append('D00001')
        decode_payload(answer, material)

    def test_short_workday_cutoff_cannot_borrow_evidence_or_use_a_calendar_duration(self):
        material = index_materials('User: File before closing the docket with at least five business days to spare.',
                                   'Archive the record for five calendar days after closing.',
                                   'design', 'DOCKET', 'd.md', 'a.md')
        answer = payload('结案前至少五个工作日提交。', ['D00001'])
        answer['items'][0]['boundary'] = {'text': '下限为五个工作日。', 'evidence': ['A00001']}
        with self.assertRaisesRegex(ValueError, 'K001.boundary: 五个工作日'):
            decode_payload(answer, material)
        answer['items'][0]['boundary']['evidence'] = ['D00001']
        decode_payload(answer, material)

    def test_finalized_revision_in_reason_cannot_cite_only_an_agent_question(self):
        material = index_materials('Assistant: Applicants wait for building approval. Should we extend the filing window?\n'
                                   'User: Extend the filing window to the end of the following day.',
                                   'Applications use the extended filing window.', 'design', 'FILING', 'd.md', 'a.md')
        answer = payload('申请窗口改到次日结束。', ['D00002'])
        answer['items'][0]['reason'] = {'text': 'The filing window was extended because applicants wait for building approval.',
                                        'evidence': ['D00001']}
        with self.assertRaisesRegex(ValueError, 'K001.reason'):
            decode_payload(answer, material)
        draft = decode_payload(answer, material, enforce_bindings=False)
        focus = build_review_hints(draft, material)['condition_checks']
        self.assertEqual(focus['revision_reasons'], [{'item_index': 1, 'confirmation_candidates': ['D00002']}])
        answer['items'][0]['reason']['evidence'].append('D00002')
        decode_payload(answer, material)

    def test_a_separate_follow_up_question_does_not_invalidate_a_reported_change(self):
        material = index_materials(
            'Assistant: We accepted the longer window at sign-off because of delivery lag. Can staff open it today?',
            'The longer window remains in use.', 'design', 'SIGNOFF', 'd.md', 'a.md')
        answer = payload('沿用较长申请窗口。', ['A00001'])
        answer['items'][0]['reason'] = {'text': 'The window was extended because of delivery lag.',
                                        'evidence': ['D00001']}
        self.assertEqual(decode_payload(answer, material)['items'][0]['reason']['evidence'][0]['source_id'],
                         'D00001')

    def test_a_revision_word_inside_a_question_does_not_confirm_it(self):
        material = index_materials('Assistant: 窗口已延长到次日了吗？',
                                   '申请窗口另行确认。', 'design', 'FILING', 'd.md', 'a.md')
        answer = payload('申请窗口仍待另行确认。', ['A00001'])
        answer['items'][0]['reason'] = {'text': '申请窗口已经延长至次日。', 'evidence': ['D00001']}
        with self.assertRaisesRegex(ValueError, 'K001.reason'):
            decode_payload(answer, material)

    def test_revision_hint_finds_the_local_reply_over_an_unrelated_item_citation(self):
        material = index_materials('Assistant: The office closes early. Should we extend the filing window?\n'
                                   'User: Yes, extend the filing window.',
                                   'Forms must be signed.', 'design', 'FILING', 'd.md', 'a.md')
        answer = payload('表单须签名。', ['A00001'])
        answer['items'][0]['reason'] = {'text': 'The filing window was extended because the office closes early.',
                                        'evidence': ['D00001']}
        draft = decode_payload(answer, material, enforce_bindings=False)
        checks = build_review_hints(draft, material)['condition_checks']
        self.assertEqual(checks['revision_reasons'], [{'item_index': 1, 'confirmation_candidates': ['D00002']}])

    def test_compact_condition_checks_reach_the_existing_second_request(self):
        line = 'User: For paper requests, file at least five business days before closing the docket.'
        material = index_materials(
            line + '\nUser: A sponsor badge cannot bypass the signed approval requirement.'
            '\nUser: Use passcode DEMO_GATE for this rehearsal only.'
            '\nAssistant: Perhaps file no more than two days after review.'
            '\nUser: The filter layout is undecided. Do not adopt the temporary banner.',
            'The signed approval requirement applies.', 'design', 'DOCKET', 'd.md', 'a.md')
        first = payload('提前五个工作日提交纸质申请。', ['D00001'])
        first['items'][0]['boundary'] = {'text': '赞助证与准入无关。', 'evidence': ['D00002']}
        first['items'] += payload('使用 DEMO_GATE。', ['D00003'])['items']
        final = payload('纸质申请须在结案前至少五个工作日提交。', ['D00001'])
        with tempfile.TemporaryDirectory() as folder:
            with model_server([(200, completion(first)), (200, completion(checked_reply(final)))]) as (url, requests):
                result = extract_grounded(material, ModelClient(Config(endpoint=url, model='fixture',
                    api_key_env='', retries=0)), Path(folder) / 'audit')
        self.assertEqual(len(requests), 2)
        hints = json.loads(requests[1]['messages'][1]['content'].split('\nreview_hints ', 1)[1])
        self.assertNotIn('scope_review', hints)
        focused = hints.get('condition_checks', {})
        self.assertEqual([(row['source_id'], line[row['start']:row['end']])
                          for row in focused.get('deadline_clauses', [])],
                         [('D00001', line)])
        self.assertEqual(focused['scope_overclaims'],
                         [{'item_index': 1, 'field': 'boundary', 'source_ids': ['D00002']}])
        self.assertEqual(focused['one_off_items'], [{'item_index': 2, 'source_ids': ['D00003']}])
        self.assertEqual(focused.get('mixed_decision_sources', []), ['D00005'])
        self.assertEqual(len(result['items']), 1)
        self.assertIn('结案前至少五个工作日', result['items'][0]['conclusion']['text'])

    def test_large_numbers_do_not_become_equal_after_decimal_context_rounding(self):
        actual = '123456789012345678901234567891'
        invented = '123456789012345678901234567899'
        other = index_materials('**用户：**\n保留原值。', '值 ' + actual, 'design', 'BIG', 'd', 'a')
        with self.assertRaises(ValueError):
            decode_payload(payload('值 ' + invented, ['A00001']), other)

    def test_unstated_fields_and_questions_receive_targeted_current_source_hints(self):
        result = decode_payload(payload('使用用户维度避免重复通知。', ['A00004']), sample())
        result['open_questions'] = [{'text': '用户通知是否只针对当前用户？',
                                    'evidence': [{'source_id': 'D00002', 'quote': '原先门槛是 40 元。'}]}]
        hints = build_review_hints(result, sample())
        by_field = {row['field']: row for row in hints['fields']}
        self.assertEqual(set(by_field), {'conclusion', 'reason', 'boundary'})
        self.assertTrue(by_field['reason']['candidate_source_ids'])
        self.assertIn('A00004', by_field['reason']['candidate_source_ids'])
        self.assertIn('A00005', by_field['boundary']['candidate_source_ids'])
        self.assertTrue(by_field['reason']['unstated_requires_check'])
        self.assertEqual(hints['questions'][0]['question_index'], 1)
        self.assertIn('A00005', hints['questions'][0]['later_related_source_ids'])

    def test_candidate_value_gap_reaches_the_same_two_call_review(self):
        first = payload('门槛从 40 元改为 120 元。', ['A00002'])
        final = payload('门槛从 40 元改为 120 元。', ['D00002', 'A00002'])
        final.update(review_notes=['补回原门槛的自身来源。'], field_checks=[
            {'item_index': 1, 'field': name, 'status': 'supported' if name == 'conclusion' else 'unstated',
             'notes': '根据本字段来源核对。'} for name in ('conclusion', 'reason', 'boundary')])
        with tempfile.TemporaryDirectory() as temporary:
            with model_server([(200, completion(first)), (200, completion(final))]) as (url, requests):
                result = extract_grounded(sample(), ModelClient(Config(endpoint=url, model='fixture',
                    api_key_env='', retries=0)), Path(temporary) / 'audit')
            self.assertEqual(len(requests), 2)
            self.assertIn('missing_atoms', requests[1]['messages'][1]['content'])
            self.assertIn('unstated_requires_check', requests[1]['messages'][1]['content'])
            self.assertIn('later_related_source_ids', requests[1]['messages'][1]['content'])
            self.assertIn('atoms', result['grounding_proof']['literal_bindings'])

    def test_scope_checks_are_saved_for_audit_without_exporting_them_to_the_default_review(self):
        line = '用户：蓝灯也用于标记计时。如果计时器离线，改用纸质登记。'
        material = index_materials(line, '如逾期则转值班组。', 'design', 'LIGHT', 'd.md', 'a.md')
        first = payload('离线时使用纸质登记。', ['D00001'])
        first['items'][0]['reason'] = {'text': '蓝灯只用于标记计时。', 'evidence': ['D00001']}
        final = payload('离线时使用纸质登记。', ['D00001'])
        with tempfile.TemporaryDirectory() as folder:
            with model_server([(200, completion(first)), (200, completion(checked_reply(final)))]) as (url, requests):
                result = extract_grounded(material, ModelClient(Config(endpoint=url, model='fixture',
                    api_key_env='', retries=0)), Path(folder) / 'audit')
            scope_audit = Path(folder) / 'audit' / 'scope-review.json'
            self.assertTrue(scope_audit.is_file())
            audit = json.loads(scope_audit.read_text())
        self.assertEqual(len(requests), 2)
        hints = json.loads(requests[1]['messages'][1]['content'].split('\nreview_hints ', 1)[1])
        self.assertNotIn('scope_review', hints)
        self.assertEqual(audit['input_fingerprint'], material.fingerprint)
        checks = audit['quantifier_checks']
        self.assertEqual([(row['field'], row['source_ids']) for row in checks], [('reason', ['D00001'])])
        saved = result['items'][0]
        self.assertEqual([ref['source_id'] for ref in saved['conclusion']['evidence']], ['D00001'])
        self.assertEqual(saved['reason']['text'], '未说明')

    def test_protocol_repair_identifies_every_field_with_missing_user_source(self):
        material = index_materials('**用户：**\n默认在访客屏显示今日活动，范围只限主馆。\n'
                                   '我提出分馆是否也展示，留待试运行后决定。',
                                   '# 当前方案\n访客屏显示今日活动，范围只限主馆。\n分馆待定。',
                                   'design', 'SCREEN', 'dialogue.md', 'design.md')
        first = payload('访客屏显示今日活动。', ['A00002'])
        wrong = copy.deepcopy(first)
        wrong['items'][0]['boundary'] = {'text': '用户明确要求范围只限主馆。', 'evidence': ['A00002']}
        wrong['open_questions'] = [{'text': '用户提出分馆是否也展示，仍待决定。', 'evidence': ['A00003']}]
        wrong.update(review_notes=[], field_checks=[
            {'item_index': 1, 'field': name, 'status': 'unstated' if name == 'reason' else 'supported',
             'notes': '核查本字段所属范围和来源。'} for name in ('conclusion', 'reason', 'boundary')])
        repaired = copy.deepcopy(wrong)
        repaired['items'][0]['boundary']['evidence'] = ['D00002']
        repaired['open_questions'][0]['evidence'] = ['D00003']
        with tempfile.TemporaryDirectory() as temporary:
            replies = [(200, completion(value)) for value in (first, wrong, repaired)]
            with model_server(replies) as (url, requests):
                result = extract_grounded(material, ModelClient(Config(endpoint=url, model='fixture',
                    api_key_env='', retries=0)), Path(temporary) / 'audit')
        self.assertEqual(len(requests), 3)
        instruction = requests[2]['messages'][1]['content'].split('\n上次完整回复：', 1)[0]
        error_text = instruction.split('\n上次完整 JSON 存在协议结构错误：', 1)[1]
        self.assertIn('items[1].boundary', error_text)
        self.assertIn('open_questions[1]', error_text)
        self.assertIn('明确用户归因', error_text)
        self.assertEqual(result['items'][0]['boundary']['evidence'][0]['source_id'], 'D00002')

    def test_attribution_error_lists_fields_of_a_maximum_size_result(self):
        invalid = {'text': '用户明确要求门槛为 120 元。', 'evidence': ['A00002']}
        fields = ('conclusion', 'reason', 'boundary')
        request = {'items': [{name: invalid for name in fields} for _ in range(MAX_ITEMS)],
                   'open_questions': [invalid] * MAX_QUESTIONS}
        with self.assertRaises(ValueError) as rejected:
            decode_payload(request, sample())
        message = str(rejected.exception)
        self.assertIn('明确用户归因', message)
        for index in range(1, MAX_ITEMS + 1):
            for name in fields:
                self.assertIn(f'items[{index}].{name}', message)
        for index in range(1, MAX_QUESTIONS + 1):
            self.assertIn(f'open_questions[{index}]', message)

    def test_final_reply_can_use_a_single_response_envelope(self):
        first = payload('默认门槛为 120 元。', ['A00002'])
        wrapped = {'response': checked_reply(first)}
        with tempfile.TemporaryDirectory() as temporary:
            replies = [(200, completion(value)) for value in (first, wrapped)]
            with model_server(replies) as (url, requests):
                folder = Path(temporary) / 'audit'
                result = extract_grounded(sample(), ModelClient(Config(endpoint=url, model='fixture',
                    api_key_env='', retries=0)), folder)
            self.assertEqual(len(requests), 2)
            self.assertEqual(result['items'][0]['conclusion']['text'], '默认门槛为 120 元。')
            self.assertIn('"response"', (folder / '02-review-1.json').read_text(encoding='utf-8'))
        with self.assertRaisesRegex(ValueError, '复核需要完整'):
            review_payload({**wrapped, 'status': 'needs_review'}, sample())

    def test_envelope_does_not_allow_repair_to_clear_existing_uncertainty(self):
        first = payload('默认门槛为 120 元。', ['A00002'])
        wrong = checked_reply(payload('用户明确要求门槛为 120 元。', ['A00002']))
        wrong['field_checks'][0]['status'] = 'unsupported'
        fixed = checked_reply(first)
        for envelope in ({'response': wrong}, {'response': wrong, 'status': 'needs_review'}):
            with self.subTest(keys=sorted(envelope)), tempfile.TemporaryDirectory() as temporary:
                replies = [(200, completion(value)) for value in (first, envelope, {'response': fixed})]
                with model_server(replies) as (url, requests), self.assertRaisesRegex(ValueError, '清除已有'):
                    extract_grounded(sample(), ModelClient(Config(endpoint=url, model='fixture',
                        api_key_env='', retries=0)), Path(temporary) / 'audit')
                self.assertEqual(len(requests), 3)


def checked_reply(candidate):
    return {**candidate, 'review_notes': [], 'field_checks': [
        {'item_index': 1, 'field': name, 'status': 'supported' if name == 'conclusion' else 'unstated',
         'notes': '逐字段核对。'} for name in ('conclusion', 'reason', 'boundary')]}


if __name__ == '__main__':
    unittest.main()
