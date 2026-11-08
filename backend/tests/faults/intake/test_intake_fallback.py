"""Fault test (US-02): if intake's run() crashes, its fallback still returns a valid Intent."""

from __future__ import annotations

from pydantic import BaseModel

from app.contracts import (
    STAGE_TIME_LIMITS_S,
    DataClass,
    Difficulty,
    IntakeInput,
    Intent,
    LLMTask,
    StageContext,
    StageName,
    StageStatus,
)
from app.intake import IntakeStage
from app.pipeline import run_stage


class _CrashingLLM:
    """Raises something other than LLMUnavailable, so run() itself fails."""

    def complete(
        self,
        task: LLMTask,
        system: str,
        user: str,
        schema: type[BaseModel],
        *,
        data_class: DataClass,
        difficulty: Difficulty,
        search_id: str | None = None,
        max_output_tokens: int = 1024,
    ) -> BaseModel:
        raise RuntimeError("wrapper bug")


def test_run_raises_and_the_fallback_answers() -> None:
    stage = IntakeStage(_CrashingLLM())  # type: ignore[arg-type]  # deliberately not a real LLMClient
    ctx = StageContext(search_id="srch_fault00001")
    inp = IntakeInput(raw_query="gaming laptop under 80k")

    intent, report = run_stage(stage, inp, ctx, STAGE_TIME_LIMITS_S[StageName.INTAKE])

    assert report.status is StageStatus.FALLBACK
    assert report.fallback_reason == "something went wrong"  # a fixed reason, never the error's own text
    assert isinstance(intent, Intent)
    assert Intent.model_validate_json(intent.model_dump_json()) == intent
    assert (intent.category, intent.detected_by) == ("laptop", "rules")
    assert intent.budget is not None and intent.budget.max is not None and intent.budget.max.amount == 80_000
