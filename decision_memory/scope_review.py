"""Point reviewers to exact scope wording in the supplied material."""
import re
from itertools import islice

from .context_sources import FENCE
from .grounded_atoms import TEMPORAL_KEYS, atoms
from .input_contract import describe_input


SCOPE_REVIEW_POLICY = {'version': 'source-scope-markers-v1', 'markers': 48, 'markers_per_source': 5,
                       'quantifier_checks': 24, 'retained_rules': 16, 'retention_sources': 2,
                       'earlier_sources': 3}
CONDITION_CHECK_POLICY = {'version': 'focused-source-conditions-v3', 'deadline_clauses': 12, 'cutoff_boundaries': 8,
                          'scope_overclaims': 10, 'one_off_items': 10,
                          'mixed_decision_sources': 10, 'revision_reasons': 10}
MARKER = re.compile(
    r'(?P<exception>除非|除[^，,。；!?;\n]{1,32}?外|例外|\b(?:unless|except(?: for)?)\b)'
    r'|(?P<condition>如果|若|仅当|只有|前提(?:是|为)|当(?!前|下|地|时)[^，,。；!?;\n]{1,24}?时'
    r'|\b(?:only if|if|when|whenever|provided that|as long as)\b)'
    r'|(?P<retention>沿用|仍(?:须|需|应|沿用|保留|有效|适用)|继续(?:使用|执行|保留)|其余不变'
    r'|\b(?:keep|retains?|retained|still (?:applies|apply|must|use|uses)|unchanged)\b)'
    r'|(?P<example>例如|比如|本次|此次|这次|本轮|\b(?:for example|such as|in this case|for this (?:pilot|run))\b)'
    r'|(?P<additive>(?:也|还)(?:用|可|有|会|能|是|用于|适用|支持)|\b(?:also|as well)\b)'
    r'|(?P<exclusive>(?<!不)仅(?:限|在|对|供|用|适用|代表|表示|允许|支持|有|可)|(?<!不)只(?:能|有|允许|限|在|对|用|'
    r'适用|代表|表示|表明|是)|唯一|排他|(?<!not )\b(?:only|exclusively|solely)\b)'
    r'|(?P<universal>所有|一律|无论|任意|全部|每(?:个|次|笔|条|批|种|份)|\b(?:all|every|each|any|regardless)\b)',
    re.I,
)
CLAUSE_BREAK = re.compile(r'[。！？；;!?\r\n]|\.(?=\s|$)')
DECISION_BREAK = re.compile(CLAUSE_BREAK.pattern + r'|[，,]')
DEADLINE_EVENT = re.compile(
    r'提前|之前|之后|以前|以后|截止|截至|(?:工作日|自然日|小时|分钟|天|周)\s*[前后](?!端|台)'
    r'|[前后](?=[ ，,]*(?:需|应|须|必须|留出|预留|至少|最多|不少于|不超过|恰好|\d|[一二两三四五六七八九十]))'
    r'|\b(?:before|after|within|prior to|following|from the time)\b', re.I)
COMPARISON = re.compile(
    r'至少|至多|不少于|不低于|不超过|最多|恰好|\b(?:at least|at most|no less than|no more than|minimum|maximum|exactly)\b',
    re.I)
CUTOFF_TARGET = (r'(?:[01]?\d|2[0-3]):[0-5]\d|(?:the[ \t]+)?'
                 r'(?:deadline|cutoff|publication|submission|approval|release|launch|opening|closing|noon|midnight)')
CUTOFF_BOUNDARY = re.compile(
    rf'(?P<inclusive>截至|不晚于|不迟于|\b(?:by|no later than|at or before|on or before)[ \t]+(?:{CUTOFF_TARGET})\b)'
    rf'|(?P<exclusive>严格早于|(?<!not )\b(?:strictly[ \t]+before|before|prior to)[ \t]+(?:{CUTOFF_TARGET})\b)',
    re.I)
NON_OVERRIDE = re.compile(
    r'(?:不能|不应|不可|不得|不允许|无法|不会)[^。；]{0,12}(?:绕过|豁免|替代|覆盖|取代)'
    r'|(?:即使|就算|哪怕)[^。；]{1,48}(?:也|仍)(?:不能|不允许|不得|不予|不可)'
    r"|\b(?:cannot|can't|does not|must not|will not)\s+(?:automatically\s+)?(?:bypass|override|waive|replace|supersede)\b",
    re.I)
