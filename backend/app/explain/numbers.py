"""The numbers a sentence names (US-20, DECIDED 2): each must match a number code worked out, or the paragraph goes.

Digits count wherever they are, glued to letters too ("16GB", "M3"), with any comma grouping ("1,14,900"): a number
code didn't give is a fact the LLM invented. So do the number words two to twenty, the tens, "hundred", "thousand",
"lakh" and "crore", the ordinals ("ranks second") and multiples ("twice", "half"). "One" doesn't: it is a pronoun far
more often than a number ("one of its best points").
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

# Digits with any comma grouping ("1,200", Indian "1,14,900"), an optional decimal part, or a decimal with no leading 0.
_DIGITS = re.compile(r"(?<![\d.,])(?:(\d+(?:,\d+)*)(?:\.(\d+))?|\.(\d+))")
_WORDS: dict[str, float] = {
    "zero": 0,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
    "hundred": 100,
    "thousand": 1000,
    "lakh": 100_000,
    "lakhs": 100_000,
    "crore": 10_000_000,
    "million": 1_000_000,
    "dozen": 12,
    # Ordinals and multiples state a number too: "it ranks second", "twice as fast", "half the weight".
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
    "sixth": 6,
    "seventh": 7,
    "eighth": 8,
    "ninth": 9,
    "tenth": 10,
    "twice": 2,
    "double": 2,
    "triple": 3,
    "thrice": 3,
    "half": 0.5,
}
_WORD = re.compile(r"\b(" + "|".join(_WORDS) + r")\b", re.IGNORECASE)
_TINY = 1e-9


@dataclass(frozen=True)
class Written:
    """A number as a sentence names it: its value, how many decimals it was written with, and its text."""

    value: float
    decimals: int
    text: str


def numbers_in(text: str) -> list[Written]:
    """Every number in the text, in order."""
    found: list[tuple[int, Written]] = []
    for match in _DIGITS.finditer(text):
        whole = (match.group(1) or "0").replace(",", "")
        fraction = match.group(2) or match.group(3) or ""
        value = float(f"{whole}.{fraction}") if fraction else float(whole)
        found.append((match.start(), Written(value, len(fraction), match.group(0))))
    for match in _WORD.finditer(text):
        word = float(_WORDS[match.group(1).lower()])
        found.append((match.start(), Written(word, 0 if word.is_integer() else 1, match.group(1))))
    return [written for _, written in sorted(found, key=lambda pair: pair[0])]


def matches(written: Written, value: float) -> bool:
    """The same number at the precision it is written: "8" is 8.4 or 8.5 written as a whole number, "8.5" only 8.5."""
    half_step: float = 0.5 / 10.0**written.decimals
    return abs(written.value - value) <= half_step + _TINY


def unmatched(text: str, allowed: Iterable[float]) -> list[str]:
    """The numbers in the text that match none of the allowed values, as written."""
    values = list(allowed)
    return [n.text for n in numbers_in(text) if not any(matches(n, v) for v in values)]
