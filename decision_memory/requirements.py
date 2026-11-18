"""Ordered requirement inputs, with namespaces over unchanged original sources."""
import hashlib
import json
import re
from dataclasses import dataclass, replace
from pathlib import Path

from .sources import (MAX_FILE_BYTES, STAGES, Sample, discover_samples, index_document,
                      index_materials, list_requirement_files, read_text)

MAX_NODES = 64
MAX_REQUIREMENT_BYTES = 8_000_000
IDENTIFIER = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z')
EVENT_PROTOCOL = 'typed-material-events-v1'
USER_ATTRIBUTION = re.compile(r'(?:用户|需求方|业务方)(?:明确)?(?:要求|指定|否决|确认|决定|强调|提出|纠正)')
SUPPLEMENT_INSTRUCTIONS = '''补充材料协议扩展（仅本输入含 supplement 时适用）：
document_type 独立于发言角色。supplement 即使含用户标记或放在产物目录，也不是正式阶段确认。
其 node_id/node_order=null 表示时间未知；来源编号中的 supplement 分组仅用于定位文件，不是阶段。
无冲突的明确口径、定义、背景事实可以沉淀。未经确认的建议、与正式决定冲突的说明不能据此变成现行规则。
events 的 node_id 允许 null，且不参加已知节点的时间排序，不编造它对应最后阶段：
1. add + null：新增背景知识，完整 item 只引用补充材料；同一 knowledge_key 已存在时不能重复 add。
2. merge + null：仅增加补充证据，不改变任何字段文字、不为未说明编造解释。
3. update + null：只给现行知识原本为未说明的理由/边界补充明确解释。结论及所有已有非空字段文字不变，
   保留原字段证据；新增字段只引补充材料。仅添加证据用 merge。不能恢复失效知识。
4. invalidate + null 禁止。补充材料不能改写已确认值、已有理由或边界；也不能借用旧确认引文支持新值。
已知节点内的 add/update/invalidate/merge 仍需本节点依据，不得引用未来正式节点。
后续正式确认可以修正背景事实，但不能倒推补充文件的发生时间。不同适用范围用不同 key，不能误合并。
字段复核和审计照常执行；审计新增 basis=supplement_direct，仅用于完全依赖补充材料的背景结论，
direct 引实际 conclusion 证据，proposal/confirmation 为空。不能将其伪装为 user_direct 或 artifact_direct。
沿用当前证据格式；只返回模型协议要求的字段，不输出程序生成的 input_contract 或 event_protocol。
'''


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
        if self.catalog[source_id].document_type == 'supplement':
            return None
        node_id = source_id.split('.')[1]
        return next(index for index, node in enumerate(self.nodes) if node['id'] == node_id)

    @property
    def has_supplements(self):
        return any(source.document_type == 'supplement' for source in self.catalog.values())

    def available_at(self, source_id, order):
        source_order = self.node_order(source_id)
        return source_order is None or (order is not None and source_order <= order)

    def prompt_text(self, artifact_only=False):
        typed = self.has_supplements
        rows = []
        for key, source in self.catalog.items():
            if artifact_only and not key.startswith('A'):
                continue
            order = self.node_order(key)
            rows.append({'source_id': key, 'node_id': self.nodes[order]['id'] if order is not None else None, 'node_order': order,
                         'file': source.file, 'line': source.line, 'role': source.role,
                         'message': source.message, 'text': source.text})
            if typed:
                rows[-1]['document_type'] = source.document_type
        extra = {}
        if typed:
            from .input_contract import describe_input
            extra['documents'] = describe_input(self)['documents']
        return json.dumps({'scope': 'requirement', 'requirement_id': self.sample_id,
                           'nodes': self.nodes, 'sources': rows, **extra}, ensure_ascii=False)


def assemble_requirement(requirement_id, nodes, supplements=()):
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
    materials = []
    for index, (prefix, name, text) in enumerate(supplements, 1):
        if prefix not in ('D', 'A') or name in files:
            raise ValueError('补充文件重复或类型无效')
        size = len(text.encode('utf-8'))
        total += size
        if size > MAX_FILE_BYTES or total > MAX_REQUIREMENT_BYTES:
            raise ValueError('单份材料最多 2 MB，整个需求最多 8 MB')
        group = f'supplement-{index:03d}'
        while group in seen:
            group += '-ref'
        indexed = {}
        index_document(indexed, prefix, name, text, document_type='supplement')
        for key, source in indexed.items():
            alias = f'{key[0]}.{group}.{key[1:]}'
            catalog[alias] = replace(source, source_id=alias)
        files[name] = hashlib.sha256(text.encode('utf-8')).hexdigest()
        materials.append([prefix, name, text])
    version_input = [requirement_id, versions]
    if materials:
        version_input.append(['supplements-v1', materials])
    fingerprint = hashlib.sha256(json.dumps(version_input, ensure_ascii=False).encode()).hexdigest()
    return RequirementSample(requirement_id, 'requirement', fingerprint, catalog, files,
                             input_bytes=total, nodes=tuple(metadata))


