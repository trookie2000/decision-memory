"""Command-line entry points for extraction, replay checks and human evaluation."""

import argparse
import json
import sys
import subprocess
import zipfile
from pathlib import Path

from .client import Config, ModelClient, ModelError
from .collaboration import BattleConfig, DocsCLI, MarioMCP, TransportError
from .evaluation import make_worksheet, score_worksheet, validate_references
from .pipeline import MODES, STAGE_MODES, extract
from .sources import BATTLE_STAGES, STAGES, discover_requirements, discover_samples, load_sample
from .storage import read_json, render_markdown, save_result, validate_saved, write_json, write_text


def parser():
    root = argparse.ArgumentParser(prog='decision-memory', description='研发阶段与完整需求的知识提取及证据核查')
    commands = root.add_subparsers(dest='command', required=True)
    inspect = commands.add_parser('inspect', help='离线检查 ZIP 或目录的需求输入')
    inspect.add_argument('--dataset', required=True, type=Path)
    inspect.add_argument('--output', type=Path)
    inspect.add_argument('--unit', choices=('stage', 'requirement'), default='requirement',
                        help='stage：每个阶段单独提取；requirement：整个需求按节点顺序合并为一份输入')
    single = commands.add_parser('extract', help='提取一个需求或独立阶段的决策与约束')
    add_sample_arguments(single, required=False)
    single.add_argument('--dataset', type=Path, help='从数据集选择需求；不读取评测标注')
    single.add_argument('--sample-id', help='--dataset 中的阶段编号；--unit requirement 时为需求目录名')
    single.add_argument('--unit', choices=('stage', 'requirement'), default='requirement',
                        help='stage：每个阶段单独提取；requirement：整个需求按节点顺序合并为一份输入')
    add_model_arguments(single, modes=STAGE_MODES)
    single.add_argument('--output', required=True, type=Path)
    judge = commands.add_parser('run', help='有限本地评委运行，一次生成 JSON/Markdown/HTML 与调用回执')
    source = judge.add_mutually_exclusive_group(required=True)
    source.add_argument('--dataset', type=Path)
    source.add_argument('--manifest', type=Path)
    judge.add_argument('--scope', choices=('stage', 'requirement'), required=True)
    judge.add_argument('--sample-id')
    judge.add_argument('--requirement-id')
    judge.add_argument('--config', required=True, type=Path)
    judge.add_argument('--mode', choices=STAGE_MODES, default='grounded')
    judge.add_argument('--output', required=True, type=Path)
    judge.add_argument('--max-http-attempts', type=int, default=32)
    batch = commands.add_parser('batch', help='对官方格式样本批量提取')
    batch.add_argument('--dataset', required=True, type=Path)
    add_model_arguments(batch)
    batch.add_argument('--output', required=True, type=Path)
    batch.add_argument('--resume', action='store_true')
    batch.add_argument('--unit', choices=('stage', 'requirement'), default='requirement',
                        help='stage：每个阶段单独提取；requirement：整个需求按节点顺序合并为一份输入')
    check = commands.add_parser('validate', help='离线核对已保存结果及其原文位置')
    add_sample_arguments(check, required=False)
    source = check.add_mutually_exclusive_group()
    source.add_argument('--dataset', type=Path)
    source.add_argument('--manifest', type=Path, help='run 使用的原始节点清单')
    check.add_argument('--result', required=True, type=Path)
    check.add_argument('--unit', choices=('stage', 'requirement'), default='requirement',
                        help='stage：每个阶段单独提取；requirement：整个需求按节点顺序合并为一份输入')
    review = commands.add_parser('review', help='生成人工评测表或计算已填写的评测结果')
    review_commands = review.add_subparsers(dest='review_command', required=True)
    initialize = review_commands.add_parser('init')
    initialize.add_argument('--result', required=True, type=Path)
    initialize.add_argument('--references', type=Path)
    initialize.add_argument('--output', required=True, type=Path)
    initialize.add_argument('--schema-version', type=int, choices=(1, 2), default=1)
    initialize.add_argument('--reference-policy', type=Path, help='v2 的 decision/uncertainty 分类策略')
    initialize.add_argument('--review-kind', choices=('human', 'agent'), default='human')
    score = review_commands.add_parser('score')
    score.add_argument('--worksheet', required=True, type=Path)
    score.add_argument('--output', type=Path)
    apply_review = review_commands.add_parser('apply', help='只导入 v2 的判定，不改写原始预测或参考')
    apply_review.add_argument('--worksheet', required=True, type=Path)
    apply_review.add_argument('--judgments', type=Path)
    apply_review.add_argument('--open-question-judgments', type=Path)
    apply_review.add_argument('--reference-judgments', type=Path)
    apply_review.add_argument('--output', required=True, type=Path)
    refs = review_commands.add_parser('check-references', help='离线核对当前阶段参考答案的 ID 和逐字引文')
    refs.add_argument('--dataset', required=True, type=Path)
    refs.add_argument('--sample-id', required=True)
    refs.add_argument('--references', required=True, type=Path)
    refs.add_argument('--output', type=Path)
    official = commands.add_parser('official', help='用官方评测标注按比赛口径自测（只用于评测，不进入提取）')
    official_commands = official.add_subparsers(dest='official_command', required=True)
    refs = official_commands.add_parser('refs', help='离线解析评测标注，输出每个需求的正向/过程参考数量')
    refs.add_argument('--dataset', required=True, type=Path)
    refs.add_argument('--output', type=Path)
    official_score = official_commands.add_parser('score', help='用评分模型给一批提取结果打分')
    official_score.add_argument('--dataset', required=True, type=Path)
    official_score.add_argument('--predictions', required=True, type=Path, help='batch 输出目录')
    official_score.add_argument('--config', required=True, type=Path, help='评分模型配置')
    official_score.add_argument('--scope', choices=('requirement', 'stage'), default='requirement')
    official_score.add_argument('--output', required=True, type=Path, help='新的评分输出目录')
    add_requirement_arguments(commands)
    add_field_arguments(commands)
    add_battle_arguments(commands)
    add_series_arguments(commands)
    return root


