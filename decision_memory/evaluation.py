"""Explicit human judgments, kept outside every inference input."""

import hashlib
import json
import copy
import re

MARKDOWN_NOISE = re.compile(r'\*\*|__|`|^\s*(?:[-*+>]|#{1,6}|\d+[.、])\s+', re.M)


SPEAKER = re.compile(r'^\s*(?:\*\*)?\s*(?:AGENT|用户|assistant|user)\s*[:：]\s*(?:\*\*)?', re.I)


def plain(text):
    """Rendered view of a Markdown line: drop emphasis/list markers and all whitespace.

    Reference authors often quote what they see rendered ("用户：…") rather than the raw
    Markdown ("**用户：** …"). Only reference checking uses this; extraction citations
    stay strictly verbatim.
    """
    return re.sub(r'\s+', '', MARKDOWN_NOISE.sub('', text))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def validate_references(references, sample):
    """Check reference identities and exact quotes, not semantic correctness."""
    if not isinstance(references, list) or not references:
        raise ValueError('参考答案必须是非空的 JSON 数组')
    identifiers(references, 'id')
    for reference in references:
        if not isinstance(reference.get('text'), str) or not reference['text'].strip():
            raise ValueError('参考条目缺少 text')
        evidence = reference.get('evidence')
        if not isinstance(evidence, list) or not evidence:
            raise ValueError('参考条目缺少逐字原文 evidence')
        for item in evidence:
            if not isinstance(item, dict) or item.get('source') not in ('dialogue', 'artifact'):
                raise ValueError('参考证据的 source 只能为 dialogue/artifact')
            prefix = 'D' if item['source'] == 'dialogue' else 'A'
            if item.get('node') is not None:
                # Multi-node samples number lines per node: D1-…, A2-….
                if type(item['node']) is not int or item['node'] < 1:
                    raise ValueError('参考证据的 node 必须是正整数')
                prefix += f'{item["node"]}-'
            quote = item.get('quote')
            # A leading speaker label on a mid-message fragment is metadata, not quoted text.
            needle = plain(SPEAKER.sub('', quote)) if isinstance(quote, str) else ''
            if not needle or not any(
                    key.startswith(prefix) and needle in plain(source.text)
                    for key, source in sample.catalog.items()):
                raise ValueError('参考答案存在无法在对应原文行中找到的引文')
    return references


def make_worksheet(result: dict, references: list[dict], review_kind='human', *, schema_version=1,
                   reference_policy=None) -> dict:
    if schema_version == 2:
        from .review_v2 import make_worksheet_v2
        return make_worksheet_v2(result, references, reference_policy, review_kind)
    if schema_version != 1 or reference_policy is not None:
        raise ValueError('reference_policy 仅用于 schema_version=2 的分轨评测')
    items = copy.deepcopy(result['items'])
    worksheet = {'sample_id': result['sample_id'], 'result_digest': digest(items),
                 'items': items, 'references': copy.deepcopy(references),
                 'references_digest': digest(references), 'prediction_digest': digest(result),
                 'review_kind': review_kind,
                 'judgments': [{'item_id': item['id'], 'correct': None, 'citation_support': None,
                                'matches': [], 'notes': ''} for item in items]}
    score_worksheet(worksheet)
    return worksheet


def apply_judgments(worksheet, judgments, review_kind='agent'):
    """A reviewer may fill judgments only, never rewrite predictions or gold."""
    score_worksheet(worksheet)
    reviewed = copy.deepcopy(worksheet)
    reviewed['review_kind'] = review_kind
    reviewed['judgments'] = copy.deepcopy(judgments)
    score_worksheet(reviewed)
    for judgment in reviewed['judgments']:
        if (judgment['correct'] is not None or judgment['citation_support'] is not None) and not judgment['notes'].strip():
            raise ValueError('已评条目必须给出 notes 判定理由')
    return reviewed


def score_worksheet(worksheet: dict) -> dict:
    if not isinstance(worksheet, dict):
        raise ValueError('评测表必须为 JSON 对象')
    version = worksheet.get('schema_version', 1)
    if type(version) is not int or version not in (1, 2):
        raise ValueError('不支持的评测表 schema_version')
    if version == 2:
        from .review_v2 import score_worksheet_v2
        return score_worksheet_v2(worksheet)
    items = worksheet.get('items')
    references = worksheet.get('references')
    judgments = worksheet.get('judgments')
    if not all(isinstance(value, list) for value in (items, references, judgments)):
        raise ValueError('评测表需要 items/references/judgments 数组')
    if worksheet.get('result_digest') != digest(items):
        raise ValueError('评测条目已改动，请从原始结果重新生成评测表')
    if 'references_digest' in worksheet and worksheet['references_digest'] != digest(references):
        raise ValueError('参考答案已改动，不能用修改后的 gold 给原始预测计分')
    review_kind = worksheet.get('review_kind', 'human')
    if review_kind not in ('human', 'agent'):
        raise ValueError('review_kind 必须为 human 或 agent')
    item_ids = identifiers(items, 'id')
    reference_ids = identifiers(references, 'id')
    judgment_ids = identifiers(judgments, 'item_id')
    if item_ids != judgment_ids:
        raise ValueError('judgments 必须与结果条目逐条对应')
    reviewed = correct = citation_reviewed = supported = 0
    matches = set()
    for judgment in judgments:
        for name in ('correct', 'citation_support'):
            if name not in judgment:
                raise ValueError(f'缺少 {name}；未审条目请显式保留 null')
            if judgment.get(name) is not None and type(judgment[name]) is not bool:
                raise ValueError(f'{name} 只能为 true/false/null')
        if not isinstance(judgment.get('notes'), str):
            raise ValueError('notes 必须是字符串')
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
    return {'metric_scope': f'local_{review_kind}_review_not_official_score', 'items': len(items),
            'reviewed_items': reviewed, 'pending_items': len(items) - reviewed,
            'correct_items': correct, 'supported_items': supported,
            'precision_reviewed': correct / reviewed if reviewed else None,
            'citation_support_reviewed': supported / citation_reviewed if citation_reviewed else None,
            'pending_citations': len(items) - citation_reviewed,
            'covered_references': len(matches), 'reference_count': len(reference_ids),
            'recall': len(matches) / len(reference_ids) if reference_ids and complete else None,
            'complete': complete, 'human_review': 'pending' if review_kind == 'agent' else ('complete' if complete else 'pending'),
            'official_score': None}


def identifiers(rows, field):
    values = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get(field), str) or not row[field]:
            raise ValueError(f'评测条目缺少 {field}')
        values.append(row[field])
    if len(values) != len(set(values)):
        raise ValueError('评测条目 ID 重复')
    return set(values)
