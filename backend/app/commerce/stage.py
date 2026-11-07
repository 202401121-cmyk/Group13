"""Commerce stub (US-52). US-24 replaces run() with real store look-ups; this becomes the fallback."""

from __future__ import annotations

from app.contracts import CommerceInput, CommerceResult, StageContext, StageName, examples


class CommerceStage:
    """Returns the two example offers and one store-search link."""

    name = StageName.COMMERCE
    is_placeholder = True

    def run(self, inp: CommerceInput, ctx: StageContext) -> CommerceResult:
        return examples.commerce()

    def fallback(self, inp: CommerceInput, ctx: StageContext, error: Exception) -> CommerceResult:
        # No prices is honest; a wrong price is not.
        return CommerceResult()