def add_field_arguments(commands):
    command = commands.add_parser('field', help='原文独立回答、逐字段证据修订与冻结对照')
    actions = command.add_subparsers(dest='field_command', required=True)
    verify = actions.add_parser('verify')
    source = verify.add_mutually_exclusive_group(required=True)
    source.add_argument('--dataset', type=Path)
    source.add_argument('--manifest', type=Path)
    verify.add_argument('--result', required=True, type=Path)
    verify.add_argument('--config', required=True, type=Path)
    verify.add_argument('--output', required=True, type=Path)
    frozen = actions.add_parser('freeze', help='冻结原预测、ZIP、参考、模型配置和源码，尚不调用模型')
    frozen.add_argument('--dataset', required=True, type=Path)
    frozen.add_argument('--predictions', required=True, type=Path)
    frozen.add_argument('--config', required=True, type=Path)
    frozen.add_argument('--judge-config', required=True, type=Path)
    frozen.add_argument('--output', required=True, type=Path)
    frozen.add_argument('--max-cases', type=int, default=2)
    frozen.add_argument('--max-calls', type=int, default=14)
    frozen.add_argument('--max-seconds', type=int, default=3600)
    for action in ('run', 'status'):
        leaf = actions.add_parser(action)
        leaf.add_argument('--output', required=True, type=Path)
        if action == 'run':
            leaf.add_argument('--env-file', type=Path, help='仅读取模型凭证变量的字面赋值，不执行文件内容')


def result_sample(result, *, dataset=None, manifest=None):
    from .input_contract import describe_input
    from .requirements import discover_requirements as event_requirements
    if manifest:
        from .judge_run import load_run_samples
        scope = 'requirement' if result.get('stage') == 'requirement' else 'stage'
        samples = [sample for sample in load_run_samples(manifest=manifest, scope=scope)
                   if sample.sample_id == result.get('sample_id')]
    else:
        fmt = (result.get('input_contract') or {}).get('source_format')
        if fmt == 'manifest' or (result.get('scope') == 'requirement' and fmt is None):
            samples = event_requirements(dataset, result['sample_id'], include_supplements=fmt is not None)
        else:
            samples = (discover_requirements(dataset, sample_id=result['sample_id']) if result['stage'] == 'requirement'
                       else discover_samples(dataset, sample_id=result['sample_id']))
    if len(samples) != 1:
        raise ValueError('未找到原结果对应的唯一输入')
    sample = samples[0]
    if result.get('schema_version') == 6 and result.get('input_contract') != describe_input(sample):
        raise ValueError('独立字段快照的输入契约与材料不符')
    validate_saved(result, sample)
    return sample