def read_requirement_manifest(path: Path, *, include_supplements=False):
    """Read the exact node pairs for whole and per-node extraction."""
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError('需求清单超过 2 MB 上限')
    value = json.loads(path.read_text(encoding='utf-8'))
    if (not isinstance(value, dict) or set(value) - {'schema_version', 'requirement_id', 'nodes', 'supplements'}
            or not {'schema_version', 'requirement_id', 'nodes'} <= set(value)
            or type(value['schema_version']) is not int or value['schema_version'] != 1):
        raise ValueError('需求清单需要 schema_version=1、requirement_id 和 nodes，不能有其他字段')
    if not isinstance(value['nodes'], list) or not 1 <= len(value['nodes']) <= MAX_NODES:
        raise ValueError('需求需要 1～64 个节点')
    root = path.parent.resolve()
    total = 0

    def material(name):
        nonlocal total
        if not isinstance(name, str) or not name.strip():
            raise ValueError('材料路径必须为相对路径')
        relative = Path(name)
        target = (root / relative).resolve()
        if (relative.is_absolute() or '..' in relative.parts or not target.is_relative_to(root)
                or '评测标注' in relative.parts):
            raise ValueError('材料路径不能越出清单目录或读取评测标注')
        text = read_text(target)
        total += len(text.encode('utf-8'))
        if total > MAX_REQUIREMENT_BYTES:
            raise ValueError('整个需求超过 8 MB 输入上限')
        return relative.as_posix(), text

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
    names = value.get('supplements', [])
    if not isinstance(names, list) or any(not isinstance(name, str) for name in names) or len(names) != len(set(names)):
        raise ValueError('supplements 必须为不重复的相对路径数组')
    used = {name for _, _, sample in nodes for name in sample.file_hashes}
    supplements = []
    for name in names:
        name, text = material(name)
        if name in used:
            raise ValueError('补充文件不能重复节点中的文件')
        used.add(name)
        supplements.append(('A' if '产物' in Path(name).parts else 'D', name, text))
    if include_supplements:
        return value['requirement_id'], nodes, supplements
    if supplements:
        raise ValueError('清单包含补充材料；请使用完整需求提取。逐节点对照尚未定义无节点材料的公平分配')
    return value['requirement_id'], nodes


def load_requirement_manifest(path: Path) -> RequirementSample:
    requirement_id, nodes, supplements = read_requirement_manifest(path, include_supplements=True)
    return assemble_requirement(requirement_id, nodes, supplements)


def discover_requirements(path: Path, requirement_id: str | None = None, *, include_supplements=True) -> list[RequirementSample]:
    if requirement_id is not None:
        identifier(requirement_id, 'requirement_id')
    grouped = {}
    for sample in discover_samples(path, requirement_id=requirement_id):
        name = sample.sample_id.removesuffix('-' + sample.stage)
        grouped.setdefault(name, []).append((sample.stage, sample.stage, sample))
    order = {stage: index for index, stage in enumerate(STAGES)}
    extras = {name: [] for name in grouped}
    if include_supplements:
        known = {name: {file for _, _, sample in nodes for file in sample.file_hashes}
                 for name, nodes in grouped.items()}
        for entry in list_requirement_files(path, lambda name: name in grouped):
            name = entry['requirement']
            if entry['name'] not in known[name]:
                extras[name].append(('D' if entry['folder'] == '上下文' else 'A', entry['name'], entry['read']()))
    return [assemble_requirement(name, sorted(nodes, key=lambda node: order[node[1]]), extras[name])
            for name, nodes in sorted(grouped.items())]


def primary_source(sample, source_id):
    source = sample.catalog[source_id]
    return source.document_type == 'artifact' or (source.document_type == 'dialogue' and source.role == 'user')


def resolves_source(sample, new_id, old_id):
    new, old = sample.node_order(new_id), sample.node_order(old_id)
    return new is not None and (new > old if old is not None else primary_source(sample, new_id))


def supplement_prompt(system, sample):
    return system + '\n' + SUPPLEMENT_INSTRUCTIONS if sample.has_supplements else system


def supplement_hash(base_hash, sample):
    return hashlib.sha256((base_hash + SUPPLEMENT_INSTRUCTIONS).encode()).hexdigest() if sample.has_supplements else base_hash
