"""Opt-in evaluation of decisions and uncertainty, without rewriting old scores.

A match nominates supporting output, not full semantic coverage of a reference.
Full/partial/absent judgments are explicit and bound to the frozen prediction.
"""

import copy

from .evaluation import digest, identifiers

KINDS = ('decision', 'uncertainty')
FIXED_FIELDS = ('sample_id', 'prediction_digest', 'items', 'open_questions', 'references', 'reference_policy')


def validate_policy(policy, references):
    if not isinstance(policy, dict) or type(policy.get('schema_version')) is not int or policy['schema_version'] != 1:
        raise ValueError('reference_policy 需要 schema_version=1')
    if policy.get('references_digest') != digest(references):
        raise ValueError('参考分类策略与固定 references 摘要不匹配')
    assignments = policy.get('assignments')
    if not isinstance(assignments, list) or identifiers(assignments, 'reference_id') != identifiers(references, 'id'):
        raise ValueError('参考分类必须与 references 的 ID 一一对应')
    result = {}
    for row in assignments:
        if row.get('kind') not in KINDS or not isinstance(row.get('rationale'), str) or not row['rationale'].strip():
            raise ValueError('每条参考需要 decision/uncertainty 分类和 rationale')
        result[row['reference_id']] = row['kind']
    return result


def make_worksheet_v2(result, references, policy, review_kind='human'):
    items = copy.deepcopy(result['items'])
    questions = copy.deepcopy(result.get('open_questions', []))
    worksheet = {'schema_version': 2, 'sample_id': result['sample_id'], 'prediction_digest': digest(result),
                 'items': items, 'open_questions': questions, 'references': copy.deepcopy(references),
                 'reference_policy': copy.deepcopy(policy), 'review_kind': review_kind}
    worksheet['content_digest'] = digest({name: worksheet[name] for name in FIXED_FIELDS})

    def pending(key, value):
        return {key: value, 'correct': None, 'citation_support': None, 'matches': [], 'notes': ''}

    worksheet['judgments'] = [pending('item_id', item['id']) for item in items]
    # IDs belong to the worksheet, not to (and never inserted into) raw predictions.
    worksheet['open_question_judgments'] = [pending('question_id', f'Q{index:03d}')
                                           for index in range(1, len(questions) + 1)]
    worksheet['reference_judgments'] = [{'reference_id': ref['id'], 'coverage': None,
                                        'supported_by': [], 'notes': ''} for ref in references]
    score_worksheet_v2(worksheet)
    return worksheet


def _judgment_stats(rows, key, expected, reference_ids):
    if not isinstance(rows, list) or identifiers(rows, key) != expected:
        raise ValueError(f'{key} judgments 必须与原始输出逐条对应')
    reviewed = correct = citation_reviewed = supported = 0
    indexed = {}
    for row in rows:
        for name in ('correct', 'citation_support'):
            if name not in row or (row[name] is not None and type(row[name]) is not bool):
                raise ValueError(f'{name} 只能为 true/false/null，且不可省略')
        if not isinstance(row.get('notes'), str):
            raise ValueError('notes 必须是字符串')
        if any(row[name] is not None for name in ('correct', 'citation_support')) and not row['notes'].strip():
            raise ValueError('已审条目必须说明判定理由')
        matches = row.get('matches')
        if (not isinstance(matches, list) or any(not isinstance(value, str) for value in matches)
                or len(matches) != len(set(matches)) or not set(matches) <= reference_ids):
            raise ValueError('matches 包含重复或未知参考 ID')
        if matches and row['correct'] is not True:
            raise ValueError('只有已确认正确的输出可以匹配参考')
        if row['correct'] is not None:
            reviewed += 1
            correct += row['correct']
        if row['citation_support'] is not None:
            citation_reviewed += 1
            supported += row['citation_support']
        indexed[row[key]] = row
    return indexed, {'outputs': len(rows), 'reviewed': reviewed, 'correct': correct,
                     'citation_reviewed': citation_reviewed, 'citation_supported': supported,
                     'pending': len(rows) - reviewed, 'pending_citations': len(rows) - citation_reviewed,
                     'precision_reviewed': correct / reviewed if reviewed else None,
                     'citation_support_reviewed': supported / citation_reviewed if citation_reviewed else None,
                     'complete': reviewed == len(rows) and citation_reviewed == len(rows)}