def run_field(args):
    from .field_verification import verify_fields
    if args.field_command == 'freeze':
        from .field_experiment import freeze
        plan = freeze(args.dataset, args.predictions, Config.load(args.config), Config.load(args.judge_config),
                      args.output, max_cases=args.max_cases, max_calls=args.max_calls, max_seconds=args.max_seconds)
        return {'status': 'frozen', 'output': str(args.output), 'cases': len(plan['cases']),
                'limits': plan['limits'], 'plan_digest': plan['plan_digest']}, 0
    if args.field_command == 'status':
        state = read_json(args.output / 'state.json')
        return {'status': state['status'], 'cases': [{key: row.get(key) for key in
            ('id', 'status', 'held_items', 'error')} for row in state.get('cases', [])]}, 0
    if args.field_command == 'run':
        from .field_experiment import check_freeze, load_credentials, run
        if args.env_file:
            plan = check_freeze(args.output)
            load_credentials(args.env_file, [Config(**plan['config']), Config(**plan['judge_config'])])
        summary = run(args.output)
        return {'output': str(args.output), 'complete_pairs': summary['complete_pairs'],
                'total_cases': summary['total_cases'], 'all_case_deltas': summary['all_case_deltas'],
                'improvement_demonstrated': summary['improvement_demonstrated'],
                'new_calls': summary['new_calls'], 'human_review': 'pending'}, 0 if summary['complete_pairs'] == summary['total_cases'] else 2
    if (args.output.suffix != '.json' or args.output.exists() or args.output.with_suffix('.md').exists()
            or args.output.with_suffix('.run').exists()):
        raise ValueError('字段结果及run目录须是尚不存在的新路径，失败或未知状态不得自动重发')
    base = read_json(args.result)
    sample = result_sample(base, dataset=args.dataset, manifest=args.manifest)
    result = verify_fields(base, sample, ModelClient(Config.load(args.config)), args.output.with_suffix('.run'))
    save_result(args.output, result, sample=sample)
    return {'output': str(args.output), 'items': len(result['items']), 'held_items': len(result['field_review_queue']),
            'verification_metrics': result['verification_metrics'], 'human_review': 'pending'}, 0


def add_requirement_arguments(commands):
    command = commands.add_parser('requirement', help='按有序节点提取完整需求，保存知识更新与失效记录')
    actions = command.add_subparsers(dest='requirement_command', required=True)
    for action in ('inspect', 'extract', 'validate', 'compare'):
        leaf = actions.add_parser(action)
        source = leaf.add_mutually_exclusive_group(required=True)
        source.add_argument('--manifest', type=Path, help='显式列出有序节点的 JSON 清单')
        source.add_argument('--dataset', type=Path, help='官方 ZIP 或目录，不读取评测标注')
        leaf.add_argument('--requirement-id', help='数据集中的需求目录名')
        if action in ('extract', 'compare'):
            add_model_arguments(leaf, modes=STAGE_MODES if action == 'extract' else MODES)
            leaf.add_argument('--output', required=True, type=Path)
            if action == 'extract':
                leaf.add_argument('--evidence-audit', choices=('v1', 'v2'), help='主动开启需求级证据准入和候选覆盖审计，仅 verified')
        elif action == 'inspect':
            leaf.add_argument('--mode', choices=STAGE_MODES, default='verified', help='检查对应模式实际使用的输入')
            leaf.add_argument('--output', type=Path)
        else:
            leaf.add_argument('--result', required=True, type=Path)


