"""Replay explicit, source-bound knowledge operations; never infer semantic merges."""
import copy

from .requirements import identifier
from .validation import validate_evidence, validate_result

FIELDS = ('conclusion', 'reason', 'boundary')
OPERATIONS = ('add', 'update', 'invalidate', 'merge')
MAX_EVENTS = 512


def compile_memory(payload, sample):
    if (not isinstance(payload, dict) or set(payload) - {'events', 'open_questions', 'review_notes'}
            or not isinstance(payload.get('events'), list) or len(payload['events']) > MAX_EVENTS):
        raise ValueError('需求结果需要 events 和 open_questions；events 最多 512 条')
    questions = validate_result({'items': [], 'open_questions': payload.get('open_questions')}, sample)['open_questions']
    notes = payload.get('review_notes', [])
    if not isinstance(notes, list) or any(not isinstance(note, str) for note in notes):
        raise ValueError('review_notes 必须为字符串数组')
    orders = {node['id']: index for index, node in enumerate(sample.nodes)}
    events, history, relations = [], [], []
    heads, versions, current, positions = {}, {}, {}, {}
    seen, last_order = set(), -1

    def citations(value, order, required=False):
        resolved = validate_evidence(value, sample, '需求事件')
        if any(sample.node_order(entry['source_id']) > order for entry in resolved):
            raise ValueError('事件不能引用未来节点')
        if required and not any(sample.node_order(entry['source_id']) == order for entry in resolved):
            raise ValueError('事件必须有所属节点的原文证据')
        return resolved

    def item(value, order):
        normalized = validate_result({'items': [value], 'open_questions': []}, sample)['items'][0]
        normalized.pop('id')
        for field in FIELDS:
            citations(normalized[field]['evidence'], order, field == 'conclusion')
        return normalized

    for raw in payload['events']:
        if not isinstance(raw, dict):
            raise ValueError('需求事件必须是对象')
        event_id = identifier(raw.get('id'), 'event.id')
        key, node_id, operation = raw.get('knowledge_key'), raw.get('node_id'), raw.get('operation')
        if event_id in seen:
            raise ValueError('事件 ID 重复')
        if not isinstance(key, str) or not key.strip() or len(key) > 128:
            raise ValueError('knowledge_key 必须为 1～128 位非空字符串')
        if not isinstance(node_id, str) or node_id not in orders or operation not in OPERATIONS:
            raise ValueError('事件 node_id 或 operation 无效')
        common = {'id', 'knowledge_key', 'node_id', 'operation', 'previous'}
        extra = {'item'} if operation in ('add', 'update') else (
            {'evidence', 'note'} if operation == 'invalidate' else {'evidence'})
        if set(raw) != common | extra:
            raise ValueError('事件字段与操作类型不一致')
        order = orders[node_id]
        if order < last_order:
            raise ValueError('事件必须按节点顺序排列')
        previous = raw['previous']
        if operation == 'add':
            if previous is not None or key in heads:
                raise ValueError('add 只能用于尚未出现的知识，previous 必须为空')
        elif key not in heads or previous != heads[key]:
            raise ValueError('previous 必须指向同一知识的最新事件')
        if operation in ('invalidate', 'merge') and key not in current:
            raise ValueError('失效与合并操作需要当前有效知识；重新确认请用 update')
        event = {name: raw[name] for name in ('id', 'knowledge_key', 'node_id', 'operation', 'previous')}
        if operation in ('add', 'update'):
            event['item'] = item(raw['item'], order)
            for index in positions.get(key, []):
                if history[index]['status'] in ('current', 'supporting'):
                    history[index]['status'] = 'superseded'
            versions[key] = versions.get(key, 0) + 1
            current[key] = copy.deepcopy(event['item'])
        elif operation == 'invalidate':
            if not isinstance(raw['note'], str) or not raw['note'].strip():
                raise ValueError('失效操作需要非空 note 说明')
            event.update(evidence=citations(raw['evidence'], order, True), note=raw['note'])
            for index in positions.get(key, []):
                if history[index]['status'] in ('current', 'supporting'):
                    history[index]['status'] = 'invalidated'
            del current[key]
        else:
            supplied = raw['evidence']
            if not isinstance(supplied, dict) or not supplied or set(supplied) - set(FIELDS):
                raise ValueError('合并证据需要 conclusion/reason/boundary 字段数组')
            event['evidence'] = {field: citations(refs, order) for field, refs in supplied.items()}
            citations([ref for refs in event['evidence'].values() for ref in refs], order, True)
            for field, refs in event['evidence'].items():
                if refs and current[key][field]['text'] == '未说明':
                    raise ValueError('合并不能为未说明字段添加证据；新增解释请用 update')
                existing = current[key][field]['evidence']
                identities = {(entry['source_id'], entry['quote']) for entry in existing}
                for ref in refs:
                    identity = (ref['source_id'], ref['quote'])
                    if identity not in identities:
                        existing.append(ref)
                        identities.add(identity)
            for index in positions.get(key, []):
                if history[index]['status'] == 'current':
                    history[index]['status'] = 'supporting'
        if previous is not None:
            relations.append({'from': event_id, 'to': previous,
                              'type': {'update': 'updates', 'invalidate': 'invalidates', 'merge': 'supports'}[operation]})
        seen.add(event_id)
        heads[key], last_order = event_id, order
        events.append(copy.deepcopy(event))
        positions.setdefault(key, []).append(len(history))
        history.append({**copy.deepcopy(event), 'version': versions[key],
                        'status': 'invalidated' if operation == 'invalidate' else 'current'})
    normalized = validate_result({'items': list(current.values()), 'open_questions': questions}, sample)
    index = [{'item_id': row['id'], 'knowledge_key': key, 'event_id': heads[key], 'version': versions[key]}
             for row, key in zip(normalized['items'], current)]
    return {**normalized, 'events': events, 'memory_index': index, 'history': history,
            'relations': relations, 'review_notes': list(notes)}
