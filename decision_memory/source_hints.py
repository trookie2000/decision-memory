"""Bounded, deterministic review hints over supplied sources, never gold."""
import math
import re
from collections import Counter
from itertools import islice

from .field_grounding import _present, symbols
from .grounded_atoms import missing_atoms, present
from .scope_review import COMMON_TERMS, CONDITION_CHECK_POLICY, SCOPE_REVIEW_POLICY, build_scope_review
from .sources import model_speakers

HINT_POLICY = {'policy': 'field-source-hints-v9', 'per_field': 6, 'artifact_candidates': 64,
               'ranking': 'nearby-turns-and-independent-finality-v4', 'later_per_field': 3,
               'linked_artifacts_per_field': 2, 'condition_checks': CONDITION_CHECK_POLICY,
               'semantic_certification': False}
REPAIR_IGNORED = COMMON_TERMS | {'after', 'before', 'for', 'within', 'of', 'in', 'at', 'hour', 'hours', 'minute', 'minutes',
                                 'day', 'days', 'calendar', 'elapsed', 'working', 'business', 'workday', 'workdays'}
LITERAL_REPAIR_POLICY = {'version': 'same-field-literal-repair-v2', 'fields': 6, 'values': 3, 'sources': 3,
                         'searched_sources_per_field': 64, 'minimum_topic_overlap': 2,
                         'ignored_terms': sorted(REPAIR_IGNORED), 'ranking': 'top-nonnumeric-topic-overlap',
                         'semantic_certification': False}
WORDS = re.compile(r'[A-Za-z][A-Za-z0-9_]*|[0-9]+(?:\.[0-9]+)?|[\u3400-\u9fff]+')
HAN = re.compile(r'[\u3400-\u9fff]')
SALIENT = re.compile(r'因为|因此|避免|原因|前提|仅|不得|必须|上限|限制|保持|退化|失败|超时|兼容|不做|不改|保留|尚未|待确认')
REASON = re.compile(r'因为|因此|避免|原因|理由|目的|用途|为了|防止|便于|用于|成本|风险|导致')
BOUNDARY = re.compile(r'前提|仅|只|范围|不得|必须|上限|限制|不适用|本期|例外|之前|之后|期间|生效|有效期|截止|尚未|待确认')
FINALITY = re.compile(r'可选|不用|不必|无需|必须|不得|取消|改为|不再|确认|待确认|尚未|本期|最终|仅|只')
REVISION = re.compile(r'取消|改为|不再|推翻|废止|替换|撤回|否决')
HUMAN_ROLES = {'user', 'participant'}
WITNESS_POLICY = {'version': 'same-item-field-witness-v1', 'max_fields': 24, 'max_sources': 3,
                  'min_new_terms': 2, 'semantic_certification': False}
WITNESS_SIGNALS = {
    'reason': re.compile(r'因为|由于|原因是|理由是|目的是|为了|避免|以免|\b(?:because|so that|to avoid|to prevent)\b', re.I),
    'boundary': re.compile(BOUNDARY.pattern + r'|\b(?:only|unless|except|provided that|if|until)\b', re.I),
}


def terms(text):
    result = set()
    for match in WORDS.finditer(text):
        word = match.group()
        if '\u3400' <= word[0] <= '\u9fff':
            result.update(word[i:i + 2] for i in range(len(word) - 1))
        else:
            result.add(word.lower())
    return result


def _repair_terms(text):
    return {word for word in terms(text) - REPAIR_IGNORED if not word[0].isdecimal()}