def run_requirement(args):
    from .requirements import discover_requirements as discover_manifest_requirements, load_requirement_manifest
    from .requirement_pipeline import extract_requirement
    from .input_contract import describe_input
    from .grounded import GroundedNeedsReview
    if args.manifest and args.requirement_id:
        raise ValueError('--manifest 不能同时传 --requirement-id')
    action = args.requirement_command
    audit = getattr(args, 'evidence_audit', None)
    if audit and args.mode != 'verified':
        raise ValueError('--evidence-audit 仅支持 --mode verified')
    saved = read_json(args.result) if action == 'validate' else None
    requirement_id = args.requirement_id or ((saved.get('requirement_id') or
                      (saved.get('sample_id') if saved.get('stage') == 'requirement' else None))
                      if isinstance(saved, dict) else None)
    if args.dataset and action != 'inspect' and not requirement_id:
        raise ValueError('--dataset 需要 --requirement-id')
    if action == 'extract' and (args.output.suffix != '.json' or args.output.exists()
                                or args.output.with_suffix('.md').exists()):
        raise ValueError('需要尚不存在的 .json 输出路径')
    if action == 'extract' and (args.output.with_suffix('.run').exists() or args.output.with_suffix('.audit.html').exists()):
        raise ValueError('审计 run 目录或报告已存在，请检查原回执；禁止自动重发')
    saved_contract = saved.get('input_contract') if isinstance(saved, dict) else None
    if saved_contract is not None and not isinstance(saved_contract, dict):
        raise ValueError('input_contract 必须为对象')
    saved_format = (saved_contract or {}).get('source_format')
    directory_input = (saved_format == 'directory' if action == 'validate'
                       else getattr(args, 'mode', None) == 'grounded')
    if args.manifest:
        samples = [load_requirement_manifest(args.manifest)]
    elif directory_input:
        samples = discover_requirements(args.dataset, sample_id=requirement_id)
    else:
        samples = discover_manifest_requirements(args.dataset, requirement_id,
                    include_supplements=action != 'validate' or saved_contract is not None)
    if action == 'inspect':
        summary = {'requirements': [{'requirement_id': sample.sample_id, 'nodes': describe_input(sample)['nodes'],
                                    'input_contract': describe_input(sample),
                                    'input_fingerprint': sample.fingerprint, 'file_hashes': sample.file_hashes,
                                    'source_lines': len(sample.catalog)} for sample in samples]}
        if args.output:
            write_json(args.output, summary)
        return summary, 0
    if len(samples) != 1:
        raise ValueError('需要选定唯一需求')
    sample = samples[0]
    if action == 'validate':
        validate_saved(saved, sample)
        if saved.get('schema_version') in (5, 6):
            replay = {'snapshot_replay': 'passed', 'event_replay': 'not_applicable_final_snapshot'}
        else:
            replay = {'event_replay': 'passed'}
        return {'requirement_id': sample.sample_id, 'exact_citations': 'passed', **replay,
                'material_scope': 'declared_input_contract' if saved_contract is not None else 'legacy_declared_node_pairs',
                'items': len(saved['items']), 'semantic_quality': 'requires_human_review'}, 0
    if action == 'compare':
        from .comparison import run_comparison
        from .requirements import read_requirement_manifest
        nodes = (read_requirement_manifest(args.manifest)[1] if args.manifest else [
            (node.stage, node.stage, node) for node in discover_samples(args.dataset, requirement_id=requirement_id)])
        result = run_comparison(sample, nodes, Config.load(args.config), args.mode, args.output)
        return {'output': str(args.output), 'requirement_id': sample.sample_id,
                'metrics': result['metrics'], 'human_review': 'pending'}, 0
    from .requirement_audit_run import AuditNeedsReview
    try:
        client = ModelClient(Config.load(args.config))
        if args.mode == 'grounded':
            result = extract(sample, client, args.mode, run_dir=args.output.with_suffix('.run'))
        else:
            result = extract_requirement(sample, client, args.mode,
                                         evidence_audit=audit, run_dir=args.output.with_suffix('.run') if audit else None)
    except (AuditNeedsReview, GroundedNeedsReview) as error:
        return {'audit_status': 'needs_review', 'draft': str(args.output.with_suffix('.run') / 'draft.json'),
                'message': str(error)}, 2
    save_result(args.output, result, sample=sample)
    return {'output': str(args.output), 'requirement_id': sample.sample_id, 'items': len(result['items']),
            **({'history_events': len(result['history'])} if 'history' in result else {}), 'metrics': result['metrics']}, 0


