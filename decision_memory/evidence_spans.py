"""Exact source-line codepoint coordinates; no guessed first occurrences."""
import hashlib
import unicodedata


def source_span(sample, source_id, start, end):
    source = sample.catalog[source_id]
    return {'source_id': source_id, 'quote': source.text[start:end], 'file': source.file,
            'line': source.line, 'message': source.message, 'role': source.role,
            'start': start, 'end': end,
            'line_sha256': hashlib.sha256(source.text.encode('utf-8')).hexdigest(),
            'file_sha256': sample.file_hashes[source.file]}


def locate_unique_quote(sample, reference):
    if not isinstance(reference, dict):
        raise ValueError('字符定位需要来源与逐字引文')
    source_id, quote = reference.get('source_id'), reference.get('quote')
    if not isinstance(source_id, str) or source_id not in sample.catalog:
        raise ValueError('字符定位来源不存在')
    if not isinstance(quote, str) or not quote.strip():
        raise ValueError('字符定位引文不能为空')
    text = sample.catalog[source_id].text
    start = text.find(quote)
    if start < 0:
        raise ValueError('字符定位引文不在原文中')
    if text.find(quote, start + 1) >= 0:
        raise ValueError('字符定位引文不唯一，请扩大连续引文')
    return source_span(sample, source_id, start, start + len(quote))


def meaningful_overlap(left, right, sample):
    if left['source_id'] != right['source_id']:
        return False
    start = max(left.get('focus_start', left['start']), right.get('focus_start', right['start']))
    end = min(left.get('focus_end', left['end']), right.get('focus_end', right['end']))
    text = sample.catalog[left['source_id']].text[start:end] if end > start else ''
    return any(not char.isspace() and unicodedata.category(char)[0] not in 'PZC' for char in text)
