"""Deterministic audit of model assertions, never a semantic truth oracle."""
import re

from .evaluation import digest
from .evidence_spans import locate_unique_quote, meaningful_overlap, source_span
from .sources import ROLE

FIELDS = ('conclusion', 'reason', 'boundary')
POLICY = 'evidence-v1'
MARKERS = ('必须', '仅', '最多', '不超过', '禁止', '不做', '改为', '改成', '保持', '替代',
           '取消', '不再', '口径', '是指', '待确认', '未说明')
CLAUSES = re.compile(r'[^。；;！？!?\n]+[。；;！？!?]?')
STRUCTURE = re.compile(r'^\s*(?:[`~]{3,}.*|[-_*]{3,}|[|:\s-]+)\s*$')
HEADINGS = ('技术方案', '需求分析', '需求说明', '背景', '目标', '范围', '设计', '实现', '总结', '产物', '结论')
ATTRIBUTION = re.compile(r'用户要求|需求方要求|用户指定')
AFFIRMATIONS = {'对', '是', '是的', '好的', '同意', '可以', '就这样', '没问题', '就按这个方案', '按这个方案', '确认'}
POLICY_DATA = {'version': POLICY, 'markers': MARKERS, 'clauses': CLAUSES.pattern,
               'structure': STRUCTURE.pattern, 'headings': HEADINGS, 'attribution': ATTRIBUTION.pattern,
               'affirmations': sorted(AFFIRMATIONS), 'max_units': 256, 'coordinates': 'line-codepoints-half-open'}
MODEL_KEYS = {'policy', 'evidence_focus', 'admission_checks', 'user_attribution_checks', 'coverage_checks'}
SAVED_KEYS = MODEL_KEYS | {'schema_version', 'input_digest', 'policy_digest', 'unit_catalog',
                           'evidence_spans', 'audit_status'}


def _object(value, keys, label):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError(label + ' 字段不完整或包含未知字段')
    return value


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label + ' 必须是非空字符串')
    return value


def _choice(value, choices, label):
    if not isinstance(value, str) or value not in choices:
        raise ValueError(label + ' 无效')
    return value


def _rows(value, label):
    if not isinstance(value, list):
        raise ValueError(label + ' 必须为数组')
    return value


def _identity(ref):
    return ref['source_id'], ref['quote']


def build_coverage_catalog(sample, policy=POLICY):
    _choice(policy, (POLICY,), '候选策略')
    units = []
    for key, source in sample.catalog.items():
        text = source.text
        heading = re.fullmatch(r'\s*#{1,6}\s*(.*?)\s*', text)
        if STRUCTURE.fullmatch(text) or (heading and heading[1] in HEADINGS):
            continue
        for match in CLAUSES.finditer(text):
            quote = match.group().strip()
            if not quote:
                continue
            if source.role != 'artifact' and not (
                    source.role in ('user', None) and any(marker in quote for marker in MARKERS)):
                continue
            start = match.start() + len(match.group()) - len(match.group().lstrip())
            units.append({'unit_id': f'U{len(units) + 1:03d}',
                          **source_span(sample, key, start, start + len(quote))})
            if len(units) > POLICY_DATA['max_units']:
                raise ValueError('审计候选超过 256 个，请准备更小的完整需求；不自动截断')
    return units


def _references(value, sample, allowed=None):
    refs, seen = [], set()
    for raw in _rows(value, '审计证据'):
        span = locate_unique_quote(sample, raw)
        identity = _identity(span)
        if identity in seen or (allowed is not None and identity not in allowed):
            raise ValueError('审计证据重复或不属于目标实际引用')
        refs.append(span)
        seen.add(identity)
    return refs


