"""Find directly cited differences between local deadline reference events."""
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, replace

from .grounded_atoms import atoms


_MAX_DIAGNOSTICS = 4
_MAX_EVIDENCE = 3
_MAX_CLAUSE_LENGTH = 320
_DURATION_KEYS = ('time-seconds:', 'business-days:', 'business-hours:')
_DISTINCT_ANCHORS = {frozenset(('opening', 'start')), frozenset(('release', 'effective'))}
_CLAUSE_BREAK = re.compile(r'[。；;！!？?\r\n]|\.(?=\s|$)')
_AMBIGUOUS = re.compile(r'\b(?:or|either|versus|rather than|instead of|unless|perhaps|maybe)\b|'
                        r'或|还是|而非|除非|可能|待定', re.I)
_CONTEXT_TOKEN = re.compile(r'\w+')
_FRAMING = {'a', 'an', 'the', 'please', 'must', 'should', 'shall'}

_EN_DIRECTION = re.compile(r'[ \t]+(?:(?:minimum|maximum)[ \t]+)?'
                           r'(?:(?P<before>before|prior[ \t]+to)|(?P<after>after|following))[ \t]+', re.I)
_EN_BOUND = re.compile(r'\b(?:at[ \t]+(?:least|most)|no[ \t]+(?:less|fewer|more)[ \t]+than|'
                       r'exactly|within)[ \t]+$', re.I)
_EN_ARTICLE = r'(?:the[ \t]+)?'
_EN_ACCESS = (r'(?:doors?|gates?|venues?|offices?|stores?|shops?|facilit(?:y|ies)|librar(?:y|ies)|'
              r'registration|enrollment|booking|check[- \t]in|admissions?)')
_EN_ACTIVITY = (r'(?:events?|sessions?|meetings?|workshops?|trainings?|classes|class|rehearsals?|'
                r'performances?|screenings?|ceremon(?:y|ies)|appointments?|exams?|shifts?|trials?)')
_EN_DOCUMENT = r'(?:polic(?:y|ies)|rules?|regulations?|notices?|updates?|versions?|documents?|patch(?:es)?)'
_EN_ANCHOR = re.compile(
    rf'{_EN_ARTICLE}(?:'
    rf'(?P<opening>{_EN_ACCESS}[ \t]+(?:opens?|opening)|opening[ \t]+of[ \t]+{_EN_ARTICLE}{_EN_ACCESS}|'
    rf'scheduled[ \t]+doors(?:[ \t]+opening)?)'
    rf'|(?P<start>{_EN_ACTIVITY}[ \t]+(?:starts?|begins?|beginning)|'
    rf'(?:start|beginning|kickoff)[ \t]+of[ \t]+{_EN_ARTICLE}{_EN_ACTIVITY}|'
    rf'(?:scheduled[ \t]+start|kickoff)(?:[ \t]+time)?(?![ \t]+of\b))'
    rf'|(?P<release>{_EN_DOCUMENT}[ \t]+(?:is|are)[ \t]+(?:published|released)|'
    rf'(?:being[ \t]+)?(?:published|released)|(?:publication|release)(?:[ \t]+(?:date|time))?)'
    rf'|(?P<effective>(?:{_EN_DOCUMENT}[ \t]+)?(?:tak(?:e|es|ing)[ \t]+effect|'
    rf'go(?:es|ing)?[ \t]+into[ \t]+effect|becom(?:e|es|ing)[ \t]+effective)|effective[ \t]+date)'
    rf')\b', re.I,
)

_ZH_DIRECTION = re.compile(r'(?:(?P<before>[之以]?前)|(?P<after>[之以]?后|(?:时)?起))[ \t]*'
                           r'(?:(?:至少|不少于|不低于|至多|最多|不超过|恰好|正好)[ \t]*)?$')
_ZH_ACCESS = r'(?:报名|预约|入场|登记|售票|受理|申请|检票|入口|通道)'
_ZH_DOCUMENT = r'(?:政策|规定|通知|规则|公告|文档|版本|补丁|方案)'
_ZH_ANCHOR = re.compile(
    rf'(?:(?P<opening>(?:场馆|会场|场地|展馆|剧院|门店|商店|店铺|大厅|园区)?(?:正式)?'
    rf'(?:开门|开馆|开店|开园|开闸)|{_ZH_ACCESS}(?:正式)?开放)'
    rf'|(?P<start>(?:活动|演出|会议|课程|比赛|排练|演练|测试|培训|课时|流程|工作|作业|仪式|放映|巡检)?'
    rf'(?:正式)?(?:开始|开场|开赛|开演|开课|开工|启动))'
    rf'|(?P<release>{_ZH_DOCUMENT}?(?:公开|正式)?(?:发布|公布|公示|颁布))'
    rf'|(?P<effective>{_ZH_DOCUMENT}?(?:正式)?(?:生效|施行)))'
    rf'(?:的?(?:时间|时刻|日期|当天)|之日)?[ \t]*$'
)
_ZH_ACCESS_START = re.compile(rf'{_ZH_ACCESS}$')


