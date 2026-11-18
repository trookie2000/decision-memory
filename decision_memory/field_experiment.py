"""Bounded paired attribution experiment over frozen, already-published predictions."""
import hashlib
import json
import secrets
import shlex
import shutil
import time
from dataclasses import replace
from pathlib import Path

from . import __version__, official_eval
from .client import Config, ModelClient, ModelError, ModelOutputError
from .comparison import aggregate_metrics
from .evaluation import digest
from .field_verification import PROMPT_HASH, verify_fields
from .storage import read_json, save_result, validate_saved, write_json, write_text


def load_credentials(path, configs):
    """Read literal env assignments only; never source arbitrary shell startup files."""
    import os
    allowed = {name for config in configs for name in (config.api_key_env, *config.headers_env.values()) if name}
    if path.stat().st_size > 1000000:
        raise ValueError('环境配置文件超过读取上限')
    for line in path.read_text().splitlines():
        line = line.strip().removeprefix('export ')
        name, separator, raw = line.partition('=')
        if not separator or name.strip() not in allowed:
            continue
        values = shlex.split(raw, comments=True)
        if len(values) != 1 or any(char in values[0] for char in ('\n', '\r', '$', '`')):
            raise ValueError('模型凭证变量须为字面赋值；不执行命令或变量展开')
        os.environ[name.strip()] = values[0]


def implementation():
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(Path(__file__).parent.glob('*.py'))}


def file_digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def freeze(dataset, predictions, config, judge_config, output, *, max_cases=2, max_calls=14, max_seconds=3600):
    if (not dataset.is_file() or dataset.suffix != '.zip' or not 1 <= max_cases <= 5
            or not 1 <= max_calls <= 40 or not 60 <= max_seconds <= 7200):
        raise ValueError('冻结实验需要ZIP、1至5例、最多40次调用和60至7200秒预算')
    if output.exists():
        raise ValueError('冻结实验目录已存在，不能覆盖')
    base = official_eval.load_predictions(predictions)
    if any(row.get('schema_version') == 6 or row.get('stage') != 'requirement' for row in base):
        raise ValueError('对照只接受原始完整需求预测，不混合派生或阶段结果')
    if any(row.get('mode') == 'artifact-only' for row in base):
        raise ValueError('仅产物预测没有读取完整原文，不能声称与全原文字段验证是同材料对照')
    base = sorted(base, key=lambda row: row['sample_id'])[:max_cases]
    _, _, bindings = official_eval.validated_inputs(dataset, base, 'requirement')
    references = official_eval.load_references(dataset)
    if any(row['sample_id'] not in references for row in base):
        raise ValueError('所选预测缺少官方固定参考')
    output.mkdir(parents=True)
    shutil.copyfile(dataset, output / 'dataset.zip')
    files = {'dataset.zip': file_digest(output / 'dataset.zip')}
    for index, row in enumerate(base, 1):
        name = f'base/{index:02d}.json'
        write_json(output / name, row)
        files[name] = file_digest(output / name)
    write_json(output / 'references.json', {row['sample_id']: references[row['sample_id']] for row in base})
    files['references.json'] = file_digest(output / 'references.json')
    plan = {'kind': 'frozen-field-evidence-comparison-v1', 'version': __version__,
            'baseline': 'frozen_existing_prediction_not_new_extraction',
            'sample_class': 'previously_used_public_regression_not_unseen_holdout',
            'cases': [{'id': row['sample_id'], 'base': f'base/{index:02d}.json',
                       'labels': dict(zip(('A', 'B'), ('base', 'verified') if secrets.randbits(1)
                                          else ('verified', 'base')))} for index, row in enumerate(base, 1)],
            'files': files, 'prediction_bindings': bindings,
            'config': replace(config, retries=0).public(), 'judge_config': replace(judge_config, retries=0).public(),
            'implementation': implementation(), 'verifier_prompt_hash': PROMPT_HASH,
            'judge_prompt_hash': digest(official_eval.JUDGE),
            'limits': {'max_calls': max_calls, 'max_seconds': max_seconds,
                       'max_prompt_tokens': 400000, 'max_completion_tokens': 70000},
            'human_review': 'pending', 'official_score': None}
    plan['plan_digest'] = digest(plan)
    write_json(output / 'experiment.json', plan)
    write_json(output / 'state.json', {'status': 'frozen', 'plan_digest': plan['plan_digest']})
    return plan


