"""Immutable whole-versus-per-node predictions over identical original materials."""
import copy
import hashlib
import secrets
from pathlib import Path

from . import __version__
from .client import Config, ModelClient
from .evaluation import digest
from .pipeline import MODES, PROMPT_HASH as STAGE_PROMPT, extract
from .requirement_pipeline import PROMPT_HASH as WHOLE_PROMPT, extract_requirement
from .requirements import assemble_requirement
from .storage import read_json, result_digest, save_result, validate_saved, write_json, write_text
from .validation import validate_evidence


def aggregate_metrics(values):
    result = {}
    for key in ('calls', 'http_attempts', 'seconds', 'prompt_tokens', 'completion_tokens', 'estimated_cost'):
        numbers = [value.get(key) for value in values]
        result[key] = sum(numbers) if all(number is not None for number in numbers) else None
    result['seconds'] = round(result['seconds'], 3) if result['seconds'] is not None else None
    units = {value.get('cost_unit') for value in values}
    result['cost_unit'] = next(iter(units)) if len(units) == 1 else None
    result['usage_complete'] = all(value.get('usage_complete') for value in values)
    return result


def scoring_view(result):
    return {key: copy.deepcopy(result[key]) for key in ('sample_id', 'items', 'open_questions')}


def verify_scoring_views(payload, expected_digest):
    prediction = payload.get('prediction', {})
    variants = prediction.get('variants') if isinstance(prediction, dict) else None
    if payload.get('prediction_digest') != expected_digest or not isinstance(variants, dict) or set(variants) != {'A', 'B'}:
        raise ValueError('评分任务与固定比较包或匿名视图不匹配')
    for view in variants.values():
        if (not isinstance(view, dict) or set(view) != {'sample_id', 'items', 'open_questions', 'view_digest'}
                or view['view_digest'] != digest({key: value for key, value in view.items() if key != 'view_digest'})):
            raise ValueError('匿名评分视图摘要不匹配')


def node_union(sample, raw_nodes):
    """Only IDs/source locations change. Duplicates and obsolete content survive."""
    items, questions, mapping = [], [], []

    def field(value, node_id):
        refs = [{'source_id': ref['source_id'][0] + '.' + node_id + '.' + ref['source_id'][1:],
                 'quote': ref['quote']} for ref in value['evidence']]
        return {'text': value['text'], 'evidence': validate_evidence(refs, sample, '逐节点拼接')}

    for node_id, raw in raw_nodes:
        for item in raw['items']:
            new_id = f'K{len(items) + 1:03d}'
            row = {'id': new_id, **{name: field(item[name], node_id)
                                    for name in ('conclusion', 'reason', 'boundary')}}
            if 'kind' in item:
                row['kind'] = item['kind']
            items.append(row)
            mapping.append({'target': 'item', 'id': new_id, 'node_id': node_id, 'original_id': item['id']})
        for index, question in enumerate(raw['open_questions'], 1):
            questions.append(field(question, node_id))
            mapping.append({'target': 'open_question', 'id': f'Q{len(questions):03d}',
                            'node_id': node_id, 'original_index': index})
    return {'sample_id': sample.sample_id, 'items': items, 'open_questions': questions}, mapping


def _file_digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _raw_results(sample, nodes, root, config=None):
    whole = read_json(root / 'whole.json')
    validate_saved(whole, sample, ModelClient(config) if config else None)
    raw_nodes = []
    for node_id, _, node_sample in nodes:
        raw = read_json(root / 'nodes' / (node_id + '.json'))
        validate_saved(raw, node_sample, ModelClient(config) if config else None, 'baseline')
        if raw.get('mode') != 'baseline':
            raise ValueError('逐节点对照必须使用 baseline 原始结果')
        raw_nodes.append((node_id, raw))
    return whole, raw_nodes


