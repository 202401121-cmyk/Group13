"""Fault tests (US-52): a failing stage or a failing save must never crash a search."""

from __future__ import annotations

from typing import Any

import pytest

import app.contracts as C
from app.contracts import examples as ex
from app.pipeline import InMemoryResultStore, PipelineRunner, default_search_stages
from app.research import ResearchStage


def _request() -> C.SearchRequest:
    return C.SearchRequest(session_id=ex.SESSION_ID, raw_query=ex.QUERY, rubric=ex.rubric())


def _report(result: C.SearchResult, stage: C.StageName) -> C.StageReport:
    return next(r for r in result.stage_reports if r.stage is stage)


def test_failing_research_falls_back_and_the_search_finishes() -> None:
    stages = default_search_stages()
    stages.research = ResearchStage(fail=True)
    result = PipelineRunner(stages, InMemoryResultStore()).run(_request())

    research = _report(result, C.StageName.RESEARCH)
    assert research.status is C.StageStatus.FALLBACK
    assert research.fallback_reason == "simulated research failure"
    assert not result.partial
    assert result.stage_reports[-1].stage is C.StageName.COMMERCE  # every stage still had its turn
    assert result.sources == []  # the fallback found nothing, and nothing was invented
    assert C.SearchResult.model_validate_json(result.model_dump_json()) == result
    assert result.coverage_note is not None
    assert "Research used a simpler fallback: simulated research failure." in result.coverage_note


class _BrokenStore:
    def save(self, result: C.SearchResult) -> bool:
        raise ConnectionError("database is down")


class _RefusingStore:
    def save(self, result: C.SearchResult) -> bool:
        return False


def test_a_store_that_raises_only_marks_the_result_unsaved() -> None:
    result = PipelineRunner(default_search_stages(), _BrokenStore()).run(_request())
    assert result.saved is False
    assert result.decision is not None


def test_a_store_that_returns_false_marks_the_result_unsaved() -> None:
    result = PipelineRunner(default_search_stages(), _RefusingStore()).run(_request())
    assert result.saved is False


class _BrokenMatch:
    """Both run() and fallback() fail, so the runner must stop and return what it has."""

    name = C.StageName.MATCH

    def run(self, inp: C.MatchInput, ctx: C.StageContext) -> C.MatchResult:
        raise RuntimeError("matcher crashed")

    def fallback(self, inp: C.MatchInput, ctx: C.StageContext, error: Exception) -> C.MatchResult:
        raise RuntimeError("fuzzy matcher crashed too")


def test_a_failed_fallback_stops_the_search_with_a_partial_result() -> None:
    stages = default_search_stages()
    stages.match = _BrokenMatch()
    result = PipelineRunner(stages, InMemoryResultStore()).run(_request())

    statuses = {r.stage: r.status for r in result.stage_reports}
    assert statuses[C.StageName.MATCH] is C.StageStatus.FAILED
    assert statuses[C.StageName.VERIFY] is C.StageStatus.SKIPPED
    assert statuses[C.StageName.COMMERCE] is C.StageStatus.SKIPPED
    assert result.partial
    assert result.products == []
    assert result.decision is None
    assert result.coverage_note is not None and "Match failed" in result.coverage_note


SEARCH_STAGES = ["research", "extract", "match", "verify", "enrich", "decide", "explain", "commerce"]


class _AlwaysFails:
    """Any search stage with a run() that always raises; its fallback is the real stage's own."""

    def __init__(self, real: Any) -> None:
        self.real = real
        self.name = real.name

    def run(self, inp: Any, ctx: C.StageContext) -> Any:
        raise RuntimeError(f"simulated {self.name.value} failure")

    def fallback(self, inp: Any, ctx: C.StageContext, error: Exception) -> Any:
        return self.real.fallback(inp, ctx, error)


@pytest.mark.parametrize("field", SEARCH_STAGES)
def test_any_failing_search_stage_falls_back_and_the_search_finishes(field: str) -> None:
    stages = default_search_stages()
    setattr(stages, field, _AlwaysFails(getattr(stages, field)))
    result = PipelineRunner(stages, InMemoryResultStore()).run(_request())

    report = _report(result, C.StageName(field))
    assert report.status is C.StageStatus.FALLBACK
    assert report.fallback_reason == f"simulated {field} failure"
    assert not result.partial
    assert result.stage_reports[-1].stage is C.StageName.COMMERCE  # every later stage still had its turn
    assert C.SearchResult.model_validate_json(result.model_dump_json()) == result
    assert result.coverage_note is not None
