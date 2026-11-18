"""Finite local judge execution with durable receipts and no evaluation inputs."""
import json
import hashlib
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .client import ModelClient, ModelError, ModelOutputError
from .evaluation import digest
from .grounded import GroundedNeedsReview, extract_grounded
from .pipeline import STAGE_MODES, extract, first_request
from .requirements import RequirementSample, discover_requirements, load_requirement_manifest, read_requirement_manifest
from .run_report import render_result_html, render_run_html, render_run_markdown
from .sources import discover_samples
from .storage import save_result, validate_saved, write_json, write_text


class RunBudgetExhausted(ModelError):
    pass


def load_run_samples(*, dataset=None, manifest=None, scope='stage', sample_id=None, requirement_id=None):
    if scope not in ('stage', 'requirement') or bool(dataset) == bool(manifest):
        raise ValueError('需要 dataset 或 manifest，且明确 scope')
    if manifest:
        if sample_id or requirement_id:
            raise ValueError('manifest 不同时接受数据集筛选编号')
        if scope == 'requirement':
            return [load_requirement_manifest(Path(manifest))]
        _, nodes = read_requirement_manifest(Path(manifest))
        return [sample for _, _, sample in nodes]
    if scope == 'requirement':
        if sample_id:
            raise ValueError('requirement scope 使用 requirement-id')
        return discover_requirements(Path(dataset), requirement_id)
    if requirement_id:
        raise ValueError('stage scope 使用 sample-id')
    return discover_samples(Path(dataset), sample_id)


def _totals(clients):
    calls = [call for client in clients for call in client.calls]
    result = {'calls': len(calls), 'http_attempts': sum(c['http_attempts'] for c in calls),
              'seconds': round(sum(c['seconds'] for c in calls), 3)}
    for name in ('prompt_tokens', 'completion_tokens'):
        values = [c[name] for c in calls]
        result[name] = sum(values) if values and all(v is not None for v in values) else None
        result['known_' + name] = sum(v for v in values if v is not None)
    result['usage_complete'] = bool(calls) and all(c['prompt_tokens'] is not None and c['completion_tokens'] is not None for c in calls)
    prices = clients[0].config if clients else None
    cost = None
    if (prices and result['usage_complete'] and prices.input_price_per_million is not None
            and prices.output_price_per_million is not None):
        cost = (result['prompt_tokens'] * prices.input_price_per_million +
                result['completion_tokens'] * prices.output_price_per_million) / 1_000_000
    result.update(estimated_cost=cost, cost_unit=prices.cost_unit if cost is not None else None)
    return result


