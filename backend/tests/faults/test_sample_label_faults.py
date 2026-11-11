"""Fault tests (US-52, known gap 3): when a stage fails, the label follows the class that really made the output.

A real stage that fails and falls back to its stub puts made-up data in the result, so the label must appear. A real
stage with a real fallback must not be called a sample. And however long the failures make the note, the result stays
valid and the label stays whole.
"""

from __future__ import annotations

import time
from dataclasses import fields
from typing import Any

import pytest

import app.contracts as C
from app.contracts import examples as ex
from app.pipeline import (
    MAX_NOTE_CHARS,
    SAMPLE_NOTE,
    InMemoryResultStore,
    PipelineRunner,
    SearchStages,
    default_search_stages,
)
from app.research import ResearchStage
from tests.unit.test_sample_label import FakeRealStage, all_real, stub_stages

SEARCH_STAGES = [f.name for f in fields(SearchStages)]


def request() -> C.SearchRequest:
    return C.SearchRequest(session_id=ex.SESSION_ID, raw_query=ex.QUERY, rubric=ex.rubric())


def run(stages: SearchStages, **options: Any) -> C.SearchResult:
    return PipelineRunner(stages, InMemoryResultStore(), **options).run(request())


class _FailsToItsStub(FakeRealStage):
    """The shape D-20 asks for: a real class that fails, and whose fallback hands over to the stub it replaced."""

    def __init__(self, stub: Any) -> None:
        super().__init__(stub)
        self.baseline = stub

    def run(self, inp: Any, ctx: C.StageContext) -> Any:
        raise RuntimeError("the real stage crashed")

    def fallback(self, inp: Any, ctx: C.StageContext, error: Exception) -> Any:
        return self.baseline.run(inp, ctx)


@pytest.mark.parametrize("field", SEARCH_STAGES)
def test_a_real_stage_that_falls_back_to_its_stub_gives_the_note(field: str) -> None:
    stages = all_real()
    setattr(stages, field, _FailsToItsStub(getattr(stub_stages(), field)))
    result = run(stages)
    report = next(r for r in result.stage_reports if r.stage is C.StageName(field))
    assert report.status is C.StageStatus.FALLBACK
    assert result.coverage_note is not None
    assert result.coverage_note.endswith(SAMPLE_NOTE)
    assert (
        "used a simpler fallback: something went wrong." in result.coverage_note
    )  # what the note already said is kept
    assert "crashed" not in result.coverage_note  # a fixed reason, never the error's own text


class _TimesOutToItsStub(_FailsToItsStub):
    def run(self, inp: Any, ctx: C.StageContext) -> Any:
        time.sleep(0.3)  # in a background thread: the test only waits for the limit below
        return self.baseline.run(inp, ctx)


def test_a_real_stage_that_times_out_and_uses_its_stub_gives_the_note() -> None:
    stages = all_real()
    stages.verify = _TimesOutToItsStub(default_search_stages().verify)
    result = run(stages, time_limits={C.StageName.VERIFY: 0.05})
    report = next(r for r in result.stage_reports if r.stage is C.StageName.VERIFY)
    assert report.status is C.StageStatus.TIMEOUT
    assert result.coverage_note is not None and result.coverage_note.endswith(SAMPLE_NOTE)


class _FailsToARealBaseline(FakeRealStage):
    """A second real version replaced the first: its fallback is real too, so nothing here is a sample."""

    def __init__(self, stub: Any) -> None:
        super().__init__(stub)
        self.baseline = FakeRealStage(stub)

    def run(self, inp: Any, ctx: C.StageContext) -> Any:
        raise RuntimeError("the newest version crashed")

    def fallback(self, inp: Any, ctx: C.StageContext, error: Exception) -> Any:
        return self.baseline.run(inp, ctx)


def test_a_real_stage_with_a_real_baseline_gives_no_label() -> None:
    stages = all_real()
    stages.research = _FailsToARealBaseline(ResearchStage())
    result = run(stages)
    assert result.coverage_note is not None
    assert "Research used a simpler fallback: something went wrong." in result.coverage_note
    assert SAMPLE_NOTE not in result.coverage_note


class _FailsToItsOwnFallback(FakeRealStage):
    def run(self, inp: Any, ctx: C.StageContext) -> Any:
        raise RuntimeError("the real stage crashed")


def test_a_real_stage_with_its_own_fallback_gives_no_label() -> None:
    stages = all_real()
    stages.explain = _FailsToItsOwnFallback(default_search_stages().explain)
    result = run(stages)
    assert result.coverage_note is not None
    assert SAMPLE_NOTE not in result.coverage_note


def test_a_stub_whose_own_run_fails_still_gives_the_note() -> None:
    stages = all_real()
    stages.research = ResearchStage(fail=True)
    result = run(stages)
    assert result.coverage_note is not None and result.coverage_note.endswith(SAMPLE_NOTE)


class _StubThatFailsCompletely:
    """A stub whose run() and fallback() both fail: it made nothing, so it is no reason for the label."""

    name = C.StageName.MATCH
    is_placeholder = True

    def run(self, inp: C.MatchInput, ctx: C.StageContext) -> C.MatchResult:
        raise RuntimeError("matcher crashed")

    def fallback(self, inp: C.MatchInput, ctx: C.StageContext, error: Exception) -> C.MatchResult:
        raise RuntimeError("fuzzy matcher crashed too")


def test_a_stub_that_made_nothing_gives_no_label() -> None:
    stages = all_real()
    stages.match = _StubThatFailsCompletely()
    result = run(stages)
    assert result.partial
    assert result.coverage_note is not None and "Match failed" in result.coverage_note
    assert SAMPLE_NOTE not in result.coverage_note


class _FailsWithALongReason:
    """Any stub whose run() fails with the longest reason a report can hold."""

    is_placeholder = True

    def __init__(self, stub: Any) -> None:
        self.stub = stub
        self.name = stub.name

    def run(self, inp: Any, ctx: C.StageContext) -> Any:
        raise RuntimeError("x" * 300)

    def fallback(self, inp: Any, ctx: C.StageContext, error: Exception) -> Any:
        return self.stub.fallback(inp, ctx, error)


def test_the_longest_note_there_can_be_still_fits_with_the_label() -> None:
    stages = default_search_stages()
    for field in SEARCH_STAGES:
        setattr(stages, field, _FailsWithALongReason(getattr(stages, field)))
    result = run(stages)
    assert result.coverage_note is not None
    assert len(result.coverage_note) <= MAX_NOTE_CHARS
    assert result.coverage_note.endswith(f"... {SAMPLE_NOTE}")
    assert C.SearchResult.model_validate_json(result.model_dump_json()) == result