@dataclass(frozen=True)
class _Window:
    start: int
    end: int
    duration: str
    direction: str
    anchor: str | None
    language: str
    context: tuple[str, ...]

    @property
    def timing(self):
        return self.duration, self.direction

    def span(self):
        return {'start': self.start, 'end': self.end, 'anchor': self.anchor}


def _context(before, after):
    return tuple(word.casefold() for word in _CONTEXT_TOKEN.findall(f'{before} {after}')
                 if word.casefold() not in _FRAMING or word.isupper())


def _window(clause, offset, amount, duration):
    if direction := _EN_DIRECTION.match(clause, amount.end()):
        anchor = _EN_ANCHOR.match(clause, direction.end())
        bound = _EN_BOUND.search(clause, 0, amount.start())
        start = bound.start() if bound else amount.start()
        end = anchor.end() if anchor else len(clause)
        return _Window(offset + start, offset + end, duration, direction.lastgroup,
                       anchor.lastgroup if anchor else None, 'en', _context(clause[:start], clause[end:]))
    if direction := _ZH_DIRECTION.search(clause, 0, amount.start()):
        anchor = _ZH_ANCHOR.search(clause, 0, direction.start())
        start = anchor.start() if anchor else direction.start()
        end = amount.end()
        before = clause[:start].rstrip()
        kind = anchor.lastgroup if anchor else None
        if kind == 'start' and _ZH_ACCESS_START.search(before):
            kind = None
        return _Window(offset + start, offset + end, duration, direction.lastgroup,
                       kind, 'zh', _context(before.removesuffix('在').removesuffix('于'), clause[end:]))
    return None


def _clauses(text):
    start = 0
    for boundary in _CLAUSE_BREAK.finditer(text):
        yield start, text[start:boundary.start()], boundary.group() in ('?', '？')
        start = boundary.end()
    yield start, text[start:], False


def _windows(text):
    for offset, clause, question in _clauses(text):
        if not clause or len(clause) > _MAX_CLAUSE_LENGTH:
            continue
        durations = {atom['text']: key for atom in atoms(clause) for key in atom['keys']
                     if key.startswith(_DURATION_KEYS)}
        if not durations:
            continue
        pattern = '|'.join(re.escape(raw) for raw in sorted(durations, key=len, reverse=True))
        occurrences = [(match, durations[match.group()]) for match in re.finditer(pattern, clause)]
        counts = Counter(duration for _, duration in occurrences)
        ambiguous = question or _AMBIGUOUS.search(clause)
        for occurrence, duration in occurrences:
            window = _window(clause, offset, occurrence, duration)
            if window:
                yield replace(window, anchor=None) if ambiguous or counts[duration] != 1 else window


def _single_distinct_anchor(claim, references):
    anchors = {source.anchor for _, source in references}
    return len(anchors) == 1 and frozenset(anchors | {claim.anchor}) in _DISTINCT_ANCHORS


def _record(claim, references):
    return {'claim': claim.span(), 'duration_key': claim.duration, 'direction': claim.direction,
            'evidence': [{'source_id': source_id, **source.span()}
                         for source_id, source in references[:_MAX_EVIDENCE]]}


def deadline_anchor_diagnostics(field):
    """Return bounded conflicts and advisory cross-language candidates.

    Spans use zero-based, end-exclusive codepoint offsets into the claim or
    the cited quote. Conflicts require matching surrounding action wording.
    Candidates omit that requirement only across languages, for review.
    """
    conflicts, candidates = [], []
    claims = [claim for claim in _windows(field['text']) if claim.anchor]
    if not claims:
        return {'conflicts': conflicts, 'candidates': candidates}
    timings = {claim.timing for claim in claims}
    durations = {claim.duration for claim in claims}
    cited = defaultdict(list)
    for reference in field['evidence']:
        # A clause we did not inspect may support the existing anchor.
        for _, clause, _ in _clauses(reference['quote']):
            if len(clause) > _MAX_CLAUSE_LENGTH and any(
                    durations.intersection(atom['keys']) for atom in atoms(clause)):
                return {'conflicts': [], 'candidates': []}
        for source in _windows(reference['quote']):
            if source.timing in timings:
                cited[source.timing].append((reference['source_id'], source))

    for claim in claims:
        references = cited[claim.timing]
        if not _single_distinct_anchor(claim, references):
            continue
        aligned = [(sid, source) for sid, source in references if source.context == claim.context
                   and (source.language == claim.language or claim.context)]
        if aligned:
            if len(conflicts) < _MAX_DIAGNOSTICS:
                conflicts.append(_record(claim, aligned))
        elif len(candidates) < _MAX_DIAGNOSTICS:
            bilingual = [(sid, source) for sid, source in references if source.language != claim.language]
            if bilingual:
                candidates.append(_record(claim, bilingual))
    return {'conflicts': conflicts, 'candidates': candidates}


def deadline_anchor_conflicts(field):
    return deadline_anchor_diagnostics(field)['conflicts']
