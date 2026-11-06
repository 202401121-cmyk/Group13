"""Decide stub (US-52). US-17 to US-19 replace run() with plain-Python scoring; this becomes the fallback."""

from __future__ import annotations

from app.contracts import DecideInput, DecideResult, StageContext, StageName, examples


class DecideStage:
    """Returns the example ranking: three ranked, one filtered out, one can't-tell."""

    name = StageName.DECIDE
    is_placeholder = True

    def run(self, inp: DecideInput, ctx: StageContext) -> DecideResult:
        return examples.decision()

    def fallback(self, inp: DecideInput, ctx: StageContext, error: Exception) -> DecideResult:
        return examples.decision()
