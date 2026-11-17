"""Bounded source retrieval and necessary literal bindings, never semantic certification."""
import math
import re
from collections import Counter

from .evaluation import digest
from .evidence_ids import reference_catalog

SYMBOLS = re.compile(
    r'(?<![A-Za-z0-9_])(?:@[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+'
    r'|/?[A-Za-z_][A-Za-z0-9_-]*(?:/[A-Za-z0-9_.-]+)+'
    r'|[A-Za-z_][A-Za-z0-9_]*(?:(?:\.|::|#)[A-Za-z_][A-Za-z0-9_]*)+'
    r'|(?:[A-Z][a-z0-9]+|[a-z][a-z0-9]*)(?:[A-Z][A-Za-z0-9]*)+'
    r'|[A-Z]{2,}[a-z][A-Za-z0-9]*)(?![A-Za-z0-9_])')
GENERIC = frozenset(('HTTP', 'HTTPS', 'JSON', 'API', 'CLI', 'SDK', 'UI', 'ID', 'SQL', 'HTML', 'CSS', 'JS', 'TS'))
CHINESE = re.compile(r'[\u3400-\u9fff]+')
LITERAL_POLICY = {'policy': 'source-symbols-v1', 'symbols': SYMBOLS.pattern, 'generic': sorted(GENERIC),
                  'matching': 'complete-identifier-or-qualified-path-v1'}


def symbols(text):
    return sorted({match.group() for match in SYMBOLS.finditer(text)
                   if not all(part in GENERIC for part in match.group().split('/'))})


def _present(symbol, quote):
    # Do not bind fooBar to fooBarLegacy, or a package/file to a longer path.
    # A sentence-final period is punctuation; .member/.extension is a continuation.
    left = r'[A-Za-z0-9_$@/.-]' if '/' in symbol else r'[A-Za-z0-9_$]'
    right = r'[A-Za-z0-9_$@/-]|\.[A-Za-z0-9_]' if '/' in symbol else r'[A-Za-z0-9_$]'
    if '.' in symbol or '::' in symbol or '#' in symbol:
        right = '(?:' + right + r')|(?:\.|::|#)[A-Za-z_]'
    return re.search(r'(?<!' + left + ')' + re.escape(symbol) + r'(?!(?:' + right + '))', quote) is not None


def _missing(field):
    return [symbol for symbol in symbols(field['text'])
            if not any(_present(symbol, ref['quote']) for ref in field['evidence'])]


def validate_literal_grounding(compiled):
    rows, issues = [], []
    for index, identity in enumerate(compiled['memory_index']):
        key = identity['knowledge_key']
        for name in ('conclusion', 'reason', 'boundary'):
            field = compiled['items'][index][name]
            bindings = [{'symbol': symbol, 'source_ids': sorted({ref['source_id'] for ref in field['evidence']
                                                               if _present(symbol, ref['quote'])})}
                        for symbol in symbols(field['text'])]
            missing = [row['symbol'] for row in bindings if not row['source_ids']]
            if missing:
                issues.append(key + '.' + name + ': ' + ', '.join(missing[:12]))
            rows.append({'knowledge_key': key, 'field': name, 'symbols': bindings})
    if issues:
        raise ValueError('字段自身引文未包含实现标识符；请引用实际原文，不跨字段借证：' + '；'.join(issues[:20]))
    return {'policy': LITERAL_POLICY['policy'], 'policy_digest': digest(LITERAL_POLICY),
            'input_digest': digest({key: compiled[key] for key in ('items', 'memory_index')}), 'fields': rows}


def _bigrams(text):
    return {part[index:index + 2] for part in CHINESE.findall(text) for index in range(len(part) - 1)}


def field_evidence_candidates(compiled, sample, limit=6):
    from .requirement_memory import current_order
    if type(limit) is not int or not 1 <= limit <= 12:
        raise ValueError('字段建议上限须为1至12')
    catalog = reference_catalog(sample)
    indexed = [(key, ref, _bigrams(ref['quote'])) for key, ref in catalog.items()]
    frequencies = Counter(word for _, _, words in indexed for word in words)
    result = []
    for index, identity in enumerate(compiled['memory_index']):
        order = current_order(compiled, sample, identity['knowledge_key'])
        for name in ('conclusion', 'reason', 'boundary'):
            field = compiled['items'][index][name]
            if field['text'] == '未说明':
                continue
            missing, words = _missing(field), _bigrams(field['text'])
            current = {(ref['source_id'], ref['quote']) for ref in field['evidence']}
            groups = {(sample.catalog[ref['source_id']].file, sample.catalog[ref['source_id']].message)
                      for ref in field['evidence'] if sample.catalog[ref['source_id']].message is not None}
            ranked = []
            for reference_id, ref, terms in indexed:
                source = sample.catalog[ref['source_id']]
                if not sample.available_at(ref['source_id'], order) or (ref['source_id'], ref['quote']) in current:
                    continue
                hits = sum(_present(symbol, ref['quote']) for symbol in missing)
                overlap = sum(math.log1p(len(indexed) / (1 + frequencies[word])) for word in words & terms)
                same_message = source.message is not None and (source.file, source.message) in groups
                score = 20 * hits + overlap / math.sqrt(max(1, len(terms)))
                if same_message and (hits or overlap):
                    score += 2
                if score > 0:
                    ranked.append((-score, len(ref['quote']), reference_id, ref, source))
            chosen, per_source = [], Counter()
            for _, _, reference_id, ref, source in sorted(ranked, key=lambda row: row[:3]):
                if per_source[ref['source_id']] >= 2:
                    continue
                chosen.append({'reference_id': reference_id, 'source_id': ref['source_id'],
                               'role': source.role, 'text': ref['quote']})
                per_source[ref['source_id']] += 1
                if len(chosen) == limit:
                    break
            result.append({'knowledge_key': identity['knowledge_key'], 'field': name,
                           'current_sources': [ref['source_id'] for ref in field['evidence']],
                           'missing_symbols': missing, 'suggested_sources': chosen})
    return result
