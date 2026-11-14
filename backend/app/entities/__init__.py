"""Stage match, in app/entities/ (US-15): ModelMatchStage runs in the pipeline, with FuzzyMatchStage as its baseline;
MatchStage is the Sprint 1 stub."""

from .clustering import alias_keys
from .scorer import FUZZY_THRESHOLD, FuzzyScorer, PairScorer
from .stage import FuzzyMatchStage, MatchStage, ModelMatchStage

__all__ = [
    "FUZZY_THRESHOLD",
    "FuzzyMatchStage",
    "FuzzyScorer",
    "MatchStage",
    "ModelMatchStage",
    "PairScorer",
    "alias_keys",
]
