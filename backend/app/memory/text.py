"""A remembered preference in plain words (US-06), and the limits of public.preferences (S5-1's gaps).

- Text is at most 200 characters, with no space at either end, and never the NUL character, which Postgres text and
  jsonb can't hold. NUL is dropped; other control characters become spaces, and runs of spaces become one.
- A weight is kept to two decimal places, between 0 and 10: `real` can't hold a weight such as 1e-46 (it underflows),
  and 7.3 is then read back as 7.3.
"""

from __future__ import annotations

import re

from app.contracts import ROUGH_LEVEL_WEIGHTS, RoughLevel

MAX_TEXT = 200  # PreferenceItem.text and the table's check
NUL = chr(0)
_CONTROL = re.compile(r"[\x01-\x1f\x7f]")
_SPACES = re.compile(r"\s+")
LEVEL_WORDS: dict[RoughLevel, str] = {
    RoughLevel.LOW: "low importance",
    RoughLevel.GOOD_ENOUGH: "good enough will do",
    RoughLevel.MEDIUM: "medium importance",
    RoughLevel.HIGH: "high importance",
}


def clean_text(text: str, limit: int = MAX_TEXT) -> str:
    """The text without NUL (dropped) or other control characters (spaces), single-spaced, trimmed, and at most `limit`
    characters."""
    flat = _SPACES.sub(" ", _CONTROL.sub(" ", text.replace(NUL, ""))).strip()
    return flat[:limit].rstrip()


def round_weight(weight: float) -> float:
    """Two decimal places, between 0 and 10; never -0.0."""
    return round(min(10.0, max(0.0, float(weight))), 2) + 0.0


def weight_words(weight: float) -> str:
    """A rough level's words for a level's weight (US-08), else "7 out of 10"."""
    for level, at in ROUGH_LEVEL_WEIGHTS.items():
        if at == weight:
            return LEVEL_WORDS[level]
    return f"{round_weight(weight):g} out of 10"


def weight_text(label: str, weight: float) -> str:
    """'Battery life: high importance'."""
    return clean_text(f"{label}: {weight_words(weight)}")


def rule_text(source_text: str) -> str:
    """'Must have: at least 16 GB of RAM', in the shopper's own words."""
    return clean_text(f"Must have: {source_text}")


def exclusion_text(value: str, kind: str = "brand") -> str:
    """'Never Acme' for a brand; 'Never Acme Book 14 (model)' for a model, feature or seller, so two leave-outs of the
    same words but different kinds never read alike."""
    return clean_text(f"Never {value}" if kind == "brand" else f"Never {value} ({kind})")


def without_nul(value: str) -> str:
    """The text with only NUL taken out (Postgres text and jsonb can't hold it); nothing else changes."""
    return value.replace(NUL, "")
