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
ATOM_POLICY = {'policy': 'grounded-atoms-v1', 'constant': CONSTANT.pattern, 'number': NUMBER.pattern,
               'numbers': 'exact-decimal-with-commas; explicit-time-equivalence; no-fuzzy-rounding',
               'bare_minimum': 10, 'seconds': {k: str(v) for k, v in SECONDS.items()},
               'semantic_certification': False}


def exact_decimal(value):
    text = format(value, 'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def atoms(text, *, all_numbers=False):
    rows = [{'text': match.group(), 'keys': ['constant:' + match.group()]} for match in CONSTANT.finditer(text)]
    seen = {row['text'] for row in rows}
    for match in NUMBER.finditer(text):
        raw = match.group().strip()
        time = TIME.fullmatch(raw)
        numeric = Decimal((time[1] if time else raw).strip().replace(',', ''))
        if not all_numbers and numeric < ATOM_POLICY['bare_minimum'] and not time:
            continue
        keys = ['number:' + exact_decimal(numeric)]
        if time:
            with localcontext() as context:
                context.prec = max(28, len(numeric.as_tuple().digits) + 8)
                keys.append('time-seconds:' + exact_decimal(numeric * SECONDS[time[2]]))
        if raw not in seen:
            rows.append({'text': raw, 'keys': keys})
            seen.add(raw)
    return rows


def present(atom, text):
    available = {key for candidate in atoms(text, all_numbers=True) for key in candidate['keys']}
    return bool(set(atom['keys']) & available)


def missing_atoms(field):
    return [atom for atom in atoms(field['text']) if not any(present(atom, ref['quote']) for ref in field['evidence'])]


def validate_atoms(items):
    fields, issues = [], []
    for item in items:
        for name in ('conclusion', 'reason', 'boundary'):
            field = item[name]
            bindings = [{'text': atom['text'], 'keys': atom['keys'],
                         'source_ids': [ref['source_id'] for ref in field['evidence'] if present(atom, ref['quote'])]}
                        for atom in atoms(field['text'])]
            absent = [row['text'] for row in bindings if not row['source_ids']]
            if absent:
                issues.append(item['id'] + '.' + name + ': ' + ', '.join(absent[:12]))
            fields.append({'item_id': item['id'], 'field': name, 'bindings': bindings})
    if issues:
        raise ValueError('字段自身引文缺少数值或大写常量；引用实际原文，不跨字段借证：' + '；'.join(issues[:20]))
    return {'policy': ATOM_POLICY['policy'], 'policy_digest': digest(ATOM_POLICY), 'fields': fields}
