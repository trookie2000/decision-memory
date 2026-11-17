"""Extraction, optional decision reconstruction, and evidence review."""

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone

from . import __version__, prompts
from .client import ModelClient, ModelOutputError
from .sources import Sample
from .validation import validate_evidence, validate_result

MODES = ('baseline', 'verified', 'artifact-only')
STAGE_MODES = (*MODES, 'grounded')
PROMPT_HASH = hashlib.sha256((prompts.EXTRACT + prompts.DECISIONS + prompts.REVIEW).encode()).hexdigest()


def run_signature(sample: Sample, client: ModelClient, mode: str) -> str:
    values = [sample.fingerprint, client.config.public(), mode, PROMPT_HASH, __version__]
    return hashlib.sha256(json.dumps(values, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def checked_call(client, system, user, validator):
    for attempt in range(2):
        try:
            return validator(client.complete(system, user))
        except ValueError as error:
            if attempt or (isinstance(error, ModelOutputError) and not error.protocol_repair_allowed):
                raise
            user += '\n上一次输出校验失败。重新依据原始材料输出完整 JSON。错误为 ' + str(error)[:600]


def extraction_context(sample, mode):
    return (replace(sample, catalog={key: value for key, value in sample.catalog.items() if key.startswith('A')})
            if mode == 'artifact-only' else sample)


def first_request(sample, mode):
    """The exact initial system/user pair, shared with all-input preflight."""
    if mode == 'grounded':
        from .grounded import EXTRACT, source_packet
        return EXTRACT, source_packet(sample)
    from .requirements import RequirementSample
    context = extraction_context(sample, mode)
    if isinstance(sample, RequirementSample):
        from .requirement_pipeline import EXTRACT
        return EXTRACT, context.prompt_text()
    return (prompts.DECISIONS if mode == 'verified' else prompts.EXTRACT), context.prompt_text()


def validate_decisions(payload, sample):
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
        normalized.append({**decision, 'evidence': evidence})
    return normalized


def validate_review(payload, sample):
    result = validate_result(payload, sample)
    notes = payload.get('review_notes')
    if not isinstance(notes, list) or any(not isinstance(note, str) for note in notes):
        raise ValueError('复核结果需要 review_notes 字符串数组')
    return {**result, 'review_notes': notes}


def drop_superseded(result, decisions, context):
    """Deterministically drop items whose conclusion rests only on superseded proposals.

    A line counts as superseded-only when a superseded decision cites it and no active
    decision cites it. Artifact lines are final by definition and never superseded-only.
    """
    superseded, active = set(), set()
    for decision in decisions:
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
    if mode == 'verified':
        decisions = checked_call(client, prompts.DECISIONS, sources, lambda payload: validate_decisions(payload, context))
        extraction_input += '\n待核对的决策轨迹 ' + json.dumps(decisions, ensure_ascii=False)
    result = checked_call(client, prompts.EXTRACT, extraction_input, lambda payload: validate_result(payload, context))
    if mode == 'verified':
        review_input = sources + '\n候选结果 ' + json.dumps(result, ensure_ascii=False)
        review_input += '\n决策轨迹 ' + json.dumps(decisions, ensure_ascii=False)
        result = checked_call(client, prompts.REVIEW, review_input, lambda payload: validate_review(payload, context))
        result, dropped = drop_superseded(result, decisions, context)
        result['review_notes'] = result.get('review_notes', []) + dropped
    return {**result, 'decisions': decisions, 'sample_id': sample.sample_id, 'stage': sample.stage,
            'mode': mode, 'input_fingerprint': sample.fingerprint, 'input_file_hashes': sample.file_hashes,
            'run_signature': run_signature(sample, client, mode),
            'created_at': datetime.now(timezone.utc).isoformat(), 'version': __version__, 'prompt_hash': PROMPT_HASH,
            'model': client.config.model, 'metrics': client.metrics(), 'calls': client.calls,
            'checks': {'exact_citations': 'passed', 'model_review': mode == 'verified',
                       'human_review': 'pending', 'empty_result': not bool(result['items'])}}
