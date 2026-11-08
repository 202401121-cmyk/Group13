"""Near-duplicate threads are merged (US-12): the same question asked twice is one voice, not two.

Titles are lowercased, with punctuation and common words removed. Two threads whose word sets overlap by more than 60%
(shared words divided by all the words of both) become one: the higher-scored thread, by upvotes and then comments.

A Source has one url and contracts are fixed, so the other threads' URLs are kept as the last lines of the text, which
survive into the saved result. A proper also_urls field would be a contract change (D-23).

Every word counts here, however short and in any script: the 3 in "Swift 3" is what makes it a different laptop.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Sequence

from app.contracts import Source
from app.contracts.research import MAX_SOURCE_CHARS

from .reddit.base import STOP_WORDS

MERGE_ABOVE = 0.60
SAME_TOPIC = "Same topic: "
# Exactly what we write: a poster's own line can't pass as a merged URL unless it is a real thread's address, short.
MERGED_LINE = re.compile(r"^Same topic: https://www\.reddit\.com/r/[A-Za-z0-9_]{2,21}/comments/[a-z0-9]{1,13}/$")
MAX_MERGED_LINES = 50  # one listing's worth, so these lines can never fill the text


def _title_words(title: str) -> set[str]:
    """Letters, digits and the marks that build a letter in many scripts, so a Hindi word stays one word."""
    kept = "".join(c if c.isalnum() or unicodedata.category(c).startswith("M") else " " for c in title.casefold())
    return {word for word in kept.split() if word not in STOP_WORDS}


def title_overlap(left: str, right: str) -> float:
    """Shared words divided by all words of both. 0.0 when either title has no words that count."""
    a, b = _title_words(left), _title_words(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def merge_near_duplicates(threads: Sequence[Source]) -> list[Source]:
    """Every thread once, highest score first. Ties keep file order, so the same threads give the same answer."""
    ranked = sorted(
        enumerate(threads), key=lambda pair: (-(pair[1].upvotes or 0), -(pair[1].comment_count or 0), pair[0])
    )
    kept: list[Source] = []
    for _, thread in ranked:
        for index, better in enumerate(kept):
            if title_overlap(better.title, thread.title) > MERGE_ABOVE:
                kept[index] = _with_other_url(better, thread)
                break
        else:
            kept.append(thread)
    return kept


def _with_other_url(thread: Source, other: Source) -> Source:
    body, merged = split_merged(thread.text)
    tail = "\n".join([*merged, f"{SAME_TOPIC}{other.url}"])
    room = MAX_SOURCE_CHARS - len(tail) - 2  # the body gives way, never a URL
    text = f"{body[:room].rstrip()}\n\n{tail}".strip()  # stripped before hashing, as the contract strips it
    return Source.model_validate(
        {**thread.model_dump(), "text": text, "text_hash": hashlib.sha256(text.encode()).hexdigest()}
    )


def split_merged(text: str) -> tuple[str, list[str]]:
    """The text, and the other URLs an earlier merge left on its end."""
    lines = text.split("\n")
    at = len(lines)
    while at > 0 and len(lines) - at < MAX_MERGED_LINES and MERGED_LINE.match(lines[at - 1]):
        at -= 1
    return "\n".join(lines[:at]).rstrip(), lines[at:]
