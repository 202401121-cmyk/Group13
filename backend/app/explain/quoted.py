"""Words of a quote in a sentence, word for word (US-22, DECIDED 4): the fallback's rule, and the guard that keeps a
community post's words out of an explanation (D-53)."""

from __future__ import annotations

import re
from collections.abc import Sequence

_WORDS = re.compile(r"[^\W_]+(?:['’][^\W_]+)*")


def words(text: str) -> list[str]:
    """Letters and digits, capitals aside."""
    return [w.casefold() for w in _WORDS.findall(text)]


def quotes_words(text: str, quotes: Sequence[str], n: int) -> bool:
    """The text holds `n` words of one of the quotes in a row, word for word."""
    mine = words(text)
    grams = {tuple(mine[i : i + n]) for i in range(len(mine) - n + 1)}
    for quote in quotes:
        theirs = words(quote)
        if any(tuple(theirs[i : i + n]) in grams for i in range(len(theirs) - n + 1)):
            return True
    return False