def run_official(args):
    from . import official_eval
    if args.official_command == 'refs':
        references = official_eval.load_references(args.dataset)
        summary = {name: {'positives': [ref['id'] for ref in value['positives']],
                          'negatives': [ref['id'] for ref in value['negatives']]}
                   for name, value in references.items()}
        if args.output:
            write_json(args.output, references)
        return summary, 0
    if args.output.exists() and any(args.output.iterdir()):
        raise ValueError('评分输出目录已有内容，请使用新目录')
    report = official_eval.evaluate(args.dataset, args.predictions, ModelClient(Config.load(args.config)), args.scope)
    write_json(args.output / 'report.json', report)
    write_text(args.output / 'report.md', official_eval.render_report(report))
    return {'output': str(args.output), 'total': {key: report['total'][key] for key in
            ('recall', 'recall_strict', 'precision', 'citation_support_rate', 'citation_sample',
             'projected_points')}}, 0


def add_series_arguments(commands):
    series = commands.add_parser('series', help='独立后台连续生成和评测多个 case')
    actions = series.add_subparsers(dest='series_command', required=True)
    start = actions.add_parser('start', help='启动有限批次；默认在后台执行')
    start.add_argument('--config', required=True, type=Path)
    start.add_argument('--model-config', required=True, type=Path)
    start.add_argument('--manifest', required=True, type=Path)
    start.add_argument('--output', required=True, type=Path)
    start.add_argument('--mode', choices=MODES, default='baseline')
    start.add_argument('--max-seconds', type=int, default=7200)
    start.add_argument('--foreground', action='store_true')
    for action in ('resume', 'status', 'stop', 'run'):
        command = actions.add_parser(action)
        command.add_argument('--output', required=True, type=Path)
        if action == 'resume':
            command.add_argument('--foreground', action='store_true')
            command.add_argument('--retry-extraction', action='store_true')
        if action == 'run':
            command.add_argument('--resume', action='store_true')
            command.add_argument('--retry-extraction', action='store_true')


def run_series(args):
    from .series import Series
    if args.series_command == 'start':
        series = Series.create(args.output, BattleConfig.load(args.config), args.model_config,
                               read_json(args.manifest), mode=args.mode, max_seconds=args.max_seconds)
        result = series.run() if args.foreground else series.launch()
    else:
        series = Series(args.output)
        if args.series_command == 'status':
            return series.summary(), 0
        if args.series_command == 'stop':
            return series.request_stop(), 0
        if args.series_command == 'run':
            result = series.run(resume=args.resume, retry_extraction=args.retry_extraction)
        else:
            result = (series.run(resume=True, retry_extraction=args.retry_extraction) if args.foreground
                      else series.launch(resume=True, retry_extraction=args.retry_extraction))
    return result, 0 if result['status'] == 'completed' or result.get('launch') == 'started' else 2


