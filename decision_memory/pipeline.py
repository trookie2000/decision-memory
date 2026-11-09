"""Extraction, optional decision reconstruction, and evidence review."""

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone

from . import __version__, prompts
from .client import ModelClient
from .sources import Sample
from .validation import validate_evidence, validate_result

MODES = ('baseline', 'verified', 'artifact-only')
PROMPT_HASH = hashlib.sha256((prompts.EXTRACT + prompts.DECISIONS + prompts.REVIEW).encode()).hexdigest()


def run_signature(sample: Sample, client: ModelClient, mode: str) -> str:
    values = [sample.fingerprint, client.config.public(), mode, PROMPT_HASH, __version__]
    return hashlib.sha256(json.dumps(values, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def checked_call(client, system, user, validator):
    for attempt in range(2):
        try:
            return validator(client.complete(system, user))
        except ValueError as error:
            if attempt:
                raise
            user += '\n上一次输出校验失败。重新依据原始材料输出完整 JSON。错误为 ' + str(error)[:600]


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


def extract(sample: Sample, client: ModelClient, mode: str = 'verified') -> dict:
    if mode not in MODES:
        raise ValueError('不支持的提取模式')
    context = sample
    if mode == 'artifact-only':
        context = replace(sample, catalog={key: value for key, value in sample.catalog.items() if key.startswith('A')})
    sources = context.prompt_text()
    decisions = []
    extraction_input = sources
    if mode == 'verified':
        decisions = checked_call(client, prompts.DECISIONS, sources, lambda payload: validate_decisions(payload, context))
        extraction_input += '\n待核对的决策轨迹 ' + json.dumps(decisions, ensure_ascii=False)
    result = checked_call(client, prompts.EXTRACT, extraction_input, lambda payload: validate_result(payload, context))
    if mode == 'verified':
        review_input = sources + '\n候选结果 ' + json.dumps(result, ensure_ascii=False)
        result = checked_call(client, prompts.REVIEW, review_input, lambda payload: validate_review(payload, context))
    return {**result, 'decisions': decisions, 'sample_id': sample.sample_id, 'stage': sample.stage,
            'mode': mode, 'input_fingerprint': sample.fingerprint, 'input_file_hashes': sample.file_hashes,
            'run_signature': run_signature(sample, client, mode),
            'created_at': datetime.now(timezone.utc).isoformat(), 'version': __version__, 'prompt_hash': PROMPT_HASH,
            'model': client.config.model, 'metrics': client.metrics(), 'calls': client.calls,
            'checks': {'exact_citations': 'passed', 'model_review': mode == 'verified',
                       'human_review': 'pending', 'empty_result': not bool(result['items'])}}
