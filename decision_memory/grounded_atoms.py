"""Necessary field-local constant/value bindings, never semantic verification."""
import re
from decimal import Decimal, localcontext

from .evaluation import digest

CONSTANT = re.compile(r'(?<![A-Za-z0-9_])[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+(?![A-Za-z0-9_])')
NUMBER = re.compile(r'(?<![A-Za-z0-9_.])(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?'
                    r'(?:\s*(?:毫秒|分钟|小时|秒|天|ms|min|h|s|d))?(?![A-Za-z0-9_.])')
TIME = re.compile(r'^(.*?)(毫秒|分钟|小时|秒|天|ms|min|h|s|d)$')
SECONDS = {'毫秒': Decimal('.001'), 'ms': Decimal('.001'), '秒': Decimal(1), 's': Decimal(1),
           '分钟': Decimal(60), 'min': Decimal(60), '小时': Decimal(3600), 'h': Decimal(3600),
           '天': Decimal(86400), 'd': Decimal(86400)}
ENGLISH_SECONDS = {'millisecond': '.001', 'second': '1', 'minute': '60', 'hour': '3600',
                   'day': '86400', 'calendar day': '86400', 'week': '604800'}
FULL_SECONDS = {**SECONDS, **{unit: Decimal(value) for name, value in ENGLISH_SECONDS.items()
                             for unit in (name, name + 's')}}
FULL_UNITS = r'毫秒|分钟|小时|秒|天|milliseconds?|seconds?|minutes?|hours?|(?:calendar\s+)?days?|weeks?|ms|min|h|s|d'
FULL_NUMBER = re.compile(r'(?<![A-Za-z0-9_.])(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?'
                         rf'(?:\s*(?:{FULL_UNITS})(?![A-Za-z0-9_])|(?![A-Za-z0-9_.]))', re.I)
FULL_TIME = re.compile(rf'^(.*?)({FULL_UNITS})$', re.I)
CHINESE_VALUES = dict(zip('零一二三四五六七八九', range(10))) | {'〇': 0, '两': 2}
ENGLISH_SMALL = dict(zip(('zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine',
                          'ten', 'eleven', 'twelve', 'thirteen', 'fourteen', 'fifteen', 'sixteen',
                          'seventeen', 'eighteen', 'nineteen'), range(20)))
ENGLISH_TENS = dict(zip(('twenty', 'thirty', 'forty', 'fifty', 'sixty', 'seventy', 'eighty', 'ninety'),
                        range(20, 100, 10)))
ENGLISH_VALUE = (rf"(?:{'|'.join(ENGLISH_TENS)})(?:[- \t]+(?:{'|'.join(tuple(ENGLISH_SMALL)[1:10])}))?"
                 rf"|{'|'.join(ENGLISH_SMALL)}")
CHINESE_VALUE = r'[一二三四五六七八九]?十[一二三四五六七八九]?|[零〇一二两三四五六七八九]'
ARABIC_VALUE = r'(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?'
ENGLISH_DURATION_UNIT = r'milliseconds?|seconds?|minutes?|hours?|(?:calendar[- \t]+)?days?|weeks?'
WRITTEN_DURATION = re.compile(
    r'(?<![\d零〇一二两三四五六七八九十百千万亿])'
    rf'(?P<chinese>{CHINESE_VALUE})'
    r'[ \t]*(?:个[ \t]*)?(?P<chinese_unit>毫秒|分钟|小时|自然日|秒|天|周)'
    rf'|(?<![A-Za-z0-9_.-])(?P<english>{ENGLISH_VALUE})[- \t]+'
    rf'(?P<english_unit>{ENGLISH_DURATION_UNIT})(?![A-Za-z0-9_])', re.I)
LONG_ENGLISH_VALUE = re.compile(r'\b(?:hundred|thousand|million|billion|trillion)(?:[- \t]+and)?[- \t]+$', re.I)
ELAPSED_UNITS = rf'(?:elapsed|continuous)[- \t]+(?:{ENGLISH_DURATION_UNIT})'
CHINESE_ELAPSED_UNITS = r'自然日|(?:连续[ \t]*)?(?:毫秒|分钟|小时|秒|天|周)'