class ReceiptClient(ModelClient):
    def __init__(self, config, path, before, notify, *, max_seconds):
        super().__init__(config)
        self.path, self.before, self.notify = path, before, notify
        self.started, self.max_seconds = time.monotonic(), max_seconds

    def complete(self, system, user, *, strict_json=False):
        self.before()
        remaining = self.max_seconds - (time.monotonic() - self.started)
        if remaining < 1:
            raise RunBudgetExhausted('样本达到总时限，停止启动请求')
        if len(system) + len(user) > self.config.max_input_chars:
            raise ModelError('输入超过 max_input_chars，尚未发送请求')
        self.headers()  # Validate environment before saving a started receipt.
        number = len(self.calls) + 1
        stem = self.path / f'{number:03d}'
        body = {'model': self.config.model, 'stream': self.config.stream, 'max_tokens': self.config.max_tokens,
                'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}]}
        if self.config.json_mode:
            body['response_format'] = {'type': 'json_object'}
        if self.config.temperature is not None:
            body['temperature'] = self.config.temperature
        if self.config.reasoning_effort is not None:
            body['reasoning_effort'] = self.config.reasoning_effort
        request = {'body': body, 'body_sha256': hashlib.sha256(json.dumps(body).encode()).hexdigest(),
                   'strict_json': True, 'config': self.config.public()}
        write_json(stem.with_suffix('.request.json'), request)
        receipt = {'number': number, 'status': 'started', 'provider_execution': 'unknown',
                   'request_digest': digest(request), 'started_at': time.time()}
        write_json(stem.with_suffix('.receipt.json'), receipt)
        self.notify()
        requested = self.config
        self.config = replace(requested, timeout=min(requested.timeout, remaining), retries=0)
        try:
            payload = super().complete(system, user, strict_json=True)
            write_json(stem.with_suffix('.response.json'), payload)
            receipt.update(status='received', provider_execution='response_received')
            return payload
        except BaseException as error:
            call = self.calls[-1] if len(self.calls) >= number else None
            unknown = bool(call and call.get('transport_status') == 'unknown')
            receipt.update(status='unknown' if unknown else 'failed',
                           provider_execution='unknown' if unknown else 'response_received' if call else 'not_sent')
            if isinstance(error, ModelOutputError):
                error.protocol_repair_allowed = False
                receipt['response_diagnostic'] = error.diagnostic
                if error.response_text is not None:
                    write_text(stem.with_suffix('.invalid-response.txt'), error.response_text)
            raise
        finally:
            self.config = requested
            receipt['finished_at'] = time.time()
            receipt['call'] = dict(self.calls[-1]) if len(self.calls) >= number else None
            write_json(stem.with_suffix('.receipt.json'), receipt, overwrite=True)
            self.notify()


def execute_run(samples, config, output, *, scope='stage', mode='grounded', max_http_attempts=32,
                max_input_tokens=500_000, max_output_tokens=100_000, max_seconds=2700):
    samples, output = list(samples), Path(output)
    if not samples or scope not in ('stage', 'requirement') or mode not in STAGE_MODES:
        raise ValueError('输入、scope 或 mode 无效')
    if any(isinstance(sample, RequirementSample) != (scope == 'requirement') for sample in samples):
        raise ValueError('输入实际范围与 scope 不一致')
    if len({s.sample_id for s in samples}) != len(samples):
        raise ValueError('样本编号重复')
    if type(max_http_attempts) is not int or not 1 <= max_http_attempts <= 32:
        raise ValueError('max_http_attempts 需要1至32')
    for value, upper in ((max_input_tokens, 500_000), (max_output_tokens, 100_000), (max_seconds, 2700)):
        if type(value) is not int or not 1 <= value <= upper:
            raise ValueError('token 和时间预算超出允许范围')
    if output.exists():
        raise ValueError('输出目录已存在；先核对原回执，禁止自动覆盖或重发')
    effective = replace(config, retries=0)
    ModelClient(effective).headers()
    for sample in samples:
        system, user = first_request(sample, mode)
        if len(system) + len(user) > config.max_input_chars:
            raise ValueError('输入预检超过 max_input_chars，未发送请求')
    from .batch_gate import implementation_signature
    output.mkdir(parents=True, exist_ok=False)
    clients, started = [], time.monotonic()
    report = {'schema_version': 1, 'version': __version__, 'status': 'running', 'input_scope': scope, 'mode': mode,
              'created_at': datetime.now(timezone.utc).isoformat(), 'model': config.model,
              'requested_config': config.public(), 'effective_config': effective.public(),
              'implementation': implementation_signature(), 'transport_policy': 'no-auto-http-retry',
              'quality': {'human_review': 'pending', 'official_score': None, 'self_grading': False},
              'limits': {'max_http_attempts': max_http_attempts, 'known_input_tokens': max_input_tokens,
                         'known_output_tokens': max_output_tokens, 'sample_seconds': max_seconds},
              'records': [{'sample_id': s.sample_id, 'stage': s.stage, 'status': 'not_started',
                           'input_fingerprint': s.fingerprint, 'input_file_hashes': s.file_hashes,
                           'nodes': list(s.nodes) if isinstance(s, RequirementSample) else []} for s in samples]}

    def persist():
        report['metrics'] = _totals(clients)
        report['succeeded'] = sum(r['status'] == 'success' for r in report['records'])
        report['failed'] = sum(r['status'] in ('failed', 'needs_review') for r in report['records'])
        report['wall_seconds'] = round(time.monotonic() - started, 3)
        write_json(output / 'report.json', report, overwrite=True)
        write_text(output / 'report.md', render_run_markdown(report), overwrite=True)
        write_text(output / 'report.html', render_run_html(report), overwrite=True)

    def before():
        totals = _totals(clients)
        if (totals['http_attempts'] >= max_http_attempts or totals['known_prompt_tokens'] >= max_input_tokens
                or totals['known_completion_tokens'] >= max_output_tokens):
            raise RunBudgetExhausted('达到调用或已知 token 预算，停止启动请求')

    persist()
    try:
        for index, (sample, row) in enumerate(zip(samples, report['records']), 1):
            folder = output / 'samples' / f'{index:03d}'
            row.update(status='running', started_at=time.time())
            sample_started = time.monotonic()
            client = ReceiptClient(effective, folder / 'calls', before, persist, max_seconds=max_seconds)
            clients.append(client)
            persist()
            stop = False
            try:
                before()
                if mode == 'grounded':
                    result = extract_grounded(sample, client, folder / 'audit')
                elif isinstance(sample, RequirementSample):
                    from .requirement_pipeline import extract_requirement
                    result = extract_requirement(sample, client, mode)
                else:
                    result = extract(sample, client, mode)
                save_result(folder / 'result.json', result, sample=sample)
                validate_saved(result, sample)
                write_text(folder / 'result.html', render_result_html(result, sample))
                row.update(status='success', items=len(result['items']), open_questions=len(result['open_questions']),
                           result_digest=result['result_digest'])
            except (ValueError, OSError, ModelError) as error:
                unknown = any(c.get('transport_status') == 'unknown' for c in client.calls)
                if unknown:
                    row['status'], report['status'], stop = 'unknown', 'unknown', True
                elif isinstance(error, RunBudgetExhausted):
                    row['status'], report['status'], stop = 'not_completed', 'budget_exhausted', True
                elif isinstance(error, GroundedNeedsReview):
                    row['status'] = 'needs_review'
                else:
                    row['status'] = 'failed'
                row['error'] = str(error)
                if any('HTTP ' + str(code) in str(error) for code in (401, 402, 403)):
                    report['stop_reason'], stop = 'authentication_or_billing', True
                if stop and report['status'] == 'running':
                    report['status'] = 'failed'
            finally:
                row['metrics'] = client.metrics()
                row['wall_seconds'] = round(time.monotonic() - sample_started, 3)
                persist()
            if stop:
                break
        if report['status'] == 'running':
            success = report['succeeded']
            report['status'] = 'completed' if success == len(samples) else 'partial' if success else 'failed'
    except BaseException:
        report['status'] = 'interrupted'
        persist()
        raise
    persist()
    return report
