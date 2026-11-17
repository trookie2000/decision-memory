"""Self-contained escaped HTML with source-line codepoint highlights."""
from collections import Counter, defaultdict
from html import escape


def _e(value):
    return escape(str(value), quote=True)


def _marked(text, spans):
    intervals = sorted((row.get('focus_start', row['start']), row.get('focus_end', row['end'])) for row in spans)
    merged = []
    for start, end in intervals:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    parts, cursor = [], 0
    for start, end in merged:
        parts.extend([_e(text[cursor:start]), '<mark>', _e(text[start:end]), '</mark>'])
        cursor = end
    return ''.join(parts) + _e(text[cursor:])


def render_audit_html(result, sample):
    audit = result['evidence_audit']
    counts = Counter(row['disposition'] for row in audit['coverage_checks'])
    html = ['<!doctype html><html lang="zh-CN"><meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width,initial-scale=1">',
            '<title>需求知识证据审计</title><style>',
            'body{font:16px/1.6 system-ui,sans-serif;max-width:1100px;margin:2rem auto;padding:0 1rem;color:#18202c}',
            'table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccd3dc;padding:.5rem;text-align:left}',
            'pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f6f8;padding:1rem}',
            'mark{background:#fff1a8}a{color:#1456b8}small{color:#48566b}section{margin:1.5rem 0}</style>',
            '<h1>需求知识证据审计：' + _e(result['sample_id']) + '</h1>',
            '<p>角色、位置、关联和清单完整性由程序检查。业务语义由模型判断，仍需独立评测。候选处置完整度不等于召回率。</p>',
            '<p>状态：' + _e(audit['audit_status']) + '；候选：' + _e(len(audit['unit_catalog']))
            + '；处置：' + _e(dict(counts)) + '</p><h2>当前知识</h2>']
    for index, item in enumerate(result['items']):
        identity = result['memory_index'][index]['knowledge_key']
        html.append('<section><h3>' + _e(identity) + '</h3>')
        for name, label in [('conclusion', '结论'), ('reason', '理由'), ('boundary', '边界')]:
            html.append('<p><strong>' + label + '：</strong>' + _e(item[name]['text']) + '</p>')
            for ref in item[name]['evidence']:
                html.append('<p><a href="#src-' + _e(ref['source_id']) + '">' + _e(ref['source_id'])
                            + '</a>：' + _e(ref['quote']) + '</p>')
        html.append('</section>')
    html.append('<h2>准入依据</h2><pre>' + _e('\n'.join(
        row['knowledge_key'] + ' · ' + row['basis'] + ' · ' + row['notes'] for row in audit['admission_checks'])) + '</pre>')
    html.append('<h2>候选处置</h2><table><tr><th>候选</th><th>原文片段</th><th>处置与模型判定</th><th>依据</th></tr>')
    catalog = {row['unit_id']: row for row in audit['unit_catalog']}
    for row in audit['coverage_checks']:
        unit = catalog[row['unit_id']]
        html.append('<tr><td>' + _e(row['unit_id']) + '<br><a href="#src-' + _e(unit['source_id']) + '">'
                    + _e(unit['source_id']) + '</a></td><td>' + _e(unit['quote']) + '</td><td>'
                    + _e(row['disposition']) + '<br>' + _e(row['semantic_coverage']) + '</td><td>' + _e(row['notes']))
        for target in row['targets']:
            label = target.get('knowledge_key', target.get('event_id', target.get('question_id', target.get('source_id', ''))))
            html.append('<br>' + _e(target['type']) + '：' + _e(label) + ' · ' + _e(target['evidence']['quote']))
        html.append('</td></tr>')
    html.append('</table><h2>历史</h2>')
    for row in result['history']:
        html.append('<p>' + _e(row['id']) + ' · ' + _e(row['knowledge_key']) + ' · ' + _e(row['status']) + '</p>')
        if 'item' in row:
            html.append('<pre>' + _e('\n'.join(row['item'][name]['text'] for name in ('conclusion', 'reason', 'boundary'))) + '</pre>')
        elif 'note' in row:
            html.append('<p>' + _e(row['note']) + '</p>')
    html.append('<h2>未决项</h2>')
    for index, question in enumerate(result['open_questions'], 1):
        html.append('<p>' + _e(f'Q{index:03d}') + '：' + _e(question['text']) + '</p>')
    html.append('<h2>原文与位置</h2><p>区间为索引行内 Unicode 码点，起点包含、终点不包含。</p>')
    by_source = defaultdict(list)
    for span in audit['evidence_spans'] + audit['unit_catalog']:
        by_source[span['source_id']].append(span)
    for row in audit['coverage_checks']:
        for target in row['targets']:
            span = target['evidence']
            by_source[span['source_id']].append(span)
    for source_id, spans in by_source.items():
        source = sample.catalog[source_id]
        html.append('<section id="src-' + _e(source_id) + '"><h3>' + _e(source_id) + '</h3><small>'
                    + _e(source.file) + ' · 行 ' + _e(source.line) + ' · 角色 ' + _e(source.role or 'unknown')
                    + '</small><pre>' + _marked(source.text, spans) + '</pre></section>')
    html.append('</html>')
    return '\n'.join(html)
