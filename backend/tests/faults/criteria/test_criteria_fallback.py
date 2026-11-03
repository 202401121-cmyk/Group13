"""Fault test (US-03): if the criteria stage's run() crashes, its fallback still gives valid criteria."""

from __future__ import annotations

from pydantic import BaseModel

from app.contracts import (
    STAGE_TIME_LIMITS_S,
    CriteriaInput,
    CriteriaSet,
    DataClass,
    Difficulty,
    LLMTask,
    StageContext,
    StageName,
    StageStatus,
)
from app.criteria import CriteriaStage, fallback_set
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


class _BrokenStore:
    def get(self, category: str, use_case: str | None) -> CriteriaSet | None:
        raise OSError("disk unreadable")

    def put(self, criteria_set: CriteriaSet) -> None:
        raise OSError("disk full")


INP = CriteriaInput(category="mixer_grinder", category_label="Mixer grinder", use_case="daily chutneys")


def _check(stage: CriteriaStage) -> None:
    result, report = run_stage(
        stage, INP, StageContext(search_id="srch_fault00001"), STAGE_TIME_LIMITS_S[StageName.CRITERIA]
    )
    assert report.status is StageStatus.FALLBACK
    assert report.fallback_reason
    assert isinstance(result, CriteriaSet)
    assert CriteriaSet.model_validate_json(result.model_dump_json()) == result
    assert result == fallback_set(INP)
    assert result.criteria[0].key == "cooking_results"  # the kitchen appliance family


def test_run_raises_and_the_fallback_answers() -> None:
    _check(CriteriaStage(_CrashingLLM()))  # type: ignore[arg-type]  # deliberately not a real LLMClient


def test_a_broken_store_also_falls_back() -> None:
    _check(CriteriaStage(None, _BrokenStore()))
