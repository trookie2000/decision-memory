"""Index original lines without mixing stages or evaluation annotations."""

import hashlib
import json
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

MAX_FILE_BYTES = 2_000_000
STAGES = {
    'analysis': ('需求确认记录.md', '需求分析.md'),
    'design': ('技术设计确认记录.md', '技术方案.md'),
}
# Requirement-level input: nodes are processed in this order (需求分析 → 技术设计).
NODE_ORDER = ('analysis', 'design')
NODE_LABELS = {'analysis': '需求分析', 'design': '技术设计'}
REQUIREMENT = 'requirement'
BATTLE_STAGES = ('analysis', 'design', REQUIREMENT)
ROLE = re.compile(r'^\s*\*\*(AGENT|用户|assistant|user)\s*[:：]?\*\*', re.I)
ROLE_NAMES = {'agent': 'agent', 'assistant': 'agent', '用户': 'user', 'user': 'user'}
DOCUMENT_TYPES = ('dialogue', 'artifact', 'supplement')


@dataclass(frozen=True)
class Source:
    source_id: str
    file: str
    line: int
    message: int | None
    text: str
    role: str | None = None
    node: int | None = None
    document_type: str = 'dialogue'


@dataclass
class Sample:
    sample_id: str
    stage: str
    fingerprint: str
    catalog: dict[str, Source]
    file_hashes: dict[str, str]
    node_stages: dict[int, str] = field(default_factory=dict)

    def documents(self, sources=None) -> list[dict]:
        """Describe provenance without assigning supplementary files a place in time."""
        documents = {}
        for source in self.catalog.values() if sources is None else sources:
            documents.setdefault(source.file, {
                'file': source.file, 'document_type': source.document_type,
                'node': source.node,
                'stage': self.node_stages.get(source.node) if source.node is not None else None,
            })
        return list(documents.values())

    def prompt_text(self, artifact_only: bool = False) -> str:
        # role tells the model who said a line: user confirmation vs. agent proposal vs. final artifact.
        selected = [source for source in self.catalog.values()
                    if not artifact_only or source.source_id.startswith('A')]
        if self.stage == REQUIREMENT:
            nodes = {}
            for source in selected:
                if source.node is None:
                    continue
                node = nodes.setdefault(source.node, {'node': source.node, 'stage': self.node_stages[source.node]})
                node[source.source_id[:1]] = source.file
            sources = [{'source_id': source.source_id, 'node': source.node, 'role': source.role,
                        'document_type': source.document_type,
                        'message': source.message, 'text': source.text} for source in selected]
            return json.dumps({'stage': REQUIREMENT, 'nodes': [nodes[key] for key in sorted(nodes)],
                               'documents': self.documents(selected),
                               'sources': sources}, ensure_ascii=False)
        files = {prefix: source.file for source in self.catalog.values() for prefix in source.source_id[:1]}
        sources = [{'source_id': source.source_id, 'role': source.role,
                    'document_type': source.document_type, 'message': source.message,
                    'text': source.text} for source in selected]
        return json.dumps({'stage': self.stage, 'files': {'D': files.get('D'), 'A': files.get('A')},
                           'sources': sources}, ensure_ascii=False)


def make_sample(dialogue: str, artifact: str, stage: str, sample_id: str = 'sample',
                dialogue_name: str = 'dialogue.md', artifact_name: str = 'artifact.md') -> Sample:
    if stage not in STAGES:
        raise ValueError('stage 必须为 analysis 或 design')
    if not dialogue.strip() or not artifact.strip():
        raise ValueError('对话与最终产物均不能为空')
    if any(len(text.encode('utf-8')) > MAX_FILE_BYTES for text in (dialogue, artifact)):
        raise ValueError('单份材料超过 2 MB 输入上限')
    catalog = {}
    index_pair(catalog, dialogue_name, dialogue, artifact_name, artifact)
    fingerprint = hashlib.sha256(json.dumps(
        [stage, dialogue_name, dialogue, artifact_name, artifact], ensure_ascii=False
    ).encode()).hexdigest()
    file_hashes = {dialogue_name: hashlib.sha256(dialogue.encode('utf-8')).hexdigest(),
                  artifact_name: hashlib.sha256(artifact.encode('utf-8')).hexdigest()}
    return Sample(sample_id, stage, fingerprint, catalog, file_hashes)


def index_pair(catalog, dialogue_name, dialogue, artifact_name, artifact, node=None):
    """Number every non-empty line; dialogue lines inherit the speaker of their message."""
    index_document(catalog, 'D', dialogue_name, dialogue, node, default_role=None, document_type='dialogue')
    index_document(catalog, 'A', artifact_name, artifact, node, document_type='artifact')


