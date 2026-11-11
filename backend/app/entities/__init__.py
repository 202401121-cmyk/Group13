"""Stage match, in app/entities/ (US-15): FuzzyMatchStage runs in the pipeline; MatchStage is the Sprint 1 stub."""

from .clustering import alias_keys
from .scorer import FUZZY_THRESHOLD, FuzzyScorer, PairScorer
from .stage import FuzzyMatchStage, MatchStage

__all__ = ["FUZZY_THRESHOLD", "FuzzyMatchStage", "FuzzyScorer", "MatchStage", "PairScorer", "alias_keys"]
