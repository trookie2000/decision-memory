"""Durable, at-most-once dispatch of the authorized Mario employees."""

import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .battle_protocol import marker, parse_document, sections, worker_message
from .client import Config, ModelClient
from .collaboration import BattleConfig, DocsCLI, MarioMCP, TransportError, receipt_details
from .evaluation import apply_judgments, digest, make_worksheet, score_worksheet, validate_references
from .sources import STAGES, load_sample, read_text
from .storage import read_json, render_markdown, validate_saved, write_json, write_text
from .task_delivery import DocumentDelivery, message_size_rejection, verify_task

TERMINAL = {'human_review', 'agent_review_complete', 'agent_review_pending'}
PAUSED = {'create_unknown', 'thread_unknown', 'protocol_error', 'waiting_timeout',
          'awaiting_model', 'extraction_failed', 'extraction_unknown', 'input_create_unknown', 'dispatch_rejected'}
BATTLE_STAGES = (*STAGES, 'requirement')


def file_digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@contextmanager
def run_lock(root):
    # Kernel releases flock on exit/SIGKILL; never remove a possibly-live lock file.
    with (root / '.battle.lock').open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('协调器正在运行，同一输出目录不能并发推进') from None
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


class Battle:
    def __init__(self, root, *, docs=None, mario_factory=None, clock=None, extractor=None):
        self.root = Path(root).resolve()
        self.state = read_json(self.root / 'state.json')
        if self.state.get('schema') != 1 or self.state.get('state_digest') != digest(
                {key: value for key, value in self.state.items() if key != 'state_digest'}):
            raise ValueError('协调器状态已被改动或 schema 不支持')
        self.config = BattleConfig(**self.state['config'])
        self.docs = docs or DocsCLI(self.config)
        self.mario_factory = mario_factory or MarioMCP
        self.mario = None
        self.clock = clock or time.time
        self.extractor = extractor or self._extract_process
        self.verify_files()

    @classmethod
    def create(cls, root, config, sample_id, stage, brief, *, clarification='', model_config=None,
               mode='baseline', batch_root=None, **dependencies):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', sample_id) or stage not in BATTLE_STAGES:
            raise ValueError('sample-id 或 stage 无效')
        if mode not in ('baseline', 'verified', 'artifact-only'):
            raise ValueError('提取模式无效')
        if not isinstance(brief, str) or not brief.strip() or len(brief) > 20_000:
            raise ValueError('brief 必须是非空且不超过 20000 字的任务说明')
        root = Path(root).resolve()
        if root.exists() and any(root.iterdir()):
            raise ValueError('输出目录已有内容，请使用 battle resume，不能重新 start')
        root.mkdir(parents=True, exist_ok=True)
        task_id = 'DM-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid.uuid4().hex[:12]
        state = {'schema': 1, 'task_id': task_id, 'sample_id': sample_id, 'stage': stage,
                 'config': config.public(), 'brief': brief, 'clarification': clarification,
                 'mode': mode, 'model_config': str(Path(model_config).resolve()) if model_config else None,
                 'phase': 'author', 'round': 1, 'status': 'running', 'thread_id': None,
                 'operations': {}, 'files': {}, 'extractions': [], 'created_at': time.time(),
                 'human_review': 'pending', 'frozen': False}
        if batch_root is not None:
            if stage != 'requirement':
                raise ValueError('整批冻结仅用于 requirement')
            state['batch_root'] = str(Path(batch_root).resolve())
        state['state_digest'] = digest(state)
        write_json(root / 'state.json', state)
        return cls(root, **dependencies)

    def path(self, relative):
        target = (self.root / relative).resolve()
        if not target.is_relative_to(self.root) or target == self.root:
            raise ValueError('运行产物路径越界')
        return target

    def input_path(self, filename):
        return self.path(f'inputs/{self.state["sample_id"]}/{filename}')

    def save(self):
        self.state['state_digest'] = digest({key: value for key, value in self.state.items() if key != 'state_digest'})
        write_json(self.root / 'state.json', self.state, overwrite=True)

    def put_text(self, relative, content):
        target = self.path(relative)
        if target.exists():
            if target.read_text(encoding='utf-8') != content:
                raise ValueError('已有运行产物与恢复内容不一致：' + relative)
        else:
            write_text(target, content)
        self.state['files'][relative] = file_digest(target)
        self.save()

    def put_json(self, relative, value):
        self.put_text(relative, json.dumps(value, ensure_ascii=False, indent=2) + '\n')

    def verify_files(self):
        for relative, expected in self.state['files'].items():
            if file_digest(self.path(relative)) != expected:
                raise ValueError('运行产物已被改动，拒绝继续或重新计分：' + relative)

    def event(self, name, **extra):
        event = {'event': name, 'task_id': self.state['task_id'], 'timestamp': self.clock(), **extra}
        line = json.dumps(event, ensure_ascii=False)
        with (self.root / 'events.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(line + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        print(line, file=sys.stderr, flush=True)

    def summary(self):
        return {'task_id': self.state['task_id'], 'sample_id': self.state['sample_id'],
                'phase': self.state['phase'], 'round': self.state['round'], 'status': self.state['status'],
                'group_id': self.config.group_id, 'thread_id': self.state['thread_id'],
                'human_review': 'pending', 'sample_frozen': self.state['frozen'],
                'extraction_attempted': bool(self.state['extractions']),
                'model_prediction_available': bool(self.state.get('prediction')),
                'model_extraction_run': bool(self.state.get('prediction')),
                'kim_reply_readback': 'not_independently_verified_history_api_unavailable',
                'extractions': self.state['extractions'], 'last_error': self.state.get('last_error'),
                'operations': [{key: op.get(key) for key in
                                ('key', 'identity', 'document', 'task_input', 'delivery_recovery', 'dispatch', 'receipt', 'done', 'deadline', 'last_error')}
                               for op in self.state['operations'].values()],
                'score': self.state.get('score'), 'output': str(self.root)}

    def audit(self):
        """Offline replay of saved evidence; never polls, sends, or changes state."""
        self.verify_files()
        checked, message_keys, recoveries = [], [], []
        for op in self.state['operations'].values():
            payload = read_json(self.path(f'operations/{op["key"]}/payload.json'))
            if digest(payload) != op['identity']['input_version']:
                raise ValueError('派发 payload 指纹不匹配')
            message_keys.extend((op.get('receipt') or {}).get('message_keys', []))
            if task := op.get('task_input'):
                packet = read_json(self.path(task['base'] + '/task-packet.json'))
                binding = read_json(self.path(task['base'] + '/delivery-binding.json')) if self.path(task['base'] + '/delivery-binding.json').exists() else None
                if (packet['identity'] != op['identity'] or packet['payload'] != payload
                        or packet['result_document'] != op['document'] or digest(packet) != task['digest']
                        or packet['completion_marker'] != marker(op['identity'], op['nonce'])):
                    raise ValueError('任务输入文档与原操作／结果版本未绑定')
                if binding:
                    notice = read_text(self.path(task['base'] + '/notification.md'))
                    if (binding['identity'] != op['identity'] or binding['task_input_digest'] != task['digest']
                            or binding['task_input_document'] != task['document'] or binding['result_document'] != op['document']
                            or binding['notification_digest'] != digest(notice)
                            or binding['request_digest'] != digest(packet['request'])):
                        raise ValueError('Docs 任务与通知的版本绑定不一致')
                    verify_task(read_text(self.path(task['base'] + '/task-input-readback.md')), packet)
                elif op['dispatch'] in ('started', 'acknowledged', 'unknown'):
                    raise ValueError('派发缺少任务输入与短通知绑定')
            if recovery := op.get('delivery_recovery'):
                original = read_json(self.path(recovery['original_receipt_path']))
                if (not message_size_rejection(original) or file_digest(self.path(recovery['original_receipt_path'])) != recovery['original_receipt_sha256']
                        or recovery['identity'] != op['identity'] or recovery['document'] != op['document']
                        or recovery['original_request_sha256'] != file_digest(self.path(f'operations/{op["key"]}/request.md'))):
                    raise ValueError('明确拒绝恢复未绑定原回执、请求和任务身份')
            if op.get('done'):
                parsed = parse_document(read_text(self.path(f'operations/{op["key"]}/response.md')),
                                        op['identity'], marker(op['identity'], op['nonce']), self.state['stage'],
                                        op.get('task_input', {}).get('digest'))
                if parsed is None or parsed != read_json(self.path(f'operations/{op["key"]}/result.json')):
                    raise ValueError('已保存员工原文无法重放出相同结果')
                checked.append({'operation': op['key'], 'status': parsed['metadata']['status'],
                                'document': op['document'], 'protocol': 'passed',
                                'reference_quotes': 'passed' if op['identity']['role'] == 'author'
                                else 'not_applicable_reviewer_prose_not_checked'})
            # Read-only audit of separately confirmed operator recovery. This is
            # evidence accounting, NOT permission for the coordinator to resend.
            prefix = f'operations/{op["key"]}/'
            for relative in sorted(self.state['files']):
                if not relative.startswith(prefix) or not re.fullmatch(
                        r'operator-recovery-\d+-confirmation\.json', relative[len(prefix):]):
                    continue
                confirmation = read_json(self.path(relative))
                original = op.get('receipt') or {}
                if (confirmation.get('confirmed_not_sent_by') != 'user'
                        or not isinstance(confirmation.get('reason'), str) or not confirmation['reason'].strip()
                        or confirmation.get('identity') != op['identity']
                        or confirmation.get('document') != op.get('document')
                        or not original.get('task_id')
                        or confirmation.get('original_dispatch_task_id') != original['task_id']
                        or confirmation.get('original_thread_id') != original.get('thread_id')
                        or original.get('message_keys')):
                    raise ValueError('操作恢复记录缺少用户未送达确认或与原操作不匹配')
                receipt_path = relative.removesuffix('confirmation.json') + 'dispatch-receipt.json'
                if receipt_path not in self.state['files']:
                    raise ValueError('操作恢复缺少受完整性保护的真实派发回执')
                details = receipt_details(read_json(self.path(receipt_path)))
                if details['is_error'] or not details['message_keys'] or not details['thread_id']:
                    raise ValueError('操作恢复回执缺少真实 messageKey/threadId 或返回错误')
                if details['thread_id'] != original.get('thread_id') and confirmation.get('new_dispatch_thread') is not True:
                    raise ValueError('操作恢复换线程但未保留对应确认')
                recoveries.append({'operation': op['key'], 'confirmation': relative,
                                   'receipt': receipt_path, **details})
        ordinary_count = len(message_keys)
        for recovery in recoveries:
            message_keys.extend(recovery['message_keys'])
        if len(message_keys) != len(set(message_keys)):
            raise ValueError('多个操作复用了同一 messageKey，需人工核对派发')
        for op in self.state['operations'].values():
            receipt = op.get('receipt') or {}
            expected_thread = (op.get('dispatch_thread_id') if self.config.dispatch_context == 'operation'
                               else self.state['thread_id'])
            if receipt.get('thread_id') and receipt['thread_id'] != expected_thread:
                raise ValueError('派发回执 Mario threadId 与保存的上下文绑定不一致')
        frozen = None
        if self.state['frozen']:
            frozen = read_json(self.path('frozen.json'))
            if self.state['stage'] == 'requirement':
                from .requirement_flow import RequirementFlow
                RequirementFlow(self).audit(frozen)
                # Common receipt/protocol checks above still apply to every operation.
                return {'task_id': self.state['task_id'], 'status': self.state['status'], 'read_only': True,
                        'files_checked': len(self.state['files']), 'completed_operations': checked,
                        'ordinary_dispatch_receipts': ordinary_count, 'operator_recoveries': recoveries,
                        'distinct_dispatch_receipts': len(message_keys), 'frozen': frozen,
                        'extraction_attempts': len(self.state['extractions']),
                        'model_prediction_available': bool(self.state.get('prediction')),
                        'agent_score_available': self.state.get('score') is not None,
                        'human_review': 'pending', 'kim_reply_readback': 'not_independently_verified'}
            sample = load_sample(self.input_path('dialogue.md'), self.input_path('artifact.md'), self.state['stage'])
            references = read_json(self.path('scoring/references.json'))
            validate_references(references, sample)
            if (sample.fingerprint != frozen['input_fingerprint'] or sample.file_hashes != frozen['file_hashes']
                    or digest(references) != frozen['references_digest'] or frozen['round'] != self.state['round']):
                raise ValueError('冻结输入或参考版本不匹配')
            review = read_json(self.path(f'operations/r{self.state["round"]:02d}-reviewer/result.json'))
            if review['metadata']['status'] != 'passed':
                raise ValueError('样本未通过审题，不能标记为 frozen')
            if self.state.get('prediction'):
                prediction = read_json(self.path(self.state['prediction']))
                validate_saved(prediction, sample)
                worksheet = read_json(self.path('scoring/worksheet.json'))
                if (worksheet['prediction_digest'] != digest(prediction) or worksheet['items'] != prediction['items']
                        or worksheet['references'] != references):
                    raise ValueError('评分工作表与原始预测／冻结参考不匹配')
                if self.state.get('score'):
                    reviewed = read_json(self.path('scoring/review.json'))
                    if (reviewed['items'] != prediction['items'] or reviewed['references'] != references
                            or score_worksheet(reviewed) != self.state['score']):
                        raise ValueError('已保存评分不能根据固定预测重算')
        if not self.state.get('prediction') and self.state.get('score'):
            raise ValueError('没有真实预测时不得存在评分')
        return {'task_id': self.state['task_id'], 'status': self.state['status'],
                'read_only': True, 'files_checked': len(self.state['files']), 'completed_operations': checked,
                'ordinary_dispatch_receipts': ordinary_count, 'operator_recoveries': recoveries,
                'operator_recovery_dispatch_receipts': len(message_keys) - ordinary_count,
                'distinct_dispatch_receipts': len(message_keys), 'frozen': frozen,
                'extraction_attempts': len(self.state['extractions']),
                'model_prediction_available': bool(self.state.get('prediction')),
                'agent_score_available': self.state.get('score') is not None,
                'human_review': 'pending', 'kim_reply_readback': 'not_independently_verified'}

    def report(self):
        summary = self.summary()
        write_json(self.root / 'summary.json', summary, overwrite=True)
        lines = ['# Mario 协调器验证结果', '', f'任务：{summary["task_id"]}', '',
                 f'状态：{summary["status"]}；阶段：{summary["phase"]}；轮次：{summary["round"]}。', '',
                 '两位员工的回传属于 Agent 初评，人工确认仍为 pending。发送回执不等于员工完成。', '']
        for op in summary['operations']:
            doc = op.get('document') or {}
            lines.append(f'- {op["key"]}：派发 {op.get("dispatch")}，完成 {bool(op.get("done"))}；'
                         + (f'[结果文档]({doc["url"]})' if doc.get('url') else '尚无可核验文档'))
        if summary.get('last_error'):
            lines.extend(['', '当前错误：' + summary['last_error']])
        if summary.get('score'):
            lines.extend(['', '## Agent 初评分（不是人工或正式分数）', '', '```json',
                          json.dumps(summary['score'], ensure_ascii=False, indent=2), '```'])
        else:
            lines.extend(['', '尚未生成完整评测结果，不报告准确率、覆盖率或正式成绩。'])
        write_text(self.root / 'report.md', '\n'.join(lines) + '\n', overwrite=True)
        return summary

    def author_result(self, round_number=None):
        return read_json(self.path(f'operations/r{round_number or self.state["round"]:02d}-author/result.json'))

    def remote_payload(self, role):
        round_number = self.state['round']
        if role == 'author':
            value = {'brief': self.state['brief']}
            if round_number > 1:
                review = read_json(self.path(f'operations/r{round_number - 1:02d}-reviewer/result.json'))
                value.update(previous=self.author_result(round_number - 1),
                             review_feedback=review['metadata']['feedback'],
                             review_clarification=self.state['clarification'])
            return value
        if self.state['stage'] == 'requirement':
            from .requirement_flow import RequirementFlow
            return RequirementFlow(self).payload(role)
        if role == 'reviewer':
            author = self.author_result()
            return {'dialogue': author['dialogue'], 'artifact': author['artifact'],
                    'references': author['references'], 'brief': self.state['brief'],
                    'review_clarification': self.state['clarification']}
        prediction = read_json(self.path(self.state['prediction']))
        return {'dialogue': read_text(self.input_path('dialogue.md')),
                'artifact': read_text(self.input_path('artifact.md')),
                'references': read_json(self.path('scoring/references.json')), 'prediction': prediction}

    def operation(self, role):
        key = f'r{self.state["round"]:02d}-{role}' if role != 'scorer' else f'score-a{len(self.state["extractions"]):02d}'
        if key not in self.state['operations']:
            payload = self.remote_payload(role)
            identity = {'task_id': self.state['task_id'], 'sample_id': self.state['sample_id'],
                        'role': role, 'round': self.state['round'], 'input_version': digest(payload)}
            if role == 'scorer':
                identity['prediction_digest'] = (payload['prediction_digest'] if self.state['stage'] == 'requirement'
                                                  else digest(payload['prediction']))
            op = {'key': key, 'identity': identity, 'nonce': uuid.uuid4().hex,
                  'create': 'new', 'dispatch': 'new', 'done': False, 'polls': 0}
            self.state['operations'][key] = op
            self.save()
        op = self.state['operations'][key]
        # Deterministic regeneration recovers a crash between state and payload writes.
        self.put_json(f'operations/{key}/payload.json', self.remote_payload(role))
        if digest(read_json(self.path(f'operations/{key}/payload.json'))) != op['identity']['input_version']:
            raise ValueError('派发输入版本发生变化')
        return op

    def ensure_document(self, op):
        relative = f'operations/{op["key"]}'
        if op.get('document'):
            return True
        recovered = self.path(relative + '/create-recovered-receipt.json')
        receipt_path = recovered if recovered.exists() else self.path(relative + '/create-receipt.json')
        if not receipt_path.exists() and op['create'] == 'new':
            placeholder = '# 等待真实员工回传\n\n## assignment.json\n\n```json\n' + json.dumps(
                op['identity'], ensure_ascii=False, indent=2) + '\n```\n\nstatus: waiting\n'
            self.put_text(relative + '/placeholder.md', placeholder)
            op['create'] = 'started'
            self.save()  # Write-ahead: do not create again after an unknown outcome.
            self.event('document_create_started', operation=op['key'])
            try:
                receipt = self.docs.create(f'{self.state["task_id"]} {self.state["sample_id"]} {op["key"]}',
                                           self.path(relative + '/placeholder.md'))
                self.put_json(relative + '/create-receipt.json', receipt)
            except Exception as error:
                op['last_error'] = f'{type(error).__name__}: {error}'
        if receipt_path.exists():
            try:
                op['document'] = self.docs.document(read_json(receipt_path))
                op['create'] = 'completed'
                self.save()
                return True
            except Exception as error:
                op['last_error'] = f'{type(error).__name__}: {error}'
        self.state['status'] = 'create_unknown'
        self.state['last_error'] = op.get('last_error', '创建结果未知；核对原回执，禁止自动重建')
        self.save()
        return False

    def restore_receipt(self, op):
        path = self.dispatch_receipt_path(op)
        if not path.exists():
            return
        details = receipt_details(read_json(path))
        op['receipt'] = details
        if details['thread_id']:
            if self.config.dispatch_context == 'operation':
                if op.get('dispatch_thread_id') and op['dispatch_thread_id'] != details['thread_id']:
                    raise ValueError('Mario 回执 threadId 与本次操作不一致')
                op['dispatch_thread_id'] = details['thread_id']
            else:
                if self.state['thread_id'] and self.state['thread_id'] != details['thread_id']:
                    raise ValueError('Mario 回执 threadId 与本协调任务不一致')
                self.state['thread_id'] = details['thread_id']
        op['dispatch'] = 'acknowledged' if details['message_keys'] and not details['is_error'] else 'unknown'
        if rejected := message_size_rejection(read_json(path)):
            op['dispatch'] = 'rejected'
            op['last_error'] = f'{rejected["code"]}: {rejected["message"]}'
        self.save()

    def dispatch_receipt_path(self, op):
        if op.get('delivery_recovery'):
            return self.path(op['delivery_recovery']['path'] + '/dispatch-receipt.json')
        recovered = self.path(f'operations/{op["key"]}/dispatch-recovered-receipt.json')
        return recovered if recovered.exists() else self.path(f'operations/{op["key"]}/dispatch-receipt.json')

    def recover_rejected_dispatch(self, operation, reason):
        """One explicit audited change of transport after a proven size rejection.

        Preparation never sends a message. Resume performs the single new attempt.
        Unknown network outcomes, missing keys alone, and already-complete ops fail closed.
        """
        with run_lock(self.root):
            if read_json(self.root / 'state.json') != self.state:
                raise ValueError('状态已由其他进程更新，请重新读取')
            self.verify_files()
            op = self.state['operations'].get(operation)
            if not op or op.get('done') or self.state['phase'] != op['identity']['role']:
                raise ValueError('只能恢复当前未完成的明确拒绝操作')
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError('明确拒绝恢复必须记录授权原因')
            if op.get('delivery_recovery'):
                return self.summary()  # never allocate a second attempt on repeat
            original_path = self.dispatch_receipt_path(op)
            original = read_json(original_path)
            rejected = message_size_rejection(original)
            if not rejected:
                raise ValueError('没有明确 MESSAGE_TOO_LARGE_TO_SEND 拒绝证据；禁止重发未知发送')
            fetched = self.docs.fetch(op['document']['doc_id'])
            late = parse_document(fetched['content'], op['identity'], marker(op['identity'], op['nonce']), self.state['stage'])
            if late is not None:
                self.accept_result(op, fetched['content'], late)
                return self.report()
            assignment = sections(fetched['content']).get('assignment.json', '')
            if not assignment or json.loads(assignment) != op['identity']:
                raise ValueError('结果文档不是本任务等待占位，可能正在写入；禁止重发')
            path = f'operations/{operation}/delivery-recovery-01'
            recovery = {'path': path, 'reason': reason, 'created_at': self.clock(), 'rejection': rejected,
                        'original_receipt_path': str(original_path.relative_to(self.root)),
                        'original_receipt_sha256': file_digest(original_path),
                        'original_request_sha256': file_digest(self.path(f'operations/{operation}/request.md')),
                        'identity': op['identity'], 'document': op['document']}
            self.put_json(path + '/original-operation.json', op)
            self.put_json(path + '/original-state.json', self.state)
            self.put_json(path + '/authorization.json', recovery)
            self.put_text(path + '/result-before-recovery.md', fetched['content'])
            op['delivery_recovery'] = recovery
            # Retain the same operation thread, identity, nonce and result document.
            op['dispatch'] = 'new'
            op['receipt'] = None
            op['last_error'] = None
            self.state.update(status='running', last_error=None)
            self.save()
            self.event('size_rejection_recovery_prepared', operation=operation, rejection=rejected)
            return self.report()

    def dispatch(self, op):
        self.restore_receipt(op)
        if op['dispatch'] != 'new':
            return True
        if self.config.dispatch_context == 'task' and not self.state['thread_id'] and any(other['dispatch'] != 'new'
                                              for other in self.state['operations'].values() if other is not op):
            self.state['status'] = 'thread_unknown'
            self.state['last_error'] = '前次派发缺少 threadId；先接回原回执，禁止新开协调线程'
            self.save()
            return False
        # Verify the newly created document before letting an employee write to it.
        fetched = self.docs.fetch(op['document']['doc_id'])
        assignment = sections(fetched['content']).get('assignment.json', '')
        if not assignment or json.loads(assignment) != op['identity']:
            raise ValueError('新建文档的 assignment.json 回读不匹配，尚未发送消息')
        if self.mario is None:
            self.mario = self.mario_factory(self.config)
            self.state['mario_capabilities'] = self.mario.discover()
            self.save()
        identity = op['identity']
        request_path = self.path(f'operations/{op["key"]}/request.md')
        request = (read_text(request_path) if request_path.exists() else
                   worker_message(identity, marker(identity, op['nonce']), op['document']['url'],
                                  self.state['stage'], read_json(self.path(f'operations/{op["key"]}/payload.json'))))
        self.put_text(f'operations/{op["key"]}/request.md', request)
        message = DocumentDelivery(self).prepare(op, request, force=bool(op.get('delivery_recovery')))
        if message is None:
            return False
        op['dispatch'] = 'started'
        op['deadline'] = self.clock() + self.config.worker_timeout
        self.state['status'] = 'waiting'
        self.save()  # The dispatch attempt is durable before any outbound call.
        self.event('dispatch_started', operation=op['key'], document=op['document']['url'])
        try:
            employee = self.config.author_id if identity['role'] == 'author' else self.config.reviewer_id
            thread_id = self.state['thread_id'] if self.config.dispatch_context == 'task' else op.get('dispatch_thread_id')
            receipt = self.mario.dispatch(self.state['mario_capabilities']['skill_id'], thread_id,
                                          employee, message)
            receipt_path = str(self.dispatch_receipt_path(op).relative_to(self.root))
            self.put_json(receipt_path, receipt)
            self.restore_receipt(op)
        except Exception as error:
            op['dispatch'] = 'unknown'
            op['last_error'] = f'{type(error).__name__}: {error}'
            self.state['last_error'] = op['last_error']
            self.save()
            self.event('dispatch_unknown_no_resend', operation=op['key'], error=op['last_error'])
        return True

    def accept_result(self, op, content, parsed):
        if task := op.get('task_input'):
            # The returned digest binds to the exact input already read back before
            # dispatch. Preserve both versions; never take an echoed prediction.
            packet = read_json(self.path(task['base'] + '/task-packet.json'))
            verify_task(read_text(self.path(task['base'] + '/task-input-readback.md')), packet)
        self.put_text(f'operations/{op["key"]}/response.md', content)
        self.put_json(f'operations/{op["key"]}/result.json', parsed)
        op['done'] = True
        self.state.update(last_error=None, status='running')
        self.save()
        self.event('employee_completed_and_validated', operation=op['key'], doc_id=op['document']['doc_id'])
        self.advance(op['identity']['role'], parsed)

    def remote_step(self, role):
        op = self.operation(role)
        if not self.ensure_document(op) or not self.dispatch(op):
            return
        try:
            fetched = self.docs.fetch(op['document']['doc_id'])
            op['polls'] += 1
            op['last_capture'] = fetched.get('capture')
        except Exception as error:
            op['last_error'] = f'{type(error).__name__}: {error}'
            self.state['last_error'] = op['last_error']
            fetched = None
        if fetched:
            try:
                parsed = parse_document(fetched['content'], op['identity'], marker(op['identity'], op['nonce']), self.state['stage'],
                                        op.get('task_input', {}).get('digest'))
            except (ValueError, KeyError, TypeError) as error:
                self.put_text(f'operations/{op["key"]}/invalid-{op["polls"]:04d}.md', fetched['content'])
                self.state['status'] = 'protocol_error'
                self.state['last_error'] = f'员工已回传但程序校验失败：{error}'
                self.save()
                return
            if parsed is not None:
                self.accept_result(op, fetched['content'], parsed)
                return
        self.state['status'] = ('dispatch_rejected' if op['dispatch'] == 'rejected' else
                                ('waiting_timeout' if self.clock() >= op['deadline'] else 'waiting'))
        if op['dispatch'] == 'rejected':
            self.state['last_error'] = op['last_error']
        elif self.state['status'] == 'waiting_timeout' and not self.state.get('last_error'):
            self.state['last_error'] = (f'{op["key"]} 等待真实员工完成标记超过 worker_timeout；'
                                       f'结果文档 {op["document"]["doc_id"]} 未返回匹配的完成结果；'
                                       '保留原消息回执，不自动重发')
        self.save()
        self.event(self.state['status'], operation=op['key'], polls=op['polls'])

    def advance(self, role, result):
        if role == 'author':
            self.state['phase'] = 'reviewer'
        elif role == 'reviewer':
            status = result['metadata']['status']
            if status == 'passed':
                self.freeze()
                self.state['phase'] = 'extract'
            elif status == 'needs_revision' and self.state['round'] < self.config.max_rounds:
                self.state['round'] += 1
                self.state['phase'] = 'author'
            else:
                self.state['status'] = 'human_review'
                self.state['last_error'] = result['metadata']['feedback']
        else:
            if self.state['stage'] == 'requirement':
                from .requirement_flow import RequirementFlow
                score = RequirementFlow(self).score(result['judgments'])
            else:
                worksheet = read_json(self.path('scoring/worksheet.json'))
                reviewed = apply_judgments(worksheet, result['judgments'], review_kind='agent')
                score = score_worksheet(reviewed)
                self.put_json('scoring/review.json', reviewed)
                self.put_json('scoring/score.json', score)
            self.state['score'] = score
            self.state['status'] = 'agent_review_complete' if score['complete'] else 'agent_review_pending'
            self.state['phase'] = 'done'
        self.save()

    def freeze(self):
        if self.state['stage'] == 'requirement':
            from .requirement_flow import RequirementFlow
            return RequirementFlow(self).freeze()
        author = self.author_result()
        input_dir = f'inputs/{self.state["sample_id"]}'
        self.put_text(input_dir + '/dialogue.md', author['dialogue'])
        self.put_text(input_dir + '/artifact.md', author['artifact'])
        self.put_json('scoring/references.json', author['references'])
        sample = load_sample(self.input_path('dialogue.md'), self.input_path('artifact.md'), self.state['stage'])
        self.put_json('frozen.json', {'task_id': self.state['task_id'], 'sample_id': self.state['sample_id'],
                                    'stage': self.state['stage'], 'round': self.state['round'],
                                    'input_fingerprint': sample.fingerprint, 'file_hashes': sample.file_hashes,
                                    'references_digest': digest(author['references']), 'review_kind': 'agent',
                                    'human_review': 'pending'})
        self.state['frozen'] = True
        self.save()

    @staticmethod
    def _extract_process(dialogue, artifact, stage, config, mode, output):
        model = Config.load(config)
        timeout = min(3600, model.timeout * (model.retries + 1) * (6 if mode == 'verified' else 2) + 120)
        if stage == 'requirement':
            # One whole call (or verified steps) plus one baseline call per node.
            from .requirements import read_requirement_manifest
            count = len(read_requirement_manifest(Path(dialogue))[1])
            timeout = min(7200, model.timeout * (model.retries + 1) * (2 * count + 4) + 120)
            args = [sys.executable, '-m', 'decision_memory', 'requirement', 'compare', '--manifest', str(dialogue),
                    '--config', str(config), '--mode', mode, '--output', str(output.parent)]
        else:
            args = [sys.executable, '-m', 'decision_memory', 'extract', '--dialogue', str(dialogue),
                    '--artifact', str(artifact), '--stage', stage, '--config', str(config),
                    '--mode', mode, '--output', str(output)]
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return {'command': args, 'exit_code': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr}

    def extraction_step(self):
        if not self.state['model_config']:
            self.state['status'] = 'awaiting_model'
            self.save()
            return
        if self.state['stage'] == 'requirement':
            from .batch_gate import verify_batch_gate
            verify_batch_gate(self)
        attempts = self.state['extractions']
        if not attempts:
            attempts.append({'number': 1, 'status': 'new'})
            self.save()
        attempt = attempts[-1]
        relative = f'extraction/attempt-{attempt["number"]:02d}'
        prediction_path = self.path(relative + '/prediction.json')
        if prediction_path.exists():
            prediction = read_json(prediction_path)
            if attempt.get('config_digest') != file_digest(self.state['model_config']):
                raise ValueError('预测产生后模型配置已改变，不能将其作为当前配置的结果复用')
            if self.state['stage'] == 'requirement':
                from .comparison import validate_comparison
                from .requirement_flow import RequirementFlow
                flow = RequirementFlow(self)
                sample, nodes = flow.inputs()
                validate_comparison(prediction, sample, nodes, prediction_path.parent,
                                     Config.load(Path(self.state['model_config'])))
                rendered = '# 整需求提取对照\n\n两个匿名视图及原始结果已保存；语义质量待评审。\n'
                for name in [*prediction['raw_files'], 'job.json']:
                    self.state['files'][relative + '/' + name] = file_digest(prediction_path.parent / name)
                worksheet = flow.worksheets(prediction)
            else:
                sample = load_sample(self.input_path('dialogue.md'), self.input_path('artifact.md'), self.state['stage'])
                validate_saved(prediction, sample, ModelClient(Config.load(Path(self.state['model_config']))), self.state['mode'])
                rendered = render_markdown(prediction)
                worksheet = make_worksheet(prediction, read_json(self.path('scoring/references.json')), review_kind='agent')
            self.put_text(relative + '/prediction.md', rendered)
            self.state['files'][relative + '/prediction.json'] = file_digest(prediction_path)
            self.state['prediction'] = relative + '/prediction.json'
            attempt['status'] = 'completed'
            self.put_json('scoring/worksheet.json', worksheet)
            self.state['phase'] = 'scorer'
            self.state['status'] = 'running'
            self.state['last_error'] = None
            self.save()
            return
        if attempt['status'] != 'new':
            self.state['status'] = 'extraction_failed' if attempt['status'] == 'failed' else 'extraction_unknown'
            self.save()
            return
        attempt['status'] = 'started'
        attempt['config_digest'] = file_digest(self.state['model_config'])
        self.save()
        self.event('independent_cli_extraction_started', attempt=attempt['number'], mode=self.state['mode'])
        try:
            source = self.input_path('manifest.json') if self.state['stage'] == 'requirement' else self.input_path('dialogue.md')
            receipt = self.extractor(source, self.input_path('artifact.md'),
                                     self.state['stage'], Path(self.state['model_config']), self.state['mode'], prediction_path)
            self.put_json(relative + '/cli-receipt.json', receipt)
            if receipt['exit_code'] != 0 or not prediction_path.exists():
                raise TransportError(f'decision-memory extract exit={receipt["exit_code"]}：{receipt["stderr"].strip()}')
        except Exception as error:
            attempt['status'] = 'failed'
            attempt['error'] = f'{type(error).__name__}: {error}'
            self.state['last_error'] = attempt['error']
            self.state['status'] = 'extraction_failed'
            self.save()
            return
        self.extraction_step()

    def tick(self):
        self.verify_files()
        if self.state['status'] in TERMINAL:
            return
        if self.state['stage'] == 'requirement' and self.state['phase'] == 'scorer':
            from .batch_gate import verify_batch_gate
            verify_batch_gate(self)
        if self.state['phase'] == 'extract':
            self.extraction_step()
        else:
            role = self.state['phase']
            key = f'r{self.state["round"]:02d}-{role}' if role != 'scorer' else f'score-a{len(self.state["extractions"]):02d}'
            op = self.state['operations'].get(key)
            # Recover a crash after saving a result but before changing phase.
            if op and op.get('done'):
                self.advance(role, read_json(self.path(f'operations/{key}/result.json')))
            else:
                self.remote_step(role)

    def run(self, wait_seconds=45, retry_extraction=False, model_config=None):
        if not 0 <= wait_seconds <= 7200:
            raise ValueError('wait-seconds 必须为 0 到 7200')
        with run_lock(self.root):
            # Reload under the lock so a stale instance cannot overwrite a newer state.
            fresh = read_json(self.root / 'state.json')
            if fresh != self.state:
                raise ValueError('状态已由其他进程更新，请重新 resume')
            for operation in self.state['operations'].values():
                self.restore_receipt(operation)
            if model_config is not None:
                if self.state.get('prediction') or self.state['phase'] != 'extract':
                    raise ValueError('只能在待提取状态配置模型；已有预测不能换配置重评分')
                self.state['model_config'] = str(Path(model_config).resolve())
            if retry_extraction:
                if self.state['status'] not in ('extraction_failed', 'extraction_unknown'):
                    raise ValueError('retry-extraction 只适用于失败或结果未知的独立提取')
                self.state['extractions'].append({'number': len(self.state['extractions']) + 1, 'status': 'new'})
                self.state['status'] = 'running'
            self.save()
            end = time.monotonic() + wait_seconds
            try:
                while True:
                    self.tick()
                    self.report()
                    if self.state['status'] in TERMINAL | PAUSED or time.monotonic() >= end:
                        break
                    if self.state['status'] == 'waiting':
                        time.sleep(min(self.config.poll_interval, max(0, end - time.monotonic())))
            except (OSError, ValueError, TransportError, subprocess.SubprocessError) as error:
                self.state['last_error'] = f'{type(error).__name__}: {error}'
                self.save()
                self.report()
                raise
            return self.report()

    def reconcile(self, *, create_receipt=None, dispatch_receipt=None, operation=None):
        """Adopt operator-supplied real receipts, never retry uncertain side effects."""
        with run_lock(self.root):
            if read_json(self.root / 'state.json') != self.state:
                raise ValueError('状态已由其他进程更新，请重新接回')
            if operation:
                candidates = [op for op in self.state['operations'].values() if op['key'] == operation]
            else:
                candidates = [op for op in self.state['operations'].values()
                              if (create_receipt and op['create'] == 'started' and not op.get('document'))
                              or (dispatch_receipt and op['dispatch'] in ('started', 'unknown'))]
            if len(candidates) != 1:
                raise ValueError('没有唯一的待接回操作，请用 --operation 指定已有操作')
            op = candidates[0]
            if create_receipt:
                if op.get('document') or op['create'] != 'started':
                    raise ValueError('没有需要接回的未知创建操作')
                receipt = read_json(Path(create_receipt))
                document = self.docs.document(receipt)
                fetched = self.docs.fetch(document['doc_id'])
                assignment = sections(fetched['content']).get('assignment.json', '')
                completed = parse_document(fetched['content'], op['identity'], marker(op['identity'], op['nonce']), self.state['stage'])
                if not completed and (not assignment or json.loads(assignment) != op['identity']):
                    raise ValueError('待接回文档不属于当前操作')
                self.put_json(f'operations/{op["key"]}/create-recovered-receipt.json', receipt)
                op['document'] = document
                op['create'] = 'completed'
            if dispatch_receipt:
                if op['dispatch'] not in ('started', 'unknown'):
                    raise ValueError('没有需要接回的未知派发')
                receipt = read_json(Path(dispatch_receipt))
                details = receipt_details(receipt)
                if not details['thread_id'] or not details['message_keys']:
                    raise ValueError('接回派发回执必须包含真实 threadId 和 messageKey')
                self.put_json(f'operations/{op["key"]}/dispatch-recovered-receipt.json', receipt)
                self.restore_receipt(op)
            self.save()
            return self.report()