IRRELEVANCE = re.compile(
    r'无关|不影响|(?:无[需须]|不[需须])(?:核对|检查|审核|审批|核验|满足)|\birrelevant\b|\bdoes not matter\b', re.I)
ONE_OFF = re.compile(
    r'(?:本次|这次|本轮)[^。；]{0,8}(?:演练|彩排|联调)'
    r'|(?:仅|只)(?:用于|适用于|限于|在)[^。；]{0,12}(?:演练|彩排|联调)'
    r'|(?:只是|仅供|限于)[^。；]{0,12}(?:演练|彩排|联调)'
    r'|\b(?:this rehearsal|this dry run|this drill|(?:one-off|one-time) (?:rehearsal|drill|exercise|test))\b', re.I)
PENDING_DECISION = re.compile(
    r'先别定|待定|待确认|尚未定|还没定|回头再定|暂未决定|未敲定'
    r'|\b(?:undecided|still pending|still open|not decided yet|defer the decision)\b', re.I)
FINAL_DECISION = re.compile(
    r'不做|不进入|不采用|不保留|正式(?:取消|保留)|已经确认'
    r'|\b(?:do not adopt|will not use|rejected|has been approved)\b|(?<!not )(?<!not yet )\bconfirmed\b', re.I)
FINAL_REVISION = re.compile(
    r'(?:已|已经|被)(?:延长|缩短|调整|推迟|提前|改为)|(?:延长|缩短|调整|推迟|提前)(?:了|到|至|为)'
    r'|\b(?:we|they|the team|was|were|has been|have been|had been)\s+'
    r'(?:extended|shortened|revised|moved|rescheduled|pushed back)\b',
    re.I)
PROPOSAL_QUESTION = re.compile(
    r'要不要|是否(?:应|还|要|仍|需|可以)|可否'
    r'|\b(?:do we still|(?:should|could|can|would) we\s+(?:extend|shorten|revise|move|adjust|change|keep|apply))\b',
    re.I)
REVISION_REASON_POLICY = {'version': 'confirmed-revision-reason-v1', 'revision_phrase': FINAL_REVISION.pattern,
                          'proposal_question': PROPOSAL_QUESTION.pattern}
SCOPE_CONDITIONS = {'condition', 'exception', 'retention'}
WEAKER_WORDING = {'exclusive': {'additive', 'example'}, 'universal': {'condition', 'exception', 'example'}}
HUMAN_ROLES = {'user', 'participant'}
FIELDS = ('conclusion', 'reason', 'boundary')
COMMON_TERMS = {
    'agent', 'also', 'and', 'are', 'assistant', 'each', 'from', 'keep', 'must', 'only', 'same', 'shall',
    'still', 'that', 'the', 'their', 'them', 'these', 'they', 'this', 'unchanged', 'use', 'used', 'user',
    'uses', 'using', 'when', 'will', 'with', '用户', '助手', '沿用', '保留', '继续', '仍需', '适用', '规则',
}


def _scope_sources(sample, speakers, orders):
    fences, quoted_lines = {}, {}
    for sid, source in sample.catalog.items():
        if source.document_type == 'dialogue':
            if speakers[sid][0] in HUMAN_ROLES and not source.text.lstrip().startswith('#'):
                yield sid, source
            continue
        if source.document_type != 'artifact':
            continue
        identity = (source.file, orders[sid])
        text = source.text
        fence = FENCE.fullmatch(text)
        if opening := fences.get(identity):
            if fence and fence[1].startswith(opening) and not fence[2].strip():
                del fences[identity]
            continue
        if fence:
            fences[identity] = fence[1]
            continue
        if text[:4].expandtabs(4).startswith('    '):
            continue
        stripped = text.lstrip()
        if stripped.startswith('#'):
            continue
        if stripped.startswith('>') or quoted_lines.get(identity) == source.line - 1:
            if stripped.lstrip('> ').strip():
                quoted_lines[identity] = source.line
            continue
        yield sid, source


def _wording(text):
    return {match.lastgroup for match in MARKER.finditer(text)}


