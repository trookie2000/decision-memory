"""Offline fixtures test orchestration only; they are NOT live employee results."""

import copy
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from decision_memory.battle_protocol import marker, parse_document, sections
from decision_memory.collaboration import BattleConfig, DocsCLI
from decision_memory.coordinator import Battle, run_lock
from decision_memory.evaluation import apply_judgments, make_worksheet, score_worksheet
from decision_memory.storage import read_json, write_json
from http_fixture import completion, model_server
from test_validation import valid_payload


def document_fixture(identity, done, status=None):
    role = identity['role']
    metadata = {**identity, 'status': status or ('passed' if role == 'reviewer' else 'completed')}
    if role == 'reviewer':
        metadata['feedback'] = '离线 fixture 反馈；不是员工真实评审。'
    text = '## result.json\n\n```json\n' + json.dumps(metadata, ensure_ascii=False) + '\n```\n'
    if role == 'author':
        references = [{'id': 'R1', 'text': 'GOLD_ONLY_SENTINEL_不准进入提取器',
                       'evidence': [{'source': 'dialogue', 'quote': '120 元'}]}]
        text += ('## dialogue.md\n\n```markdown\n门槛改成 120 元\n```\n'
                 '## artifact.md\n\n```markdown\n本次活动门槛为 120 元\n```\n'
                 '## gold.json\n\n```json\n' + json.dumps(references, ensure_ascii=False) + '\n```\n')
    if role == 'scorer':
        text += '## judgments.json\n\n```json\n' + json.dumps([
            {'item_id': 'K001', 'correct': True, 'citation_support': True, 'matches': ['R1'],
             'notes': '离线测试 fixture，非质量结论。'}], ensure_ascii=False) + '\n```\n'
    return text + '\n' + done + '\n'


class FixtureDocs:
    """In-memory test double; never used by the CLI/live transport."""
    document = staticmethod(DocsCLI.document)

    def __init__(self):
        self.contents = {}
        self.creates = []
        self.fail_create = False
        self.fail_read = False

    def create(self, title, content_path):
        doc_id = 'fixture-doc-' + str(len(self.creates) + 1)
        receipt = {'ok': True, 'data': {'result': {'docId': doc_id,
                   'openDocUrl': 'https://docs.corp.kuaishou.com/d/home/' + doc_id}}}
        self.creates.append(receipt)
        self.contents[doc_id] = Path(content_path).read_text(encoding='utf-8')
        if self.fail_create:
            raise TimeoutError('fixture: creation may have succeeded')
        return receipt

    def fetch(self, doc_id):
        if self.fail_read:
            raise TimeoutError('fixture: read timeout')
        return {'content': self.contents[doc_id], 'capture': {'test_fixture': True}}


