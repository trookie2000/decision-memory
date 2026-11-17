"""Validate model field review and explicit, source-bound rationale gaps."""
import re

from .evaluation import digest
from .validation import validate_evidence

FIELDS = ('conclusion', 'reason', 'boundary')
UNKNOWN_REASON = re.compile(
    r'(?:原因|理由|依据)[^。；\n]{0,24}(?:未说明|未明确|未解释|未给出|未知|待确认)'
    r'|(?:没有|尚未|未)(?:说明|明确|解释|给出)[^。；\n]{0,12}(?:原因|理由|依据)')
REASON_HEADING = re.compile(r'^\s*#{1,6}\s*(?:原因|理由|依据)未说明(?:的决定|的事项|的设计)?\s*$')
UNKNOWN_STATE = re.compile(r'未说明|未明确|未解释|未给出|未知|待确认|(?:没有|尚未)(?:说明|明确|解释|给出)')
CLAUSES = re.compile(r'[^。；;！？!?\n]+[。；;！？!?]?')


def reason_obligations(sample):
    """Find explicit gap statements, not inferred missing reasons or gold labels."""
    result, seen = [], set()
    for key, source in sample.catalog.items():
        if REASON_HEADING.fullmatch(source.text):
            continue
        for match in CLAUSES.finditer(source.text):
            quote = match.group().strip()
            if UNKNOWN_REASON.search(quote) and (key, quote) not in seen:
                result.append({'source_id': key, 'quote': quote})
                seen.add((key, quote))
    return result


def _quotes_gap(reference, gap):
    quote = reference['quote']
    return (reference['source_id'] == gap['source_id'] and UNKNOWN_STATE.search(quote)
            and (quote in gap['quote'] or gap['quote'] in quote))


def validate_grounding_review(value, compiled, sample):
    if not isinstance(value, dict) or set(value) not in (
            {'fields', 'reason_checks'}, {'schema_version', 'input_digest', 'fields', 'reason_checks'}):
        raise ValueError('字段复核需要 fields 和 reason_checks')
    binding = digest({key: compiled[key] for key in ('items', 'memory_index')})
    if 'schema_version' in value and (type(value['schema_version']) is not int
            or value['schema_version'] != 1 or value['input_digest'] != binding):
        raise ValueError('字段复核与当前知识摘要不匹配')
    items = {row['knowledge_key']: compiled['items'][index]
             for index, row in enumerate(compiled['memory_index'])}
    node_orders = {node['id']: index for index, node in enumerate(sample.nodes)}
    events = {event['id']: event for event in compiled['events']}
    positions = {row['knowledge_key']: node_orders[events[row['event_id']]['node_id']]
                 for row in compiled['memory_index']}
    expected = {(key, field) for key in items for field in FIELDS}
    fields = value.get('fields')
    if not isinstance(fields, list):
        raise ValueError('字段复核 fields 必须为数组')
    indexed = {}
    for row in fields:
        if (not isinstance(row, dict) or set(row) != {'knowledge_key', 'field', 'status', 'notes'}
                or not isinstance(row['knowledge_key'], str) or not isinstance(row['field'], str)):
            raise ValueError('字段复核条目格式错误')
        target = (row['knowledge_key'], row['field'])
        if target not in expected or target in indexed:
            raise ValueError('字段复核有重复或未知的当前知识字段')
        if not isinstance(row['notes'], str) or not row['notes'].strip():
            raise ValueError('字段复核需要具体判定理由')
        field = items[target[0]][target[1]]
        allowed = {'unstated'} if field['text'] == '未说明' else (
            {'supported', 'partial'} if target[1] == 'reason' else {'supported'})
        if not isinstance(row['status'], str) or row['status'] not in allowed:
            raise ValueError('字段复核仍有不受支持或与正文不一致的判定')
        indexed[target] = row
    if set(indexed) != expected:
        raise ValueError('字段复核必须覆盖全部当前知识字段')
    obligations = {(row['source_id'], row['quote']): row for row in reason_obligations(sample)}
    checks = value.get('reason_checks')
    if not isinstance(checks, list):
        raise ValueError('理由未知处理清单必须为数组')
    normalized, seen = [], set()
    for row in checks:
        if not isinstance(row, dict) or set(row) != {
                'source_id', 'quote', 'status', 'knowledge_key', 'unknown_text', 'evidence', 'notes'}:
            raise ValueError('理由未知处理条目格式错误')
        source_id = row['source_id']
        if not isinstance(source_id, str) or not isinstance(row['quote'], str):
            raise ValueError('理由未知清单需要真实来源和声明片段')
        identity = (source_id, row['quote'])
        if identity not in obligations or identity in seen:
            raise ValueError('理由未知清单包含重复或未知声明')
        gap = obligations[identity]
        if (not isinstance(row['notes'], str) or not row['notes'].strip()
                or not isinstance(row['unknown_text'], str)):
            raise ValueError('理由未知处理需要具体说明')
        evidence = validate_evidence(row['evidence'], sample, '理由未知复核')
        if not evidence:
            raise ValueError('理由未知处理需要原文依据')
        key, status = row['knowledge_key'], row['status']
        if status == 'retained':
            if not isinstance(key, str) or key not in items:
                raise ValueError('理由未知必须关联当前有效知识')
            reason = items[key]['reason']
            text = row['unknown_text'].strip()
            if not text or not UNKNOWN_STATE.search(text) or text not in reason['text']:
                raise ValueError('理由未知未保留在当前 reason 正文')
            if not any(_quotes_gap(ref, gap) for ref in evidence):
                raise ValueError('保留理由未知需要引用其原始声明')
            if reason['text'] != '未说明':
                if indexed[(key, 'reason')]['status'] != 'partial':
                    raise ValueError('部分理由未知需要 partial 字段判定')
                if not any(_quotes_gap(ref, gap) for ref in reason['evidence']):
                    raise ValueError('部分理由未知需要 reason 中的对应原文证据')
            if any(sample.node_order(ref['source_id']) > positions[key] for ref in evidence):
                raise ValueError('理由未知不能用晚于当前知识的声明补证据；请更新事件')
        elif status in ('superseded', 'out_of_scope'):
            if key is not None or row['unknown_text']:
                raise ValueError('非保留的理由未知不能关联当前知识或补写未知正文')
            if status == 'superseded' and not any(
                    sample.node_order(ref['source_id']) > sample.node_order(source_id) for ref in evidence):
                raise ValueError('理由未知已解决或取消必须提供更晚节点的依据')
            if status == 'out_of_scope' and any(
                    _quotes_gap(ref, gap) for item in items.values()
                    for field in FIELDS for ref in item[field]['evidence']):
                raise ValueError('当前知识已经引用的理由未知不能标为范围外')
        else:
            raise ValueError('理由未知处理状态无效')
        normalized.append({**row, 'evidence': evidence})
        seen.add(identity)
    if seen != set(obligations):
        raise ValueError('理由未知清单必须逐项处理原文明示的未知')
    return {'schema_version': 1, 'input_digest': binding, 'fields': fields, 'reason_checks': normalized}
