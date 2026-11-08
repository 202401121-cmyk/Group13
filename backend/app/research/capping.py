"""The cap on each source, in tokens counted the way the wrapper counts them (US-28).

Whole paragraphs go until the cap. For Reddit, the post comes first, then the comments, highest score first, until the
cap is reached. So a capped source is what's most worth reading, and it fits every model on the extraction routes
without the wrapper having to trim it, which could cut off the end of its data block.
"""

from __future__ import annotations

import hashlib

from app.contracts import Source, SourceType
from app.llm.fitting import BYTES_PER_TOKEN, MESSAGE_OVERHEAD_TOKENS, estimate_tokens

from .dedupe import split_merged

_PARAGRAPH = "\n\n"
_SENTENCE_ENDS = (". ", "! ", "? ")


def cap_text(text: str, max_tokens: int) -> str:
    """Whole paragraphs up to the cap. If the first alone is too long, a sentence end, then a word, then a character."""
    if estimate_tokens(text) <= max_tokens:
        return text
    kept: list[str] = []
    for paragraph in text.split(_PARAGRAPH):
        if estimate_tokens(_PARAGRAPH.join([*kept, paragraph])) > max_tokens:
            break
        kept.append(paragraph)
    return _PARAGRAPH.join(kept) if kept else _cut_paragraph(text, max_tokens)


def _cut_paragraph(text: str, max_tokens: int) -> str:
    max_bytes = max(0, max_tokens - MESSAGE_OVERHEAD_TOKENS) * BYTES_PER_TOKEN
    piece = text.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")
    end = max(piece.rfind(mark) for mark in _SENTENCE_ENDS)
    if end > 0:
        return piece[: end + 1]
    words = piece.rsplit(None, 1)
    # The last word may be cut short, so it goes, unless the piece is one long word and there is nothing else to keep.
    return words[0] if len(words) == 2 else piece


def cap_source(source: Source, max_tokens: int) -> Source:
    """This source within `max_tokens`, with its text_hash still true. An unchanged source comes back as it is."""
    text = _capped_body(source, max_tokens)
    comments = sorted(source.comments, key=lambda comment: -comment.upvotes)  # a stable sort: ties keep their order
    used = estimate_tokens(text)
    kept = []
    for comment in comments:
        used += estimate_tokens(comment.text)
        if used > max_tokens:
            break
        kept.append(comment)
    if text == source.text and tuple(kept) == source.comments:
        return source
    changes = {"text": text, "text_hash": hashlib.sha256(text.encode()).hexdigest(), "comments": tuple(kept)}
    return Source.model_validate({**source.model_dump(), **changes})


def _capped_body(source: Source, max_tokens: int) -> str:
    """A merged Reddit thread's other URLs are kept, whatever is cut (dedupe.py)."""
    if source.type is not SourceType.REDDIT:
        return cap_text(source.text, max_tokens)
    body, merged = split_merged(source.text)
    if not merged:
        return cap_text(source.text, max_tokens)
    tail = "\n".join(merged)
    room = max_tokens - estimate_tokens(tail)
    return f"{cap_text(body, room)}{_PARAGRAPH}{tail}".strip()