def _compile_qualified(english_value, english_unit, chinese_unit, chinese_value=CHINESE_VALUE):
    return re.compile(
        r'(?<![A-Za-z0-9_.\-])'
        rf'(?P<english>{ARABIC_VALUE}|{english_value})[- \t]+'
        rf'(?P<english_unit>{english_unit})(?![A-Za-z0-9_])'
        r'|(?<![\d.零〇一二两三四五六七八九十百千万亿])'
        rf'(?P<chinese>{ARABIC_VALUE}|{chinese_value})[ \t]*(?:个[ \t]*)?'
        rf'(?P<chinese_unit>{chinese_unit})', re.I)


V5_QUALIFIED_DURATION = _compile_qualified(
    ENGLISH_VALUE, rf'(?:business|working)[- \t]+days?|workdays?|{ELAPSED_UNITS}', '工作日|' + CHINESE_ELAPSED_UNITS)
QUALIFIED_ENGLISH_UNITS = rf'(?:business|working)[- \t]+(?:days?|hours?)|workdays?|{ELAPSED_UNITS}'
QUALIFIED_CHINESE_UNITS = '工作日|工作小时|' + CHINESE_ELAPSED_UNITS
V6_QUALIFIED_DURATION = _compile_qualified(ENGLISH_VALUE + '|a|an', QUALIFIED_ENGLISH_UNITS, QUALIFIED_CHINESE_UNITS)
FRACTIONAL_ENGLISH_VALUE = rf'(?:{ENGLISH_VALUE})[- \t]+and[- \t]+a[- \t]+half|half[- \t]+an?|{ENGLISH_VALUE}|a|an'
FRACTIONAL_CHINESE_VALUE = rf'(?:{CHINESE_VALUE})[ \t]*个[ \t]*半|{CHINESE_VALUE}|半'
V7_QUALIFIED_DURATION = _compile_qualified(FRACTIONAL_ENGLISH_VALUE, QUALIFIED_ENGLISH_UNITS,
                                          QUALIFIED_CHINESE_UNITS, FRACTIONAL_CHINESE_VALUE)
QUALIFIED_DURATION = _compile_qualified(
    FRACTIONAL_ENGLISH_VALUE, QUALIFIED_ENGLISH_UNITS + r'|milliseconds?|seconds?|minutes?|hours?',
    QUALIFIED_CHINESE_UNITS, FRACTIONAL_CHINESE_VALUE)
V6_RELATIVE_DAY = re.compile(
    r'(?P<chinese_direction>(?<![剩余])下|上)(?:[一1]个?|个)(?P<chinese_day>工作日|自然日)'
    r'|\b(?P<english_direction>next|following|previous|prior)[- \t]+'
    r'(?P<english_day>(?:business|working|calendar)[- \t]+day|workday)\b', re.I)
V7_RELATIVE_DAY = re.compile(
    r'(?P<chinese_direction>(?:(?<![剩余])下|上)(?:[一1]个?|个)|次一?|同(?:一个?|个))'
    r'(?P<chinese_day>工作日|自然日)'
    r'|\b(?P<english_direction>next|following|previous|prior|same)[- \t]+'
    r'(?P<english_day>(?:business|working|calendar)[- \t]+day|workday)\b', re.I)
V8_RELATIVE_DAY = re.compile(
    r'(?P<chinese_direction>(?:(?<![剩余])下|上)(?:[一1]个?|个)|次一?|同(?:一个?|个)|另一个?)'
    r'(?P<chinese_day>工作日|(?:本地)?自然日)'
    r'|(?P<plain_chinese_direction>另[一1]|同[一1]|前[一1]|后[一1]|次)[日天]'
    r'|\b(?P<english_direction>next|following|previous|prior|same)[- \t]+'
    r'(?P<english_day>(?:business|working|(?:local[- \t]+)?calendar)[- \t]+day|workday|day)\b'
    r'|(?<=\b(?:on|to) )(?:another|a[- \t]+different)[- \t]+day\b', re.I)
RELATIVE_DAY = re.compile(
    r'(?P<chinese_direction>(?:(?<![剩余])下|上)(?:[一1]个?|个)|次一?|同(?:一个?|个)|另一个?)'
    r'(?P<chinese_day>工作日|(?:本地)?自然日)'
    r'|(?P<plain_chinese_direction>另[一1]|同[一1]|(?<!提)前[一1]|后[一1]|(?<![每本这此各逐零一二两三四五六七八九十0-9])次)[日天]'
    r'|\b(?P<english_direction>next|following|previous|prior|same)[- \t]+'
    r'(?P<english_day>(?:business|working|(?:local[- \t]+)?calendar)[- \t]+day|workday|day)\b'
    r'|\b(?P<different_date>(?:another|a[- \t]+different)[- \t]+day)\b', re.I)
