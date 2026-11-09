"""Command-line entry points for extraction, replay checks and human evaluation."""

import argparse
import json
import sys
import zipfile
from pathlib import Path

from .client import Config, ModelClient, ModelError
from .evaluation import make_worksheet, score_worksheet
from .pipeline import MODES, extract
from .sources import STAGES, discover_samples, load_sample
from .storage import read_json, render_markdown, save_result, validate_saved, write_json, write_text


def parser():
    root = argparse.ArgumentParser(prog='decision-memory', description='研发阶段决策提取与证据核查')
    commands = root.add_subparsers(dest='command', required=True)
    inspect = commands.add_parser('inspect', help='离线检查官方 ZIP 或目录的阶段输入')
    inspect.add_argument('--dataset', required=True, type=Path)
    inspect.add_argument('--output', type=Path)
    single = commands.add_parser('extract', help='提取一个阶段的决策与约束')
    add_sample_arguments(single)
    add_model_arguments(single)
    single.add_argument('--output', required=True, type=Path)
    batch = commands.add_parser('batch', help='对官方格式样本批量提取')
    batch.add_argument('--dataset', required=True, type=Path)
    add_model_arguments(batch)
    batch.add_argument('--output', required=True, type=Path)
    batch.add_argument('--resume', action='store_true')
    check = commands.add_parser('validate', help='离线核对已保存结果及其原文位置')
    add_sample_arguments(check, required=False)
    check.add_argument('--dataset', type=Path)
    check.add_argument('--result', required=True, type=Path)
    review = commands.add_parser('review', help='生成人工评测表或计算已填写的评测结果')
    review_commands = review.add_subparsers(dest='review_command', required=True)
    initialize = review_commands.add_parser('init')
    initialize.add_argument('--result', required=True, type=Path)
    initialize.add_argument('--references', type=Path)
    initialize.add_argument('--output', required=True, type=Path)
    score = review_commands.add_parser('score')
    score.add_argument('--worksheet', required=True, type=Path)
    score.add_argument('--output', type=Path)
    return root


def add_sample_arguments(command, required=True):
    command.add_argument('--dialogue', required=required, type=Path)
    command.add_argument('--artifact', required=required, type=Path)
    command.add_argument('--stage', required=required, choices=STAGES)


def add_model_arguments(command):
    command.add_argument('--config', required=True, type=Path)
    command.add_argument('--mode', choices=MODES, default='verified')


def inspect_dataset(path):
    samples = discover_samples(path)
    return {'samples': [{'sample_id': sample.sample_id, 'stage': sample.stage,
                         'fingerprint': sample.fingerprint,
                         'file_hashes': sample.file_hashes,
                         'dialogue_lines': sum(key.startswith('D') for key in sample.catalog),
                         'artifact_lines': sum(key.startswith('A') for key in sample.catalog),
                         'files': sorted({source.file for source in sample.catalog.values()})}
                        for sample in samples], 'count': len(samples)}


def batch_extract(args):
    samples = discover_samples(args.dataset)
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
            summary = {'mode': args.mode, 'total': len(samples), 'processed': len(records),
                       'succeeded': sum(row['status'] != 'failed' for row in records),
                       'failed': sum(row['status'] == 'failed' for row in records),
                       'reused': sum(row['status'] == 'reused' for row in records), 'records': records}
            write_json(args.output / 'summary.json', summary, overwrite=True)
        return summary
    finally:
        lock.unlink(missing_ok=True)


def run(args):
    if args.command == 'inspect':
        value = inspect_dataset(args.dataset)
        if args.output:
            write_json(args.output, value)
        return value, 0
    if args.command == 'extract':
        if args.output.exists() or args.output.with_suffix('.md').exists() or args.output.suffix != '.json':
            raise ValueError('需要尚不存在的 .json 输出路径')
        sample = load_sample(args.dialogue, args.artifact, args.stage)
        client = ModelClient(Config.load(args.config))
        result = extract(sample, client, args.mode)
        save_result(args.output, result)
        return {'output': str(args.output), 'items': len(result['items']), 'metrics': result['metrics']}, 0
    if args.command == 'batch':
        summary = batch_extract(args)
        return summary, 2 if summary['failed'] else 0
    if args.command == 'validate':
        result = read_json(args.result)
        if args.dataset:
            matches = [sample for sample in discover_samples(args.dataset) if sample.sample_id == result.get('sample_id')]
            if len(matches) != 1:
                raise ValueError('样本包内未找到结果对应的唯一阶段')
            sample = matches[0]
        else:
            if not all((args.dialogue, args.artifact, args.stage)):
                raise ValueError('需要 --dataset 或完整的 --dialogue/--artifact/--stage')
            sample = load_sample(args.dialogue, args.artifact, args.stage)
        validate_saved(result, sample)
        return {'exact_citations': 'passed', 'items': len(result['items']), 'semantic_quality': 'requires_human_review'}, 0
    if args.review_command == 'init':
        references = read_json(args.references) if args.references else []
        worksheet = make_worksheet(read_json(args.result), references)
        write_json(args.output, worksheet)
        return {'output': str(args.output), 'pending_items': len(worksheet['items'])}, 0
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
    except (ValueError, OSError, ModelError, zipfile.BadZipFile) as error:
        print('错误 ' + str(error), file=sys.stderr)
        return 2