def run_comparison(sample, nodes, config, mode, output):
    if mode not in MODES:
        raise ValueError('整需求比较模式无效')
    output = Path(output)
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError('比较输出目录已有内容；未知或失败调用不能在原目录自动重跑')
    if [node[0] for node in nodes] != [node['id'] for node in sample.nodes]:
        raise ValueError('比较节点与需求顺序不一致')
    if assemble_requirement(sample.sample_id, nodes).fingerprint != sample.fingerprint:
        raise ValueError('逐节点输入版本与整需求输入不一致，尚未调用模型')
    output.mkdir(parents=True, exist_ok=True)
    methods = ('whole', 'per_node') if secrets.randbits(1) else ('per_node', 'whole')
    labels = dict(zip(('A', 'B'), methods))
    job = {'kind': 'requirement-comparison', 'requirement_id': sample.sample_id,
           'input_fingerprint': sample.fingerprint, 'input_file_hashes': sample.file_hashes,
           'mode': mode, 'config': config.public(), 'labels': labels, 'version': __version__,
           'prompt_hashes': {'whole': WHOLE_PROMPT, 'per_node': STAGE_PROMPT}}
    write_json(output / 'job.json', job)
    # Write-ahead job + new output guard forbid accidental repeat after partial failure.
    whole = extract_requirement(sample, ModelClient(config), mode)
    save_result(output / 'whole.json', whole)
    raw_nodes = []
    for node_id, _, node_sample in nodes:
        raw = extract(node_sample, ModelClient(config), 'baseline')
        save_result(output / 'nodes' / (node_id + '.json'), raw)
        raw_nodes.append((node_id, raw))
    union, mapping = node_union(sample, raw_nodes)
    views = {'whole': scoring_view(whole), 'per_node': union}
    raw_names = ['whole.json', *['nodes/' + node[0] + '.json' for node in nodes]]
    bundle = {**job, 'schema_version': 1,
              'job_digest': digest(job), 'prediction': {'sample_id': sample.sample_id,
                  'variants': {label: views[method] for label, method in labels.items()}},
              'view_digests': {label: digest(views[method]) for label, method in labels.items()},
              'mapping': mapping, 'raw_files': {name: _file_digest(output / name) for name in raw_names},
              'metrics': {'whole': whole['metrics'], 'per_node': aggregate_metrics([raw['metrics'] for _, raw in raw_nodes])},
              'human_review': 'pending', 'official_score': None}
    bundle['result_digest'] = result_digest(bundle)
    write_json(output / 'prediction.json', bundle)
    write_text(output / 'comparison.md', '# 同材料提取比较\n\n'
               'whole 为整需求提取；per_node 为旧算法逐节点提取后原样拼接，仅重编 ID 与来源。\n\n'
               '原始结果：whole.json 与 nodes/。预测已保存，尚未进行语义评分。\n')
    return bundle


def validate_comparison(bundle, sample, nodes, root, config=None):
    root = Path(root)
    if (not isinstance(bundle, dict) or bundle.get('kind') != 'requirement-comparison'
            or bundle.get('schema_version') != 1 or bundle.get('requirement_id') != sample.sample_id
            or bundle.get('input_fingerprint') != sample.fingerprint
            or bundle.get('input_file_hashes') != sample.file_hashes
            or bundle.get('result_digest') != result_digest(bundle)):
        raise ValueError('比较包身份、输入或保存摘要不匹配')
    job = read_json(root / 'job.json')
    if bundle.get('job_digest') != digest(job) or any(bundle.get(key) != value for key, value in job.items()):
        raise ValueError('比较包与调用前的固定任务不一致')
    if config and job['config'] != config.public():
        raise ValueError('比较模型配置已经改变')
    labels = bundle.get('labels')
    if (not isinstance(labels, dict) or set(labels) != {'A', 'B'}
            or sorted(labels.values()) != ['per_node', 'whole']):
        raise ValueError('匿名方法标签不完整或重复')
    expected_names = ['whole.json', *['nodes/' + node[0] + '.json' for node in nodes]]
    actual_files = {name: _file_digest(root / name) for name in expected_names}
    if bundle.get('raw_files') != actual_files:
        raise ValueError('比较使用的原始预测文件已改动')
    whole, raw_nodes = _raw_results(sample, nodes, root, config)
    if whole.get('mode') != job['mode']:
        raise ValueError('整需求提取模式与固定任务不一致')
    union, mapping = node_union(sample, raw_nodes)
    views = {'whole': scoring_view(whole), 'per_node': union}
    expected = {'sample_id': sample.sample_id, 'variants': {label: views[method] for label, method in labels.items()}}
    if (bundle.get('prediction') != expected or bundle.get('mapping') != mapping
            or bundle.get('view_digests') != {label: digest(view) for label, view in expected['variants'].items()}
            or bundle.get('metrics') != {'whole': whole['metrics'],
                                          'per_node': aggregate_metrics([raw['metrics'] for _, raw in raw_nodes])}):
        raise ValueError('评分视图、映射或用量与原始结果不一致')
    return bundle
