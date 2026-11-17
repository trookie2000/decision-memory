"""Bounded, deterministic review hints over supplied sources, never gold."""
import math
import re
from collections import Counter

from .field_grounding import _present, symbols
from .grounded_atoms import missing_atoms, present

HINT_POLICY = {'policy': 'field-source-hints-v2', 'per_field': 6, 'artifact_candidates': 64,
               'ranking': 'rare-bigrams-identifiers-atoms-unstated-finality-v2', 'later_per_field': 3,
               'semantic_certification': False}
WORDS = re.compile(r'[A-Za-z][A-Za-z0-9_]*|[0-9]+(?:\.[0-9]+)?|[\u3400-\u9fff]+')
SALIENT = re.compile(r'因为|因此|避免|原因|前提|仅|不得|必须|上限|限制|保持|退化|失败|超时|兼容|不做|不改|保留|尚未|待确认')
REASON = re.compile(r'因为|因此|避免|原因|理由|目的|用途|为了|防止|便于|用于|成本|风险|导致')
BOUNDARY = re.compile(r'前提|仅|只|范围|不得|必须|上限|限制|不适用|本期|例外|之前|之后|尚未|待确认')
FINALITY = re.compile(r'可选|不用|不必|无需|必须|不得|取消|改为|不再|确认|待确认|尚未|本期|最终|仅|只')


def terms(text):
    result = set()
    for match in WORDS.finditer(text):
        word = match.group()
        if '\u3400' <= word[0] <= '\u9fff':
            result.update(word[i:i + 2] for i in range(len(word) - 1))
        else:
            result.add(word.lower())
    return result


def build_review_hints(result, sample):
    indexed = [(key, source, terms(source.text)) for key, source in sample.catalog.items()]
    frequency = Counter(term for _, _, words in indexed for term in words)
    fields, cited = [], set()
    positions = {key: index for index, (key, _, _) in enumerate(indexed)}
    for index, item in enumerate(result['items'], 1):
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
            ranked = []
            for key, source, words in indexed:
                if key in current:
                    continue
                hits = sum(_present(s, source.text) for s in missing) + sum(present(atom, source.text) for atom in absent_atoms)
                overlap = sum(math.log1p(len(indexed) / (1 + frequency[t])) for t in wanted & words)
                adjacent = any(source.file == old.file and 0 < abs(source.line - old.line) <= 2 for old in current_sources)
                same_message = any(source.file == old.file and source.message is not None and source.message == old.message
                                   for old in current_sources)
                focus = REASON if name == 'reason' else BOUNDARY if name == 'boundary' else SALIENT
                boost = 3 if overlap and focus.search(source.text) else 0
                score = hits * 30 + overlap / math.sqrt(max(1, len(words))) + boost + (2 if adjacent else 0) + (1 if same_message and overlap else 0)
                if score > 0:
                    ranked.append((-score, key))
            anchors = current or {ref['source_id'] for ref in item['conclusion']['evidence']}
            earliest = min((positions[key] for key in anchors), default=-1)
            later = [(score, -positions[key], key) for score, key in ranked
                     if positions[key] > earliest and FINALITY.search(sample.catalog[key].text)]
            fields.append({'item_index': index, 'field': name, 'missing_symbols': missing,
                           'missing_atoms': [{'text': atom['text'], 'candidate_source_ids': [key for key, source, _ in indexed if present(atom, source.text)][:6]}
                                             for atom in absent_atoms],
                           'unstated_requires_check': unstated,
                           'later_related_source_ids': [key for _, _, key in sorted(later)[:HINT_POLICY['later_per_field']]],
                           'absent_from_entire_input': [s for s, ids in available.items() if not ids],
                           'candidate_source_ids': [key for _, key in sorted(ranked)[:HINT_POLICY['per_field']]]})
    questions = []
    for index, question in enumerate(result['open_questions'], 1):
        cited.update(ref['source_id'] for ref in question['evidence'])
        wanted = terms(question['text'])
        earliest = min((positions[ref['source_id']] for ref in question['evidence']), default=-1)
        ranked = []
        for key, source, words in indexed:
            overlap = sum(math.log1p(len(indexed) / (1 + frequency[t])) for t in wanted & words)
            if overlap and positions[key] > earliest:
                ranked.append((-overlap / math.sqrt(max(1, len(words))), -positions[key], key))
        questions.append({'question_index': index,
                          'later_related_source_ids': [key for _, _, key in sorted(ranked)[:HINT_POLICY['per_field']]]})
    salient = [key for key, source, _ in indexed if key.startswith('A') and key not in cited and SALIENT.search(source.text)]
    return {'policy': HINT_POLICY['policy'], 'semantic_certification': False, 'fields': fields, 'questions': questions,
            'unreferenced_artifact_candidates': salient[:HINT_POLICY['artifact_candidates']],
            'unreferenced_salient_total': len(salient), 'full_source_count': len(indexed)}
