"""Version-bound Docs task inputs and deliberately small KIM notifications.

These documents contain assignments, never fabricated employee results.
The notification limit is a local policy, not a claimed KIM platform limit.
"""

import json
import re

from .battle_protocol import marker, sections
from .collaboration import receipt_details
from .evaluation import digest
from .storage import read_json


def message_size_rejection(receipt):
    """Recognize only the explicit rejection actually observed in this integration.

    Missing messageKey alone (including timeouts and exclamation marks) is unknown.
    This is evidence for an explicit recovery command, never automatic retry.
    """
    details = receipt_details(receipt)
    text = '\n'.join(block.get('text', '') for block in receipt.get('content', []) if block.get('type') == 'text')
    first = text.split('\n\n', 1)[0]
    if (details['message_keys'] or details['task_status'] != 'completed'
            or not first.startswith('消息发送失败') or not re.search(r'(?<!\d)82000(?!\d)', first)
            or 'MESSAGE_TOO_LARGE_TO_SEND' not in first):
        return None
    return {'code': '82000', 'message': 'MESSAGE_TOO_LARGE_TO_SEND',
            'source': 'explicit_mario_dispatch_receipt', 'evidence': first}


def task_packet(op, stage, payload, request):
    identity = op['identity']
    if digest(payload) != identity['input_version']:
        raise ValueError('Docs 任务输入与原始 input_version 不匹配')
    if 'prediction_digest' in identity:
        if stage == 'requirement':
            from .comparison import verify_scoring_views
            verify_scoring_views(payload, identity['prediction_digest'])
        elif digest(payload.get('prediction')) != identity['prediction_digest']:
            raise ValueError('Docs 任务输入与原始 prediction_digest 不匹配')
    return {'schema': 1, 'kind': 'decision_memory_task_input', 'identity': identity,
            'stage': stage, 'result_document': op['document'], 'completion_marker': marker(identity, op['nonce']),
            'request': request, 'payload': payload}


MAX_INPUT_RECREATES = 2


def render_task(packet, ascii_only=False):
    # ASCII escapes avoid a Docs write path that occasionally corrupts multi-byte characters.
    body = json.dumps(packet, ensure_ascii=ascii_only, indent=2)
    # Safely nest arbitrary source Markdown/backticks inside the JSON data fence.
    fence = '~' * max(3, 1 + max((len(part) for part in re.findall(r'~+', body)), default=0))
    content = ('# 待执行任务输入（不是员工评分结果）\n\n'
               '完整任务要求与原始材料均在下面的 task.json 中。请完整读取 request 字符串并执行；'
               'payload 是对应的原始数据。不要修改本输入文档。真实结果只能写入 result_document。\n\n'
               f'任务内容摘要：{digest(packet)}\n\n'
               '结果的 result.json 还须添加 task_input_digest，值为上述任务内容摘要；'
               '其他身份字段和完成标记严格沿用 task.json，不将本输入文档当作完成结果。\n\n'
               f'## task.json\n\n{fence}json\n{body}\n{fence}\n')
    if len(content.encode('utf-8')) > 2_000_000:
        raise ValueError('任务输入超过本地 2 MB 保护上限；尚未创建或派发')
    return content


def verify_task(content, expected):
    blocks = sections(content)
    if 'task.json' not in blocks or json.loads(blocks['task.json']) != expected:
        raise ValueError('Docs 任务文档回读与已固定任务不一致，禁止发送通知或采纳结果')
    task_packet({'identity': expected['identity'], 'nonce': expected['completion_marker'].rsplit('|', 1)[-1],
                 'document': expected['result_document']}, expected['stage'], expected['payload'], expected['request'])


def short_notification(packet, document, limit):
    identity = packet['identity']
    identity_text = json.dumps(identity, ensure_ascii=False, separators=(',', ':'))
    message = (f'【待执行任务；{identity["sample_id"]}；{identity["role"]}；第{identity["round"]}轮】\n'
               f'任务输入：{document["url"]}\n'
               f'结果回传：{packet["result_document"]["url"]}\n'
               f'任务身份：{identity_text}\n'
               f'task_input_digest: {digest(packet)}\n'
               '请用已有 Docs 读取能力完整读取任务输入文档的 task.json，执行其中 request；'
               '由你这位真实员工独立作答，在本群展示回复和结果链接。'
               '输入文档不是已完成结果，禁止修改它；只写指定结果文档、不改权限。'
               'result.json 须保留全部任务身份字段，并添加上面的 task_input_digest；'
               '完成后最后一行使用 task.json 的 completion_marker。'
               '不重写原始预测或参考，不委派外部 Codex，不操作键盘鼠标。'
               '无法读取或写入时如实报告，不能代填完成。')
    # Include the real structural mention that the transport prepends.
    if len(message.encode('utf-8')) + 64 > limit:
        raise ValueError('短通知仍超过本地通知长度限制，尚未发送')
    return message


