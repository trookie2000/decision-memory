"""Offline transport fixtures; no fabricated data is used as live results."""

import copy
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from decision_memory.battle_protocol import marker, parse_document, sections, worker_message
from decision_memory.collaboration import BattleConfig, MarioMCP
from decision_memory.coordinator import Battle, file_digest
from decision_memory.evaluation import digest
from decision_memory.task_delivery import DocumentDelivery, message_size_rejection, render_task, short_notification, task_packet, verify_task
from test_coordinator import FixtureDocs, FixtureMario, document_fixture
from decision_memory.storage import read_json, write_json


def refusal():
    return {'content': [{'type': 'text', 'text': '消息发送失败，错误码 82000，提示 MESSAGE_TOO_LARGE_TO_SEND（消息体积过大）。'}],
            'isError': False, '_meta': {'threadId': 'fixture-thread', 'taskId': 'fixture-rejected', 'taskStatus': 'completed'}}


class TaskDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'case'
        self.docs = FixtureDocs()
        self.mario = FixtureMario(self.docs)
        self.dependencies = {'docs': self.docs, 'mario_factory': lambda config: self.mario}
        self.config = BattleConfig(notification_max_bytes=4000, poll_interval=1)
        events = patch.object(Battle, 'event')
        events.start()
        self.addCleanup(events.stop)

    def new(self, brief='fixture brief'):
        return Battle.create(self.root, self.config, 'T004', 'analysis', brief, **self.dependencies)

    def recovered(self):
        return Battle(self.root, **self.dependencies)

    def test_large_request_is_lossless_in_docs_and_small_in_kim(self):
        battle = self.new('SOURCE_SENTINEL 中文原文\n' * 600)
        battle.run(0)
        self.assertEqual(battle.state['phase'], 'reviewer')
        sent = self.mario.sent[0]
        self.assertLess(len(sent['message'].encode()) + 64, 4000)
        self.assertNotIn('SOURCE_SENTINEL', sent['message'])
        op = battle.state['operations']['r01-author']
        packet = read_json(self.root / 'operations/r01-author/task-packet.json')
        self.assertEqual(packet['payload']['brief'], battle.state['brief'])
        self.assertEqual(digest(packet), op['task_input']['digest'])
        self.assertIn('SOURCE_SENTINEL', packet['request'])
        self.assertNotEqual(op['task_input']['document']['doc_id'], op['document']['doc_id'])
        self.assertEqual(battle.audit()['completed_operations'][0]['protocol'], 'passed')

    def test_oversize_sources_above_old_cutoff_can_use_docs(self):
        payload = {'dialogue': '原文 ' * 30000, 'artifact': '文件', 'references': [], 'prediction': {'items': []}}
        identity = {'task_id': 'fixture', 'sample_id': 'T004', 'role': 'scorer', 'round': 1,
                    'input_version': digest(payload), 'prediction_digest': digest(payload['prediction'])}
        op = {'identity': identity, 'nonce': 'nonce', 'document': {'doc_id': 'result', 'url': 'https://docs.corp.kuaishou.com/d/home/result'}}
        request = worker_message(identity, marker(identity, 'nonce'), op['document']['url'], 'analysis', payload)
        self.assertGreater(len(request.encode()), 60000)
        packet = task_packet(op, 'analysis', payload, request)
        verify_task(render_task(packet), packet)
        notice = short_notification(packet, {'url': 'https://docs.corp.kuaishou.com/d/home/input'}, 4000)
        self.assertLess(len(notice.encode()), 4000)
        self.assertEqual(json.loads(sections(render_task(packet))['task.json'])['payload'], payload)

    def test_task_version_and_prediction_digest_are_strict(self):
        payload = {'prediction': {'items': []}}
        identity = {'task_id': 't', 'sample_id': 's', 'role': 'scorer', 'round': 1,
                    'input_version': digest(payload), 'prediction_digest': 'wrong'}
        with self.assertRaisesRegex(ValueError, 'prediction_digest'):
            task_packet({'identity': identity, 'document': {}, 'nonce': 'n'}, 'analysis', payload, 'request')

    def test_missing_or_wrong_returned_task_digest_is_rejected(self):
        identity = {'task_id': 't', 'sample_id': 's', 'role': 'reviewer', 'round': 1, 'input_version': 'v'}
        done = marker(identity, 'n')
        for extra in ({}, {'task_input_digest': 'wrong'}):
            with self.subTest(extra=extra), self.assertRaisesRegex(ValueError, 'task_input_digest'):
                parse_document(document_fixture({**identity, **extra}, done), identity, done, 'analysis', 'expected')
        parsed = parse_document(document_fixture({**identity, 'task_input_digest': 'expected'}, done),
                                identity, done, 'analysis', 'expected')
        self.assertEqual(parsed['metadata']['status'], 'passed')

    def test_scoring_request_avoids_redundant_input_echo(self):
        identity = {'task_id': 't', 'sample_id': 's', 'role': 'scorer', 'round': 1,
                    'input_version': 'v', 'prediction_digest': 'p'}
        request = worker_message(identity, marker(identity, 'n'),
                                 'https://docs.corp.kuaishou.com/d/home/result', 'analysis', {'prediction': {}})
        self.assertIn('不要重复回显 prediction.json、references.json、dialogue 或 artifact', request)
        self.assertIn('写入后须回读核对', request)

    def test_task_creation_unknown_is_never_retried(self):
        battle = self.new('large data ' * 1000)
        original_create = self.docs.create

        def fail_input(title, path):
            if 'task-input.md' in str(path):
                self.docs.fail_create = True
            return original_create(title, path)

        self.docs.create = fail_input
        battle.run(0)
        self.assertEqual(battle.state['status'], 'input_create_unknown')
        self.recovered().run(0)
        self.assertEqual(len(self.docs.creates), 2)
        self.assertEqual(len(self.mario.sent), 0)

    def test_saved_input_create_receipt_recovers_without_second_document(self):
        battle = self.new('large input ' * 1000)
        self.mario.complete_immediately = False
        battle.run(0)
        op = battle.state['operations']['r01-author']
        # Simulate a process stopped after the receipt was persisted, before the
        # document binding or first dispatch was checkpointed.
        op['dispatch'] = 'new'
        op['task_input'].pop('document')
        op['task_input']['create'] = 'started'
        battle.save()
        # Recovery of document creation alone is tested without replaying a send.
        with patch.object(self.mario, 'dispatch', side_effect=AssertionError('no new send in document recovery')):
            resumed = self.recovered()
            restored_op = resumed.state['operations']['r01-author']
            request = (self.root / 'operations/r01-author/request.md').read_text()
            DocumentDelivery(resumed).prepare(restored_op, request)
        self.assertEqual(len(self.docs.creates), 2)
        self.assertTrue(restored_op['task_input']['verified'])

    def test_repeated_recovery_preparation_does_not_reset_new_send_state(self):
        self.mario.complete_immediately = False
        self.mario.receipt = refusal
        battle = self.new()
        battle.run(0)
        battle.recover_rejected_dispatch('r01-author', 'authorized size correction')
        op = battle.state['operations']['r01-author']
        op['dispatch'] = 'started'
        battle.save()
        recovered = self.recovered()
        recovered.recover_rejected_dispatch('r01-author', 'repeat must not reset')
        self.assertEqual(recovered.state['operations']['r01-author']['dispatch'], 'started')
        self.assertEqual(len(self.mario.sent), 1)

    def test_partly_written_result_prevents_rejection_recovery(self):
        self.mario.complete_immediately = False
        self.mario.receipt = refusal
        battle = self.new()
        battle.run(0)
        self.docs.contents[self.mario.sent[-1]['doc_id']] = '真实员工正在写入的正文，尚未完成'
        with self.assertRaisesRegex(ValueError, '可能正在写入'):
            battle.recover_rejected_dispatch('r01-author', 'must not repeat')
        self.assertEqual(len(self.mario.sent), 1)

    def test_readback_mismatch_never_dispatches(self):
        battle = self.new('large data ' * 1000)
        original_fetch = self.docs.fetch

        def alter_input(doc_id):
            response = original_fetch(doc_id)
            if '## task.json' in response['content']:
                response['content'] = response['content'].replace('large data', 'tampered')
            return response

        self.docs.fetch = alter_input
        with self.assertRaisesRegex(ValueError, '不一致'):
            battle.run(0)
        self.assertEqual(len(self.mario.sent), 0)

    def test_explicit_rejection_recovery_preserves_original_and_sends_once(self):
        self.mario.complete_immediately = False
        self.mario.receipt = refusal
        battle = self.new()
        battle.run(0)
        self.assertEqual(battle.state['status'], 'dispatch_rejected')
        original = self.root / 'operations/r01-author/dispatch-receipt.json'
        checksum = file_digest(original)
        before_identity = copy.deepcopy(battle.state['operations']['r01-author']['identity'])
        before_doc = copy.deepcopy(battle.state['operations']['r01-author']['document'])
        battle.recover_rejected_dispatch('r01-author', 'user requested fix and resume')
        self.assertEqual(file_digest(original), checksum)
        self.assertEqual(len(self.mario.sent), 1)  # prepare does not send
        self.mario.complete_immediately = True
        self.mario.receipt = lambda: {'_meta': {'threadId': 'fixture-thread', 'taskId': 'fixture-new', 'taskStatus': 'completed'},
                                     'content': [{'type': 'text', 'text': 'messageKey: OMSG_recovered'}]}
        recovered = self.recovered()
        recovered.run(0)
        self.assertEqual(recovered.state['phase'], 'reviewer')
        op = recovered.state['operations']['r01-author']
        self.assertEqual(op['identity'], before_identity)
        self.assertEqual(op['document'], before_doc)
        self.assertEqual(file_digest(original), checksum)
        self.assertEqual(self.mario.sent[-1]['thread'], 'fixture-thread')
        self.assertEqual(recovered.audit()['distinct_dispatch_receipts'], 1)
        with self.assertRaisesRegex(ValueError, '未完成'):
            recovered.recover_rejected_dispatch('r01-author', 'repeat')
        self.assertEqual(len(self.mario.sent), 2)

    def test_unknown_dispatch_cannot_use_rejection_recovery(self):
        self.mario.complete_immediately = False
        self.mario.timeout = True
        battle = self.new()
        battle.run(0)
        with self.assertRaises((ValueError, FileNotFoundError)):
            battle.recover_rejected_dispatch('r01-author', 'cannot infer unsent from timeout')
        self.assertEqual(len(self.mario.sent), 1)

    def test_acknowledged_notification_timeout_reports_exact_wait_target(self):
        self.mario.complete_immediately = False
        battle = self.new()
        battle.run(0)
        operation = battle.state['operations']['r01-author']
        battle.clock = lambda: operation['deadline'] + 1
        battle.run(0)
        self.assertEqual(battle.state['status'], 'waiting_timeout')
        self.assertIn(operation['document']['doc_id'], battle.state['last_error'])
        self.assertIn('不自动重发', battle.state['last_error'])
        self.assertEqual(len(self.mario.sent), 1)

    def test_late_completion_is_adopted_instead_of_sending_recovery(self):
        self.mario.complete_immediately = False
        self.mario.receipt = refusal
        battle = self.new()
        battle.run(0)
        self.mario.finish_last()
        battle.recover_rejected_dispatch('r01-author', 'late result check')
        self.assertEqual(battle.state['phase'], 'reviewer')
        self.assertFalse(battle.state['operations']['r01-author'].get('delivery_recovery'))
        self.assertEqual(len(self.mario.sent), 1)

    def test_receipt_rejection_detection_is_conservative(self):
        self.assertEqual(message_size_rejection(refusal())['code'], '82000')
        for text in ('!!!', '连接超时', '可能会遇到 MESSAGE_TOO_LARGE_TO_SEND 82000',
                     '如果消息发送失败，82000，MESSAGE_TOO_LARGE_TO_SEND',
                     '消息发送失败，错误码 182000，MESSAGE_TOO_LARGE_TO_SEND',
                     '消息发送失败，错误码 82000，MESSAGE_TOO_LARGE_TO_SEND，messageKey: OMSG_abc'):
            value = refusal()
            value['content'][0]['text'] = text
            self.assertIsNone(message_size_rejection(value))

    def test_short_notice_resume_waits_without_recreating_or_resending(self):
        self.mario.complete_immediately = False
        battle = self.new('large source ' * 1000)
        battle.run(0)
        op = battle.state['operations']['r01-author']
        notice_bytes = file_digest(self.root / 'operations/r01-author/notification.md')
        original_request = file_digest(self.root / 'operations/r01-author/request.md')
        self.recovered().run(0)
        self.assertEqual(len(self.docs.creates), 2)
        self.assertEqual(len(self.mario.sent), 1)
        self.assertEqual(file_digest(self.root / 'operations/r01-author/notification.md'), notice_bytes)
        self.assertEqual(file_digest(self.root / 'operations/r01-author/request.md'), original_request)
        self.mario.finish_last()
        recovered = self.recovered()
        recovered.run(0)
        self.assertEqual(recovered.state['phase'], 'reviewer')

    def test_docs_normalizing_fence_delimiters_does_not_change_task_content(self):
        identity = {'task_id': 't', 'sample_id': 's', 'role': 'author', 'round': 1, 'input_version': digest({'brief': '~~~内嵌~~~'})}
        op = {'identity': identity, 'nonce': 'nonce', 'document': {'doc_id': 'r', 'url': 'https://docs.corp.kuaishou.com/d/home/r'}}
        packet = task_packet(op, 'analysis', {'brief': '~~~内嵌~~~'}, '原样要求 ```markdown\n~~~\n```')
        content = render_task(packet)
        fence = re.search(r'^~+json$', content, re.M)[0].removesuffix('json')
        normalized = content.replace(fence + 'json', '```json').replace('\n' + fence + '\n', '\n```\n')
        verify_task(normalized, packet)

    def test_transport_refuses_oversized_notification_before_rpc(self):
        mcp = MarioMCP.__new__(MarioMCP)
        mcp.config = self.config
        with patch.object(mcp, 'rpc') as rpc, self.assertRaisesRegex(ValueError, '本地保护'):
            mcp.dispatch('skill', None, self.config.author_id, '汉' * 3000)
        rpc.assert_not_called()


if __name__ == '__main__':
    unittest.main()