DIFFERENT_DATE_PREFIX = re.compile(r'\b(?:on|to|choose|pick|select)[ \t]+$|'
                                   r'\b(?:reschedule|schedule|move|shift)[^.;!?\r\n]{0,60}\bfor[ \t]+$', re.I)
TEMPORAL_KEYS = ('time-seconds:', 'business-days:', 'business-hours:', 'relative-business-day:', 'relative-calendar-day:')
V5_QUANTITY_PREFIX = re.compile(r'(?:[+−-]|[\d零〇一二两三四五六七八九十][ \t]*[/／点．][ \t]*|'
                                r'\bpoint[- \t]+)$', re.I)
QUANTITY_PREFIX = re.compile(r'(?:[+−-]|[\d零〇一二两三四五六七八九十百千万]\s*[/／⁄∕点．]\s*|'
                             r'[零〇一二两三四五六七八九十百千万]分之\s*|\bpoint[- \t]+)$', re.I)
UNQUANTIFIED_ARTICLE = re.compile(r'\b(?:on|during)\s+$', re.I)
WRITTEN_SECONDS = {**FULL_SECONDS, '自然日': Decimal(86400), '周': Decimal(604800)}
ENGLISH_VALUES = ENGLISH_SMALL | ENGLISH_TENS | {'a': 1, 'an': 1}
LEGACY_ATOM_POLICY = {'policy': 'grounded-atoms-v1', 'constant': CONSTANT.pattern, 'number': NUMBER.pattern,
                      'numbers': 'exact-decimal-with-commas; explicit-time-equivalence; no-fuzzy-rounding',
                      'bare_minimum': 10, 'seconds': {k: str(v) for k, v in SECONDS.items()},
                      'semantic_certification': False}
V2_ATOM_POLICY = {**LEGACY_ATOM_POLICY, 'policy': 'grounded-atoms-v2',
                  'numbers': 'exact-decimal-with-commas; time-needs-equal-normalized-unit; no-fuzzy-rounding'}
V3_ATOM_POLICY = {**V2_ATOM_POLICY, 'policy': 'grounded-atoms-v3', 'number': FULL_NUMBER.pattern,
                  'full_english_units': True, 'seconds': {key: str(value) for key, value in FULL_SECONDS.items()}}
V4_ATOM_POLICY = {**V3_ATOM_POLICY, 'policy': 'grounded-atoms-v4', 'written_duration': WRITTEN_DURATION.pattern,
                  'written_seconds': {key: str(value) for key, value in WRITTEN_SECONDS.items()}}
V5_ATOM_POLICY = {**V4_ATOM_POLICY, 'policy': 'grounded-atoms-v5',
                  'qualified_duration': V5_QUALIFIED_DURATION.pattern, 'quantity_prefix': V5_QUANTITY_PREFIX.pattern}
V6_ATOM_POLICY = {**V5_ATOM_POLICY, 'policy': 'grounded-atoms-v6',
                  'qualified_duration': V6_QUALIFIED_DURATION.pattern, 'relative_day': V6_RELATIVE_DAY.pattern}
V7_ATOM_POLICY = {**V6_ATOM_POLICY, 'policy': 'grounded-atoms-v7', 'qualified_duration': V7_QUALIFIED_DURATION.pattern,
                  'relative_day': V7_RELATIVE_DAY.pattern, 'quantity_prefix': QUANTITY_PREFIX.pattern,
                  'unquantified_article': UNQUANTIFIED_ARTICLE.pattern}
V8_ATOM_POLICY = {**V7_ATOM_POLICY, 'policy': 'grounded-atoms-v8', 'relative_day': V8_RELATIVE_DAY.pattern}
V9_ATOM_POLICY = {**V8_ATOM_POLICY, 'policy': 'grounded-atoms-v9', 'relative_day': RELATIVE_DAY.pattern,
                  'different_date_prefix': DIFFERENT_DATE_PREFIX.pattern}