def _quantifier_checks(item_index, name, field, allowed, tokenize, clauses):
    claim = _wording(field['text']) & WEAKER_WORDING.keys()
    if not claim:
        return
    topic = tokenize(field['text']) - COMMON_TERMS
    cited = []
    for ref in field['evidence']:
        sid, quote = ref['source_id'], ref['quote']
        if sid not in allowed:
            continue
        if quote not in clauses:
            clauses[quote] = [(tokenize(clause) - COMMON_TERMS, _wording(clause))
                              for clause in CLAUSE_BREAK.split(quote) if clause.strip()]
        cited.extend((sid, words, kinds) for words, kinds in clauses[quote])
    aligned = [(sid, kinds) for sid, words, kinds in cited if len(words & topic) >= 2]
    if not aligned:
        aligned = [(sid, kinds) for sid, _, kinds in cited]
    supported = set().union(*(kinds for _, kinds in aligned))
    for quantifier in sorted(claim - supported):
        weaker = WEAKER_WORDING[quantifier]
        sources = list(dict.fromkeys(sid for sid, kinds in aligned if weaker & kinds))
        if sources:
            yield {'item_index': item_index, 'field': name, 'quantifier': quantifier, 'source_ids': sources}


def _retained_sources(item_index, source_ids, signals, vocabulary, orders, frequency, sample, tokenize):
    anchors = sorted((sid for sid in source_ids if orders.get(sid)
                      and 'retention' in signals.get(sid, ())),
                     key=lambda sid: (-orders[sid], sid))[:SCOPE_REVIEW_POLICY['retention_sources']]
    if not anchors:
        return None
    anchor_terms = {sid: set().union(*(tokenize(clause) for clause in CLAUSE_BREAK.split(sample.catalog[sid].text)
                                      if 'retention' in _wording(clause))) - COMMON_TERMS for sid in anchors}
    ranked = []
    for sid in signals:
        if orders.get(sid) is None or orders[sid] >= orders[anchors[0]]:
            continue
        for anchor in anchors:
            if orders[sid] >= orders[anchor]:
                continue
            shared = sorted(anchor_terms[anchor] & vocabulary[sid])
            if len(shared) >= 2:
                ranked.append((-sum(1 / frequency[word] for word in shared), -orders[sid], sid, anchor))
    if ranked:
        definitions = list(dict.fromkeys(sid for _, _, sid, _ in sorted(ranked)))[:SCOPE_REVIEW_POLICY['earlier_sources']]
        used = {anchor for _, _, sid, anchor in ranked if sid in definitions}
        return {'item_index': item_index, 'retention_source_ids': [sid for sid in anchors if sid in used],
                'earlier_source_ids': definitions}
    return None


def _clause_ranges(text):
    start = 0
    for end in CLAUSE_BREAK.finditer(text):
        yield start, end.end()
        start = end.end()
    if start < len(text):
        yield start, len(text)


def _proposed_revision(text):
    text = text.rstrip()
    if not text.endswith(('?', '？')):
        return False
    *preceding, question = CLAUSE_BREAK.split(text[:-1])
    return (bool(PROPOSAL_QUESTION.search(question) or FINAL_REVISION.search(question))
            and not any(FINAL_REVISION.search(clause) for clause in preceding))


def revision_reason_needs_confirmation(reason, speakers):
    evidence = reason['evidence']
    return bool(evidence and FINAL_REVISION.search(reason['text']) and all(
        speakers[ref['source_id']][0] == 'agent' and _proposed_revision(ref['quote']) for ref in evidence))


def _confirmation_candidates(reason, sample, eligible, vocabulary, orders):
    origin_ids = [ref['source_id'] for ref in reason['evidence']]
    topic = set().union(*(vocabulary[sid] for sid in origin_ids)) - COMMON_TERMS
    ranked = []
    for sid in eligible:
        source = sample.catalog[sid]
        if source.document_type != 'dialogue':
            continue
        distance = min((source.line - sample.catalog[key].line for key in origin_ids
                        if orders[key] == orders[sid] and sample.catalog[key].file == source.file
                        and 0 < source.line - sample.catalog[key].line <= 5), default=None)
        overlap = len(vocabulary[sid] & topic)
        if distance is not None and overlap >= 2:
            ranked.append((-overlap, distance, sid))
    return [sid for _, _, sid in sorted(ranked)[:3]]


def _mixed_decision(text):
    preceding = [(bool(PENDING_DECISION.search(clause)), bool(FINAL_DECISION.search(clause)))
                 for clause in DECISION_BREAK.split(text)]
    return any(pending for pending, _ in preceding) and any(final and not pending for pending, final in preceding)


