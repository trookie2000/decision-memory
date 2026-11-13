"""Index original lines without mixing stages or evaluation annotations."""

import hashlib
import json
import re
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath

MAX_FILE_BYTES = 2_000_000
STAGES = {
    'analysis': ('需求确认记录.md', '需求分析.md'),
    'design': ('技术设计确认记录.md', '技术方案.md'),
}
ROLE = re.compile(r'^\s*\*\*(?:AGENT|用户|assistant|user)\s*[:：]?\*\*', re.I)


@dataclass(frozen=True)
class Source:
    source_id: str
    file: str
    line: int
    message: int | None
    text: str


@dataclass
class Sample:
    sample_id: str
    stage: str
    fingerprint: str
    catalog: dict[str, Source]
    file_hashes: dict[str, str]

    def prompt_text(self, artifact_only: bool = False) -> str:
        sources = [asdict(source) for source in self.catalog.values()
                   if not artifact_only or source.source_id.startswith('A')]
        return json.dumps({'stage': self.stage, 'sources': sources}, ensure_ascii=False)


def make_sample(dialogue: str, artifact: str, stage: str, sample_id: str = 'sample',
                dialogue_name: str = 'dialogue.md', artifact_name: str = 'artifact.md') -> Sample:
    if stage not in STAGES:
        raise ValueError('stage 必须为 analysis 或 design')
    if not dialogue.strip() or not artifact.strip():
        raise ValueError('对话与最终产物均不能为空')
    if any(len(text.encode('utf-8')) > MAX_FILE_BYTES for text in (dialogue, artifact)):
        raise ValueError('单份材料超过 2 MB 输入上限')
    catalog = {}
    for prefix, name, text in (('D', dialogue_name, dialogue), ('A', artifact_name, artifact)):
        message = 0
        for number, line in enumerate(text.removeprefix('\ufeff').splitlines(), 1):
            if prefix == 'D' and ROLE.match(line):
                message += 1
            if line.strip():
                source_id = f'{prefix}{number:05d}'
                catalog[source_id] = Source(source_id, name, number, message or None, line)
    fingerprint = hashlib.sha256(json.dumps(
        [stage, dialogue_name, dialogue, artifact_name, artifact], ensure_ascii=False
    ).encode()).hexdigest()
    file_hashes = {dialogue_name: hashlib.sha256(dialogue.encode('utf-8')).hexdigest(),
                  artifact_name: hashlib.sha256(artifact.encode('utf-8')).hexdigest()}
    return Sample(sample_id, stage, fingerprint, catalog, file_hashes)


def read_text(path: Path) -> str:
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError(f'文件超过 2 MB 上限 {path.name}')
    return path.read_bytes().decode('utf-8')


def load_sample(dialogue: Path, artifact: Path, stage: str) -> Sample:
    return make_sample(read_text(dialogue), read_text(artifact), stage,
                       dialogue.parent.name + '-' + stage, str(dialogue), str(artifact))


def discover_samples(path: Path, sample_id: str | None = None) -> list[Sample]:
    """Read recognized stage pairs from a ZIP or directory, never annotation contents."""
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

        samples = []
        for name in sorted(names):
            parts = PurePosixPath(name).parts
            if len(parts) < 3 or parts[-2] != '上下文' or '评测标注' in parts:
                continue
            for stage, (dialogue_name, artifact_name) in STAGES.items():
                if parts[-1] != dialogue_name:
                    continue
                parent = PurePosixPath(name).parent.parent
                candidate_id = parent.name + '-' + stage
                if sample_id is not None and candidate_id != sample_id:
                    continue
                artifact = (parent / '产物' / artifact_name).as_posix()
                if artifact not in names:
                    raise ValueError(f'缺少阶段最终产物 {artifact}')
                samples.append(make_sample(read(name), read(artifact), stage, candidate_id, name, artifact))
        if not samples:
            raise ValueError('未找到官方格式的阶段样本')
        if len({item.sample_id for item in samples}) != len(samples):
            raise ValueError('样本编号重复，请使用不同需求目录名')
        return sorted(samples, key=lambda sample: sample.sample_id)
    finally:
        if archive:
            archive.close()
