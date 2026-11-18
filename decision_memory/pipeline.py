"""Extraction, optional decision reconstruction, and evidence review."""

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone

from . import __version__, prompts
from .client import ModelClient, ModelOutputError
from .sources import REQUIREMENT, Sample
from .validation import validate_evidence, validate_field, validate_result

MODES = ('baseline', 'verified', 'artifact-only')
STAGE_MODES = (*MODES, 'grounded')
PROMPT_HASH = hashlib.sha256((prompts.EXTRACT + prompts.DECISIONS + prompts.REVIEW).encode()).hexdigest()


def run_signature(sample: Sample, client: ModelClient, mode: str) -> str:
    values = [sample.fingerprint, sample.documents(), client.config.public(), mode, PROMPT_HASH, __version__]
    return hashlib.sha256(json.dumps(values, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def checked_call(client, system, user, validator, repair=None):
    """Validate strictly; retry once with the error; then optionally repair conservatively."""
    for attempt in range(2):
        payload = None
        try:
            payload = client.complete(system, user)
            return validator(payload)
        except ValueError as error:
            if ((isinstance(error, ModelOutputError) and not error.protocol_repair_allowed)
                    or (attempt and (repair is None or payload is None))):
                raise
            if attempt:
                return repair(payload, error)
            user += '\n上一次输出校验失败。重新依据原始材料输出完整 JSON。错误为 ' + str(error)[:600]


def extraction_context(sample, mode):
    return (replace(sample, catalog={key: value for key, value in sample.catalog.items() if key.startswith('A')})
            if mode == 'artifact-only' else sample)


def first_request(sample, mode):
    """The exact initial system/user pair, shared with all-input preflight."""
    if mode == 'grounded':
        from .grounded import EXTRACT, source_packet
        return EXTRACT, source_packet(sample)
    from .requirements import RequirementSample, supplement_prompt
    context = extraction_context(sample, mode)
    if isinstance(sample, RequirementSample):
        from .requirement_pipeline import EXTRACT
        return supplement_prompt(EXTRACT, sample), context.prompt_text()
    return (prompts.DECISIONS if mode == 'verified' else prompts.EXTRACT), context.prompt_text()


def repair_decisions(payload, sample):
    """Keep only trace entries whose citations match; the trace is an aid, never output as knowledge."""
    from .validation import evidence_ok

    def references(value):
        return [reference for reference in value
                if isinstance(reference, dict) and evidence_ok(reference, sample)] if isinstance(value, list) else []

    kept = []
    rows = payload.get('decisions') if isinstance(payload, dict) else None
    for decision in rows if isinstance(rows, list) else []:
        if not isinstance(decision, dict):
            continue
        evidence = references(decision.get('evidence'))
        if evidence:
            fixed = {**decision, 'evidence': evidence}
            if 'resolution_evidence' in fixed:
                fixed['resolution_evidence'] = references(fixed['resolution_evidence'])
            if 'reason' in fixed:
                try:
                    fixed['reason'] = validate_field(fixed['reason'], sample, 'decisions.reason', False)
                except ValueError:
                    fixed['reason'] = {'text': '未说明', 'evidence': []}
            kept.append(fixed)
    if not kept:
        raise ValueError('决策轨迹中没有可核对原文的记录')
    return validate_decisions({'decisions': kept}, sample)


def repaired(validator, sample, notes):
    """Build a repair hook that strips unsupported citations, records what changed, then re-validates."""
    from .validation import repair_result

    def hook(payload, error):
        fixed, changes = repair_result(payload, sample)
        if not fixed['items']:
            raise error  # nothing verifiable survives: fail loudly instead of returning an empty result
        notes.extend(['二次校验仍失败（' + str(error)[:120] + '），已保守修复：'] + changes)
        return validator(fixed)
    return hook


def validate_decisions(payload, sample):
    if not isinstance(payload, dict):
        raise ValueError('决策轨迹必须是 JSON 对象')
    decisions = payload.get('decisions')
    if not isinstance(decisions, list):
        raise ValueError('决策轨迹必须包含 decisions 数组')
    normalized = []
    for decision in decisions:
        if not isinstance(decision, dict) or decision.get('status') not in ('active', 'superseded', 'pending', 'conflict'):
            raise ValueError('决策轨迹状态无效')
        if any(not isinstance(decision.get(key), str) or not decision[key].strip() for key in ('topic', 'text')):
            raise ValueError('决策轨迹缺少 topic/text')
        evidence = validate_evidence(decision.get('evidence'), sample, 'decisions')
        if not evidence:
            raise ValueError('决策轨迹缺少证据')
        extra = {}
        if 'scope' in decision:
            if not isinstance(decision['scope'], str) or not decision['scope'].strip():
                raise ValueError('决策轨迹 scope 必须是非空字符串')
            extra['scope'] = decision['scope'].strip()
        if 'resolution_evidence' in decision:
            extra['resolution_evidence'] = validate_evidence(decision['resolution_evidence'], sample, 'decisions.resolution')
        if 'reason' in decision:
            extra['reason'] = validate_field(decision['reason'], sample, 'decisions.reason', False)
        normalized.append({**decision, 'evidence': evidence, **extra})
    return normalized


def validate_review(payload, sample):
    result = validate_result(payload, sample)
    notes = payload.get('review_notes')
    if not isinstance(notes, list) or any(not isinstance(note, str) for note in notes):
        raise ValueError('复核结果需要 review_notes 字符串数组')
    return {**result, 'review_notes': notes}


def drop_superseded(result, decisions, context):
    """Deterministically drop items whose conclusion rests only on superseded proposals.

    Requirements use evidenced, scope-aware transitions, including earlier artifacts.
    The legacy single-stage path retains its line-based proposal filter.
    """
    if context.stage == REQUIREMENT:
        from .memory import reconcile
        return reconcile(result, decisions, context)
    superseded, active = set(), set()
    for decision in decisions:
        if decision['status'] in ('superseded', 'active'):
            target = superseded if decision['status'] == 'superseded' else active
            target.update(evidence['source_id'] for evidence in decision['evidence'])
    stale = {key for key in superseded - active if not key.startswith('A')}
    kept, dropped = [], []
    for item in result['items']:
        cited = {evidence['source_id'] for evidence in item['conclusion']['evidence']}
        (dropped if cited and cited <= stale else kept).append(item)
    if not dropped:
        return result, []
    renumbered = validate_result({'items': [{key: value for key, value in item.items() if key != 'id'}
                                            for item in kept],
                                  'open_questions': result['open_questions']}, context)
    notes = [f'{item["id"]} 结论仅引用已被推翻的提议，已剔除：{item["conclusion"]["text"]}' for item in dropped]
    return {**result, **renumbered}, notes


def extract(sample: Sample, client: ModelClient, mode: str = 'verified', *, run_dir=None) -> dict:
    if mode == 'grounded':
        from .grounded import extract_grounded
        return extract_grounded(sample, client, run_dir)
    if mode not in MODES:
        raise ValueError('不支持的提取模式')
    context = extraction_context(sample, mode)
    sources = context.prompt_text()
    decisions = []
    extraction_input = sources
    repairs = []
    if mode == 'verified':
        def trace_repair(payload, error):
            fixed = repair_decisions(payload, context)
            repairs.append('决策轨迹二次校验仍失败（' + str(error)[:120] + '），已保守修复引用；未补写内容')
            return fixed
        decisions = checked_call(client, prompts.DECISIONS, sources, lambda payload: validate_decisions(payload, context),
                                 trace_repair)
        extraction_input += '\n待核对的决策轨迹 ' + json.dumps(decisions, ensure_ascii=False)
    extract_validator = lambda payload: validate_result(payload, context)
    result = checked_call(client, prompts.EXTRACT, extraction_input, extract_validator,
                          repaired(extract_validator, context, repairs))
    if mode == 'verified':
        review_input = sources + '\n候选结果 ' + json.dumps(result, ensure_ascii=False)
        review_input += '\n决策轨迹 ' + json.dumps(decisions, ensure_ascii=False)
        review_validator = lambda payload: validate_review(payload, context)

        def review_repair(payload, error):
            from .validation import repair_result
            fixed, changes = repair_result(payload, context)
            if not fixed['items']:
                raise error
            repairs.extend(['复核结果二次校验仍失败（' + str(error)[:120] + '），已保守修复：'] + changes)
            notes = payload.get('review_notes') if isinstance(payload, dict) else None
            fixed['review_notes'] = [note for note in notes if isinstance(note, str)] if isinstance(notes, list) else []
            return review_validator(fixed)
        result = checked_call(client, prompts.REVIEW, review_input, review_validator, review_repair)
        result, dropped = drop_superseded(result, decisions, context)
        result['review_notes'] = result.get('review_notes', []) + dropped
    if repairs:
        result['review_notes'] = result.get('review_notes', []) + repairs
    return {**result, 'decisions': decisions, 'sample_id': sample.sample_id, 'stage': sample.stage,
            'mode': mode, 'input_fingerprint': sample.fingerprint, 'input_file_hashes': sample.file_hashes,
            'input_documents': sample.documents(),
            'run_signature': run_signature(sample, client, mode),
            'created_at': datetime.now(timezone.utc).isoformat(), 'version': __version__, 'prompt_hash': PROMPT_HASH,
            'model': client.config.model, 'metrics': client.metrics(), 'calls': client.calls,
            'checks': {'exact_citations': 'passed', 'model_review': mode == 'verified',
                       'human_review': 'pending', 'empty_result': not bool(result['items']),
                       'citation_repairs': bool(repairs),
                       'requires_review': bool(result.get('review_queue'))}}
