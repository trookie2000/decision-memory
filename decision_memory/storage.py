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
    if isinstance(result, dict) and (result.get('schema_version') == 6
                                    or result.get('extraction_protocol') == 'independent-fields-v1'):
        from .field_verification import validate_verified_saved
        return validate_verified_saved(result, sample, client, mode)
    if isinstance(result, dict) and (result.get('schema_version') == 5 or result.get('mode') == 'grounded'
                                    or result.get('extraction_protocol') in ('whole-line-grounded-v1', 'whole-line-grounded-v2')):
        from .grounded import validate_grounded_saved
        return validate_grounded_saved(result, sample, client, mode)
    from .requirements import RequirementSample
    if isinstance(sample, RequirementSample) or (isinstance(result, dict) and result.get('scope') == 'requirement'):
        from .requirement_pipeline import validate_requirement_saved
        if not isinstance(sample, RequirementSample):
            raise ValueError('需求结果需要完整需求输入，请使用 requirement validate')
        return validate_requirement_saved(result, sample, client, mode)
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
    decisions = validate_decisions({'decisions': result.get('decisions')}, context)
    if result['decisions'] != decisions:
        raise ValueError('决策轨迹的来源位置与原文不一致')
    if 'input_documents' in result and result['input_documents'] != sample.documents():
        raise ValueError('材料类型或节点元数据与输入不一致')
    if 'decision_history' in result:
        from .memory import reconcile
        history, _ = reconcile({'items': [], 'open_questions': []}, decisions, context)
        if result['decision_history'] != history['decision_history']:
            raise ValueError('决策历史的引用、节点或状态转移与原文不一致')
        queue = result.get('review_queue')
        if not isinstance(queue, list):
            raise ValueError('程序核查队列必须为数组')
        candidates = []
        for entry in queue:
            if not isinstance(entry, dict):
                raise ValueError('程序核查记录必须为对象')
            if entry.get('kind') == 'candidate_requires_review':
                candidate = entry.get('item')
                normalized = validate_result({'items': [candidate], 'open_questions': []}, context)['items'][0]
                if any(candidate.get(key) != value for key, value in normalized.items() if key != 'id'):
                    raise ValueError('核查候选的引用位置与原文不一致')
                candidates.append(candidate)
        replay, _ = reconcile({'items': candidates, 'open_questions': []}, decisions, context)
        if queue != replay['review_queue']:
            raise ValueError('程序核查队列与决策轨迹不一致')
    return result