class DocumentDelivery:
    def __init__(self, battle):
        self.battle = battle

    def prepare(self, op, request, *, force=False):
        battle = self.battle
        if not force and not op.get('task_input') and len(request.encode('utf-8')) + 64 <= battle.config.notification_max_bytes:
            return request
        base = op.get('delivery_recovery', {}).get('path', f'operations/{op["key"]}')
        payload = read_json(battle.path(f'operations/{op["key"]}/payload.json'))
        packet = task_packet(op, battle.state['stage'], payload, request)
        if not op.get('task_input'):
            op['task_input'] = {'create': 'new', 'base': base, 'digest': digest(packet), 'ascii': True}
            battle.save()
        current = op['task_input']
        if current['digest'] != digest(packet):
            raise ValueError('Docs 待执行任务版本发生变化')
        base = current['base']
        battle.put_json(base + '/task-packet.json', packet)
        battle.put_text(base + '/task-input.md', render_task(packet, current.get('ascii', False)))
        receipt_path = battle.path(base + '/task-input-create-receipt.json')
        if not current.get('document'):
            if not receipt_path.exists() and current['create'] == 'new':
                current['create'] = 'started'
                battle.save()
                battle.event('task_input_create_started', operation=op['key'], task_input_digest=current['digest'])
                try:
                    receipt = battle.docs.create(f'待执行输入 {battle.state["task_id"]} {op["key"]}',
                                                  battle.path(base + '/task-input.md'))
                    battle.put_json(base + '/task-input-create-receipt.json', receipt)
                except Exception as error:
                    current['last_error'] = f'{type(error).__name__}: {error}'
            if receipt_path.exists():
                current['document'] = battle.docs.document(read_json(receipt_path))
                current['create'] = 'completed'
                if current['document']['doc_id'] == op['document']['doc_id']:
                    raise ValueError('任务输入文档不能与结果回传文档相同')
                battle.save()
            else:
                battle.state.update(status='input_create_unknown', last_error=current.get('last_error') or
                                    '任务输入文档创建结果未知；保留原回执，不自动重建')
                battle.save()
                return None
        fetched = battle.docs.fetch(current['document']['doc_id'])
        try:
            verify_task(fetched['content'], packet)
        except ValueError:
            # Nothing has been sent yet: the notification is built only after a matching
            # readback. Keep the corrupted copy for audit and create a fresh input document.
            corrupted = current.get('corrupted', [])
            if len(corrupted) >= MAX_INPUT_RECREATES:
                raise
            battle.put_text(base + '/task-input-readback-mismatch.md', fetched['content'])
            retry_base = f'operations/{op["key"]}/input-retry-{len(corrupted) + 1:02d}'
            op['task_input'] = {'create': 'new', 'base': retry_base, 'digest': current['digest'], 'ascii': True,
                                'corrupted': corrupted + [current['document']]}
            battle.save()
            battle.event('task_input_recreate', operation=op['key'], corrupted_doc=current['document']['doc_id'],
                         attempt=len(corrupted) + 1)
            return self.prepare(op, request, force=force)
        if not battle.path(base + '/task-input-readback.md').exists():
            battle.put_text(base + '/task-input-readback.md', fetched['content'])
        current['last_capture'] = fetched.get('capture')
        current['verified'] = True
        battle.save()
        message = short_notification(packet, current['document'], battle.config.notification_max_bytes)
        battle.put_text(base + '/notification.md', message)
        battle.put_json(base + '/delivery-binding.json', {'identity': op['identity'], 'task_input_digest': digest(packet),
                        'task_input_document': current['document'], 'result_document': op['document'],
                        'completion_marker': packet['completion_marker'], 'notification_bytes': len(message.encode('utf-8')),
                        'request_digest': digest(request), 'notification_digest': digest(message)})
        return message
