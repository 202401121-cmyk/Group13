"""How likely two names are one product (US-15, DECIDED 9): the one place a matcher plugs in.

The rules in clustering.py decide first (codes, numbers, brands, editions). Only a pair they leave open is scored. S3-15
swaps FuzzyScorer for the trained model behind the same two members, and nothing else changes.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from rapidfuzz import fuzz

# D-56, ml/MODELS.md card 8: until our own labels exist, the RapidFuzz baseline uses the threshold chosen on WDC
# Products 80% corner cases (80cc20rnd000un, title, token_set_ratio): 0.79.
FUZZY_THRESHOLD = 0.79


class PairScorer(Protocol):
    """Scores pairs of normalised names from 0 to 1. A pair at or above `threshold` is the same product."""

    threshold: float

    def score(self, pairs: Sequence[tuple[str, str]]) -> list[float]: ...


class FuzzyScorer:
    """The fuzzy baseline: rapidfuzz token_set_ratio, divided by 100 (ml/scripts/match_baseline.py)."""

    def __init__(self, threshold: float = FUZZY_THRESHOLD) -> None:
        self.threshold = threshold

    def score(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
        return [fuzz.token_set_ratio(left, right) / 100 for left, right in pairs]
