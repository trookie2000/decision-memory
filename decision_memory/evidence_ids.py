"""Resolve model-selected reference IDs against immutable, uniquely located source text."""
import copy
import json

from .requirement_audit import CLAUSES
from .requirement_grounding import reason_obligations
from .requirement_memory import FIELDS, MAX_EVENTS

SOURCE_COLUMNS = ('source_id', 'node_id', 'node_order', 'file', 'line', 'role', 'message', 'text')
CODEC_POLICY = {'evidence_format': 'source-id-v1', 'source_format': 'columnar-v1',
                'columns': SOURCE_COLUMNS, 'clauses': CLAUSES.pattern, 'fragments': 'unique-only-C001'}


def reference_catalog(sample):
    result = {}
    for key, source in sample.catalog.items():
        if not source.text.strip():
            continue
        result[key] = {'source_id': key, 'quote': source.text}
        pieces = [match.group().strip() for match in CLAUSES.finditer(source.text)]
        if len(pieces) <= 1:
            continue
        for index, quote in enumerate(pieces, 1):
            position = source.text.find(quote)
            if quote and position >= 0 and source.text.find(quote, position + 1) < 0:
                result[f'{key}#C{index:03d}'] = {'source_id': key, 'quote': quote}
    return result


def source_packet(sample):
    rows = []
    for key, source in sample.catalog.items():
        order = sample.node_order(key)
        rows.append([key, sample.nodes[order]['id'] if order is not None else None, order, source.file, source.line,
                     source.role, source.message, source.text])
    fragments = [[key, ref['source_id'], ref['quote']] for key, ref in reference_catalog(sample).items()
                 if key not in sample.catalog]
    extra = {}
    if sample.has_supplements:
        from .input_contract import describe_input
        extra['documents'] = describe_input(sample)['documents']
    return json.dumps({'scope': 'requirement', 'requirement_id': sample.sample_id,
        'source_format': 'columnar-v1', 'nodes': sample.nodes, 'source_columns': SOURCE_COLUMNS, 'sources': rows,
        'fragment_columns': ['reference_id', 'source_id', 'text'], 'source_fragments': fragments, **extra},
        ensure_ascii=False, separators=(',', ':'))


def gap_catalog(sample):
    return [{'gap_id': f'G{index:03d}', **row} for index, row in enumerate(reason_obligations(sample), 1)]


def _object(value, keys, label):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError(label + ' 字段不完整或包含未知字段')
    return value


def _list(value, label):
    if not isinstance(value, list):
        raise ValueError(label + ' 必须为数组')
    return value


def _reference(value, catalog):
    if not isinstance(value, str) or value not in catalog:
        raise ValueError('证据须使用真实来源编号或唯一句片编号，不能手写引文')
    return dict(catalog[value])


def _references(value, catalog):
    ids = _list(value, '编号证据')
    result, seen = [], set()
    for key in ids:
        reference = _reference(key, catalog)
        identity = (reference['source_id'], reference['quote'])
        if identity in seen:
            raise ValueError('编号证据不能重复')
        result.append(reference)
        seen.add(identity)
    return result


def decode_event_stream(payload, sample):
    if (not isinstance(payload, dict) or set(payload) - {'events', 'open_questions', 'review_notes'}
            or not isinstance(payload.get('events'), list) or len(payload['events']) > MAX_EVENTS):
        raise ValueError('编号事件流需要 events/open_questions，最多512条事件')
    result, catalog = copy.deepcopy(payload), reference_catalog(sample)
    for event in result['events']:
        if not isinstance(event, dict):
            raise ValueError('编号事件必须为对象')
        operation = event.get('operation')
        if operation in ('add', 'update'):
            item = event.get('item')
            if not isinstance(item, dict):
                raise ValueError('新增或更新需要完整 item')
            for name in FIELDS:
                field = item.get(name)
                if not isinstance(field, dict):
                    raise ValueError('知识需要完整字段')
                field['evidence'] = _references(field.get('evidence'), catalog)
        elif operation == 'invalidate':
            event['evidence'] = _references(event.get('evidence'), catalog)
        elif operation == 'merge':
            evidence = event.get('evidence')
            if not isinstance(evidence, dict) or set(evidence) - set(FIELDS):
                raise ValueError('合并需要逐字段编号证据')
            event['evidence'] = {key: _references(value, catalog) for key, value in evidence.items()}
        else:
            raise ValueError('编号事件操作无效')
    for question in _list(result.get('open_questions'), 'open_questions'):
        if not isinstance(question, dict):
            raise ValueError('未决项需要对象')
        question['evidence'] = _references(question.get('evidence'), catalog)
    return result


def _gap(value, gaps):
    if not isinstance(value, str) or value not in gaps:
        raise ValueError('理由未知需要真实 gap_id')
    return gaps[value]


