"""Stable references to literal source passages, never model-written quotations."""
from __future__ import annotations

import re


def source_passages(article_id, text):
    passages, start = [], 0
    while start < len(text):
        end = min(start + 220, len(text))
        if end < len(text):
            boundary = text.rfind(' ', start + 100, end)
            if boundary > start:
                end = boundary
        quote = text[start:end].strip()
        if len(quote) < 12:
            # Overlap the tail so a short last fragment is not lost.
            quote = text[max(0, len(text) - 24):].strip()
        if len(quote) >= 12:
            passages.append({'id': f'{article_id}:p{len(passages) + 1}', 'text': quote})
        start = end
    return passages


def passage_quote(article_id, reference, text):
    passages = {p['id']: p['text'] for p in source_passages(article_id, text)}
    if not isinstance(reference, str) or reference not in passages:
        raise ValueError('Unknown or mismatched source passage reference')
    return passages[reference]


def summary_quotes(article_id, quotes, text):
    if not isinstance(quotes, list):
        return quotes  # The summary validator reports the malformed structure.
    # Literal quotes remain supported for old fixtures/imports and are still verified.
    return [passage_quote(article_id, q, text)
            if isinstance(q, str) and re.fullmatch(r'[a-f0-9]{20}:p[0-9]+', q) else q
            for q in quotes]