def _condition_checks(result, sample, eligible, speakers, vocabulary, orders):
    deadlines, cutoffs, overclaims, temporary, mixed, revisions = [], [], [], [], [], []
    seen = set()
    for sid in eligible:
        text = sample.catalog[sid].text
        if _mixed_decision(text):
            mixed.append(sid)
        cutoffs.extend({'source_id': sid, 'start': match.start(), 'end': match.end(), 'relation': match.lastgroup}
                       for match in islice(CUTOFF_BOUNDARY.finditer(text),
                                           CONDITION_CHECK_POLICY['cutoff_boundaries'] - len(cutoffs)))
        for start, end in _clause_ranges(text):
            clause = text[start:end]
            if not DEADLINE_EVENT.search(clause) or clause.strip() in seen:
                continue
            if any(key.startswith(TEMPORAL_KEYS)
                   for atom in atoms(clause) for key in atom['keys']):
                seen.add(clause.strip())
                deadlines.append((not bool(COMPARISON.search(clause)), {'source_id': sid, 'start': start, 'end': end}))
    for number, item in enumerate(result['items'], 1):
        for field in FIELDS:
            value = item[field]
            if IRRELEVANCE.search(value['text']):
                ids = [ref['source_id'] for ref in value['evidence']
                       if ref['source_id'] in eligible and NON_OVERRIDE.search(ref['quote'])]
                if ids:
                    overclaims.append({'item_index': number, 'field': field, 'source_ids': ids})
        ids = list(dict.fromkeys(ref['source_id'] for field in FIELDS for ref in item[field]['evidence']
                                 if ref['source_id'] in eligible and ONE_OFF.search(ref['quote'])))
        if ids:
            temporary.append({'item_index': number, 'source_ids': ids})
        if revision_reason_needs_confirmation(item['reason'], speakers):
            candidates = _confirmation_candidates(item['reason'], sample, eligible, vocabulary, orders)
            revisions.append({'item_index': number, 'confirmation_candidates': candidates})
    return {'deadline_clauses': [row for _, row in sorted(deadlines, key=lambda pair: pair[0])
                                 [:CONDITION_CHECK_POLICY['deadline_clauses']]],
            'cutoff_boundaries': cutoffs,
            'scope_overclaims': overclaims[:CONDITION_CHECK_POLICY['scope_overclaims']],
            'one_off_items': temporary[:CONDITION_CHECK_POLICY['one_off_items']],
            'mixed_decision_sources': mixed[:CONDITION_CHECK_POLICY['mixed_decision_sources']],
            'revision_reasons': revisions[:CONDITION_CHECK_POLICY['revision_reasons']]}


def build_scope_review(result, sample, speakers, vocabulary, frequency, tokenize):
    signals, locations = {}, []
    orders = {sid: row['node_order'] for sid, row in describe_input(sample)['source_nodes'].items()}
    for sid, source in _scope_sources(sample, speakers, orders):
        signals[sid] = set()
        for position, match in enumerate(islice(MARKER.finditer(source.text), SCOPE_REVIEW_POLICY['markers_per_source'])):
            kind = match.lastgroup
            signals[sid].add(kind)
            locations.append((kind not in SCOPE_CONDITIONS, position, (sid, *match.span(), kind)))
    warnings, retained, clauses = [], [], {}
    for item_index, item in enumerate(result['items'], 1):
        item_sources = set()
        for name in FIELDS:
            field = item[name]
            item_sources.update(ref['source_id'] for ref in field['evidence'])
            warnings.extend(_quantifier_checks(item_index, name, field, signals, tokenize, clauses))
        if len(retained) < SCOPE_REVIEW_POLICY['retained_rules']:
            rule = _retained_sources(item_index, item_sources, signals, vocabulary, orders, frequency, sample, tokenize)
            if rule:
                retained.append(rule)
    selected = sorted(locations, key=lambda row: row[:2])[:SCOPE_REVIEW_POLICY['markers']]
    review = {'source_markers': [row for _, _, row in selected],
              'quantifier_checks': warnings[:SCOPE_REVIEW_POLICY['quantifier_checks']], 'retained_rules': retained}
    focused = _condition_checks(result, sample, signals, speakers, vocabulary, orders)
    if any(focused.values()):
        review['condition_checks'] = focused
    return review
