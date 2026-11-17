"""All-case input/config/code freeze, checked even on direct Battle resume."""
import hashlib
from pathlib import Path

from .client import Config
from .evaluation import digest
from .requirement_protocol import reference_policy, validate_requirement_references
from .requirements import assemble_requirement, read_requirement_manifest
from .storage import read_json, result_digest, write_json

IMPLEMENTATION_FILES = ('__init__.py', 'batch_gate.py', 'battle_protocol.py', 'cli.py', 'client.py', 'collaboration.py',
                        'comparison.py', 'coordinator.py', 'evaluation.py', 'pipeline.py', 'requirement_flow.py',
                        'prompts.py', 'requirement_memory.py', 'requirement_grounding.py', 'requirement_pipeline.py', 'requirement_protocol.py',
                        'requirements.py', 'review_v2.py', 'series.py', 'sources.py', 'storage.py', 'task_delivery.py', 'validation.py')
IMPLEMENTATION_FILES += ('evidence_spans.py', 'requirement_audit.py', 'requirement_audit_run.py',
                         'requirement_audit_report.py')
IMPLEMENTATION_FILES += ('grounded.py', 'judge_run.py', 'run_report.py', 'field_grounding.py', 'evidence_ids.py', 'source_hints.py', 'context_sources.py')
IMPLEMENTATION_FILES += ('grounded_atoms.py',)


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def implementation_signature():
    root = Path(__file__).parent
    return {name: file_hash(root / name) for name in IMPLEMENTATION_FILES}


def _packet(root, state):
    if state.get('implementation') != implementation_signature():
        raise ValueError('批次实现或提示词已改变，请使用新批次，不能改写固定实验')
    if file_hash(root / 'manifest.json') != state['manifest_digest']:
        raise ValueError('批次清单已改变')
    config = Config.load(Path(state['model_config']))
    if file_hash(state['model_config']) != state['model_config_digest']:
        raise ValueError('批次模型配置已改变')
    cases = {}
    for case in state['cases']:
        sid = case['sample_id']
        folder = root / 'cases' / sid
        current = read_json(folder / 'state.json')
        if (current.get('state_digest') != digest({k: v for k, v in current.items() if k != 'state_digest'})
                or current.get('frozen') is not True or current.get('stage') != 'requirement'):
            raise ValueError('批次中仍有尚未冻结的需求')
        frozen = read_json(folder / 'frozen.json')
        requirement_id, nodes = read_requirement_manifest(folder / 'inputs' / sid / 'manifest.json')
        sample = assemble_requirement(requirement_id, nodes)
        references = read_json(folder / 'scoring/references.json')
        validate_requirement_references(references, sample)
        policy = read_json(folder / 'scoring/reference-policy.json')
        review = read_json(folder / f'operations/r{current["round"]:02d}-reviewer/result.json')
        if (sample.sample_id != sid or frozen['input_fingerprint'] != sample.fingerprint
                or frozen['file_hashes'] != sample.file_hashes or frozen['references_digest'] != digest(references)
                or policy != reference_policy(references) or frozen['reference_policy_digest'] != digest(policy)
                or review['metadata']['status'] != 'passed' or frozen['task_id'] != current['task_id']
                or frozen['round'] != current['round']):
            raise ValueError('批次冻结的需求、审题或参考策略不一致')
        cases[sid] = {'input_fingerprint': sample.fingerprint, 'frozen_digest': digest(frozen),
                      'references_digest': digest(references), 'reference_policy_digest': digest(policy)}
    return {'kind': 'requirement-batch-freeze', 'schema_version': 1,
            'case_ids': [case['sample_id'] for case in state['cases']], 'cases': cases,
            'manifest_digest': state['manifest_digest'], 'mode': state['mode'],
            'model_config_digest': state['model_config_digest'], 'model_config': config.public(),
            'implementation': state['implementation']}


def freeze_batch(root, state):
    root = Path(root)
    value = _packet(root, state)
    value['result_digest'] = result_digest(value)
    path = root / 'frozen-batch.json'
    if path.exists():
        if read_json(path) != value:
            raise ValueError('已有整批冻结记录与当前输入不一致')
    else:
        write_json(path, value)
    return value


def verify_batch_gate(battle):
    if not battle.state.get('batch_root'):
        return
    root = Path(battle.state['batch_root']).resolve()
    if battle.root != (root / 'cases' / battle.state['sample_id']).resolve():
        raise ValueError('需求不属于声明的批次路径')
    if not (root / 'frozen-batch.json').exists():
        raise ValueError('批次尚未全部冻结，不能提取单个需求')
    state = read_json(root / 'series-state.json')
    if state.get('state_digest') != digest({k: v for k, v in state.items() if k != 'state_digest'}):
        raise ValueError('批次状态摘要不匹配')
    frozen = read_json(root / 'frozen-batch.json')
    packet = _packet(root, state)
    packet['result_digest'] = result_digest(packet)
    if (packet != frozen or battle.state['sample_id'] not in frozen['case_ids']
            or battle.state['mode'] != frozen['mode']
            or file_hash(battle.state['model_config']) != frozen['model_config_digest']):
        raise ValueError('批次冻结、提取模式或模型配置不匹配')
