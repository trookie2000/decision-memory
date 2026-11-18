"""Project evidenced decision transitions into current knowledge and an audit history.

The model supplies semantic relationships (topic, scope, and status). This module
checks their provenance and chronology; it does not infer semantic equivalence
from a filename, a source ID, or an uncited assertion.
"""

from .validation import validate_result


def _issue(decision):
    return decision['topic'].strip(), decision.get('scope', '未说明').strip()


def _primary(reference, sample):
    source = sample.catalog[reference['source_id']]
    return source.document_type == 'artifact' or (
        source.document_type == 'dialogue' and source.role == 'user')


def _later(new, old, sample):
    first, last = (sample.catalog[ref['source_id']] for ref in (old, new))
    if last.node is None or not _primary(new, sample):
        return False
    if first.document_type == 'supplement':
        # Lower-authority background has no inferred date. It cannot displace a
        # confirmed decision, but may itself be corrected by that decision.
        return True
    if first.node is None:
        return False
    if last.node != first.node:
        return last.node > first.node
    if last.file == first.file:
        if last.line != first.line:
            return last.line > first.line
        if first.text.count(old['quote']) == last.text.count(new['quote']) == 1:
            return last.text.index(new['quote']) > first.text.index(old['quote'])
        return False
    # A final artifact can confirm an agent's proposal within a stage. It does
    # not automatically overrule a user's decision in that same stage.
    return first.role == 'agent' and last.document_type == 'artifact'


def _covers(reference, retired):
    """Retire a quoted fragment, never an entire line containing several claims."""
    return (reference['source_id'] == retired['source_id']
            and reference['quote'] in retired['quote'])


def _all_covered(evidence, references):
    return bool(evidence) and all(any(_covers(ref, old) for old in references) for ref in evidence)


def _has_current_support(evidence, current):
    return any(ref['source_id'] == active['source_id'] and
               (ref['quote'] in active['quote'] or active['quote'] in ref['quote'])
               for ref in evidence for active in current)


def _unique(references):
    seen, result = set(), []
    for reference in references:
        key = reference['source_id'], reference['quote']
        if key not in seen:
            seen.add(key)
            result.append(reference)
    return result


def reconcile(result, decisions, sample):
    """Return current items, decision history, and a separate machine-review queue.

    Decisions must already have validated exact quotations. Reconciliation never
    calls a model or rewrites the meaning of a field. Different scopes are kept
    separate, and an unproven transition is queued rather than silently trusted.
    """
    active = [decision for decision in decisions if decision['status'] == 'active']
    current = [ref for decision in active for ref in decision['evidence'] if _primary(ref, sample)]
    retired, resolved, blocked, conflicts = [], [], [], []
    history, queue, notes = [], [], []
    for decision in decisions:
        if decision['status'] == 'conflict':
            conflicts.extend(decision['evidence'])
            queue.append({'kind': 'source_conflict', 'topic': decision['topic'],
                          'scope': decision.get('scope', '未说明'), 'text': decision['text'],
                          'evidence': decision['evidence'], 'reason': '原文存在尚未解决的冲突'})
            continue
        if decision['status'] not in ('superseded', 'pending'):
            continue
        replacements = [candidate for candidate in active if _issue(candidate) == _issue(decision)]
        evidence = _unique([ref for candidate in replacements for ref in candidate['evidence']]
                           + decision.get('resolution_evidence', []))
        superseded = [old for old in decision['evidence']
                      if any(_later(new, old, sample) for new in evidence)]
        if not superseded:
            if decision['status'] == 'superseded':
                queue.append({'kind': 'unverified_transition', 'topic': decision['topic'],
                              'scope': decision.get('scope', '未说明'), 'text': decision['text'],
                              'evidence': decision['evidence'],
                              'reason': '未找到同议题、同适用范围内有明确依据的后续决定'})
                if any(_primary(ref, sample) for ref in decision['evidence']):
                    blocked.extend(ref for candidate in replacements for ref in candidate['evidence']
                                   if sample.catalog[ref['source_id']].document_type == 'supplement')
            continue
        # An active decision may reuse the old passage to explain a change. Its
        # newly confirmed evidence keeps the resulting item current.
        target = retired if decision['status'] == 'superseded' else resolved
        target.extend(superseded)
        resolutions = _unique([new for new in evidence
                               if any(_later(new, old, sample) for old in superseded)])
        history.append({'id': f'H{len(history) + 1:03d}', 'topic': decision['topic'],
                        'scope': decision.get('scope', '未说明'),
                        'status': 'superseded' if decision['status'] == 'superseded' else 'resolved',
                        'text': decision['text'], 'evidence': superseded,
                        'resolution_evidence': resolutions,
                        'reason': decision.get('reason', {'text': '未说明', 'evidence': []}),
                        'source_nodes': sorted({sample.catalog[ref['source_id']].node for ref in superseded
                                                if sample.catalog[ref['source_id']].node is not None}),
                        'resolved_at_node': min(sample.catalog[ref['source_id']].node for ref in resolutions),
                        'previously_confirmed': True if any(_primary(ref, sample) for ref in superseded) else None})
    kept = []
    for item in result['items']:
        evidence = item['conclusion']['evidence']
        if _all_covered(evidence, retired) and not _has_current_support(evidence, current):
            notes.append(f'{item["id"]} 结论仅引用已有后续明确决定的失效片段，已剔除：{item["conclusion"]["text"]}')
        elif _all_covered(evidence, blocked + conflicts):
            queue.append({'kind': 'candidate_requires_review', 'item': item,
                          'reason': '结论仅依赖未解决的冲突或试图覆盖明确决定的补充材料'})
            notes.append(f'{item["id"]} 已移入程序核查队列，未作为业务未决事项输出')
        else:
            kept.append(item)
    questions = []
    for question in result['open_questions']:
        if _all_covered(question['evidence'], resolved + retired):
            notes.append('移除已在后续节点解决的待确认事项：' + question['text'])
        else:
            questions.append(question)
    normalized = validate_result({'items': [{key: value for key, value in item.items() if key != 'id'}
                                            for item in kept], 'open_questions': questions}, sample)
    return {**result, **normalized, 'decision_history': history, 'review_queue': queue}, notes