class FixtureMario:
    def __init__(self, docs, revision_rounds=0):
        self.docs = docs
        self.revision_rounds = revision_rounds
        self.sent = []
        self.timeout = False
        self.complete_immediately = True
        self.thread = 'fixture-thread'

    def discover(self):
        return {'skill_id': 'fixture-skill', 'tools': [], 'skills': []}

    def dispatch(self, skill_id, thread_id, employee_id, message):
        doc_id = re.search(r'https://docs\.corp\.kuaishou\.com/d/home/([^\s]+)', message)[1]
        blocks = sections(self.docs.contents[doc_id])
        task_digest = None
        request = message
        if 'task.json' in blocks:
            from decision_memory.evaluation import digest
            packet = json.loads(blocks['task.json'])
            identity, done = packet['identity'], packet['completion_marker']
            doc_id = packet['result_document']['doc_id']
            task_digest = digest(packet)
            request = packet['request']
        else:
            identity = json.loads(blocks['assignment.json'])
            done = re.search(r'^DM_DONE\|.+$', message, re.M)[0]
        self.sent.append({'identity': identity, 'thread': thread_id, 'message': message,
                          'employee': employee_id, 'doc_id': doc_id, 'marker': done,
                          'task_input_digest': task_digest, 'request': request})
        if self.complete_immediately:
            self.finish_last()
        if self.timeout:
            raise TimeoutError('fixture: dispatch result unknown')
        return self.receipt()

    def receipt(self):
        return {'isError': False, '_meta': {'threadId': self.thread, 'taskId': 'fixture-task', 'taskStatus': 'completed'},
                'content': [{'type': 'text', 'text': 'messageKey: OMSG_fixture_' + str(len(self.sent))}]}

    def finish_last(self):
        sent = self.sent[-1]
        identity = sent['identity']
        status = 'needs_revision' if identity['role'] == 'reviewer' and identity['round'] <= self.revision_rounds else None
        output_identity = {**identity, 'task_input_digest': sent['task_input_digest']} if sent.get('task_input_digest') else identity
        self.docs.contents[sent['doc_id']] = document_fixture(output_identity, sent['marker'], status)


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        events = patch.object(Battle, 'event')
        events.start()
        self.addCleanup(events.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'battle'
        self.docs = FixtureDocs()
        self.mario = FixtureMario(self.docs)
        self.now = 100.0
        self.config = BattleConfig(worker_timeout=30, poll_interval=1, notification_max_bytes=60000)
        self.model_config = Path(self.temp.name) / 'model.toml'

    def deps(self):
        return {'docs': self.docs, 'mario_factory': lambda config: self.mario, 'clock': lambda: self.now}

    def new(self, model_config=None):
        return Battle.create(self.root, self.config, 'S003', 'analysis', 'fixture brief',
                             clarification='fixture clarification', model_config=model_config, **self.deps())

    def resume(self):
        return Battle(self.root, **self.deps())

    def drive(self, battle, count=15):
        for _ in range(count):
            battle.run(0)
            if battle.state['status'] not in ('running', 'waiting'):
                break
        return battle

    def test_real_cli_http_extraction_and_agent_score_after_two_rounds(self):
        self.mario.revision_rounds = 1
        with model_server([(200, completion(valid_payload()))]) as (endpoint, requests):
            self.model_config.write_text(f'[model]\nendpoint="{endpoint}"\nmodel="fixture"\napi_key_env=""\nretries=0\n')
            battle = self.drive(self.new(self.model_config))
        self.assertEqual(battle.state['status'], 'agent_review_complete')
        self.assertEqual([s['identity']['role'] for s in self.mario.sent], ['author', 'reviewer', 'author', 'reviewer', 'scorer'])
        self.assertIsNone(self.mario.sent[0]['thread'])
        self.assertTrue(all(s['thread'] == 'fixture-thread' for s in self.mario.sent[1:]))
        self.assertIn('fixture 反馈', self.mario.sent[2]['message'])
        self.assertEqual(len(requests), 1)
        self.assertNotIn('GOLD_ONLY_SENTINEL', json.dumps(requests, ensure_ascii=False))
        self.assertNotIn('fixture clarification', json.dumps(requests, ensure_ascii=False))
        self.assertEqual(battle.state['score']['human_review'], 'pending')
        self.assertEqual(battle.state['score']['metric_scope'], 'local_agent_review_not_official_score')
        prediction = read_json(self.root / battle.state['prediction'])
        self.assertEqual(prediction['sample_id'], 'S003-analysis')
        self.assertEqual(prediction['items'][0]['conclusion']['text'], '门槛为 120 元')
        self.assertIsNone(battle.state['score']['official_score'])
        audited = battle.audit()
        self.assertEqual(audited['distinct_dispatch_receipts'], 5)
        self.assertTrue(audited['model_prediction_available'])
        self.assertTrue(audited['agent_score_available'])
        self.resume().run(0)
        self.assertEqual(len(self.mario.sent), 5)
        self.assertEqual(len(self.docs.creates), 5)

    def test_missing_model_still_completes_real_transport_independent_work(self):
        battle = self.drive(self.new())
        self.assertEqual(battle.state['status'], 'awaiting_model')
        self.assertTrue((self.root / 'frozen.json').exists())
        self.assertEqual(len(self.mario.sent), 2)
        self.assertFalse(battle.summary()['model_extraction_run'])
        self.assertIsNone(battle.summary()['score'])
        self.assertFalse(battle.audit()['model_prediction_available'])

    def test_operation_dispatch_context_runs_full_cycle_without_shared_history(self):
        self.assertIn('dispatch_context', BattleConfig.__dataclass_fields__)

        class IndependentMario(FixtureMario):
            def receipt(self):
                receipt = super().receipt()
                receipt['_meta']['threadId'] = 'fixture-operation-' + str(len(self.sent))
                return receipt

        self.config = BattleConfig(worker_timeout=30, poll_interval=1, dispatch_context='operation')
        self.mario = IndependentMario(self.docs)
        with model_server([(200, completion(valid_payload()))]) as (endpoint, requests):
            self.model_config.write_text(f'[model]\nendpoint="{endpoint}"\nmodel="fixture"\napi_key_env=""\nretries=0\n')
            battle = self.drive(self.new(self.model_config))
        self.assertEqual(battle.state['status'], 'agent_review_complete')
        self.assertEqual(len(requests), 1)
        self.assertTrue(all(sent['thread'] is None for sent in self.mario.sent))
        self.assertIsNone(battle.state['thread_id'])
        self.assertEqual(len({op['dispatch_thread_id'] for op in battle.state['operations'].values()}), 3)
        self.assertEqual(battle.audit()['distinct_dispatch_receipts'], 3)
        self.resume().run(0)
        self.assertEqual(len(self.mario.sent), 3)

    def test_operation_dispatch_context_rejects_mismatched_thread_binding(self):
        self.assertIn('dispatch_context', BattleConfig.__dataclass_fields__)
        self.config = BattleConfig(worker_timeout=30, poll_interval=1, dispatch_context='operation')
        battle = self.new()
        battle.run(0)
        battle.state['operations']['r01-author']['dispatch_thread_id'] = 'wrong-thread'
        battle.save()
        with self.assertRaisesRegex(ValueError, 'threadId'):
            battle.audit()

    def test_valid_prediction_clears_obsolete_transport_error(self):
        with model_server([(200, completion(valid_payload()))]) as (endpoint, _):
            self.model_config.write_text(f'[model]\nendpoint="{endpoint}"\nmodel="fixture"\napi_key_env=""\nretries=0\n')
            battle = self.new(self.model_config)
            battle.run(0)
            battle.run(0)
            self.assertEqual(battle.state['phase'], 'extract')
            battle.state['last_error'] = 'historical credential failure'
            battle.save()
            battle.run(0)
        self.assertEqual(battle.state['phase'], 'scorer')
        self.assertIsNone(battle.state['last_error'])

    def test_unknown_dispatch_is_not_resent_and_late_document_is_accepted(self):
        self.mario.timeout = True
        self.mario.complete_immediately = False
        battle = self.new()
        battle.run(0)
        self.now = 140
        recovered = self.resume()
        recovered.run(0)
        self.assertEqual(recovered.state['status'], 'waiting_timeout')
        self.assertEqual(len(self.mario.sent), 1)
        self.mario.finish_last()
        recovered = self.resume()
        recovered.run(0)
        self.assertEqual(recovered.state['phase'], 'reviewer')
        recovered.run(0)
        self.assertEqual(recovered.state['status'], 'thread_unknown')
        self.assertEqual(len(self.mario.sent), 1)
        receipt = Path(self.temp.name) / 'actual-dispatch-receipt.json'
        write_json(receipt, self.mario.receipt())
        recovered.reconcile(dispatch_receipt=receipt, operation='r01-author')
        self.mario.timeout = False
        recovered.run(0)
        self.assertEqual(len(self.mario.sent), 2)
        self.assertEqual(self.mario.sent[-1]['thread'], 'fixture-thread')

    def test_dispatch_receipt_recovers_crash_before_thread_checkpoint(self):
        self.mario.complete_immediately = False
        battle = self.new()
        battle.run(0)
        op = next(iter(battle.state['operations'].values()))
        battle.state['thread_id'] = None
        op['dispatch'] = 'started'
        battle.save()
        recovered = self.resume()
        recovered.run(0)
        self.assertEqual(recovered.state['thread_id'], 'fixture-thread')
        self.assertEqual(len(self.mario.sent), 1)

    def test_unknown_create_not_retried_and_can_adopt_verified_receipt(self):
        self.docs.fail_create = True
        battle = self.new()
        battle.run(0)
        self.assertEqual(battle.state['status'], 'create_unknown')
        self.resume().run(0)
        self.assertEqual(len(self.docs.creates), 1)
        self.assertEqual(len(self.mario.sent), 0)
        receipt = Path(self.temp.name) / 'actual-create-receipt.json'
        write_json(receipt, self.docs.creates[0])
        recovered = self.resume()
        recovered.reconcile(create_receipt=receipt)
        recovered.run(0)
        self.assertEqual(recovered.state['phase'], 'reviewer')
        self.assertEqual(len(self.docs.creates), 1)

    def test_completed_author_recovers_crash_before_phase_transition(self):
        battle = self.new()
        battle.run(0)
        battle.state['phase'] = 'author'
        battle.save()
        self.docs.fail_read = True
        recovered = self.resume()
        recovered.run(0)
        self.assertEqual(recovered.state['phase'], 'reviewer')
        self.assertEqual(len(self.mario.sent), 1)

    def test_invalid_completed_document_is_preserved_and_never_resent(self):
        self.mario.complete_immediately = False
        battle = self.new()
        battle.run(0)
        sent = self.mario.sent[-1]
        wrong = {**sent['identity'], 'input_version': 'wrong-version'}
        self.docs.contents[sent['doc_id']] = document_fixture(wrong, sent['marker'])
        recovered = self.resume()
        recovered.run(0)
        self.assertEqual(recovered.state['status'], 'protocol_error')
        self.assertEqual(len(list((self.root / 'operations/r01-author').glob('invalid-*.md'))), 1)
        self.assertEqual(len(self.mario.sent), 1)

    def test_transient_read_failure_then_late_completion(self):
        self.mario.complete_immediately = False
        battle = self.new()
        battle.run(0)
        self.docs.fail_read = True
        self.resume().run(0)
        self.docs.fail_read = False
        self.mario.finish_last()
        recovered = self.resume()
        recovered.run(0)
        self.assertEqual(recovered.state['phase'], 'reviewer')
        self.assertEqual(len(self.mario.sent), 1)

    def test_scorer_cannot_change_item_id_or_invent_reference_match(self):
        payload = {**valid_payload(), 'sample_id': 'fixture'}
        payload['items'][0]['id'] = 'K001'
        worksheet = make_worksheet(payload, [{'id': 'R1', 'text': 'frozen'}], review_kind='agent')
        judgments = [{'item_id': 'K001', 'correct': True, 'citation_support': True,
                      'matches': ['invented-reference'], 'notes': 'fixture'}]
        with self.assertRaisesRegex(ValueError, '未知'):
            apply_judgments(worksheet, judgments)
        judgments[0].update(matches=['R1'], item_id='changed')
        with self.assertRaisesRegex(ValueError, '逐条'):
            apply_judgments(worksheet, judgments)

    def test_corrupt_frozen_file_is_detected_by_offline_audit(self):
        battle = self.drive(self.new())
        frozen = read_json(self.root / 'frozen.json')
        frozen['input_fingerprint'] = 'wrong'
        write_json(self.root / 'frozen.json', frozen, overwrite=True)
        with self.assertRaisesRegex(ValueError, '改动'):
            battle.audit()

    def test_offline_audit_counts_confirmed_recovery_without_resending(self):
        battle = self.new()
        battle.run(0)
        op = next(iter(battle.state['operations'].values()))
        op['dispatch'] = 'unknown'
        op['receipt']['message_keys'] = []
        battle.save()
        confirmation = {'confirmed_not_sent_by': 'user', 'reason': 'fixture user confirmation',
                        'identity': op['identity'], 'document': op['document'],
                        'original_dispatch_task_id': op['receipt']['task_id'],
                        'original_thread_id': op['receipt']['thread_id'], 'new_dispatch_thread': True}
        prefix = f'operations/{op["key"]}/operator-recovery-01-'
        battle.put_json(prefix + 'confirmation.json', confirmation)
        receipt = self.mario.receipt()
        receipt['_meta']['threadId'] = 'fixture-recovery-thread'
        receipt['content'][0]['text'] = 'messageKey: OMSG_recovery'
        battle.put_json(prefix + 'dispatch-receipt.json', receipt)
        state_before = (self.root / 'state.json').read_bytes()
        audited = battle.audit()
        self.assertEqual(audited['ordinary_dispatch_receipts'], 0)
        self.assertEqual(audited['operator_recovery_dispatch_receipts'], 1)
        self.assertEqual(audited['distinct_dispatch_receipts'], 1)
        self.assertEqual(len(self.mario.sent), 1)
        self.assertEqual((self.root / 'state.json').read_bytes(), state_before)
        # A protected but mismatched confirmation is still rejected semantically.
        confirmation['identity'] = {**op['identity'], 'input_version': 'wrong'}
        from decision_memory.coordinator import file_digest
        write_json(self.root / (prefix + 'confirmation.json'), confirmation, overwrite=True)
        battle.state['files'][prefix + 'confirmation.json'] = file_digest(self.root / (prefix + 'confirmation.json'))
        with self.assertRaisesRegex(ValueError, '不匹配'):
            battle.audit()

    def test_round_cap_retains_dispute_without_extraction(self):
        self.mario.revision_rounds = 9
        battle = self.drive(self.new())
        self.assertEqual(battle.state['status'], 'human_review')
        self.assertEqual(battle.state['round'], 3)
        self.assertEqual(len(self.mario.sent), 6)
        self.assertFalse(battle.state['frozen'])

    def test_modified_frozen_gold_is_rejected_before_more_calls(self):
        self.drive(self.new())
        (self.root / 'scoring/references.json').write_text('[]')
        with self.assertRaisesRegex(ValueError, '改动'):
            self.resume()
        self.assertEqual(len(self.mario.sent), 2)

    def test_changed_state_and_duplicate_start_are_rejected(self):
        self.new()
        with self.assertRaisesRegex(ValueError, '已有内容'):
            self.new()
        state = read_json(self.root / 'state.json')
        state['round'] = 99
        write_json(self.root / 'state.json', state, overwrite=True)
        with self.assertRaisesRegex(ValueError, '改动'):
            self.resume()

    def test_lock_excludes_concurrent_runner_and_releases_on_close(self):
        battle = self.new()
        with run_lock(self.root):
            with self.assertRaisesRegex(ValueError, '正在运行'):
                battle.run(0)
        battle.run(0)
        self.assertEqual(len(self.mario.sent), 1)

    def test_failed_extraction_requires_explicit_retry_and_preserves_error(self):
        self.model_config.write_text('[model]\nendpoint="https://example.invalid/chat/completions"\nmodel="fixture"\napi_key_env="MISSING_BATTLE_TEST_KEY"\n')
        battle = self.drive(self.new(self.model_config))
        self.assertEqual(battle.state['status'], 'extraction_failed')
        self.assertIn('MISSING_BATTLE_TEST_KEY', battle.state['last_error'])
        self.resume().run(0)
        self.assertEqual(len(self.resume().state['extractions']), 1)
        retried = self.resume()
        retried.run(0, retry_extraction=True)
        self.assertEqual(len(retried.state['extractions']), 2)
        self.assertTrue((self.root / 'extraction/attempt-01/cli-receipt.json').exists())
        self.assertIsNone(retried.summary()['score'])


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.identity = {'task_id': 'test', 'sample_id': 'S003', 'role': 'author', 'round': 1, 'input_version': 'abc'}
        self.done = marker(self.identity, 'unique-nonce')

    def test_completed_document_is_bound_to_every_identity_field(self):
        text = document_fixture(self.identity, self.done)
        self.assertIsNotNone(parse_document(text, self.identity, self.done, 'analysis'))
        for key, value in [('task_id', 'other'), ('role', 'reviewer'), ('round', True), ('input_version', 'different')]:
            wrong = {**self.identity, key: value}
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, '不匹配'):
                parse_document(document_fixture(wrong, self.done), self.identity, self.done, 'analysis')

    def test_marker_mentioned_in_body_does_not_complete(self):
        text = document_fixture(self.identity, self.done) + '\n还在写入\n'
        self.assertIsNone(parse_document(text, self.identity, self.done, 'analysis'))
        self.assertIsNone(parse_document('quoted ' + self.done, self.identity, self.done, 'analysis'))

    def test_docs_markdown_escaped_footer_is_recognized_without_source_changes(self):
        text = document_fixture(self.identity, self.done).replace('DM_DONE|', 'DM\\_DONE\\|')
        parsed = parse_document(text, self.identity, self.done, 'analysis')
        self.assertEqual(parsed['dialogue'], '门槛改成 120 元\n')

    def test_unverifiable_gold_fails_closed(self):
        text = document_fixture(self.identity, self.done).replace('"quote": "120 元"', '"quote": "不存在"')
        with self.assertRaisesRegex(ValueError, '无法'):
            parse_document(text, self.identity, self.done, 'analysis')

    def test_duplicate_sections_fail_and_source_headings_stay_in_source(self):
        text = '## dialogue.md\n```markdown\n## artifact.md\n源文本\n```\n'
        self.assertEqual(sections(text)['dialogue.md'], '## artifact.md\n源文本\n')
        with self.assertRaisesRegex(ValueError, '重复'):
            sections(text + text)

    def test_score_adapter_cannot_change_gold_and_preserves_null(self):
        result = {**valid_payload(), 'sample_id': 'fixture'}
        result['items'][0]['id'] = 'K001'
        worksheet = make_worksheet(result, [{'id': 'R1', 'text': 'frozen'}], review_kind='agent')
        reviewed = apply_judgments(worksheet, copy.deepcopy(worksheet['judgments']))
        self.assertIsNone(score_worksheet(reviewed)['recall'])
        worksheet['references'][0]['text'] = 'changed'
        with self.assertRaisesRegex(ValueError, '参考答案'):
            score_worksheet(worksheet)

    def test_config_rejects_unauthorized_destinations(self):
        for setting in ({'group_id': 'other'}, {'author_id': 'other'}, {'max_rounds': 4},
                        {'mcp_url': 'http://example.com/mcp'}, {'worker_timeout': False}):
            with self.subTest(setting=setting), self.assertRaises(ValueError):
                BattleConfig(**setting)


if __name__ == '__main__':
    unittest.main()