def render_markdown(result):
    title = ('需求知识' if result.get('scope') == 'requirement' else
             '需求交接' if result.get('stage') == 'requirement' else '阶段交接')
    lines = [f'# {title} {result["sample_id"]}', '',
             f'模式 `{result["mode"]}`。模型 `{result["model"]}`。', '',
             '引用已通过程序逐字核对。条目语义和覆盖范围仍需人工评审。', '']
    if result.get('scope') == 'requirement':
        schema = result.get('schema_version')
        if schema == 6:
            review_status = '独立原文回答及逐字段归因已完成，仍待人工确认。'
        else:
            proof = result.get('grounding_proof' if schema == 5 else 'grounding_review')
            review_status = '模型已逐字段复核，仍待人工确认。' if proof else '未执行结构化字段复核。'
            if isinstance(proof, dict) and proof.get('citation_retrieval_policy_digest'):
                review_status = '模型已逐字段复核正文；程序补充引文的语义支持仍待独立确认。'
        lines.extend(['字段支持复核：' + review_status, ''])
        lines.extend(['节点顺序：' + ' → '.join(node['id'] + '（' + node['stage'] + '）' for node in result['nodes']),
                      '', '以下条目为最终知识快照。' if result.get('schema_version') in (5, 6) else '以下条目为当前知识，历史版本另列。', ''])
    for item in result['items']:
        lines.extend([f'## {item["id"]}', ''])
        for key, title in (('conclusion', '结论'), ('reason', '理由'), ('boundary', '适用边界')):
            field = item[key]
            lines.extend([f'**{title}**', '', field['text'], ''])
            for evidence in field['evidence']:
                label = ' · 程序补充的流程标题' if evidence['source_id'] in field.get('evidence_context', {}).get('added_source_ids', []) else ''
                if evidence['source_id'] in field.get('evidence_retrieval', {}).get('added_source_ids', []):
                    label += ' · 程序检索补充引文'
                lines.extend([f'- `{evidence["source_id"]}` · {evidence["file"]} 第 {evidence["line"]} 行' + label,
                              f'  > {evidence["quote"]}', ''])
    if result['open_questions']:
        lines.extend(['## 待确认', ''])
        for question in result['open_questions']:
            lines.append('- ' + question['text'])
            for evidence in question['evidence']:
                label = ' · 程序补充的流程标题' if evidence['source_id'] in question.get('evidence_context', {}).get('added_source_ids', []) else ''
                lines.append(f'  - `{evidence["source_id"]}` · {evidence["file"]} 第 {evidence["line"]} 行{label} · {evidence["quote"]}')
        lines.append('')
    if result.get('decision_history'):
        lines.extend(['## 决策历史', '', '以下内容已被后续决定替换或解决，不属于现行知识。', ''])
        for history in result['decision_history']:
            lines.extend([f'### {history["id"]} · {history["topic"]} · {history["status"]}', '',
                          f'适用范围：{history["scope"]}。解决节点：{history["resolved_at_node"]}。', '',
                          history['text'], '', f'原因：{history["reason"]["text"]}', ''])
            for label, references in (('原记录', history['evidence']), ('后续依据', history['resolution_evidence']),
                                      ('原因依据', history['reason']['evidence'])):
                for evidence in references:
                    lines.append(f'- {label} `{evidence["source_id"]}` · {evidence["file"]} 第 {evidence["line"]} 行 · {evidence["quote"]}')
            lines.append('')
    if result.get('review_queue'):
        lines.extend(['## 程序核查队列', '', '这些是程序发现的疑点，不代表原文明确提出的业务待定事项。', ''])
        for entry in result['review_queue']:
            text = entry['item']['conclusion']['text'] if 'item' in entry else entry['text']
            references = entry['item']['conclusion']['evidence'] if 'item' in entry else entry['evidence']
            lines.extend([f'- [{entry["kind"]}] {text} — {entry["reason"]}'])
            for evidence in references:
                lines.append(f'  - `{evidence["source_id"]}` · {evidence["file"]} 第 {evidence["line"]} 行 · {evidence["quote"]}')
        lines.append('')
    if result.get('schema_version') == 6:
        lines.extend(['## 独立字段修订', '', '原预测与原历史保存在 JSON 的 base_result，以下为派生快照，不改写原事件。', ''])
        for row in result['verification']['field_checks']:
            if row['action'] != 'keep':
                lines.append(f'- `{row["item_id"]}.{row["field"]}` · {row["action"]} · {row["notes"]}')
        lines.extend(['', '## 字段待复核', ''])
        for entry in result['field_review_queue']:
            lines.append('- `' + entry['item_id'] + '` · ' + entry['original']['conclusion']['text'])
            for issue in entry['issues']:
                lines.append('  - ' + issue['field'] + '：' + issue['notes'])
        lines.append('')
    if result.get('scope') == 'requirement' and result.get('schema_version') not in (5, 6):
        lines.extend(['## 变更历史', '', '已失效或已替换的历史版本不作为现行知识。', ''])
        for event in result['history']:
            lines.extend([f'### {event["id"]} · {event["operation"]} · {event["status"]}', '',
                          f'知识 `{event["knowledge_key"]}`，节点 `{event["node_id"] if event["node_id"] is not None else "未提供（补充材料）"}`，版本 {event["version"]}。', ''])
            if 'item' in event:
                for name, label in (('conclusion', '结论'), ('reason', '理由'), ('boundary', '边界')):
                    lines.extend([f'{label}：{event["item"][name]["text"]}', ''])
                    for ref in event['item'][name]['evidence']:
                        lines.append(f'- `{ref["source_id"]}` · {ref["file"]} 第 {ref["line"]} 行 · {ref["quote"]}')
                    lines.append('')
            else:
                if 'note' in event:
                    lines.extend([event['note'], ''])
                refs = event['evidence']
                if isinstance(refs, dict):
                    refs = [ref for field in refs.values() for ref in field]
                for ref in refs:
                    lines.append(f'- `{ref["source_id"]}` · {ref["file"]} 第 {ref["line"]} 行 · {ref["quote"]}')
                lines.append('')
    if result.get('scope') == 'requirement' and result.get('grounding_review'):
        lines.extend(['## 理由未知处理', ''])
        for check in result['grounding_review']['reason_checks']:
            lines.extend([f'- `{check["source_id"]}` · {check["quote"]} · `{check["status"]}` · {check["notes"]}'])
            for ref in check['evidence']:
                lines.append(f'  - `{ref["source_id"]}` · {ref["file"]} 第 {ref["line"]} 行 · {ref["quote"]}')
        lines.append('')
    lines.extend(['## 运行记录', '', '```json', json.dumps(result['metrics'], ensure_ascii=False, indent=2), '```', ''])
    return '\n'.join(lines)


def save_result(path: Path, result, *, sample=None):
    if path.suffix != '.json':
        raise ValueError('提取结果输出文件须使用 .json 扩展名')
    markdown = path.with_suffix('.md')
    if path.exists() or markdown.exists():
        raise ValueError('输出已存在，请指定新的输出文件或使用 batch --resume')
    if result.get('schema_version') in (5, 6) or result.get('mode') == 'grounded':
        if sample is None:
            raise ValueError('grounded 保存需要原始材料')
        result['result_digest'] = result_digest(result)
        validate_saved(result, sample)
        rendered = render_markdown(result)
        write_text(markdown, rendered)
        write_json(path, result)
        return
    if result.get('schema_version') in (3, 4) and result.get('scope') == 'requirement':
        from .requirement_audit_report import render_audit_html
        report = path.with_suffix('.audit.html')
        if sample is None or report.exists():
            raise ValueError('新审计结果需要完整原文和不存在的 HTML 报告路径')
        # Verify and render everything before publishing; the success JSON is written last.
        result['result_digest'] = result_digest(result)
        validate_saved(result, sample)
        rendered, html = render_markdown(result), render_audit_html(result, sample)
        write_text(markdown, rendered)
        write_text(report, html)
        write_json(path, result)
        return
    result['result_digest'] = result_digest(result)
    write_json(path, result)
    write_text(markdown, render_markdown(result))