def field_witnesses(draft, sample):
    """Flag source candidates from other fields in the same item."""
    names = ('conclusion', 'reason', 'boundary')
    speakers = model_speakers(sample)
    hints = []
    for index, item in enumerate(draft['items'], 1):
        declared = {name: {ref['source_id'] for ref in item[name]['evidence']} for name in names}
        all_ids = set().union(*declared.values())
        eligible = {sid: sample.catalog[sid].text for sid in sorted(all_ids)
                    if sample.catalog[sid].document_type == 'artifact'
                    or (sample.catalog[sid].document_type == 'dialogue' and speakers[sid][0] in HUMAN_ROLES)}
        for name in names:
            field = item[name]
            own_words = set().union(*(_repair_terms(ref['quote']) for ref in field['evidence']))
            missing = _repair_terms(field['text']) - own_words
            unstated = field['text'] == '未说明'
            ranked = []
            for sid, text in eligible.items():
                if sid in declared[name]:
                    continue
                if unstated:
                    signal = WITNESS_SIGNALS.get(name)
                    if signal and signal.search(text):
                        ranked.append((-len(_repair_terms(item['conclusion']['text']) & _repair_terms(text)), sid))
                else:
                    shared = len(missing & _repair_terms(text))
                    if shared >= WITNESS_POLICY['min_new_terms']:
                        ranked.append((-shared, sid))
            if ranked:
                hints.append({'item_index': index, 'field': name,
                              'check': 'unstated' if unstated else 'own_citation',
                              'candidate_source_ids': [sid for _, sid in sorted(ranked)[:WITNESS_POLICY['max_sources']]]})
                if len(hints) == WITNESS_POLICY['max_fields']:
                    return hints
    return hints


def literal_repair_hints(gaps, sample):
    words = {sid: _repair_terms(source.text) for sid, source in sample.catalog.items()}
    hints = []
    for gap in gaps[:LITERAL_REPAIR_POLICY['fields']]:
        field = gap['value']
        cited = {ref['source_id'] for ref in field['evidence']}
        topic = _repair_terms(field['text']) | set().union(*(words[sid] for sid in cited))
        overlaps = ((len(topic & vocabulary), sid) for sid, vocabulary in words.items() if sid not in cited)
        ranked = sorted((-score, sid) for score, sid in overlaps if score >= LITERAL_REPAIR_POLICY['minimum_topic_overlap'])
        candidates = ranked[:LITERAL_REPAIR_POLICY['searched_sources_per_field']]
        missing = []
        for atom in gap['atoms'][:LITERAL_REPAIR_POLICY['values']]:
            matching = ((score, sid) for score, sid in candidates if present(atom, sample.catalog[sid].text))
            if matches := list(islice(matching, LITERAL_REPAIR_POLICY['sources'])):
                locations = [sid for score, sid in matches if score == matches[0][0]]
                missing.append({'text': atom['text'], 'candidate_source_ids': locations})
        if missing:
            hints.append({'item_id': gap['item_id'], 'field': gap['field'], 'missing': missing})
    return hints


