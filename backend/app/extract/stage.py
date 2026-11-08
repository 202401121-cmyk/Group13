"""The extract stage. ExtractStage is the stub (US-52), and stays the default and the fallback until P10 (US-14, US-42).

GuardedExtractStage reads one source through the wrapper with every US-28 defence in place: the text only inside the
data block, the answer only as schema JSON, and each claim checked by code. It isn't wired in yet: P10 gives it the real
prompt and wires it, and tests/faults/test_hostile_pages.py must keep passing.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime

from app.contracts import (
    DataClass,
    Difficulty,
    ExtractInput,
    ExtractionResult,
    LLMClient,
    LLMTask,
    LLMUnavailable,
    SourceType,
    StageContext,
    StageName,
    examples,
)

from .parsing import RawExtraction, to_extraction_result
from .prompts import SYSTEM_PROMPT, user_message

log = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


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


class GuardedExtractStage:
    name = StageName.EXTRACT
    is_placeholder = False
    baseline = ExtractStage()

    def __init__(self, llm: LLMClient, now: Callable[[], datetime] = _utc_now) -> None:
        self.llm = llm
        self._now = now

    def run(self, inp: ExtractInput, ctx: StageContext) -> ExtractionResult:
        source = inp.source
        reddit = source.type is SourceType.REDDIT
        try:
            raw = self.llm.complete(
                LLMTask.EXTRACT_CLAIMS,
                SYSTEM_PROMPT,
                user_message(source),
                RawExtraction,
                data_class=DataClass.REDDIT if reddit else DataClass.PUBLIC,
                difficulty=Difficulty.LONG,
                search_id=ctx.search_id,
            )
        except LLMUnavailable:
            log.info("extract: a %s source was dropped: no valid answer", source.type.value)
            return ExtractionResult(source_id=source.id)
        return to_extraction_result(raw, source, inp.criterion_keys, self._now())

    def fallback(self, inp: ExtractInput, ctx: StageContext, error: Exception) -> ExtractionResult:
        return self.baseline.fallback(inp, ctx, error)
