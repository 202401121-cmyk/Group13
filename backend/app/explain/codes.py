"""Code's own numbers in an evidence sentence, each by its kind (US-22, S5-4b DECIDED 3 (A)).

A phrase states one of code's numbers by its kind: "N out of 10" a criterion's score, "N out of 100" the product's
score, "N points" a criterion's points, "ranks #N" or "#N" the rank. The evidence call is never given code's numbers,
so such a phrase is right only when its number is that kind's own value, for the criterion the sentence is about, or
when a cited quote says it. A phrase that names another of code's numbers ("a score of 1 out of 10" from a rank of 1)
is misstated, and the sentence goes. A right one passes by this rule alone: it is taken out before the model reads the
sentence, so the model judges only what the quotes must back.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence

from .facts import ProductFacts
from .numbers import Written, matches, numbers_in

_LEAD = r"(?:,\s*)?(?:\b(?:with|at)\s+)?(?:\b(?:a|its)\s+)?(?:\bscore\s+of\s+)?"
_PHRASE = re.compile(
    rf"{_LEAD}(?P<score>\d+(?:\.\d+)?)\s+out\s+of\s+10\b"
    rf"|{_LEAD}(?P<total>\d+(?:\.\d+)?)\s+out\s+of\s+100\b"
    r"|(?:\badds\s+)?(?P<points>\d+)\s+points?\b"
    r"|\b(?:ranks?|ranked)\s+#?(?P<rank>\d+)\b"
    r"|#(?P<hash>\d+)\b",
    re.IGNORECASE,
)
_KIND = {"score": "score", "total": "total", "points": "points", "rank": "rank", "hash": "rank"}
_SPACE_BEFORE = re.compile(r"\s+([,.;:!?])")
_DOUBLE = re.compile(r"([,;:])\s*([,.;:!?])")


def values(facts: ProductFacts, key: str) -> dict[str, list[float]]:
    """Each kind's own values, for a sentence about this criterion."""
    about = [f for f in facts.facts() if f.key == key]
    return {
        "score": [f.score for f in about],
        "points": [float(f.points) for f in about],
        "total": [] if facts.score is None else [float(facts.score)],
        "rank": [float(facts.rank)],
    }


def _phrases(text: str) -> Iterator[tuple[str, Written, re.Match[str]]]:
    for match in _PHRASE.finditer(text):
        group = next(name for name, value in match.groupdict().items() if value is not None)
        (written,) = numbers_in(match.group(group))
        yield _KIND[group], written, match


def misstated(text: str, facts: ProductFacts, key: str, quotes: Sequence[str]) -> bool:
    """A phrase states a number that is neither its kind's own value nor in a cited quote."""
    own = values(facts, key)
    quoted = [n.value for quote in quotes for n in numbers_in(quote)]
    return any(
        not any(matches(written, v) for v in own[kind]) and not any(matches(written, v) for v in quoted)
        for kind, written, _ in _phrases(text)
    )


def without_codes(text: str, facts: ProductFacts, key: str) -> str:
    """The sentence with every phrase that states its kind's own value taken out: what the model reads."""
    own = values(facts, key)
    left = text
    for kind, written, match in reversed(list(_phrases(text))):
        if any(matches(written, v) for v in own[kind]):
            left = left[: match.start()] + left[match.end() :]
    left = _DOUBLE.sub(r"\2", _SPACE_BEFORE.sub(r"\1", " ".join(left.split())))
    return left.strip(" ,;:")
