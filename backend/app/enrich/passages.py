"""The parts of a page the fact call reads (D-101): the passages around the product's name and around the words of the
criteria looked up for it, never the whole page. A smaller call answers sooner.

Each place the page names the product or uses one of the criterion's words keeps up to AROUND characters each side,
cut only at a space, so no word is broken. Parts that touch are merged. When the page is longer than PAGE_CHARS, the
parts holding the most of the criteria's distinct words go first (a word many labels share, such as "quality" or
"life", counts half), those also naming the product ahead of the rest, ties in page order; the parts kept are sent in
page order, with GAP between them. A claim's quote is still checked against the whole page, so a quote that spans a
gap is not on the page and is dropped. Plain code: nothing here is sent anywhere.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

AROUND = 200  # characters kept each side of a place the page names the product or a criterion's word
PAGE_CHARS = 1500  # at most this much of one page goes in the call
GAP = "\n[...]\n"  # where text was left out
_LITTLE = frozenset({"and", "for", "the", "with", "per", "its", "how", "what", "from", "into"})
_COMMON = frozenset({"quality", "value", "money", "life", "build", "overall", "use", "ease", "good", "best"})
_NAME = ""  # a part's mark for naming the product: no criterion word is empty


def criterion_words(names: Iterable[str]) -> list[str]:
    """The words a criterion is found by on a page: the words of its label and key, three letters or more, in order."""
    words: list[str] = []
    for name in names:
        for word in re.split(r"[^0-9a-z]+", name.casefold()):
            if len(word) >= 3 and word not in _LITTLE and word not in words:
                words.append(word)
    return words


def passages(text: str, product: str, words: Sequence[str], *, around: int = AROUND, most: int = PAGE_CHARS) -> str:
    """The page's text around each place it names the product or uses one of `words`, at most `most` characters."""
    if len(text) <= most:
        return text
    parts = _merged([*_spans(text, _name_pattern(product), _NAME, around), *_word_spans(text, words, around)])
    if not parts:
        return _cut(text, 0, most)
    kept: list[tuple[int, int]] = []
    room = most
    for begin, finish, _ in sorted(parts, key=lambda part: (-_weight(part[2]), part[0])):
        if room <= 0:
            break
        start, end = _whole_words(text, begin, min(finish, begin + room))
        if end > start:
            kept.append((start, end))
            room -= end - start
    return GAP.join(text[start:end].strip() for start, end in sorted(kept))


def _name_pattern(product: str) -> re.Pattern[str] | None:
    words = [re.escape(word) for word in product.casefold().split()]
    if not words:
        return None
    return re.compile(r"(?<!\w)" + r"\s+".join(words) + r"(?!\w)", re.IGNORECASE)


def _word_spans(text: str, words: Sequence[str], around: int) -> list[tuple[int, int, str]]:
    """A criterion's word and its other forms: "battery" finds "batteries", "weight" finds "weighs"."""
    spans: list[tuple[int, int, str]] = []
    for word in words:
        stem = word[:-1] if len(word) > 4 else word
        spans += _spans(text, re.compile(r"(?<!\w)" + re.escape(stem) + r"\w*", re.IGNORECASE), word, around)
    return spans


def _spans(text: str, pattern: re.Pattern[str] | None, mark: str, around: int) -> list[tuple[int, int, str]]:
    if pattern is None:
        return []
    return [(max(0, m.start() - around), min(len(text), m.end() + around), mark) for m in pattern.finditer(text)]


def _merged(spans: Sequence[tuple[int, int, str]]) -> list[tuple[int, int, frozenset[str]]]:
    """Spans that overlap or touch, as one, holding every word (and the name) its parts found."""
    merged: list[tuple[int, int, frozenset[str]]] = []
    for start, end, mark in sorted(spans):
        if merged and start <= merged[-1][1]:
            last = merged[-1]
            merged[-1] = (last[0], max(last[1], end), last[2] | {mark})
        else:
            merged.append((start, end, frozenset({mark})))
    return merged


def _weight(marks: frozenset[str]) -> float:
    """A part's worth: 1 for each distinct criterion word it holds, half for a word many labels share, and half for
    naming the product."""
    return sum(0.5 if mark in _COMMON or mark == _NAME else 1.0 for mark in marks)


def _whole_words(text: str, start: int, end: int) -> tuple[int, int]:
    """The span shrunk to whole words: it starts after a space (or at the page's start) and ends before one."""
    if start > 0 and not text[start - 1].isspace():
        space = re.search(r"\s", text[start:end])
        start = end if space is None else start + space.end()
    if end < len(text) and not text[end].isspace():
        end = max(start, text.rfind(" ", start, end), text.rfind("\n", start, end))
    return start, end


def _cut(text: str, start: int, most: int) -> str:
    begin, end = _whole_words(text, start, min(len(text), start + most))
    return text[begin:end].strip()