def _current_evidence(value, items, sample):
    focus = {}
    normalized = []
    for row in _rows(value, 'evidence_focus'):
        _object(row, {'knowledge_key', 'field', 'source_id', 'quote', 'focus_quote'}, '证据焦点')
        key = _text(row['knowledge_key'], 'knowledge_key')
        field = _choice(row['field'], FIELDS, '焦点字段')
        if key not in items:
            raise ValueError('焦点知识不存在')
        ref = locate_unique_quote(sample, row)
        identity = (key, field, *_identity(ref))
        allowed = {_identity(r) for r in items[key][field]['evidence']}
        if _identity(ref) not in allowed or identity in focus:
            raise ValueError('焦点未关联当前证据或重复')
        quote = _text(row['focus_quote'], 'focus_quote')
        offset = ref['quote'].find(quote)
        if offset < 0 or ref['quote'].find(quote, offset + 1) >= 0:
            raise ValueError('focus_quote 必须在完整引文内唯一匹配')
        focus[identity] = (ref['start'] + offset, ref['start'] + offset + len(quote))
        normalized.append(dict(row))
    spans = []
    for key, item in items.items():
        for field in FIELDS:
            for ref in item[field]['evidence']:
                span = locate_unique_quote(sample, ref)
                identity = (key, field, *_identity(span))
                start, end = focus.get(identity, (span['start'], span['end']))
                spans.append({'knowledge_key': key, 'field': field, **span,
                              'focus_start': start, 'focus_end': end})
    return normalized, spans


def _admissions(value, items, sample):
    rows, indexed = [], {}
    for row in _rows(value, 'admission_checks'):
        _object(row, {'knowledge_key', 'basis', 'direct', 'proposal', 'confirmation', 'notes'}, '准入')
        key = _text(row['knowledge_key'], 'knowledge_key')
        if key not in items or key in indexed:
            raise ValueError('准入必须唯一关联当前知识')
        basis = _choice(row['basis'], ('user_direct', 'artifact_direct', 'confirmed_proposal', 'needs_review'), '准入路径')
        _text(row['notes'], '准入说明')
        allowed = {_identity(r) for r in items[key]['conclusion']['evidence']}
        anchors = {name: _references(row[name], sample, allowed) for name in ('direct', 'proposal', 'confirmation')}
        direct, proposal, confirmation = (anchors[name] for name in ('direct', 'proposal', 'confirmation'))
        if basis in ('user_direct', 'artifact_direct'):
            role = 'user' if basis == 'user_direct' else 'artifact'
            if not direct or proposal or confirmation or any(r['role'] != role for r in direct):
                raise ValueError('直接准入需要对应真实角色的当前结论证据')
            if role == 'user' and any(re.sub(r'[\W_]+', '', ROLE.sub('', r['quote'])) in AFFIRMATIONS for r in direct):
                raise ValueError('短确认不能单独充当直接规则，请关联 Agent 提议')
        if basis == 'confirmed_proposal':
            if (direct or not proposal or not confirmation or any(r['role'] != 'agent' for r in proposal)
                    or any(r['role'] != 'user' for r in confirmation)):
                raise ValueError('确认提议需要 Agent 提议和用户确认的双重证据')
            position = lambda r: (sample.node_order(r['source_id']), r['line'])
            if min(map(position, confirmation)) <= max(map(position, proposal)):
                raise ValueError('用户确认必须晚于所引用的全部提议')
        normalized = {**row, **anchors}
        rows.append(normalized)
        indexed[key] = normalized
    if set(indexed) != set(items):
        raise ValueError('准入清单必须覆盖全部当前知识')
    return rows, indexed


def _attributions(value, items, sample):
    expected = {(key, field) for key, item in items.items() for field in ('reason', 'boundary')
                if ATTRIBUTION.search(item[field]['text'])}
    rows, seen = [], set()
    for row in _rows(value, 'user_attribution_checks'):
        _object(row, {'knowledge_key', 'field', 'evidence', 'notes'}, '用户归因')
        key = _text(row['knowledge_key'], '归因 knowledge_key')
        field = _choice(row['field'], ('reason', 'boundary'), '归因字段')
        identity = (key, field)
        if identity not in expected or identity in seen:
            raise ValueError('用户归因清单包含未知或重复字段')
        _text(row['notes'], '用户归因说明')
        refs = _references(row['evidence'], sample, {_identity(r) for r in items[key][field]['evidence']})
        if not refs or any(r['role'] != 'user' for r in refs):
            raise ValueError('用户归因需要该字段的实际用户证据')
        rows.append({**row, 'evidence': refs})
        seen.add(identity)
    if seen != expected:
        raise ValueError('用户归因清单未覆盖显式用户要求')
    return rows


