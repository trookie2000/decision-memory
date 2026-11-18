"""Explicit structural context for plain, single-header artifact flow blocks.

This only carries a flow's title into a citation of that flow's body. It never
searches nearby prose, another field, another block, or another input node.
The extra source is labelled as program supplied, not model selected.
"""
import re

FENCE = re.compile(r'^ {0,3}(`{3,}|~{3,})([^`~]*)$')
ARROW = re.compile(r'^\s+(?:->|→|=>|-->)')
CONTEXT_POLICY = {'policy': 'flow-header-v1', 'source_role': 'artifact',
                  'fences': ['', 'text', 'plaintext'], 'header': 'first-unindented-line',
                  'body': 'all-indented-with-at-least-one-arrow', 'fence_pattern': FENCE.pattern,
                  'arrow_pattern': ARROW.pattern, 'semantic_certification': False}


def flow_context(sample):
    groups, mapping = {}, {}
    for key, source in sample.catalog.items():
        if source.role == 'artifact':
            namespace = key.rsplit('.', 1)[0] if '.' in key else source.node
            groups.setdefault((namespace, source.file), []).append((key, source))
    for (_, filename), rows in groups.items():
        opening, fence, block, eligible = None, None, [], False
        for key, source in sorted(rows, key=lambda row: row[1].line):
            match = FENCE.fullmatch(source.text)
            if opening is None:
                if match:
                    opening, fence, block = source.line, match[1], []
                    eligible = match[2].strip() in CONTEXT_POLICY['fences']
                continue
            if match and match[1][0] == fence[0] and len(match[1]) >= len(fence) and not match[2].strip():
                if eligible and len(block) >= 2:
                    header_id, header = block[0]
                    body = block[1:]
                    if (header.text == header.text.lstrip() and not ARROW.match(' ' + header.text)
                            and all(row.text != row.text.lstrip() for _, row in body)
                            and any(ARROW.match(row.text) for _, row in body)):
                        for body_id, _ in body:
                            mapping[body_id] = {'file': filename, 'opening_line': opening,
                                                'closing_line': source.line, 'header_source_id': header_id}
                opening, fence, block = None, None, []
            else:
                block.append((key, source))
    return mapping


def add_flow_headers(declared, mapping, max_refs):
    expanded, blocks = list(declared), {}
    for key in declared:
        if key not in mapping:
            continue
        context = mapping[key]
        header = context['header_source_id']
        if header in declared:
            continue
        if header not in blocks:
            blocks[header] = {**context, 'anchor_source_ids': []}
            expanded.append(header)
        blocks[header]['anchor_source_ids'].append(key)
    if len(expanded) > max_refs:
        raise ValueError('加入流程标题后来源超过16个，不得截断结构上下文')
    record = None
    if blocks:
        record = {'policy': CONTEXT_POLICY['policy'], 'declared_source_ids': list(declared),
                  'added_source_ids': expanded[len(declared):], 'blocks': list(blocks.values())}
    return expanded, record


def declared_source_ids(field):
    """Validate the record before reconstructing the model's original selection."""
    actual = [ref['source_id'] for ref in field['evidence']]
    context = field.get('evidence_context')
    if context is None:
        return actual
    if (not isinstance(context, dict)
            or set(context) != {'policy', 'declared_source_ids', 'added_source_ids', 'blocks'}
            or context['policy'] != CONTEXT_POLICY['policy']):
        raise ValueError('流程标题上下文记录无效')
    declared, added = context['declared_source_ids'], context['added_source_ids']
    if (not isinstance(declared, list) or not isinstance(added, list) or not added
            or any(not isinstance(key, str) for key in declared + added)
            or declared + added != actual or len(set(actual)) != len(actual)
            or not isinstance(context['blocks'], list) or len(context['blocks']) != len(added)):
        raise ValueError('流程标题上下文与字段引文不一致')
    for header, block in zip(added, context['blocks']):
        if (not isinstance(block, dict)
                or set(block) != {'file', 'opening_line', 'closing_line', 'header_source_id', 'anchor_source_ids'}
                or block['header_source_id'] != header or not isinstance(block['file'], str)
                or type(block['opening_line']) is not int or type(block['closing_line']) is not int
                or not block['opening_line'] < block['closing_line']
                or not isinstance(block['anchor_source_ids'], list) or not block['anchor_source_ids']
                or any(key not in declared for key in block['anchor_source_ids'])):
            raise ValueError('流程标题结构记录无效')
    return declared