def check_freeze(output):
    plan = read_json(output / 'experiment.json')
    if (plan.get('plan_digest') != digest({key: value for key, value in plan.items() if key != 'plan_digest'})
            or plan.get('implementation') != implementation()
            or any(file_digest(output / name) != checksum for name, checksum in plan['files'].items())):
        raise ValueError('冻结输入、参考、预测、配置或实现已改变，禁止沿用本实验运行')
    return plan


class Budget:
    def __init__(self, output, limits):
        self.output, self.limits, self.started, self.calls = output, limits, time.monotonic(), []

    def persist(self):
        write_json(self.output / 'calls.json', {'calls': self.calls,
            'wall_seconds': round(time.monotonic() - self.started, 3)}, overwrite=True)

    def check(self):
        if len(self.calls) >= self.limits['max_calls'] or time.monotonic() - self.started >= self.limits['max_seconds']:
            raise ModelError('冻结实验调用次数或总时限达到上限，停止启动新请求')
        for key in ('prompt_tokens', 'completion_tokens'):
            if sum((row.get('metrics') or {}).get(key) or 0 for row in self.calls) >= self.limits['max_' + key]:
                raise ModelError('冻结实验token启动阈值已达到上限，保留在途结果并停止新请求')


class RecordedClient(ModelClient):
    def __init__(self, config, budget, task):
        super().__init__(replace(config, retries=0))
        self.budget, self.task = budget, task

    def complete(self, system, user, *, strict_json=False):
        self.budget.check()
        number = len(self.budget.calls) + 1
        stem = f'raw/{number:03d}'
        entry = {'number': number, 'task': self.task, 'status': 'started', 'provider_execution': 'unknown',
                 'request': stem + '-request.json', 'response': None}
        self.budget.calls.append(entry)
        write_json(self.budget.output / entry['request'], {'system': system, 'user': user})
        self.budget.persist()
        try:
            result = super().complete(system, user, strict_json=True)
            entry.update(status='completed', provider_execution='response_received', response=stem + '-response.json')
            write_json(self.budget.output / entry['response'], result)
            return result
        except BaseException as error:
            call = self.calls[-1] if self.calls else {}
            entry['status'] = 'unknown' if call.get('transport_status') == 'unknown' else 'failed'
            entry['provider_execution'] = ('response_received' if call.get('transport_status') in ('received', 'http_error')
                                           else 'unknown' if call else 'not_sent')
            if isinstance(error, ModelOutputError) and error.response_text is not None:
                entry['invalid_response'] = stem + '-invalid.txt'
                write_text(self.budget.output / entry['invalid_response'], error.response_text)
            raise
        finally:
            entry['metrics'] = {key: self.calls[-1].get(key) for key in (
                'prompt_tokens', 'completion_tokens', 'http_attempts', 'seconds', 'status', 'transport_status')} if self.calls else {}
            known = all(entry['metrics'].get(key) is not None for key in ('prompt_tokens', 'completion_tokens'))
            priced = known and self.config.input_price_per_million is not None and self.config.output_price_per_million is not None
            cost = ((entry['metrics']['prompt_tokens'] * self.config.input_price_per_million
                     + entry['metrics']['completion_tokens'] * self.config.output_price_per_million) / 1000000) if priced else None
            entry['metrics'].update(calls=1, estimated_cost=cost,
                                    cost_unit=self.config.cost_unit if priced else None, usage_complete=known)
            self.budget.persist()


def summarize(plan, cases, calls):
    complete = [case for case in cases if case.get('status') == 'complete']
    stats = {}
    for method in ('base', 'verified'):
        judgments = [case['judgments'][method] for case in complete]
        stats[method] = official_eval.metrics(judgments) if judgments else None
    deltas = None
    if len(complete) == len(plan['cases']) and stats['base'] and stats['verified']:
        keys = ('recall', 'precision', 'citation_support_rate')
        if all(stats[m][key] is not None for m in stats for key in keys):
            deltas = {key: stats['verified'][key] - stats['base'][key] for key in keys}
    return {'kind': plan['kind'], 'plan_digest': plan['plan_digest'], 'cases': cases,
            'total_cases': len(plan['cases']), 'complete_pairs': len(complete), 'paired_metrics': stats,
            'all_case_deltas': deltas, 'improvement_demonstrated': bool(deltas and
                all(value >= 0 for value in deltas.values()) and any(value > 0 for value in deltas.values())),
            'interpretation': 'small_previously_used_public_regression_not_generalization_or_official_score',
            'new_calls': len(calls), 'execution_metrics': aggregate_metrics([row['metrics'] for row in calls]),
            'human_review': 'pending', 'official_score': None}


