"""Extract stub (US-52). US-14 and US-42 replace run() with LLM claim extraction; this becomes the fallback."""

from __future__ import annotations

from app.contracts import ExtractInput, ExtractionResult, StageContext, StageName, examples


class ExtractStage:
    """Returns the example claims that belong to this one source, plus the example mentions."""

    name = StageName.EXTRACT
    is_placeholder = True

    def run(self, inp: ExtractInput, ctx: StageContext) -> ExtractionResult:
        return ExtractionResult(
            source_id=inp.source.id,
            claims=[c for c in examples.claims() if c.source_id == inp.source.id],
            mentions=examples.mentions(),
        )

    def fallback(self, inp: ExtractInput, ctx: StageContext, error: Exception) -> ExtractionResult:
        # Nothing read from this source is better than a guess (build rule 5).
        return ExtractionResult(source_id=inp.source.id)
