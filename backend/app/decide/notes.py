"""The decide stage's notes for the coverage note (US-18, S3-10b), read back from the result so they always match it.

- Left out: no shown product had any information on a criterion. DecideResult has no notes field, so the result records
  a left-out criterion in every ranked product's contributions: state "unknown", weight 0.
- Not ranked: no shown product had evidence on any criterion, so every ranked product has no score.
Criterion names and fixed words only, never what the shopper typed or a source said.
"""

from __future__ import annotations

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
    words = [attribute_words(k) for k in keys]
    named = words[0] if len(words) == 1 else f"{', '.join(words[:-1])} and {words[-1]}"
    pronoun = "it was" if len(words) == 1 else "they were"
    return f"We found no information on {named} for any product, so {pronoun} left out of the scores."


def not_ranked(result: DecideResult) -> bool:
    """Were the products listed with no score, as nothing could be ranked on?"""
    return bool(result.ranked) and all(r.score is None for r in result.ranked)


def decide_note(result: DecideResult) -> str | None:
    """The stage's note: "Not ranked: ..." when nothing was scored (every criterion was left out then, so naming them
    would only repeat it), else the left-out note, else None."""
    if not_ranked(result):
        return NOT_RANKED_NOTE
    return left_out_note(result)
