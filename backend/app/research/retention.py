"""Reddit data may be kept at most 24 hours (D-53), but a saved search stays until someone deletes it.

So Reddit's words never reach the saved row. A Reddit source keeps what says it was read (its URL, community, fetch
time, archive and counts), and loses its title, text, comments and author hash. Any quote taken from it goes too,
wherever in the result it sits: US-14's claims will carry them. The search itself, and the result the shopper sees, are
untouched.

Only `quote` is cleared outside the sources, because nothing else carries Reddit's words yet. US-14 and US-16 fill
fields that can (ResolvedValue.value, ExplanationSentence.text, trade_off, CommunitySignal.note), and must extend this
before those reach a saved row.
"""

from __future__ import annotations

import copy
import hashlib
from typing import Any

from .dedupe import MAX_MERGED_LINES, MERGED_LINE

REDDIT_TEXT_NOT_KEPT = "[Reddit text is not kept]"


def without_reddit_text(result: dict[str, Any]) -> dict[str, Any]:
    """A copy of a finished search, as JSON, with no Reddit text in it."""
    saved = copy.deepcopy(result)
    sources = saved.get("sources")
    if not isinstance(sources, list):
        return saved
    reddit = [s for s in sources if isinstance(s, dict) and s.get("type") == "reddit"]
    for source in reddit:
        _strip_source(source)
    ids = {s.get("id") for s in reddit}
    if ids:
        _strip_quotes(saved, ids)
    return saved


def _strip_source(source: dict[str, Any]) -> None:
    # Only a merged thread's other URL survives (dedupe.py), in the exact form we write. A poster's own line in that
    # exact form would survive too, but it can hold nothing except the address of a public thread.
    merged = [line for line in str(source.get("text", "")).splitlines() if MERGED_LINE.match(line)]
    merged = merged[-MAX_MERGED_LINES:]  # a poster's own lines can't push the saved text past its limit
    text = "\n".join([REDDIT_TEXT_NOT_KEPT, *merged])
    source.update(
        title="",
        text=text,
        text_hash=hashlib.sha256(text.encode()).hexdigest(),
        comments=[],
        author_hash=None,
    )


def _strip_quotes(node: Any, ids: set[Any]) -> None:
    if isinstance(node, dict):
        if node.get("source_id") in ids and "quote" in node:
            node["quote"] = REDDIT_TEXT_NOT_KEPT
        for value in node.values():
            _strip_quotes(value, ids)
    elif isinstance(node, list):
        for value in node:
            _strip_quotes(value, ids)
