"""Validate field-level citations against exact original text."""

from .sources import Sample


def validate_evidence(evidence: object, sample: Sample, location: str) -> list[dict]:
    if not isinstance(evidence, list):
        raise ValueError(f'{location} 证据必须是数组')
    resolved = []
    for reference in evidence:
        if not isinstance(reference, dict):
            raise ValueError(f'{location} 证据必须是对象')
        source_id = reference.get('source_id')
        quote = reference.get('quote')
        if not isinstance(source_id, str) or source_id not in sample.catalog:
            raise ValueError(f'{location} 来源编号不存在')
        source = sample.catalog[source_id]
        if not isinstance(quote, str) or not quote.strip() or quote not in source.text:
            raise ValueError(f'{location} 引文与 {source_id} 原文不匹配')
        resolved.append({'source_id': source_id, 'quote': quote, 'file': source.file,
                         'line': source.line, 'message': source.message})
    return resolved


def validate_field(field: object, sample: Sample, location: str, required: bool) -> dict:
    if not isinstance(field, dict) or not isinstance(field.get('text'), str):
        raise ValueError(f'{location} 必须包含 text 和 evidence')
    text = field['text'].strip()
    if not text or (required and text == '未说明'):
        raise ValueError(f'{location} 缺少明确结论')
    evidence = validate_evidence(field.get('evidence'), sample, location)
    if text == '未说明' and evidence:
        raise ValueError(f'{location} 未说明字段应无证据')
    if text != '未说明' and not evidence:
        raise ValueError(f'{location} 缺少原文证据')
    return {'text': text, 'evidence': evidence}


def validate_result(payload: object, sample: Sample) -> dict:
    if not isinstance(payload, dict):
        raise ValueError('模型结果必须是 JSON 对象')
    items, questions = payload.get('items'), payload.get('open_questions')
    if not isinstance(items, list) or not isinstance(questions, list):
        raise ValueError('结果必须包含 items 和 open_questions 数组')
    normalized = []
    seen = set()
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict):
            raise ValueError('条目必须是对象')
        entry = {'id': f'K{index:03d}'}
        for name in ('conclusion', 'reason', 'boundary'):
            entry[name] = validate_field(item.get(name), sample, f'items[{index}].{name}', name == 'conclusion')
        identity = tuple(entry[name]['text'] for name in ('conclusion', 'reason', 'boundary'))
        if identity in seen:
            raise ValueError('存在完全重复条目')
        seen.add(identity)
        normalized.append(entry)
    open_questions = [validate_field(item, sample, 'open_questions', True) for item in questions]
    return {'items': normalized, 'open_questions': open_questions}