def add_battle_arguments(commands):
    from .coordinator import BATTLE_STAGES
    battle = commands.add_parser('battle', help='通过真实 Mario 员工出题、审题、修订与独立评测')
    actions = battle.add_subparsers(dest='battle_command', required=True)
    doctor = actions.add_parser('doctor', help='只读核对 Mario 工具/技能及可选 Docs 文档')
    doctor.add_argument('--config', required=True, type=Path)
    doctor.add_argument('--doc-id')
    start = actions.add_parser('start', help='在指定测试群启动新的协调任务')
    start.add_argument('--config', required=True, type=Path)
    start.add_argument('--output', required=True, type=Path)
    start.add_argument('--sample-id', required=True)
    start.add_argument('--stage', required=True, choices=BATTLE_STAGES,
                       help='requirement：一道题包含需求分析 + 技术设计两个环节')
    start.add_argument('--brief-file', required=True, type=Path)
    start.add_argument('--clarification-file', type=Path, help='在首次审题时才加入的合成测试条件')
    start.add_argument('--model-config', type=Path)
    start.add_argument('--mode', choices=MODES, default='baseline')
    start.add_argument('--wait-seconds', type=int, default=45)
    resume = actions.add_parser('resume', help='恢复原任务，绝不重发未知结果的消息')
    resume.add_argument('--output', required=True, type=Path)
    resume.add_argument('--wait-seconds', type=int, default=45)
    resume.add_argument('--model-config', type=Path)
    resume.add_argument('--retry-extraction', action='store_true', help='显式开始新的提取尝试；保留失败记录')
    status = actions.add_parser('status', help='只读查看持久状态')
    status.add_argument('--output', required=True, type=Path)
    audit = actions.add_parser('audit', help='完全离线重放回传、冻结输入、原始预测与评分的完整性检查')
    audit.add_argument('--output', required=True, type=Path)
    audit.add_argument('--report', type=Path)
    reconcile = actions.add_parser('reconcile', help='接回人工核对过的真实创建/派发回执，不重发')
    reconcile.add_argument('--output', required=True, type=Path)
    reconcile.add_argument('--create-receipt', type=Path)
    reconcile.add_argument('--dispatch-receipt', type=Path)
    reconcile.add_argument('--operation', help='回执所属的已有操作，例如 r01-author')
    recover = actions.add_parser('recover-delivery', help='对明确体积拒绝改用 Docs 输入；不重发未知发送')
    recover.add_argument('--output', required=True, type=Path)
    recover.add_argument('--operation', required=True)
    recover.add_argument('--reason', required=True, help='已获准恢复的原因，写入审计记录')


def run_battle(args):
    from .coordinator import Battle
    if args.battle_command == 'doctor':
        config = BattleConfig.load(args.config)
        capabilities = MarioMCP(config).discover()
        if args.doc_id:
            document = DocsCLI(config).fetch(args.doc_id)
            capabilities['document'] = {'doc_id': args.doc_id, 'characters': len(document['content']),
                                        'capture': document['capture']}
        return {'read_only': True, **capabilities}, 0
    if args.battle_command == 'start':
        battle = Battle.create(args.output, BattleConfig.load(args.config), args.sample_id, args.stage,
                               args.brief_file.read_text(encoding='utf-8'),
                               clarification=args.clarification_file.read_text(encoding='utf-8') if args.clarification_file else '',
                               model_config=args.model_config, mode=args.mode)
        summary = battle.run(args.wait_seconds)
    else:
        battle = Battle(args.output)
        if args.battle_command == 'status':
            return battle.summary(), 0
        if args.battle_command == 'recover-delivery':
            return battle.recover_rejected_dispatch(args.operation, args.reason), 0
        if args.battle_command == 'audit':
            report = battle.audit()
            if args.report:
                write_json(args.report, report)
            return report, 0
        if args.battle_command == 'reconcile':
            if not args.create_receipt and not args.dispatch_receipt:
                raise ValueError('请提供经核对的真实 create-receipt 或 dispatch-receipt')
            return battle.reconcile(create_receipt=args.create_receipt, dispatch_receipt=args.dispatch_receipt,
                                    operation=args.operation), 0
        summary = battle.run(args.wait_seconds, args.retry_extraction, args.model_config)
    code = 0 if summary['status'] == 'agent_review_complete' else (3 if summary['status'] in ('waiting', 'running') else 2)
    return summary, code


def add_sample_arguments(command, required=True):
    command.add_argument('--dialogue', required=required, type=Path)
    command.add_argument('--artifact', required=required, type=Path)
    command.add_argument('--stage', required=required, choices=STAGES)


def add_model_arguments(command, modes=MODES):
    command.add_argument('--config', required=True, type=Path)
    command.add_argument('--mode', choices=modes, default='verified')


def discover(path, unit='requirement', sample_id=None):
    return (discover_requirements if unit == 'requirement' else discover_samples)(path, sample_id=sample_id)


