"""Enrich stub (US-52). US-41 replaces run() with targeted look-ups; this becomes the fallback."""

from __future__ import annotations

from app.contracts import EnrichInput, EnrichResult, StageContext, StageName


class EnrichStage:
    """Enriches nothing: no searches, no LLM calls, no new claims."""

    name = StageName.ENRICH
    is_placeholder = True

    def run(self, inp: EnrichInput, ctx: StageContext) -> EnrichResult:
        return EnrichResult()

    def fallback(self, inp: EnrichInput, ctx: StageContext, error: Exception) -> EnrichResult:
        return EnrichResult()
