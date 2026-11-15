"""The decide stage's notes for the coverage note (US-18, S3-10b), read back from the result so they always match it.

- Left out: no shown product had any information on a criterion. DecideResult has no notes field, so the result records
  a left-out criterion in every ranked product's contributions: state "unknown", weight 0.
- Not ranked: no shown product had evidence on any criterion, so every ranked product has no score.
Criterion names and fixed words only, never what the shopper typed or a source said.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.contracts import DecideResult, ValueState

from .units import attribute_words

NOT_RANKED_NOTE = "Not ranked: we found no scores for your criteria."


def left_out(result: DecideResult) -> list[str]:
    """The criterion keys left out of every ranked product's score, in the rubric's order."""
    if not result.ranked:
        return []
    first = result.ranked[0].contributions
    return [
        c.criterion_key
        for c in first
        if c.state is ValueState.UNKNOWN
        and all(
            any(o.criterion_key == c.criterion_key and o.state is ValueState.UNKNOWN for o in r.contributions)
            for r in result.ranked
        )
    ]


def left_out_note(result: DecideResult) -> str | None:
    """ "We found no information on battery life for any product, so it was left out of the scores." None if none."""
    keys = left_out(result)
    if not keys:
        return None
    pronoun = "it was" if len(keys) == 1 else "they were"
    return f"We found no information on {_listed(keys)} for any product, so {pronoun} left out of the scores."


def not_ranked(result: DecideResult) -> bool:
    """Were the products listed with no score, as nothing could be ranked on?"""
    return bool(result.ranked) and all(r.score is None for r in result.ranked)


def _listed(keys: Sequence[str]) -> str:
    words = [attribute_words(k) for k in keys]
    return words[0] if len(words) == 1 else f"{', '.join(words[:-1])} and {words[-1]}"


def compared_note(keys: Sequence[str]) -> str | None:
    """ "We scored battery life and weight by comparing what the sources say about each product." None if none
    (US-18): those criteria were scored from the products' stated facts, not from opinions."""
    if not keys:
        return None
    return f"We scored {_listed(keys)} by comparing what the sources say about each product."


def decide_note(result: DecideResult, compared: Sequence[str] = ()) -> str | None:
    """The stage's note: "Not ranked: ..." when nothing was scored (every criterion was left out then, so naming them
    would only repeat it); else the left-out note and the compared note, each when there is one; else None."""
    if not_ranked(result):
        return NOT_RANKED_NOTE
    parts = [note for note in (left_out_note(result), compared_note(compared)) if note is not None]
    return " ".join(parts) or None
