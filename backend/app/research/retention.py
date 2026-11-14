"""Reddit data may be kept at most 24 hours (D-53), but a saved search stays until someone deletes it.

So Reddit's words never reach the saved row. A Reddit source keeps what says it was read: its URL, community, fetch
time, archive and counts, and each comment's ID, place in the thread and upvotes. Its title, text and comment bodies
become the marker, and its author hashes go, as a hash can follow a person from thread to thread. This runs at the save
(DatabaseResultStore.save), and in one other place: the final event a progress stream still sends once its run has been
dropped at its keep-time (app/api/search_runs.py), so that copy holds no Reddit text either. Nowhere else. So the search
itself, and the result the shopper sees, are untouched, except in that one late final event.

A quote stays only if it names a source we know isn't Reddit. A claim keeps its value, score and source link, and its
quote becomes the marker. SearchResult.citations (1.7.0) holds quotes, and a citation's quote is optional: from Reddit,
or from a source we can't name, it is dropped (None), so the evidence route can say it was withheld.

Since S3-10b verify is real, so a resolved value can be a claim's own words ("about 6 hours on a full charge"). A string
value, or an alternative's, is replaced by the marker unless every claim it rests on is cited from a source we know
isn't Reddit; the claims behind it are read from SearchResult.citations. Numbers and true/false are not words, so they
stay, and so do the claim ids. An alternative's unit is too short for the marker, so it is dropped instead (None).

Product names are names, not a poster's words, so they stay from any source (US-15; Om, 11 Nov 2026). Community notes
hold no Reddit text yet: nothing fills them until US-42. tests/unit/research/test_retention.py lists every text field in
a SearchResult, and fails on a new one, or when US-20 or US-42 makes real the stages whose fields could hold them.
"""

from __future__ import annotations

import copy
import hashlib
from functools import lru_cache
from typing import Any

from .dedupe import split_merged
from .reddit.subreddits import load_subreddits

REDDIT_TEXT_NOT_KEPT = "[Reddit text is not kept]"


def without_reddit_text(result: dict[str, Any]) -> dict[str, Any]:
    """A copy of a finished search, as JSON, with no Reddit text in it."""
    saved = copy.deepcopy(result)
    sources = saved.get("sources")
    if not isinstance(sources, list):
        _strip_values(saved, set())  # with no sources, no source can be known not to be Reddit
        _drop_cited_quotes(saved, set())
        return saved
    others: set[Any] = set()
    for source in sources:
        if isinstance(source, dict) and source.get("type") == "reddit":
            _strip_source(source)
        elif isinstance(source, dict):
            others.add(source.get("id"))
    _strip_values(saved, others)
    _strip_quotes(saved, others)
    _drop_cited_quotes(saved, others)
    return saved


@lru_cache(maxsize=1)
def _searched_subreddits() -> frozenset[str]:
    """Every subreddit research lists threads from, so the only ones a merged thread can be in. None if unreadable."""
    try:
        config = load_subreddits()
    except Exception:  # a save must never fail on this: with no list, no merged line can be kept
        return frozenset()
    names = [name for names in config.categories.values() for name in names]
    names += [name for group in config.shared for name in group.subreddits]
    return frozenset(name.lower() for name in names)


def _kept_link(line: str) -> bool:
    """A "Same topic" line names a thread in one of our own subreddits, so it holds an address and no free words."""
    subreddit = line.removeprefix("Same topic: https://www.reddit.com/r/").split("/", 1)[0]
    return subreddit.lower() in _searched_subreddits()


def _strip_source(source: dict[str, Any]) -> None:
    # A merged thread's other URL survives (dedupe.py): only the lines at the end, in the exact form we write, as dedupe
    # reads them. A poster's own line in that form, anywhere else, goes. One at the end survives only if it names a
    # thread in a subreddit we search, so the most it can hold is a thread's ID: an address, never a sentence.
    _, merged = split_merged(str(source.get("text", "")))
    text = "\n".join([REDDIT_TEXT_NOT_KEPT, *(line for line in merged if _kept_link(line))])
    comments = source.get("comments")
    source.update(
        title=REDDIT_TEXT_NOT_KEPT,
        text=text,
        text_hash=hashlib.sha256(text.encode()).hexdigest(),
        comments=[_strip_comment(c) for c in comments] if isinstance(comments, list) else [],
        author_hash=None,
    )


def _strip_comment(comment: Any) -> Any:
    return {**comment, "text": REDDIT_TEXT_NOT_KEPT, "author_hash": None} if isinstance(comment, dict) else None


def _claims_kept(saved: dict[str, Any], others: set[Any]) -> set[Any]:
    """The claims cited from a source we know isn't Reddit: only words resting on these alone may be kept."""
    citations = saved.get("citations")
    if not isinstance(citations, list):
        return set()
    return {c.get("claim_id") for c in citations if isinstance(c, dict) and c.get("source_id") in others}


def _may_keep(claim_ids: Any, kept: set[Any]) -> bool:
    return isinstance(claim_ids, list) and bool(claim_ids) and all(i in kept for i in claim_ids)


def _strip_values(saved: dict[str, Any], others: set[Any]) -> None:
    """A resolved value's words, and its alternatives', unless every claim behind them is cited from a source we know
    isn't Reddit. A number or true/false is not words, so it stays."""
    products = saved.get("products")
    if not isinstance(products, list):
        return
    kept = _claims_kept(saved, others)
    for summary in products:
        values = summary.get("values") if isinstance(summary, dict) else None
        for value in values if isinstance(values, list) else []:
            if not isinstance(value, dict):
                continue
            if isinstance(value.get("value"), str) and not _may_keep(value.get("supporting_claim_ids"), kept):
                value["value"] = REDDIT_TEXT_NOT_KEPT
            alternatives = value.get("alternatives")
            for alternative in alternatives if isinstance(alternatives, list) else []:
                if not isinstance(alternative, dict) or _may_keep(alternative.get("claim_ids"), kept):
                    continue
                if isinstance(alternative.get("value"), str):
                    alternative["value"] = REDDIT_TEXT_NOT_KEPT
                if alternative.get("unit") is not None:
                    alternative["unit"] = None  # too short for the marker (16 characters at most)


def _drop_cited_quotes(saved: dict[str, Any], others: set[Any]) -> None:
    """A citation's quote is optional, so one that can't be kept is dropped, not replaced by the marker."""
    citations = saved.get("citations")
    if not isinstance(citations, list):
        return
    for citation in citations:
        if isinstance(citation, dict) and citation.get("source_id") not in others:
            citation["quote"] = None


def _strip_quotes(node: Any, others: set[Any]) -> None:
    """Keep a quote only if its source is one we know isn't Reddit: if we can't tell, it can't be kept."""
    if isinstance(node, dict):
        if "quote" in node and node.get("source_id") not in others:
            node["quote"] = REDDIT_TEXT_NOT_KEPT
        for value in node.values():
            _strip_quotes(value, others)
    elif isinstance(node, list):
        for value in node:
            _strip_quotes(value, others)
