"""Explicit human judgments, kept outside every inference input."""

import hashlib
import json


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def make_worksheet(result: dict, references: list[dict]) -> dict:
    items = result['items']
    worksheet = {'sample_id': result['sample_id'], 'result_digest': digest(items),
                 'items': items, 'references': references,
                 'judgments': [{'item_id': item['id'], 'correct': None, 'citation_support': None,
                                'matches': [], 'notes': ''} for item in items]}
    score_worksheet(worksheet)
    return worksheet


def score_worksheet(worksheet: dict) -> dict:
    items = worksheet.get('items')
    references = worksheet.get('references')
    judgments = worksheet.get('judgments')
    if not all(isinstance(value, list) for value in (items, references, judgments)):
        raise ValueError('评测表需要 items/references/judgments 数组')
    if worksheet.get('result_digest') != digest(items):
        raise ValueError('评测条目已改动，请从原始结果重新生成评测表')
    item_ids = identifiers(items, 'id')
    reference_ids = identifiers(references, 'id')
    judgment_ids = identifiers(judgments, 'item_id')
    if item_ids != judgment_ids:
        raise ValueError('judgments 必须与结果条目逐条对应')
    reviewed = correct = citation_reviewed = supported = 0
    matches = set()
    for judgment in judgments:
        for name in ('correct', 'citation_support'):
            if judgment.get(name) is not None and type(judgment[name]) is not bool:
                raise ValueError(f'{name} 只能为 true/false/null')
        assigned = judgment.get('matches')
        if not isinstance(assigned, list) or any(not isinstance(key, str) for key in assigned):
            raise ValueError('matches 必须是参考条目 ID 数组')
        if len(assigned) != len(set(assigned)) or not set(assigned).issubset(reference_ids):
            raise ValueError('matches 包含重复或未知参考条目')
        if assigned and judgment.get('correct') is not True:
            raise ValueError('只有已确认正确的条目可以匹配参考答案')
        if judgment.get('correct') is not None:
            reviewed += 1
            correct += judgment['correct']
        if judgment.get('citation_support') is not None:
            citation_reviewed += 1
            supported += judgment['citation_support']
        matches.update(assigned)
    complete = reviewed == len(items) and citation_reviewed == len(items)
    return {'metric_scope': 'local_human_review_not_official_score', 'items': len(items),
            'reviewed_items': reviewed, 'pending_items': len(items) - reviewed,
            'precision_reviewed': correct / reviewed if reviewed else None,
            'citation_support_reviewed': supported / citation_reviewed if citation_reviewed else None,
            'pending_citations': len(items) - citation_reviewed,
            'covered_references': len(matches), 'reference_count': len(reference_ids),
            'recall': len(matches) / len(reference_ids) if reference_ids and complete else None,
            'complete': complete, 'official_score': None}


def identifiers(rows, field):
    values = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get(field), str) or not row[field]:
            raise ValueError(f'评测条目缺少 {field}')
        values.append(row[field])
    if len(values) != len(set(values)):
        raise ValueError('评测条目 ID 重复')
    return set(values)
