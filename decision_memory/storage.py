"""Atomic outputs and replay checks for interrupted or repeated CLI runs."""

import json
import os
import tempfile
from dataclasses import replace
from pathlib import Path

from .evaluation import digest
from .pipeline import run_signature, validate_decisions
from .validation import validate_result


def write_text(path: Path, content: str, overwrite: bool = False):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if overwrite:
            os.replace(temporary, path)
        else:
            os.link(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def write_json(path: Path, value, overwrite: bool = False):
    write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + '\n', overwrite)


def read_json(path: Path):
    if path.stat().st_size > 16_000_000:
        raise ValueError('JSON 文件超过 16 MB 上限')
    return json.loads(path.read_text(encoding='utf-8'))


def result_digest(result):
    return digest({key: value for key, value in result.items() if key != 'result_digest'})


def validate_saved(result, sample, client=None, mode=None):
    if not isinstance(result, dict) or result.get('input_fingerprint') != sample.fingerprint:
        raise ValueError('输入版本不匹配，请使用新的输出目录运行')
    if result.get('input_file_hashes') != sample.file_hashes:
        raise ValueError('原始文件哈希不匹配，请使用新的输出目录运行')
    if result.get('result_digest') != result_digest(result):
        raise ValueError('结果文件已改动或缺少完整性校验')
    if client and result.get('run_signature') != run_signature(sample, client, mode):
        raise ValueError('运行配置或提示词已改变，请使用新的输出目录运行')
    context = sample
    if result.get('mode') == 'artifact-only':
        context = replace(sample, catalog={key: value for key, value in sample.catalog.items() if key.startswith('A')})
    validated = validate_result(result, context)
    if result.get('items') != validated['items'] or result.get('open_questions') != validated['open_questions']:
        raise ValueError('结果的来源位置或编号与原文不一致')
    validate_decisions({'decisions': result.get('decisions')}, context)
    return result


def render_markdown(result):
    lines = [f'# 阶段交接 {result["sample_id"]}', '',
             f'模式 `{result["mode"]}`。模型 `{result["model"]}`。', '',
             '引用已通过程序逐字核对。条目语义和覆盖范围仍需人工评审。', '']
    for item in result['items']:
        lines.extend([f'## {item["id"]}', ''])
        for key, title in (('conclusion', '结论'), ('reason', '理由'), ('boundary', '适用边界')):
            field = item[key]
            lines.extend([f'**{title}**', '', field['text'], ''])
            for evidence in field['evidence']:
                lines.extend([f'- `{evidence["source_id"]}` · {evidence["file"]} 第 {evidence["line"]} 行',
                              f'  > {evidence["quote"]}', ''])
    if result['open_questions']:
        lines.extend(['## 待确认', ''])
        for question in result['open_questions']:
            lines.append('- ' + question['text'])
            for evidence in question['evidence']:
                lines.append(f'  - `{evidence["source_id"]}` · {evidence["file"]} 第 {evidence["line"]} 行 · {evidence["quote"]}')
        lines.append('')
    lines.extend(['## 运行记录', '', '```json', json.dumps(result['metrics'], ensure_ascii=False, indent=2), '```', ''])
    return '\n'.join(lines)


def save_result(path: Path, result):
    if path.suffix != '.json':
        raise ValueError('提取结果输出文件须使用 .json 扩展名')
    markdown = path.with_suffix('.md')
    if path.exists() or markdown.exists():
        raise ValueError('输出已存在，请指定新的输出文件或使用 batch --resume')
    result['result_digest'] = result_digest(result)
    write_json(path, result)
    write_text(markdown, render_markdown(result))
