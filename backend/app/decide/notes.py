"""The note naming criteria left out of the score (US-18): no shown product had any information on them.

DecideResult has no notes field, so the result records a left-out criterion in every ranked product's contributions:
state "unknown", weight 0. These read it back, so the note always matches the result.
"""

from __future__ import annotations

from app.contracts import DecideResult, ValueState

from .units import attribute_words


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