def inspect_dataset(path, unit='requirement'):
    from .input_contract import describe_input
    samples = discover(path, unit)
    return {'samples': [{'sample_id': sample.sample_id, 'stage': sample.stage,
                         'fingerprint': sample.fingerprint,
                         'file_hashes': sample.file_hashes,
                         'documents': sample.documents(),
                         'input_contract': describe_input(sample),
                         'dialogue_lines': sum(key.startswith('D') for key in sample.catalog),
                         'artifact_lines': sum(key.startswith('A') for key in sample.catalog),
                         'files': sorted({source.file for source in sample.catalog.values()})}
                        for sample in samples], 'count': len(samples)}


def batch_extract(args):
    samples = discover(args.dataset, args.unit)
    config = Config.load(args.config)
    if args.output.exists() and any(args.output.iterdir()) and not args.resume:
        raise ValueError('输出目录已有内容，请使用新目录或 --resume')
    args.output.mkdir(parents=True, exist_ok=True)
    lock = args.output / '.run.lock'
    try:
        stream = lock.open('x')
    except FileExistsError:
        raise ValueError('输出目录已有运行锁，请确认前次进程是否仍在运行') from None
    records = []
    try:
        with stream:
            stream.write('decision-memory batch\n')
        for sample in samples:
            client = ModelClient(config)
            path = args.output / (sample.sample_id + '.json')
            try:
                if path.exists():
                    result = validate_saved(read_json(path), sample, client, args.mode)
                    markdown = path.with_suffix('.md')
                    if markdown.exists() and markdown.read_text(encoding='utf-8') != render_markdown(result):
                        raise ValueError('Markdown 交接文件已改动，请保留修改并使用新的输出目录')
                    if not markdown.exists():
                        write_text(markdown, render_markdown(result))
                    records.append({'sample_id': sample.sample_id, 'status': 'reused', 'metrics': result['metrics']})
                else:
                    result = extract(sample, client, args.mode)
                    save_result(path, result)
                    records.append({'sample_id': sample.sample_id, 'status': 'success', 'metrics': result['metrics']})
            except (ValueError, OSError, ModelError) as error:
                records.append({'sample_id': sample.sample_id, 'status': 'failed', 'error': str(error), 'metrics': client.metrics()})
            print(f'{sample.sample_id} {records[-1]["status"]}', file=sys.stderr)
            summary = {'mode': args.mode, 'unit': args.unit, 'total': len(samples), 'processed': len(records),
                       'succeeded': sum(row['status'] != 'failed' for row in records),
                       'failed': sum(row['status'] == 'failed' for row in records),
                       'reused': sum(row['status'] == 'reused' for row in records), 'records': records}
            write_json(args.output / 'summary.json', summary, overwrite=True)
        return summary
    finally:
        lock.unlink(missing_ok=True)