def index_document(catalog, prefix, name, text, node=None, default_role='material', document_type=None):
    """Index one file. Artifact lines are 'artifact'; context lines inherit their message's speaker.

    A context file without any speaker marker (e.g. a data-definition note) is supplementary
    material: its lines get default_role ('material' for requirement inputs).
    """
    document_type = document_type or ('artifact' if prefix == 'A' else 'dialogue')
    if document_type not in DOCUMENT_TYPES:
        raise ValueError('未知材料类型：' + str(document_type))
    lines = text.removeprefix('\ufeff').splitlines()
    has_speakers = prefix == 'D' and any(ROLE.match(line) for line in lines)
    message, role = 0, ('artifact' if document_type == 'artifact' else
                        ('material' if document_type == 'supplement' else
                         (None if has_speakers else default_role)))
    tag = prefix if node is None else f'{prefix}{node}-'
    for number, line in enumerate(lines, 1):
        if has_speakers and (match := ROLE.match(line)):
            message += 1
            role = ROLE_NAMES[match[1].lower()]
        if line.strip():
            source_id = f'{tag}{number:05d}'
            if source_id in catalog:
                raise ValueError(f'同一节点下存在多份{"上下文" if prefix == "D" else "产物"}文件：{name}')
            # The numeric part of source_id identifies a source group, NOT chronology.
            # Keep it stable for archived references; only known stages receive a node.
            catalog[source_id] = Source(source_id, name, number, message or None, line, role,
                                        None if document_type == 'supplement' else node, document_type)


def make_requirement_sample(sample_id: str, nodes: list[tuple[str, str, str, str, str]]) -> Sample:
    """One sample for a whole requirement: nodes = [(stage, dialogue_name, dialogue, artifact_name, artifact)]."""
    if not nodes:
        raise ValueError('需求至少需要一个节点')
    documents = []
    for number, (stage, dialogue_name, dialogue, artifact_name, artifact) in enumerate(nodes, 1):
        if not dialogue.strip() or not artifact.strip():
            raise ValueError(f'节点 {number} 的对话与产物均不能为空')
        documents += [{'kind': 'D', 'node': number, 'label': NODE_LABELS.get(stage, stage), 'name': dialogue_name, 'text': dialogue},
                      {'kind': 'A', 'node': number, 'label': NODE_LABELS.get(stage, stage), 'name': artifact_name, 'text': artifact}]
    return requirement_from_documents(sample_id, documents)


def requirement_from_documents(sample_id: str, documents: list[dict]) -> Sample:
    """Build a requirement sample from ordered documents.

    Each document has kind D/A (its folder), a stable source-group number, label, name,
    text, and optional document_type. Supplementary files have no chronological node,
    including files in 产物/. Their source IDs still use their stable group number.
    """
    if not documents:
        raise ValueError('需求至少需要一份材料')
    catalog, node_stages, file_hashes = {}, {}, {}
    for document in documents:
        text = document['text']
        if len(text.encode('utf-8')) > MAX_FILE_BYTES:
            raise ValueError('单份材料超过 2 MB 输入上限')
        document_type = document.get('document_type') or (
            'supplement' if document['label'].startswith('补充材料：') else
            ('artifact' if document['kind'] == 'A' else 'dialogue'))
        index_document(catalog, document['kind'], document['name'], text, document['node'],
                       document_type=document_type)
        if document_type != 'supplement':
            node_stages.setdefault(document['node'], document['label'])
        file_hashes[document['name']] = hashlib.sha256(text.encode('utf-8')).hexdigest()
    if not catalog:
        raise ValueError('需求材料均为空')
    fingerprint = hashlib.sha256(json.dumps(
        [REQUIREMENT, [[d['kind'], d['node'], d['label'], d['name'], d['text']] for d in documents]],
        ensure_ascii=False).encode()).hexdigest()
    return Sample(sample_id, REQUIREMENT, fingerprint, catalog, file_hashes, node_stages)


def read_text(path: Path) -> str:
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError(f'文件超过 2 MB 上限 {path.name}')
    return path.read_bytes().decode('utf-8')


def load_sample(dialogue: Path, artifact: Path, stage: str) -> Sample:
    return make_sample(read_text(dialogue), read_text(artifact), stage,
                       dialogue.parent.name + '-' + stage, str(dialogue), str(artifact))


def discover_samples(path: Path, sample_id: str | None = None) -> list[Sample]:
    """Read recognized stage pairs from a ZIP or directory, never annotation contents."""
    samples = [make_sample(pair['dialogue'], pair['artifact'], pair['stage'], pair['requirement'] + '-' + pair['stage'],
                           pair['dialogue_name'], pair['artifact_name'])
               for pair in discover_pairs(path, lambda requirement, stage:
                                          sample_id is None or requirement + '-' + stage == sample_id)]
    if not samples:
        raise ValueError('未找到官方格式的阶段样本')
    if len({item.sample_id for item in samples}) != len(samples):
        raise ValueError('样本编号重复，请使用不同需求目录名')
    return sorted(samples, key=lambda sample: sample.sample_id)