def render_report(summary):
    lines = ['# 冻结字段证据对照', '', '比较同一份真实历史预测在字段验证前后的变化；不是新版本重新抽取或未知留出集。',
             '', f'完整配对 {summary["complete_pairs"]}/{summary["total_cases"]}；新增请求 {summary["new_calls"]} 次。', '',
             '| 方法 | 完整覆盖率 | 准确率 | 全字段引用支持率 | 条目数 |', '|---|---:|---:|---:|---:|']
    def pct(value):
        return '—' if value is None else f'{value:.1%}'
    for method, value in summary['paired_metrics'].items():
        if value:
            lines.append(f'| {method} | {pct(value["recall"])} | {pct(value["precision"])} | '
                         f'{pct(value["citation_support_rate"])} | {value["items"]} |')
    lines.extend(['', '含待复核条目的派生结果和失败任务不算完整发布配对；仍计入案例总数与成本。',
                  '全部案例完成前不计算整批提分。主覆盖仅统计完整且引用受支持的预测，删除内容不能直接算提分。',
                  '', '## 每例完成情况', ''])
    for case in summary['cases']:
        lines.append(f'- {case["id"]}：{case["status"]}；待复核 {case.get("held_items", "—")} 条；'
                     f'原条目 {case.get("base_items", "—")} → 发布 {case.get("verified_items", "—")}；'
                     f'理由退回未知 {case.get("abstained_reasons", "—")}；{case.get("error", "")}')
    lines.extend(['', '自动判断显示同批指标改善：' + str(summary['improvement_demonstrated']),
                  '本地DeepSeek/模型判卷，人工复核待完成；official_score=null。', '',
                  '新增运行用量（含失败）：', '```json',
                  json.dumps(summary['execution_metrics'], ensure_ascii=False, indent=2), '```', ''])
    return '\n'.join(lines)


def run(output):
    from .cli import result_sample
    plan = check_freeze(output)
    state = read_json(output / 'state.json')
    if state != {'status': 'frozen', 'plan_digest': plan['plan_digest']}:
        raise ValueError('本实验已启动，检查原回执；失败、未知或完成状态均不自动重发')
    config, judge = Config(**plan['config']), Config(**plan['judge_config'])
    # Check credentials before changing the one-shot state; never print values.
    ModelClient(config).headers()
    ModelClient(judge).headers()
    state.update(status='running', started_at=time.time(), cases=[])
    write_json(output / 'state.json', state, overwrite=True)
    budget = Budget(output, plan['limits'])
    references, cases = read_json(output / 'references.json'), state['cases']
    for index, task in enumerate(plan['cases'], 1):
        case = {'id': task['id'], 'status': 'started', 'judgments': {}}
        cases.append(case)
        write_json(output / 'state.json', state, overwrite=True)
        root = output / 'cases' / f'{index:02d}'
        base = read_json(output / task['base'])
        try:
            check_freeze(output)
            sample = result_sample(base, dataset=output / 'dataset.zip')
            client = RecordedClient(config, budget, f'{index:02d}-verify')
            final = verify_fields(base, sample, client, root / 'verification.run')
            save_result(root / 'verified.json', final, sample=sample)
            case.update(base_items=len(base['items']), verified_items=len(final['items']),
                        held_items=len(final['field_review_queue']),
                        abstained_reasons=sum(row['action'] == 'abstain' for row in final['verification']['field_checks']))
            _, sources, _ = official_eval.validated_inputs(output / 'dataset.zip', [base], 'requirement')
            for label, method in task['labels'].items():
                value = base if method == 'base' else final
                validate_saved(value, sample)
                # The judge sees only item texts/evidence, original sources and fixed references.
                # It receives no method, schema, prior field proof, costs, or label mapping.
                view = {'sample_id': sample.sample_id, 'stage': 'requirement',
                        'items': value['items'], 'open_questions': value['open_questions']}
                judge_client = RecordedClient(judge, budget, f'{index:02d}-judge-{label}')
                judgment = official_eval.judge_requirement(judge_client, references[sample.sample_id],
                                                          [view], source_materials=sources[sample.sample_id])
                write_json(root / ('judgment-' + label + '.json'), judgment)
                case['judgments'][method] = judgment
            case['status'] = 'complete' if not case['held_items'] else 'partial'
        except (ValueError, OSError, ModelError) as error:
            case.update(status='failed', error=str(error)[:600])
        write_json(output / 'state.json', state, overwrite=True)
    summary = summarize(plan, cases, budget.calls)
    write_json(output / 'summary.json', summary)
    write_text(output / 'report.md', render_report(summary))
    state.update(status='completed' if summary['complete_pairs'] == len(plan['cases']) else 'incomplete')
    write_json(output / 'state.json', state, overwrite=True)
    return summary