def _linked_artifacts(text, current_sources, current, item_sources, indexed, frequency):
    if not current_sources or not HAN.search(text) or any(HAN.search(source.text) for source in current_sources):
        return []
    original_terms = set().union(*(terms(source.text) for source in current_sources))
    rare = {term for term in original_terms if frequency[term] <= max(5, len(indexed) // 4)}
    ranked = []
    for key, source, words in indexed:
        if key in current or source.document_type != 'artifact' or source.text.lstrip().startswith('#'):
            continue
        same_item = key in item_sources
        nearby = any(source.file == anchor.file and source.node == anchor.node
                     and 0 < abs(source.line - anchor.line) <= 4 for anchor in current_sources)
        shared = sorted(rare & words)
        if not (same_item or nearby or len(shared) >= 2):
            continue
        weight = sum(math.log1p(len(indexed) / frequency[term]) for term in shared)
        score = weight / math.sqrt(max(1, len(words))) + 6 * same_item + 2 * nearby
        ranked.append((-score, key))
    return [key for _, key in sorted(ranked)[:HINT_POLICY['linked_artifacts_per_field']]]


def build_review_hints(result, sample):
    indexed = [(key, source, terms(source.text)) for key, source in sample.catalog.items()]
    speakers = model_speakers(sample)
    frequency = Counter(term for _, _, words in indexed for term in words)
    fields, cited = [], set()
    positions = {key: index for index, (key, source, _) in enumerate(indexed)
                 if source.document_type != 'supplement'}
    for index, item in enumerate(result['items'], 1):
        item_sources = {ref['source_id'] for field in ('conclusion', 'reason', 'boundary')
                        for ref in item[field]['evidence']}
        turns = {}
        for key in item_sources:
            role, message = speakers[key]
            source = sample.catalog[key]
            if role in HUMAN_ROLES and message is not None:
                turns.setdefault((source.file, source.node, message), []).append(source.line)
        for name in ('conclusion', 'reason', 'boundary'):
            field = item[name]
            current = {ref['source_id'] for ref in field['evidence']}
            cited.update(current)
            unstated = field['text'] == '未说明'
            wanted = terms(item['conclusion']['text'] if unstated else field['text'])
            missing = [s for s in symbols(field['text']) if not any(_present(s, ref['quote']) for ref in field['evidence'])]
            absent_atoms = missing_atoms(field)
            available = {s: [key for key, source, _ in indexed if _present(s, source.text)] for s in missing}
            current_sources = [sample.catalog[key] for key in current]
            ranked, later = [], []
            earliest = min((positions[key] for key in current or item_sources if key in positions), default=None)
            focus = REASON if name == 'reason' else BOUNDARY if name == 'boundary' else SALIENT
            for key, source, words in indexed:
                if key in current:
                    continue
                hits = sum(_present(s, source.text) for s in missing) + sum(present(atom, source.text) for atom in absent_atoms)
                overlap = sum(math.log1p(len(indexed) / (1 + frequency[t])) for t in sorted(wanted & words))
                adjacent = any(source.file == old.file and 0 < abs(source.line - old.line) <= 2 for old in current_sources)
                same_message = any(source.file == old.file and source.message is not None and source.message == old.message
                                   for old in current_sources)
                field_signal = focus.search(source.text)
                boost = 3 if overlap and field_signal else 0
                score = hits * 30 + overlap / math.sqrt(max(1, len(words))) + boost + (2 if adjacent else 0) + (1 if same_message and overlap else 0)
                if (score > 0 and earliest is not None and key in positions and positions[key] > earliest
                        and FINALITY.search(source.text)):
                    later.append((not REVISION.search(source.text), -score, -positions[key], key))
                role, message = speakers[key]
                turn_lines = turns.get((source.file, source.node, message), ()) if role in HUMAN_ROLES else ()
                if name != 'conclusion':
                    if field_signal and (turn_lines or (source.document_type == 'artifact' and key in item_sources)):
                        score += 12
                    elif any(abs(source.line - number) <= 2 for number in turn_lines):
                        score += 2
                if score > 0:
                    ranked.append((-score, key))
            hint = {'item_index': index, 'field': name, 'missing_symbols': missing,
                    'missing_atoms': [{'text': atom['text'], 'candidate_source_ids': [key for key, source, _ in indexed if present(atom, source.text)][:6]}
                                      for atom in absent_atoms],
                    'unstated_requires_check': unstated,
                    'later_related_source_ids': [key for _, _, _, key in sorted(later)[:HINT_POLICY['later_per_field']]],
                    'absent_from_entire_input': [s for s, ids in available.items() if not ids],
                    'candidate_source_ids': [key for _, key in sorted(ranked)[:HINT_POLICY['per_field']]]}
            linked = _linked_artifacts(field['text'], current_sources, current, item_sources, indexed, frequency)
            if linked:
                hint['linked_artifact_source_ids'] = linked
            fields.append(hint)
    questions = []
    for index, question in enumerate(result['open_questions'], 1):
        cited.update(ref['source_id'] for ref in question['evidence'])
        wanted = terms(question['text'])
        earliest = min((positions[ref['source_id']] for ref in question['evidence']
                        if ref['source_id'] in positions), default=None)
        ranked = []
        for key, source, words in indexed:
            overlap = sum(math.log1p(len(indexed) / (1 + frequency[t])) for t in sorted(wanted & words))
            if overlap and earliest is not None and key in positions and positions[key] > earliest:
                ranked.append((-overlap / math.sqrt(max(1, len(words))), -positions[key], key))
        questions.append({'question_index': index,
                          'later_related_source_ids': [key for _, _, key in sorted(ranked)[:HINT_POLICY['per_field']]]})
    salient = [key for key, source, _ in indexed if source.document_type == 'artifact'
               and key not in cited and SALIENT.search(source.text)]
    hints = {'policy': HINT_POLICY['policy'], 'semantic_certification': False, 'fields': fields, 'questions': questions,
             'unreferenced_artifact_candidates': salient[:HINT_POLICY['artifact_candidates']],
             'unreferenced_salient_total': len(salient), 'full_source_count': len(indexed)}
    scope = build_scope_review(result, sample, speakers, {key: words for key, _, words in indexed}, frequency, terms)
    if 'condition_checks' in scope:
        hints['condition_checks'] = scope['condition_checks']
    if any(scope.values()):
        hints['scope_review'] = {'policy': SCOPE_REVIEW_POLICY['version'], **scope}
    return hints