def _target(row, disposition, compiled, grounding, items, spans, sample):
    if not isinstance(row, dict):
        raise ValueError('覆盖目标必须为对象')
    kind = _choice(row.get('type'), ('item_field', 'history_event', 'open_question', 'reason_check'), '覆盖目标类型')
    ref = locate_unique_quote(sample, row.get('evidence'))
    allowed, result = [], dict(row)
    if kind == 'item_field':
        _object(row, {'type', 'knowledge_key', 'field', 'evidence'}, '当前知识目标')
        key, field = _text(row['knowledge_key'], '目标知识'), _choice(row['field'], FIELDS, '目标字段')
        if key not in items or disposition not in ('current', 'needs_review'):
            raise ValueError('当前知识目标状态不匹配')
        allowed = items[key][field]['evidence']
        match = [s for s in spans if s['knowledge_key'] == key and s['field'] == field and _identity(s) == _identity(ref)]
        if match:
            ref = {k: v for k, v in match[0].items() if k not in ('knowledge_key', 'field')}
    elif kind == 'history_event':
        _object(row, {'type', 'event_id', 'field', 'evidence'}, '历史目标')
        event_id = _text(row['event_id'], '历史事件')
        candidates = [r for r in compiled['history'] if r['id'] == event_id]
        if not candidates or disposition not in ('history', 'needs_review'):
            raise ValueError('历史事件不存在或目标状态错误')
        entry = candidates[0]
        if entry['status'] not in ('superseded', 'invalidated'):
            raise ValueError('现行事件不能冒充历史覆盖')
        if entry['operation'] in ('add', 'update'):
            field = _choice(row['field'], FIELDS, '历史字段')
            allowed = entry['item'][field]['evidence']
        elif entry['operation'] == 'invalidate':
            _choice(row['field'], ('note',), '失效字段')
            allowed = entry['evidence']
        else:
            field = _choice(row['field'], FIELDS, '历史合并字段')
            allowed = entry['evidence'].get(field, [])
    elif kind == 'open_question':
        _object(row, {'type', 'question_id', 'evidence'}, '未决目标')
        question_id = _text(row['question_id'], '问题编号')
        questions = {f'Q{i:03d}': q for i, q in enumerate(compiled['open_questions'], 1)}
        if question_id not in questions or disposition not in ('pending', 'needs_review'):
            raise ValueError('未决目标不存在或状态错误')
        allowed = questions[question_id]['evidence']
    else:
        _object(row, {'type', 'source_id', 'quote', 'evidence'}, '未知理由目标')
        source_id, quote = _text(row['source_id'], '未知来源'), _text(row['quote'], '未知声明')
        matches = [r for r in grounding['reason_checks'] if (r['source_id'], r['quote']) == (source_id, quote)]
        if (not matches or matches[0]['status'] != 'retained' or matches[0]['knowledge_key'] not in items
                or disposition not in ('pending', 'needs_review')):
            raise ValueError('未知理由目标未保留或没有关联当前知识')
        allowed = matches[0]['evidence']
    if _identity(ref) not in {_identity(r) for r in allowed}:
        raise ValueError('覆盖证据不属于目标实际引用')
    result['evidence'] = ref
    return result