def decode_review_reply(payload, sample):
    _object(payload, {'events', 'open_questions', 'review_notes', 'grounding_review', 'evidence_audit'}, '编号复核')
    stream = decode_event_stream({key: payload[key] for key in ('events', 'open_questions', 'review_notes')}, sample)
    catalog = reference_catalog(sample)
    gaps = {row['gap_id']: row for row in gap_catalog(sample)}
    grounding = copy.deepcopy(_object(payload['grounding_review'], {'fields', 'reason_checks'}, '字段复核'))
    _list(grounding['fields'], 'fields')
    decoded_checks = []
    for check in _list(grounding['reason_checks'], 'reason_checks'):
        _object(check, {'gap_id', 'status', 'knowledge_key', 'unknown_text', 'evidence', 'notes'}, '编号理由未知')
        original = _gap(check['gap_id'], gaps)
        decoded_checks.append({key: value for key, value in check.items() if key != 'gap_id'})
        decoded_checks[-1].update(source_id=original['source_id'], quote=original['quote'],
                                  evidence=_references(check['evidence'], catalog))
    grounding['reason_checks'] = decoded_checks
    audit = copy.deepcopy(_object(payload['evidence_audit'], {
        'policy', 'evidence_focus', 'admission_checks', 'user_attribution_checks', 'coverage_checks'}, '编号审计'))
    if audit['evidence_focus'] != []:
        raise ValueError('编号模式不手写 evidence_focus；请选择实际来源或唯一句片')
    for admission in _list(audit['admission_checks'], 'admission_checks'):
        _object(admission, {'knowledge_key', 'basis', 'direct', 'proposal', 'confirmation', 'notes'}, '编号准入')
        for name in ('direct', 'proposal', 'confirmation'):
            admission[name] = _references(admission[name], catalog)
    for row in _list(audit['user_attribution_checks'], 'user_attribution_checks'):
        _object(row, {'knowledge_key', 'field', 'evidence', 'notes'}, '编号用户归因')
        row['evidence'] = _references(row['evidence'], catalog)
    for row in _list(audit['coverage_checks'], 'coverage_checks'):
        _object(row, {'unit_id', 'disposition', 'semantic_coverage', 'targets', 'exclusion_reason', 'notes'}, '编号覆盖')
        for target in _list(row['targets'], 'targets'):
            if not isinstance(target, dict):
                raise ValueError('编号覆盖目标须为对象')
            if target.get('type') == 'reason_check':
                _object(target, {'type', 'gap_id', 'evidence'}, '编号理由目标')
                original = _gap(target.pop('gap_id'), gaps)
                target.update(source_id=original['source_id'], quote=original['quote'])
            target['evidence'] = _reference(target.get('evidence'), catalog)
    return stream, grounding, audit


def _reference_id(reference, reverse):
    if not isinstance(reference, dict):
        raise ValueError('保存证据须为对象')
    identity = (reference.get('source_id'), reference.get('quote'))
    try:
        return reverse[identity]
    except (KeyError, TypeError):
        raise ValueError('保存证据不是由来源编号或唯一句片展开的原文') from None


def encode_event_stream(compiled, sample):
    reverse = {(ref['source_id'], ref['quote']): key for key, ref in reference_catalog(sample).items()}
    result = copy.deepcopy({key: compiled[key] for key in ('events', 'open_questions', 'review_notes')})
    encode = lambda refs: [_reference_id(ref, reverse) for ref in refs]
    for event in result['events']:
        if event['operation'] in ('add', 'update'):
            for name in FIELDS:
                event['item'][name]['evidence'] = encode(event['item'][name]['evidence'])
        elif event['operation'] == 'merge':
            event['evidence'] = {name: encode(refs) for name, refs in event['evidence'].items()}
        else:
            event['evidence'] = encode(event['evidence'])
    for question in result['open_questions']:
        question['evidence'] = encode(question['evidence'])
    return result


def validate_reference_expansion(result, sample):
    """Ensure saved v2 evidence could actually have been selected from its ID table."""
    encode_event_stream(result, sample)
    reverse = {(ref['source_id'], ref['quote']): key for key, ref in reference_catalog(sample).items()}
    for check in result['grounding_review']['reason_checks']:
        for ref in check['evidence']:
            _reference_id(ref, reverse)
    audit = result['evidence_audit']
    if audit['evidence_focus']:
        raise ValueError('编号模式不能保存手写焦点')
    for row in audit['admission_checks']:
        for name in ('direct', 'proposal', 'confirmation'):
            for ref in row[name]:
                _reference_id(ref, reverse)
    for row in audit['user_attribution_checks']:
        for ref in row['evidence']:
            _reference_id(ref, reverse)
    for row in audit['coverage_checks']:
        for target in row['targets']:
            _reference_id(target['evidence'], reverse)
