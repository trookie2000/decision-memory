"""Offline, escaped reports. Rendering never invokes a model or reads gold."""
import html
import json


def esc(value):
    return html.escape(str(value), quote=True)


def page(title, body):
    return ('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'">'
            '<title>' + esc(title) + '</title><style>'
            'body{font:16px/1.65 system-ui,sans-serif;margin:32px auto;max-width:1150px;padding:0 20px;color:#182333}'
            'h1,h2,h3{line-height:1.35}a{color:#2459b5}pre,blockquote{white-space:pre-wrap;overflow-wrap:anywhere}'
            'pre,blockquote,article{background:#f4f6fa;padding:16px;border-radius:8px}article{margin:20px 0}'
            'table{border-collapse:collapse;width:100%}td,th{padding:9px;border:1px solid #d7dfe8;text-align:left}'
            'small{color:#536278}.muted{color:#536278}summary{cursor:pointer}details:target{background:#fff4cc}'
            '@media print{body{margin:0;max-width:none}details{display:block}a{color:inherit}}'
            '</style><body><h1>' + esc(title) + '</h1>' + body + '</body></html>')


def render_result_html(result, sample):
    source_anchors = {key: f'source-{index}' for index, key in enumerate(sample.catalog, 1)}
    body = ('<p>输入范围：' + esc(result.get('input_scope', result.get('scope', 'stage'))) +
            '；模式：' + esc(result['mode']) + '。引用位置已离线校验；语义质量仍待独立评审。'
            '人工确认：pending；正式成绩：未提供。</p>')
    for item in result['items']:
        body += '<article><h2>' + esc(item['id']) + '</h2>'
        for name, title in (('conclusion', '结论'), ('reason', '理由'), ('boundary', '适用边界')):
            field = item[name]
            body += '<h3>' + title + '</h3><p>' + esc(field['text']) + '</p>'
            for ref in field['evidence']:
                label = ' · 程序补充的流程标题' if ref['source_id'] in field.get('evidence_context', {}).get('added_source_ids', []) else ''
                if ref['source_id'] in field.get('evidence_retrieval', {}).get('added_source_ids', []):
                    label += ' · 程序检索补充引文'
                body += ('<p><a href="#' + source_anchors[ref['source_id']] + '">' + esc(ref['source_id']) +
                         '</a> · ' + esc(ref['file']) + ' 第 ' + esc(ref['line']) + ' 行' + label + '</p><blockquote>' +
                         esc(ref['quote']) + '</blockquote>')
        body += '</article>'
    body += '<h2>待确认事项</h2>'
    for question in result['open_questions']:
        body += '<article><p>' + esc(question['text']) + '</p>'
        for ref in question['evidence']:
            body += '<a href="#' + source_anchors[ref['source_id']] + '">' + esc(ref['source_id']) + '</a> '
            if ref['source_id'] in question.get('evidence_context', {}).get('added_source_ids', []):
                body += '<small>程序补充的流程标题</small>'
            body += '<blockquote>' + esc(ref['quote']) + '</blockquote>'
        body += '</article>'
    body += '<h2>原文位置</h2><p>包含本次明确提供的所有原文行；未加载其他阶段或评测标注。</p>'
    for key, source in sample.catalog.items():
        body += ('<details id="' + source_anchors[key] + '"><summary>' + esc(key) + ' · ' + esc(source.file) +
                 ' 第 ' + esc(source.line) + ' 行 · ' + esc(source.role) + '</summary><pre>' +
                 esc(source.text) + '</pre></details>')
    body += '<h2>实际运行用量</h2><pre>' + esc(json.dumps(result['metrics'], ensure_ascii=False, indent=2)) + '</pre>'
    return page('知识提取结果 · ' + result['sample_id'], body)


def render_run_markdown(report):
    lines = ['# 评委运行报告', '', f'状态：`{report["status"]}`；输入范围：`{report["input_scope"]}`；模式：`{report["mode"]}`。', '',
             '本报告记录执行与用量，不自动评价自己答案的准确率。人工复核 pending，正式成绩未提供。', '',
             '| 样本 | 状态 | 条目 | HTTP 次数 | 耗时（秒） |', '| --- | --- | ---: | ---: | ---: |']
    for row in report['records']:
        label = row['sample_id'].replace('|', '\\|').replace('\n', ' ')
        metrics = row.get('metrics') or {}
        lines.append(f'| {label} | {row["status"]} | {row.get("items", "—")} | {metrics.get("http_attempts", 0)} | {row.get("wall_seconds", "—")} |')
    lines.extend(['', '## 总用量', '', '```json', json.dumps(report['metrics'], ensure_ascii=False, indent=2), '```', '',
                  '用量缺失保持 null；金额仅按提供的单价估算。失败、草稿和未知调用均计入实际用量。', '',
                  'JSON/Markdown/HTML 位于各样本目录。HTML 可点击来源查看本次原文；calls 保留请求正文、响应与回执。', '',
                  'unknown 表示无法确认服务端完成情况：先核对原回执，不自动重发。', ''])
    for row in report['records']:
        if row.get('error'):
            lines.extend([f'## {row["sample_id"]} · {row["status"]}', '', row['error'], ''])
    return '\n'.join(lines)


def render_run_html(report):
    body = ('<p>状态：' + esc(report['status']) + '；输入范围：' + esc(report['input_scope']) +
            '；模式：' + esc(report['mode']) + '。</p><p>此页只汇总执行和用量。人工复核 pending，正式成绩未提供。'
            '未提供完整用量或单价时金额为 null。可用浏览器打印保存 PDF。</p>'
            '<table><thead><tr><th>样本</th><th>状态</th><th>条目</th><th>耗时（秒）</th><th>结果</th></tr></thead><tbody>')
    for index, row in enumerate(report['records'], 1):
        link = '<a href="samples/' + f'{index:03d}' + '/result.html">查看条目和来源</a>' if row['status'] == 'success' else '未发布'
        body += ('<tr><td>' + esc(row['sample_id']) + '</td><td>' + esc(row['status']) + '</td><td>' +
                 esc(row.get('items', '—')) + '</td><td>' + esc(row.get('wall_seconds', '—')) + '</td><td>' + link + '</td></tr>')
    body += '</tbody></table><h2>总用量</h2><pre>' + esc(json.dumps(report['metrics'], ensure_ascii=False, indent=2)) + '</pre>'
    for row in report['records']:
        if row.get('error'):
            body += '<h2>' + esc(row['sample_id']) + '</h2><pre>' + esc(row['error']) + '</pre>'
    return page('评委运行报告', body)
