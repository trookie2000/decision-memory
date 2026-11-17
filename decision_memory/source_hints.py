"""Bounded, deterministic review hints over supplied sources, never gold."""
import math
import re
from collections import Counter

from .field_grounding import _present, symbols

HINT_POLICY = {'policy': 'field-source-hints-v1', 'per_field': 4, 'artifact_candidates': 64,
               'ranking': 'rare-bigrams-identifiers-adjacent-context-v1', 'semantic_certification': False}
WORDS = re.compile(r'[A-Za-z][A-Za-z0-9_]*|[0-9]+(?:\.[0-9]+)?|[\u3400-\u9fff]+')
SALIENT = re.compile(r'因为|因此|避免|原因|前提|仅|不得|必须|上限|限制|保持|退化|失败|超时|兼容|不做|不改|保留|尚未|待确认')


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
    for index, item in enumerate(result['items'], 1):
        for name in ('conclusion', 'reason', 'boundary'):
            field = item[name]
            current = {ref['source_id'] for ref in field['evidence']}
            cited.update(current)
            if field['text'] == '未说明':
                continue
            wanted = terms(field['text'])
            missing = [s for s in symbols(field['text']) if not any(_present(s, ref['quote']) for ref in field['evidence'])]
            available = {s: [key for key, source, _ in indexed if _present(s, source.text)] for s in missing}
            current_sources = [sample.catalog[key] for key in current]
            ranked = []
            for key, source, words in indexed:
                if key in current:
                    continue
                hits = sum(_present(s, source.text) for s in missing)
                overlap = sum(math.log1p(len(indexed) / (1 + frequency[t])) for t in wanted & words)
                adjacent = any(source.file == old.file and 0 < abs(source.line - old.line) <= 2 for old in current_sources)
                same_message = any(source.file == old.file and source.message is not None and source.message == old.message
                                   for old in current_sources)
                score = hits * 30 + overlap / math.sqrt(max(1, len(words))) + (2 if adjacent else 0) + (1 if same_message and overlap else 0)
                if score > 0:
                    ranked.append((-score, key))
            fields.append({'item_index': index, 'field': name, 'missing_symbols': missing,
                           'absent_from_entire_input': [s for s, ids in available.items() if not ids],
                           'candidate_source_ids': [key for _, key in sorted(ranked)[:HINT_POLICY['per_field']]]})
    for question in result['open_questions']:
        cited.update(ref['source_id'] for ref in question['evidence'])
    salient = [key for key, source, _ in indexed if key.startswith('A') and key not in cited and SALIENT.search(source.text)]
    return {'policy': HINT_POLICY['policy'], 'semantic_certification': False, 'fields': fields,
            'unreferenced_artifact_candidates': salient[:HINT_POLICY['artifact_candidates']],
            'unreferenced_salient_total': len(salient), 'full_source_count': len(indexed)}
