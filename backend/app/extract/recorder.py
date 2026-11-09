"""Keeps each source's claims for the CLI's --claims (US-14), which runs one search in its own process.

SearchResult has no place for claims, and the server must not hold them after a run: Reddit text and the claims drawn
from it stay in memory for the run only (D-53). So this wraps the stage only when --claims asks for it.
"""

from __future__ import annotations

from typing import Any

from app.contracts import ExtractInput, ExtractionResult, Stage, StageContext, StageName


class RecordingStage:
    name = StageName.EXTRACT

    def __init__(self, inner: Stage[ExtractInput, ExtractionResult]) -> None:
        self.inner = inner
        self.is_placeholder = bool(getattr(inner, "is_placeholder", False))
        self.results: dict[str, ExtractionResult] = {}

    def run(self, inp: ExtractInput, ctx: StageContext) -> ExtractionResult:
        result = self.inner.run(inp, ctx)
        self.results[inp.source.id] = result
        return result

    def fallback(self, inp: ExtractInput, ctx: StageContext, error: Exception) -> ExtractionResult:
        return self.inner.fallback(inp, ctx, error)

    def __getattr__(self, name: str) -> Any:
        """Anything else the runner asks the stage, such as its coverage note, goes to the stage."""
        return getattr(self.inner, name)