def validate_evidence_audit(value, compiled, grounding, sample, policy=POLICY):
    _choice(policy, (POLICY,), '审计策略')
    if not isinstance(value, dict) or set(value) not in (MODEL_KEYS, SAVED_KEYS):
        raise ValueError('审计需要完整模型协议或完整保存协议')
    if value['policy'] != policy:
        raise ValueError('审计策略不匹配')
    units = build_coverage_catalog(sample, policy)
    by_unit = {row['unit_id']: row for row in units}
    items = {row['knowledge_key']: compiled['items'][i] for i, row in enumerate(compiled['memory_index'])}
    focus, spans = _current_evidence(value['evidence_focus'], items, sample)
    admissions, by_key = _admissions(value['admission_checks'], items, sample)
    attributions = _attributions(value['user_attribution_checks'], items, sample)
    rows, seen = [], set()
    pending = any(row['basis'] == 'needs_review' for row in admissions)
    for row in _rows(value['coverage_checks'], 'coverage_checks'):
        _object(row, {'unit_id', 'disposition', 'semantic_coverage', 'targets', 'exclusion_reason', 'notes'}, '候选处置')
        unit_id = _text(row['unit_id'], '候选编号')
        if unit_id not in by_unit or unit_id in seen:
            raise ValueError('候选清单包含未知或重复编号')
        unit = by_unit[unit_id]
        disposition = _choice(row['disposition'], ('current', 'history', 'pending', 'non_reusable', 'needs_review'), '候选处置')
        semantic = _choice(row['semantic_coverage'], ('full', 'partial', 'unclear', 'not_applicable'), '语义覆盖')
        _text(row['notes'], '候选处置说明')
        targets = [_target(t, disposition, compiled, grounding, items, spans, sample)
                   for t in _rows(row['targets'], '覆盖目标')]
        if len({digest(t) for t in targets}) != len(targets):
            raise ValueError('同一覆盖目标不能重复计数')
        if disposition == 'non_reusable':
            _choice(row['exclusion_reason'], ('schedule', 'format', 'wiring', 'chatter'), '排除原因')
            if targets or semantic != 'not_applicable':
                raise ValueError('排除候选不能携带覆盖目标或语义覆盖')
            if any(meaningful_overlap(unit, span, sample) for span in spans):
                raise ValueError('当前知识已经依赖的片段不能排除')
        else:
            if row['exclusion_reason'] is not None or semantic == 'not_applicable':
                raise ValueError('非排除候选需要有效的语义判定')
            if disposition != 'needs_review' and not targets:
                raise ValueError('覆盖候选必须关联实际目标')
            if targets and (any(not meaningful_overlap(unit, t['evidence'], sample) for t in targets)):
                raise ValueError('覆盖目标没有引用当前候选片段')
            unadmitted = any(t['type'] == 'item_field' and by_key[t['knowledge_key']]['basis'] == 'needs_review' for t in targets)
            if semantic != 'full' or unadmitted:
                disposition = 'needs_review'
        pending = pending or disposition == 'needs_review'
        rows.append({**row, 'disposition': disposition, 'targets': targets})
        seen.add(unit_id)
    if seen != set(by_unit):
        raise ValueError('候选清单必须逐项完整处置')
    binding = digest({'fingerprint': sample.fingerprint, 'files': sample.file_hashes, 'nodes': sample.nodes,
                      'roles': [(k, s.role) for k, s in sample.catalog.items()], 'policy': POLICY_DATA,
                      'compiled': {k: compiled[k] for k in ('items', 'memory_index', 'history', 'events', 'open_questions')},
                      'grounding': grounding})
    normalized = {'schema_version': 1, 'input_digest': binding, 'policy': policy, 'policy_digest': digest(POLICY_DATA),
                  'unit_catalog': units, 'evidence_spans': spans, 'evidence_focus': focus,
                  'admission_checks': admissions, 'user_attribution_checks': attributions,
                  'coverage_checks': rows, 'audit_status': 'needs_review' if pending else 'passed'}
    if set(value) == SAVED_KEYS and (type(value['schema_version']) is not int or value != normalized):
        raise ValueError('保存的审计与原文、最终事件或关联摘要不一致')
    return normalized