def score_worksheet_v2(worksheet):
    if not isinstance(worksheet, dict):
        raise ValueError('分轨评测表必须为 JSON 对象')
    if type(worksheet.get('schema_version')) is not int or worksheet['schema_version'] != 2:
        raise ValueError('分轨评测只接受 schema_version=2')
    if any(name not in worksheet for name in FIXED_FIELDS):
        raise ValueError('分轨评测缺少固定输入字段')
    if worksheet.get('content_digest') != digest({name: worksheet[name] for name in FIXED_FIELDS}):
        raise ValueError('原始预测、open_questions、参考答案或分类策略已改动')
    review_kind = worksheet.get('review_kind')
    if review_kind not in ('human', 'agent'):
        raise ValueError('review_kind 必须为 human 或 agent')
    items, questions, references = (worksheet[key] for key in ('items', 'open_questions', 'references'))
    if not all(isinstance(rows, list) for rows in (items, questions, references)):
        raise ValueError('items/open_questions/references 必须是数组')
    if any(not isinstance(ref, dict) or not isinstance(ref.get('text'), str) or not ref['text'].strip()
           for ref in references):
        raise ValueError('参考必须包含非空 text')
    for question in questions:
        if (not isinstance(question, dict) or not isinstance(question.get('text'), str)
                or not question['text'].strip() or not isinstance(question.get('evidence'), list)):
            raise ValueError('open_questions 缺少原始 text/evidence')
    kinds = validate_policy(worksheet['reference_policy'], references)
    item_index, decision_stats = _judgment_stats(worksheet.get('judgments'), 'item_id', identifiers(items, 'id'), set(kinds))
    question_ids = {f'Q{index:03d}' for index in range(1, len(questions) + 1)}
    question_index, uncertainty_stats = _judgment_stats(worksheet.get('open_question_judgments'), 'question_id',
                                                       question_ids, set(kinds))
    for row in question_index.values():
        if any(kinds[reference_id] != 'uncertainty' for reference_id in row['matches']):
            raise ValueError('open_questions 不能作为已生效决定匹配 decision 参考')
    reference_rows = worksheet.get('reference_judgments')
    if not isinstance(reference_rows, list) or identifiers(reference_rows, 'reference_id') != set(kinds):
        raise ValueError('reference_judgments 必须与所有参考逐条对应')
    counts = {kind: {'total': sum(value == kind for value in kinds.values()),
                     'full': 0, 'partial': 0, 'absent': 0, 'pending': 0} for kind in KINDS}
    for row in reference_rows:
        reference_id = row['reference_id']
        kind = kinds[reference_id]
        coverage = row.get('coverage')
        if 'coverage' not in row or coverage not in (None, 'full', 'partial', 'absent'):
            raise ValueError('coverage 只能为 full/partial/absent/null')
        links, notes = row.get('supported_by'), row.get('notes')
        if not isinstance(links, list) or not isinstance(notes, str):
            raise ValueError('参考判定需要 supported_by 数组和 notes')
        if coverage is not None and not notes.strip():
            raise ValueError('参考覆盖判定必须说明完整／部分／未覆盖的依据')
        if coverage in (None, 'absent') and links:
            raise ValueError('未审或未覆盖参考不能携带支持链接')
        if coverage in ('full', 'partial') and not links:
            raise ValueError('覆盖判定必须链接到已审原始输出')
        seen = set()
        for link in links:
            if not isinstance(link, dict) or set(link) - {'target', 'id', 'field'}:
                raise ValueError('支持链接格式无效')
            target, output_id, field = link.get('target'), link.get('id'), link.get('field')
            if target not in ('item', 'open_question') or not isinstance(output_id, str):
                raise ValueError('支持链接必须指向 item 或 open_question 的真实 ID')
            if target == 'item' and field not in ('conclusion', 'reason', 'boundary'):
                raise ValueError('item 支持链接必须指定 conclusion/reason/boundary 字段')
            if target == 'open_question' and field is not None:
                raise ValueError('open_question 不含 conclusion/reason/boundary 字段')
            if kind == 'decision' and target != 'item':
                raise ValueError('未决事项不能提升决策覆盖率')
            if kind == 'uncertainty' and target == 'item' and field == 'conclusion':
                raise ValueError('未知信息不能作为已生效结论计分；应检查未知字段或 open_questions')
            identity = (target, output_id, field)
            if identity in seen:
                raise ValueError('重复的参考支持链接')
            seen.add(identity)
            judgment = (item_index if target == 'item' else question_index).get(output_id)
            if (not judgment or judgment['correct'] is not True or judgment['citation_support'] is not True
                    or reference_id not in judgment['matches']):
                raise ValueError('参考支持必须来自正确、引用支持且明确匹配该参考的原始输出')
        counts[kind]['pending' if coverage is None else coverage] += 1
    for kind, values in counts.items():
        # No score until both output review and reference review are complete.
        output_complete = decision_stats['complete'] if kind == 'decision' else (
            decision_stats['complete'] and uncertainty_stats['complete'])
        values['complete'] = values['pending'] == 0 and output_complete
        values['coverage'] = values['full'] / values['total'] if values['total'] and values['complete'] else None
    complete = (decision_stats['complete'] and uncertainty_stats['complete']
                and all(value['complete'] for value in counts.values()))
    return {'schema_version': 2, 'metric_scope': f'local_{review_kind}_review_not_official_score',
            'decisions': decision_stats, 'uncertainties': uncertainty_stats, 'reference_coverage': counts,
            'complete': complete, 'human_review': 'pending' if review_kind == 'agent' or not complete else 'complete',
            'official_score': None}


def apply_review_v2(worksheet, *, judgments=None, open_question_judgments=None, reference_judgments=None):
    score_worksheet_v2(worksheet)
    reviewed = copy.deepcopy(worksheet)
    for key, value in (('judgments', judgments), ('open_question_judgments', open_question_judgments),
                       ('reference_judgments', reference_judgments)):
        if value is not None:
            reviewed[key] = copy.deepcopy(value)
    score_worksheet_v2(reviewed)
    return reviewed


def align_question_judgments(result, rows):
    """Import explicitly scored question text; unmatched questions stay unreviewed.

    Never fuzzy-match and never infer correctness from an omission summary or prose.
    """
    questions = result.get('open_questions', [])
    if not isinstance(questions, list) or not isinstance(rows, list):
        raise ValueError('未决事项评分必须是数组')
    assigned = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'text', 'correct', 'citation_support', 'matches', 'notes'}:
            raise ValueError('按文本导入的未决事项评分字段不完整')
        matches = [index for index, question in enumerate(questions, 1) if question['text'] == row['text']]
        if len(matches) != 1 or matches[0] in assigned:
            raise ValueError('未决事项原文匹配不唯一、重复或不存在')
        assigned[matches[0]] = {'question_id': f'Q{matches[0]:03d}',
                                **{key: copy.deepcopy(value) for key, value in row.items() if key != 'text'}}
    return [assigned.get(index, {'question_id': f'Q{index:03d}', 'correct': None, 'citation_support': None,
                                 'matches': [], 'notes': ''}) for index in range(1, len(questions) + 1)]