def run(args):
    if args.command == 'run':
        from .judge_run import execute_run, load_run_samples
        samples = load_run_samples(dataset=args.dataset, manifest=args.manifest, scope=args.scope,
                                   sample_id=args.sample_id, requirement_id=args.requirement_id)
        report = execute_run(samples, Config.load(args.config), args.output, scope=args.scope, mode=args.mode,
                             max_http_attempts=args.max_http_attempts)
        return {'output': str(args.output), 'status': report['status'], 'succeeded': report['succeeded'],
                'failed': report['failed'], 'metrics': report['metrics'], 'quality': report['quality']}, (0 if report['status'] == 'completed' else 2)
    if args.command == 'field':
        return run_field(args)
    if args.command == 'requirement':
        return run_requirement(args)
    if args.command == 'series':
        return run_series(args)
    if args.command == 'official':
        return run_official(args)
    if args.command == 'battle':
        return run_battle(args)
    if args.command == 'inspect':
        value = inspect_dataset(args.dataset, args.unit)
        if args.output:
            write_json(args.output, value)
        return value, 0
    if args.command == 'extract':
        if args.output.exists() or args.output.with_suffix('.md').exists() or args.output.suffix != '.json':
            raise ValueError('需要尚不存在的 .json 输出路径')
        if args.dataset:
            if not args.sample_id or any((args.dialogue, args.artifact, args.stage)):
                raise ValueError('--dataset 需要 --sample-id，且不能同时传独立文件参数')
            matches = discover(args.dataset, args.unit, args.sample_id)
            if len(matches) != 1:
                raise ValueError('数据集中没有对应的唯一 sample-id')
            sample = matches[0]
        else:
            if args.sample_id or not all((args.dialogue, args.artifact, args.stage)):
                raise ValueError('需要 --dataset/--sample-id 或完整的 --dialogue/--artifact/--stage')
            sample = load_sample(args.dialogue, args.artifact, args.stage)
        client = ModelClient(Config.load(args.config))
        result = extract(sample, client, args.mode,
                         run_dir=args.output.with_suffix('.run') if args.mode == 'grounded' else None)
        save_result(args.output, result, sample=sample)
        return {'output': str(args.output), 'items': len(result['items']), 'metrics': result['metrics']}, 0
    if args.command == 'batch':
        summary = batch_extract(args)
        return summary, 2 if summary['failed'] else 0
    if args.command == 'validate':
        result = read_json(args.result)
        if not isinstance(result, dict):
            raise ValueError('结果文件必须是 JSON 对象')
        if args.manifest and any((args.dialogue, args.artifact, args.stage)):
            raise ValueError('--manifest 不能同时传独立文件参数')
        contract = result.get('input_contract')
        uses_manifest_format = isinstance(contract, dict) and contract.get('source_format') == 'manifest'
        if args.manifest or (args.dataset and uses_manifest_format):
            sample = result_sample(result, dataset=args.dataset, manifest=args.manifest)
        elif args.dataset:
            unit = ('requirement' if result.get('stage') == 'requirement' else
                    'stage' if result.get('stage') in STAGES else args.unit)
            matches = [sample for sample in discover(args.dataset, unit) if sample.sample_id == result.get('sample_id')]
            if len(matches) != 1:
                raise ValueError('样本包内未找到结果对应的唯一样本')
            sample = matches[0]
        else:
            if not all((args.dialogue, args.artifact, args.stage)):
                raise ValueError('需要 --dataset、--manifest 或完整的 --dialogue/--artifact/--stage')
            sample = load_sample(args.dialogue, args.artifact, args.stage)
        validate_saved(result, sample)
        return {'exact_citations': 'passed', 'items': len(result['items']), 'semantic_quality': 'requires_human_review'}, 0
    if args.review_command == 'check-references':
        sample = discover_samples(args.dataset, sample_id=args.sample_id)[0]
        references = validate_references(read_json(args.references), sample)
        checked = {'sample_id': sample.sample_id, 'references': len(references), 'exact_reference_quotes': 'passed',
                   'input_fingerprint': sample.fingerprint, 'semantic_quality': 'requires_human_review'}
        if args.output:
            write_json(args.output, checked)
        return checked, 0
    if args.review_command == 'init':
        references = read_json(args.references) if args.references else []
        policy = read_json(args.reference_policy) if args.reference_policy else None
        worksheet = make_worksheet(read_json(args.result), references, args.review_kind,
                                   schema_version=args.schema_version, reference_policy=policy)
        write_json(args.output, worksheet)
        return {'output': str(args.output), 'pending_items': len(worksheet['items'])}, 0
    if args.review_command == 'apply':
        from .review_v2 import apply_review_v2
        if not any((args.judgments, args.open_question_judgments, args.reference_judgments)):
            raise ValueError('至少提供一组要导入的 judgments')
        reviewed = apply_review_v2(read_json(args.worksheet),
                                   judgments=read_json(args.judgments) if args.judgments else None,
                                   open_question_judgments=read_json(args.open_question_judgments) if args.open_question_judgments else None,
                                   reference_judgments=read_json(args.reference_judgments) if args.reference_judgments else None)
        write_json(args.output, reviewed)
        return {'output': str(args.output), 'score': score_worksheet(reviewed)}, 0
    score = score_worksheet(read_json(args.worksheet))
    if args.output:
        write_json(args.output, score)
    return score, 0


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        value, code = run(args)
        print(json.dumps(value, ensure_ascii=False, indent=2))
        return code
    except (ValueError, OSError, ModelError, TransportError, subprocess.SubprocessError, zipfile.BadZipFile) as error:
        print('错误 ' + str(error), file=sys.stderr)
        return 2