V10_ATOM_POLICY = {**V9_ATOM_POLICY, 'policy': 'grounded-atoms-v10', 'qualified_duration': QUALIFIED_DURATION.pattern}
# Avoid reading the last character of “班次” plus the first character of “日志” as a date.
ATOM_POLICY = {**V10_ATOM_POLICY, 'policy': 'grounded-atoms-v11',
               'relative_day': RELATIVE_DAY.pattern.replace('[日天]', '(?:日(?!志)|天)')}


class MissingAtomEvidence(ValueError):
    def __init__(self, message, gaps):
        super().__init__(message)
        self.gaps = gaps


def exact_decimal(value):
    text = format(value, 'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def _duration_amount(match, text):
    value = match['chinese'] or match['english']
    if value[0].isdecimal():
        return Decimal(value.replace(',', ''))
    if match['chinese']:
        if value == '半':
            return Decimal('.5')
        whole, _, half = value.partition('个')
        whole = whole.strip()
        if '十' in whole:
            tens, ones = whole.split('十')
            amount = 10 * CHINESE_VALUES[tens or '一'] + CHINESE_VALUES.get(ones, 0)
        else:
            amount = CHINESE_VALUES[whole]
        return amount + Decimal('.5') if half.strip() == '半' else amount
    if LONG_ENGLISH_VALUE.search(text, max(0, match.start() - 64), match.start()):
        return None
    words = value.lower().replace('-', ' ').split()
    if words[0] == 'half':
        return Decimal('.5')
    if words[-3:] == ['and', 'a', 'half']:
        return sum(ENGLISH_VALUES[word] for word in words[:-3]) + Decimal('.5')
    if all(word in ENGLISH_VALUES for word in words):
        return sum(ENGLISH_VALUES[word] for word in words)
    return None


def _time_key(amount, multiplier):
    with localcontext() as context:
        context.prec = max(28, len(Decimal(amount).as_tuple().digits) + 8)
        return 'time-seconds:' + exact_decimal(amount * multiplier)


def _excluded_quantity(text, start, prefix):
    return prefix is not None and (prefix.search(text, max(0, start - 64), start)
                                  or LONG_ENGLISH_VALUE.search(text, max(0, start - 64), start))


def _written_durations(text, *, prefix, relative_positions):
    for match in WRITTEN_DURATION.finditer(text):
        if match.start() in relative_positions or _excluded_quantity(text, match.start(), prefix):
            continue
        amount = _duration_amount(match, text)
        unit = ' '.join((match['chinese_unit'] or match['english_unit']).lower().replace('-', ' ').split())
        if amount is not None and unit in WRITTEN_SECONDS:
            yield {'text': match.group(), 'keys': ['number:' + str(amount), _time_key(amount, WRITTEN_SECONDS[unit])]}


def _relative_day(match):
    groups = match.groupdict()
    unit = (groups.get('chinese_day') or groups.get('english_day') or 'day').lower()
    calendar = 'calendar' if unit == 'day' or 'calendar' in unit or unit.endswith('自然日') else 'business'
    direction = (groups.get('chinese_direction') or groups.get('english_direction')
                 or groups.get('plain_chinese_direction') or 'another').lower()
    if direction.startswith(('上', '前')) or direction in ('previous', 'prior'):
        relation = 'previous'
    elif direction.startswith('另') or direction == 'another':
        relation = 'another'
    else:
        relation = 'same' if direction.startswith('同') or direction == 'same' else 'next'
    return {'text': match.group(), 'keys': [f'relative-{calendar}-day:{relation}']}


def _qualified_durations(text, pattern, relative_positions, prefix, article):
    for match in pattern.finditer(text):
        start = match.start()
        if start in relative_positions or _excluded_quantity(text, start, prefix):
            continue
        if (article is not None and (match['english'] or '').lower() in ('a', 'an')
                and article.search(text, max(0, start - 64), start)):
            continue
        amount = _duration_amount(match, text)
        if amount is None:
            continue
        value = exact_decimal(amount)
        unit = ' '.join((match['chinese_unit'] or match['english_unit']).lower().replace('-', ' ').split())
        if unit in ('工作小时', 'business hour', 'business hours', 'working hour', 'working hours'):
            typed = 'business-hours:' + value
        elif unit == '工作日' or unit.startswith(('business ', 'working ', 'workday')):
            typed = 'business-days:' + value
        else:
            unit = unit.removeprefix('elapsed ').removeprefix('continuous ').removeprefix('连续').strip()
            if unit not in WRITTEN_SECONDS:
                continue
            typed = _time_key(amount, WRITTEN_SECONDS[unit])
        yield start, {'text': match.group(), 'keys': ['number:' + value, typed]}


def atoms(text, *, all_numbers=False, policy=ATOM_POLICY):
    rows = [{'text': match.group(), 'keys': ['constant:' + match.group()]} for match in CONSTANT.finditer(text)]
    seen = {row['text'] for row in rows}
    numbers, times, seconds = (FULL_NUMBER, FULL_TIME, FULL_SECONDS) if policy.get('full_english_units') else (NUMBER, TIME, SECONDS)
    patterns = {key: re.compile(policy[key], re.I)
                for key in ('quantity_prefix', 'qualified_duration', 'relative_day', 'unquantified_article',
                            'different_date_prefix')
                if key in policy}
    relative = list(patterns['relative_day'].finditer(text)) if 'relative_day' in patterns else []
    if date_prefix := patterns.get('different_date_prefix'):
        relative = [match for match in relative if not match['different_date']
                    or date_prefix.search(text, max(0, match.start() - 80), match.start())]
    relative_positions = {position for match in relative for position in range(match.start(), match.end())}
    prefix = patterns.get('quantity_prefix')
    explicit = (dict(_qualified_durations(text, patterns['qualified_duration'], relative_positions, prefix,
                                         patterns.get('unquantified_article')))
                if 'qualified_duration' in patterns else {})
    for match in numbers.finditer(text):
        if (match.start() in explicit or match.start() in relative_positions
                or _excluded_quantity(text, match.start(), prefix)):
            continue
        raw = match.group().strip()
        time = times.fullmatch(raw)
        numeric = Decimal((time[1] if time else raw).strip().replace(',', ''))
        if not all_numbers and numeric < policy['bare_minimum'] and not time:
            continue
        keys = ['number:' + exact_decimal(numeric)]
        if time:
            unit = ' '.join(time[2].lower().split())
            keys.append(_time_key(numeric, seconds[unit]))
        if raw not in seen:
            rows.append({'text': raw, 'keys': keys})
            seen.add(raw)
    durations = (list(_written_durations(text, prefix=prefix, relative_positions=relative_positions))
                 if policy.get('written_duration') else [])
    for row in [*durations, *explicit.values(), *(_relative_day(match) for match in relative)]:
        if row['text'] not in seen:
            rows.append(row)
            seen.add(row['text'])
    return rows


def present(atom, text, *, policy=ATOM_POLICY):
    available = {key for candidate in atoms(text, all_numbers=True, policy=policy) for key in candidate['keys']}
    keys = set(atom['keys'])
    if policy is not LEGACY_ATOM_POLICY:
        time_values = {key for key in keys if key.startswith(TEMPORAL_KEYS)}
        if time_values:
            keys = time_values
    return bool(keys & available)


def missing_atoms(field):
    return [atom for atom in atoms(field['text']) if not any(present(atom, ref['quote']) for ref in field['evidence'])]


def validate_atoms(items, *, policy=ATOM_POLICY):
    fields, issues, gaps = [], [], []
    for item in items:
        for name in ('conclusion', 'reason', 'boundary'):
            field = item[name]
            bindings = [{'text': atom['text'], 'keys': atom['keys'],
                         'source_ids': [ref['source_id'] for ref in field['evidence']
                                        if present(atom, ref['quote'], policy=policy)]}
                        for atom in atoms(field['text'], policy=policy)]
            absent = [row for row in bindings if not row['source_ids']]
            if absent:
                issues.append(item['id'] + '.' + name + ': ' + ', '.join(row['text'] for row in absent[:12]))
                gaps.append({'item_id': item['id'], 'field': name, 'value': field, 'atoms': absent})
            fields.append({'item_id': item['id'], 'field': name, 'bindings': bindings})
    if issues:
        raise MissingAtomEvidence('字段自身引文缺少数值或大写常量；引用实际原文，不跨字段借证：'
                                  + '；'.join(issues[:20]), gaps)
    return {'policy': policy['policy'], 'policy_digest': digest(policy), 'fields': fields}