def discover_requirements(path: Path, sample_id: str | None = None) -> list[Sample]:
    """One sample per requirement directory, reading every Markdown file under 上下文/ and 产物/.

    The folder structure is fixed but the files are not: besides the known stage pairs
    (需求分析 → 技术设计, in that order) a requirement may carry extra files such as a data
    definition note. Only known stages become ordered nodes. Other files receive stable
    source-group IDs and document_type=supplement, with no inferred chronology.
    """
    known = {}
    for stage in NODE_ORDER:
        dialogue_name, artifact_name = STAGES[stage]
        known[('上下文', dialogue_name)] = stage
        known[('产物', artifact_name)] = stage
    grouped = {}
    for entry in list_requirement_files(path, lambda requirement: sample_id is None or requirement == sample_id):
        grouped.setdefault(entry['requirement'], []).append(entry)
    samples = []
    for requirement, entries in sorted(grouped.items()):
        stages = [stage for stage in NODE_ORDER
                  if any(known.get((entry['folder'], entry['file'])) == stage for entry in entries)]
        numbers = {stage: index for index, stage in enumerate(stages, 1)}
        documents, extra = [], len(stages)
        for entry in sorted(entries, key=lambda e: (known.get((e['folder'], e['file'])) is None,
                                                    NODE_ORDER.index(known[(e['folder'], e['file'])])
                                                    if (e['folder'], e['file']) in known else 0,
                                                    e['folder'] != '上下文', e['name'])):
            stage = known.get((entry['folder'], entry['file']))
            if stage is not None:
                node, label = numbers[stage], NODE_LABELS[stage]
            else:
                extra += 1
                node, label = extra, f'补充材料：{PurePosixPath(entry["file"]).stem}'
            document_type = ('dialogue' if entry['folder'] == '上下文' else 'artifact') if stage else 'supplement'
            documents.append({'kind': 'D' if entry['folder'] == '上下文' else 'A', 'node': node, 'label': label,
                              'document_type': document_type,
                              'name': entry['name'], 'text': entry['read']()})
        samples.append(requirement_from_documents(requirement, documents))
    if not samples:
        raise ValueError('未找到官方格式的需求样本')
    return samples


def list_requirement_files(path: Path, selected=lambda requirement: True) -> list[dict]:
    """List Markdown files under <需求>/上下文/ and <需求>/产物/; never the annotation directory.

    Each entry carries a lazy reader so unselected requirements are never read.
    """
    archive = None if not path.is_file() else zipfile.ZipFile(path)
    try:
        if archive:
            names = [name for name in archive.namelist() if not name.endswith('/')]

            def reader(name):
                def read():
                    with zipfile.ZipFile(path) as again:
                        if again.getinfo(name).file_size > MAX_FILE_BYTES:
                            raise ValueError('ZIP 材料超过 2 MB 上限')
                        return again.read(name).decode('utf-8')
                return read
        else:
            if not path.is_dir():
                raise ValueError('样本路径不存在')
            names = [item.relative_to(path).as_posix() for item in path.rglob('*.md')]

            def reader(name):
                def read():
                    target = (path / name).resolve()
                    if not target.is_relative_to(path.resolve()):
                        raise ValueError('样本路径超出输入目录')
                    return read_text(target)
                return read
        entries = []
        for name in sorted(names):
            parts = PurePosixPath(name).parts
            if not name.endswith('.md') or '评测标注' in parts:
                continue
            folder_index = next((i for i, part in enumerate(parts) if part in ('上下文', '产物')), None)
            if folder_index is None or folder_index == 0:
                continue
            requirement = parts[folder_index - 1]
            if not selected(requirement):
                continue
            entries.append({'requirement': requirement, 'folder': parts[folder_index],
                            'file': '/'.join(parts[folder_index + 1:]), 'name': name, 'read': reader(name)})
        return entries
    finally:
        if archive:
            archive.close()


def discover_pairs(path: Path, selected=lambda requirement, stage: True) -> list[dict]:
    """Collect (dialogue, artifact) stage pairs; unselected pairs and the annotation directory are never read."""
    archive = None
    try:
        if path.is_file():
            archive = zipfile.ZipFile(path)
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise ValueError('ZIP 包含重复路径')

            def read(name):
                if archive.getinfo(name).file_size > MAX_FILE_BYTES:
                    raise ValueError('ZIP 材料超过 2 MB 上限')
                return archive.read(name).decode('utf-8')
        else:
            if not path.is_dir():
                raise ValueError('样本路径不存在')
            names = [item.relative_to(path).as_posix() for item in path.rglob('*.md')]

            def read(name):
                target = (path / name).resolve()
                if not target.is_relative_to(path.resolve()):
                    raise ValueError('样本路径超出输入目录')
                return read_text(target)

        pairs = []
        for name in sorted(names):
            parts = PurePosixPath(name).parts
            if len(parts) < 3 or parts[-2] != '上下文' or '评测标注' in parts:
                continue
            for stage, (dialogue_name, artifact_name) in STAGES.items():
                if parts[-1] != dialogue_name:
                    continue
                parent = PurePosixPath(name).parent.parent
                if not selected(parent.name, stage):
                    continue
                artifact = (parent / '产物' / artifact_name).as_posix()
                if artifact not in names:
                    raise ValueError(f'缺少阶段最终产物 {artifact}')
                pairs.append({'requirement': parent.name, 'stage': stage, 'dialogue_name': name,
                              'dialogue': read(name), 'artifact_name': artifact, 'artifact': read(artifact)})
        return pairs
    finally:
        if archive:
            archive.close()
