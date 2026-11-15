"""Ordered requirement inputs, with namespaces over unchanged original sources."""
import hashlib
import json
import re
from dataclasses import dataclass, replace
from pathlib import Path

from .sources import MAX_FILE_BYTES, STAGES, Sample, discover_samples, index_materials, read_text

MAX_NODES = 64
MAX_REQUIREMENT_BYTES = 8_000_000
IDENTIFIER = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z')


def identifier(value, label):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError(label + ' 需要字母、数字、下划线或连字符组成的 1～128 位编号')
    return value


@dataclass
class RequirementSample(Sample):
    nodes: tuple = ()

    def node_order(self, source_id):
        if source_id not in self.catalog:
            raise ValueError('来源编号不存在')
        node_id = source_id.split('.')[1]
        return next(index for index, node in enumerate(self.nodes) if node['id'] == node_id)

    def prompt_text(self, artifact_only=False):
        rows = []
        for key, source in self.catalog.items():
            if artifact_only and not key.startswith('A'):
                continue
            order = self.node_order(key)
            rows.append({'source_id': key, 'node_id': self.nodes[order]['id'], 'node_order': order,
                         'file': source.file, 'line': source.line, 'role': source.role,
                         'message': source.message, 'text': source.text})
        return json.dumps({'scope': 'requirement', 'requirement_id': self.sample_id,
                           'nodes': self.nodes, 'sources': rows}, ensure_ascii=False)


def assemble_requirement(requirement_id, nodes):
    identifier(requirement_id, 'requirement_id')
    if not 1 <= len(nodes) <= MAX_NODES:
        raise ValueError('需求需要 1～64 个节点')
    catalog, files, metadata, versions = {}, {}, [], []
    seen, total = set(), 0
    for order, (node_id, stage, sample) in enumerate(nodes):
        identifier(node_id, 'node.id')
        if node_id in seen:
            raise ValueError('节点 ID 重复')
        if not isinstance(stage, str) or not stage.strip() or len(stage) > 64 or any(c in stage for c in '\r\n'):
            raise ValueError('节点 stage 需要 1～64 位非空标签')
        seen.add(node_id)
        total += sample.input_bytes
        if total > MAX_REQUIREMENT_BYTES:
            raise ValueError('整个需求超过 8 MB 输入上限')
        for key, source in sample.catalog.items():
            alias = f'{key[0]}.{node_id}.{key[1:]}'
            catalog[alias] = replace(source, source_id=alias)
        for name, file_hash in sample.file_hashes.items():
            if name in files and files[name] != file_hash:
                raise ValueError('不同节点的同一文件名对应了不同内容，请使用不同快照路径')
            files[name] = file_hash
        metadata.append({'id': node_id, 'stage': stage, 'order': order, 'files': list(sample.file_hashes)})
        versions.append([node_id, stage, sample.fingerprint])
    fingerprint = hashlib.sha256(json.dumps([requirement_id, versions], ensure_ascii=False).encode()).hexdigest()
    return RequirementSample(requirement_id, 'requirement', fingerprint, catalog, files, total, tuple(metadata))


def load_requirement_manifest(path: Path) -> RequirementSample:
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError('需求清单超过 2 MB 上限')
    value = json.loads(path.read_text(encoding='utf-8'))
    if (not isinstance(value, dict) or set(value) != {'schema_version', 'requirement_id', 'nodes'}
            or type(value['schema_version']) is not int or value['schema_version'] != 1):
        raise ValueError('需求清单需要 schema_version=1、requirement_id 和 nodes，不能有其他字段')
    if not isinstance(value['nodes'], list) or not 1 <= len(value['nodes']) <= MAX_NODES:
        raise ValueError('需求需要 1～64 个节点')
    root = path.parent.resolve()

    def material(name):
        if not isinstance(name, str) or not name.strip():
            raise ValueError('材料路径必须为相对路径')
        relative = Path(name)
        target = (root / relative).resolve()
        if (relative.is_absolute() or '..' in relative.parts or not target.is_relative_to(root)
                or '评测标注' in relative.parts):
            raise ValueError('材料路径不能越出清单目录或读取评测标注')
        return relative.as_posix(), read_text(target)

    nodes = []
    for node in value['nodes']:
        if not isinstance(node, dict) or set(node) != {'id', 'stage', 'dialogue', 'artifact'}:
            raise ValueError('节点需要 id、stage、dialogue 和 artifact，不能有其他字段')
        identifier(node['id'], 'node.id')
        dialogue_name, dialogue = material(node['dialogue'])
        artifact_name, artifact = material(node['artifact'])
        if dialogue_name == artifact_name:
            raise ValueError('对话和产物必须是不同文件')
        sample = index_materials(dialogue, artifact, node['stage'], node['id'], dialogue_name, artifact_name)
        nodes.append((node['id'], node['stage'], sample))
    return assemble_requirement(value['requirement_id'], nodes)


def discover_requirements(path: Path, requirement_id: str | None = None) -> list[RequirementSample]:
    if requirement_id is not None:
        identifier(requirement_id, 'requirement_id')
    grouped = {}
    for sample in discover_samples(path, requirement_id=requirement_id):
        name = sample.sample_id.removesuffix('-' + sample.stage)
        grouped.setdefault(name, []).append((sample.stage, sample.stage, sample))
    order = {stage: index for index, stage in enumerate(STAGES)}
    return [assemble_requirement(name, sorted(nodes, key=lambda node: order[node[1]]))
            for name, nodes in sorted(grouped.items())]
