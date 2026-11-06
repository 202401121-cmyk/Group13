"""Match stub (US-52). US-15, DR-02 and DR-06 replace run() with real entity matching; this becomes the fallback."""

from __future__ import annotations

from app.contracts import MatchInput, MatchResult, StageContext, StageName, examples


class MatchStage:
    """Returns the five example products and their aliases."""

    name = StageName.MATCH
    is_placeholder = True

    def run(self, inp: MatchInput, ctx: StageContext) -> MatchResult:
        return examples.match()

    def fallback(self, inp: MatchInput, ctx: StageContext, error: Exception) -> MatchResult:
        return MatchResult.model_validate({**examples.match().model_dump(), "method": "fuzzy"})
