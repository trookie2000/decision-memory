"""Bounded exact-text source supplements, explicitly not semantic certificates."""
import copy
import math
import re
from collections import Counter

CHINESE = re.compile(r'[\u3400-\u9fff]+')
RETRIEVAL_POLICY = {'policy': 'rare-exact-trigram-supplement-v1', 'anchor_length': 3,
    'minimum_new_anchors': 2, 'maximum_extra_sources': 2, 'maximum_total_sources': 16,
    'maximum_document_frequency': 'max(2,floor(source_count/20))', 'roles': ['user', 'artifact'],
    'recorded_anchors': 32, 'semantic_certification': False,
    'ranking': 'sum-in-sorted-anchor-order(log1p(N/df))/sqrt(max(1,line_characters/120));user;latest;source_id'}


def anchors(text):
    return {part[i:i + 3] for part in CHINESE.findall(text) for i in range(len(part) - 2)}


class RetrievalIndex:
    def __init__(self, catalog):
        self.rows = [(key, row, anchors(row['text']), position)
                     for position, (key, row) in enumerate(catalog.items())]
        self.frequency = Counter(anchor for _, _, terms, _ in self.rows for anchor in terms)
        self.ceiling = max(2, len(self.rows) // 20)

    def select(self, field):
        evidence = field['evidence']
        if field['text'] == '未说明' or not evidence or len(evidence) >= RETRIEVAL_POLICY['maximum_total_sources']:
            return []
        covered = set().union(*(anchors(ref['quote']) for ref in evidence))
        wanted = {a for a in anchors(field['text']) if 0 < self.frequency[a] <= self.ceiling} - covered
        used, selected = {ref['source_id'] for ref in evidence}, []
        for _ in range(min(RETRIEVAL_POLICY['maximum_extra_sources'], RETRIEVAL_POLICY['maximum_total_sources'] - len(evidence))):
            ranked = []
            for key, source, terms, position in self.rows:
                hits = wanted & terms
                if (key in used or source.get('role') not in RETRIEVAL_POLICY['roles']
                        or source.get('document_type') == 'supplement'
                        or len(hits) < RETRIEVAL_POLICY['minimum_new_anchors']):
                    continue
                score = sum(math.log1p(len(self.rows) / self.frequency[a]) for a in sorted(hits)) / math.sqrt(max(1, len(source['text']) / 120))
                ranked.append((-score, -(source['role'] == 'user'), -position, key, hits, terms))
            if not ranked:
                break
            _, _, _, key, hits, terms = min(ranked, key=lambda row: row[:4])
            selected.append({'source_id': key, 'anchors': sorted(hits, key=lambda a: (self.frequency[a], a))[:RETRIEVAL_POLICY['recorded_anchors']]})
            wanted -= terms
            used.add(key)
        return selected


def augment_evidence(result, sample, *, max_bytes=8_000_000):
    """Only add sources to stated item fields; leave input and unknowns intact."""
    from .validation import validate_evidence
    catalog = {key: {'text': s.text, 'role': s.role, 'document_type': s.document_type}
               for key, s in sample.catalog.items()}
    index, updated = RetrievalIndex(catalog), copy.deepcopy(result)
    for item in updated['items']:
        for name in ('conclusion', 'reason', 'boundary'):
            field = item[name]
            additions = index.select(field)
            if not additions:
                continue
            declared = [r['source_id'] for r in field['evidence']]
            extra = validate_evidence([{'source_id': row['source_id'], 'quote': catalog[row['source_id']]['text']}
                                       for row in additions], sample, '程序检索补充')
            field['evidence'].extend(extra)
            field['evidence_retrieval'] = {'policy': RETRIEVAL_POLICY['policy'], 'declared_source_ids': declared,
                'added_source_ids': [r['source_id'] for r in extra], 'matches': additions}
    fields = [i[name] for i in updated['items'] for name in ('conclusion', 'reason', 'boundary')] + updated['open_questions']
    if sum(len(ref['quote'].encode('utf-8')) for field in fields for ref in field['evidence']) > max_bytes:
        raise ValueError('完整整行证据含程序补充超过8 MB，不得截断业务内容或原始来源')
    return updated


def without_retrieval(field):
    """Undo only the recorded program additions before exact saved-result replay."""
    record = field.get('evidence_retrieval')
    if record is None:
        return field
    if (not isinstance(record, dict) or set(record) != {'policy', 'declared_source_ids', 'added_source_ids', 'matches'}
            or record['policy'] != RETRIEVAL_POLICY['policy']):
        raise ValueError('程序补充来源记录无效')
    declared, added, matches = record['declared_source_ids'], record['added_source_ids'], record['matches']
    actual = [r['source_id'] for r in field['evidence']]
    if (not isinstance(declared, list) or not declared or not isinstance(added, list) or not 1 <= len(added) <= 2
            or any(not isinstance(key, str) for key in declared + added) or declared + added != actual
            or len(actual) > 16 or len(set(actual)) != len(actual) or not isinstance(matches, list) or len(matches) != len(added)):
        raise ValueError('程序补充来源与字段引文不一致')
    for key, match, ref in zip(added, matches, field['evidence'][len(declared):]):
        if (not isinstance(match, dict) or set(match) != {'source_id', 'anchors'} or match['source_id'] != key
                or not isinstance(match['anchors'], list) or not 2 <= len(match['anchors']) <= 32
                or any(not isinstance(a, str) or len(a) != 3 or not CHINESE.fullmatch(a)
                       or a not in field['text'] or a not in ref['quote'] for a in match['anchors'])):
            raise ValueError('程序补充来源的精确文字片段无效')
    original = {**field, 'evidence': field['evidence'][:len(declared)]}
    original.pop('evidence_retrieval')
    return original
