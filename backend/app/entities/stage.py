"""The match stage (US-15, DR-02, DR-06): raw names in, one product per real product out.

MatchStage is the Sprint 1 stub, kept as it was (replace, never rewrite: D-20). FuzzyMatchStage replaces it in the
pipeline. In S3-15 the trained model takes over, and FuzzyMatchStage becomes its fallback.
"""

from __future__ import annotations

import logging

from app.contracts import MatchInput, MatchResult, StageContext, StageName, examples

from .clustering import match_names, unmerged
from .scorer import FuzzyScorer, PairScorer

log = logging.getLogger(__name__)


class FuzzyMatchStage:
    """Matches names by rules and the fuzzy scorer (US-15 criteria 1, 3, 4 and 6). The scorer is swappable (S3-15)."""

    name = StageName.MATCH
    is_placeholder = False

    def __init__(self, scorer: PairScorer | None = None) -> None:
        self.scorer: PairScorer = scorer if scorer is not None else FuzzyScorer()

    def run(self, inp: MatchInput, ctx: StageContext) -> MatchResult:
        return match_names(inp, self.scorer)

    def fallback(self, inp: MatchInput, ctx: StageContext, error: Exception) -> MatchResult:
        """Any error in run(): every name kept apart, method "fuzzy", and one log line (criterion 6)."""
        log.warning("match fell back: every name is kept apart (%s)", type(error).__name__)
        return unmerged(inp)


class MatchStage:
    """Returns the five example products and their aliases."""

    name = StageName.MATCH
    is_placeholder = True

    def run(self, inp: MatchInput, ctx: StageContext) -> MatchResult:
        return examples.match()

    def fallback(self, inp: MatchInput, ctx: StageContext, error: Exception) -> MatchResult:
        return MatchResult.model_validate({**examples.match().model_dump(), "method": "fuzzy"})
