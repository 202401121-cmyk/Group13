"""The sample label (US-52, known gap 3): results made by a stub say so, and real results never do.

The label follows the class that made the output, not the stage's name, because a stub lives on as the fallback of
the real class that replaces it (D-20). tests/faults/test_sample_label_faults.py covers the stages that fail.
"""

from __future__ import annotations

from dataclasses import fields
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.contracts as C
from app.cli import main as cli_main
from app.commerce import CommerceStage
from app.contracts import examples as ex
from app.criteria import CriteriaStage
from app.decide import DecideStage, WeightedDecideStage
from app.enrich import EnrichStage
from app.entities import FuzzyMatchStage, MatchStage
from app.explain import ExplainStage
from app.extract import ExtractStage
from app.intake import IntakeStage
from app.interview import InterviewStage
from app.main import create_app
from app.pipeline import (
    MAX_NOTE_CHARS,
    SAMPLE_NOTE,
    InMemoryResultStore,
    PipelineRunner,
    SearchStages,
    add_sample_label,
    default_search_stages,
)
from app.research import ResearchStage
from app.verify import TrustVerifyStage, VerifyStage

SEARCH_STAGES = [f.name for f in fields(SearchStages)]
STUBS = [
    ResearchStage,
    ExtractStage,
    MatchStage,
    VerifyStage,
    EnrichStage,
    DecideStage,
    ExplainStage,
    CommerceStage,
]
REAL_STAGES = [IntakeStage, CriteriaStage, InterviewStage, FuzzyMatchStage, WeightedDecideStage, TrustVerifyStage]
ROOM_FOR_THE_NOTE = MAX_NOTE_CHARS - len(SAMPLE_NOTE) - 1


def request() -> C.SearchRequest:
    return C.SearchRequest(session_id=ex.SESSION_ID, raw_query=ex.QUERY, rubric=ex.rubric())


def run(stages: SearchStages) -> C.SearchResult:
    return PipelineRunner(stages, InMemoryResultStore()).run(request())


class FakeRealStage:
    """A real stage, as far as the runner can tell. It happens to give the stub's output, which keeps tests short."""

    is_placeholder = False

    def __init__(self, stub: Any) -> None:
        self.stub = stub
        self.name = stub.name

    def run(self, inp: Any, ctx: C.StageContext) -> Any:
        return self.stub.run(inp, ctx)

    def fallback(self, inp: Any, ctx: C.StageContext, error: Exception) -> Any:
        return self.stub.fallback(inp, ctx, error)


def stub_stages() -> SearchStages:
    """Every search stage as its Sprint 1 stub. Since US-15 the default match is real; MatchStage is its stub."""
    stages = default_search_stages()
    stages.match = MatchStage()
    return stages


def all_real() -> SearchStages:
    stubs = stub_stages()
    return SearchStages(**{name: FakeRealStage(getattr(stubs, name)) for name in SEARCH_STAGES})


# --- which classes are placeholders --------------------------------------------------------------


@pytest.mark.parametrize("stage", STUBS)
def test_every_stub_says_it_is_a_placeholder(stage: type) -> None:
    assert vars(stage)["is_placeholder"] is True


@pytest.mark.parametrize("stage", REAL_STAGES)
def test_the_real_stages_say_they_are_not(stage: type) -> None:
    assert vars(stage)["is_placeholder"] is False


# --- the label -----------------------------------------------------------------------------------


def test_stub_stages_give_only_the_note() -> None:
    assert run(default_search_stages()).coverage_note == SAMPLE_NOTE


def test_a_stub_never_reports_a_failure_that_did_not_happen() -> None:
    context = C.StageContext(search_id="srch_test")
    research_input = C.ResearchInput(rubric=ex.rubric())
    assert ResearchStage().run(research_input, context).failures == []


class _RealResearchThatTimedOut(FakeRealStage):
    def run(self, inp: C.ResearchInput, ctx: C.StageContext) -> C.ResearchResult:
        return ex.research()  # the example carries a YouTube timeout


def test_real_stages_give_no_label_and_keep_their_own_note() -> None:
    stages = all_real()
    stages.research = _RealResearchThatTimedOut(ResearchStage())
    assert run(stages).coverage_note == "YouTube timed out."


def test_with_one_stub_a_real_failure_comes_before_the_note() -> None:
    stages = all_real()
    stages.research = _RealResearchThatTimedOut(ResearchStage())
    stages.decide = DecideStage()
    assert run(stages).coverage_note == f"YouTube timed out. {SAMPLE_NOTE}"


@pytest.mark.parametrize("field", SEARCH_STAGES)
def test_one_stub_among_real_stages_is_enough_for_the_note(field: str) -> None:
    stages = all_real()
    setattr(stages, field, getattr(stub_stages(), field))
    note = run(stages).coverage_note
    assert note is not None and note.endswith(SAMPLE_NOTE)


class _RealResearchWithNoSources(FakeRealStage):
    def run(self, inp: C.ResearchInput, ctx: C.StageContext) -> C.ResearchResult:
        return C.ResearchResult(queries=["best laptop"], sources=[])


def test_a_stub_that_was_skipped_made_nothing_so_it_gives_no_label() -> None:
    stages = all_real()
    stages.research = _RealResearchWithNoSources(ResearchStage())
    stages.extract = ExtractStage()  # a stub, but with no sources it never runs
    result = run(stages)
    extract = next(r for r in result.stage_reports if r.stage is C.StageName.EXTRACT)
    assert extract.status is C.StageStatus.SKIPPED
    assert result.coverage_note is not None
    assert SAMPLE_NOTE not in result.coverage_note


def test_the_note_is_exactly_the_agreed_sentence() -> None:
    assert SAMPLE_NOTE == (
        "These are sample results while live research is being built. They are not real recommendations."
    )


def test_the_api_and_the_cli_show_the_note(capsys: pytest.CaptureFixture[str]) -> None:
    response = TestClient(create_app({})).post("/api/search", json=request().model_dump(mode="json"))
    assert SAMPLE_NOTE in response.json()["coverage_note"]
    assert cli_main(["best laptop for coding under 70k"]) == 0
    assert f"Coverage note: {SAMPLE_NOTE}" in capsys.readouterr().out


# --- fitting the 400-character limit -------------------------------------------------------------


def test_with_no_note_the_label_stands_alone() -> None:
    assert add_sample_label(None) == SAMPLE_NOTE
    assert add_sample_label("") == SAMPLE_NOTE


def test_a_note_that_fits_is_kept_whole() -> None:
    note = "x" * ROOM_FOR_THE_NOTE
    labelled = add_sample_label(note)
    assert labelled == f"{note} {SAMPLE_NOTE}"
    assert len(labelled) == MAX_NOTE_CHARS


@pytest.mark.parametrize("extra", [1, 50, MAX_NOTE_CHARS - ROOM_FOR_THE_NOTE])
def test_a_long_note_gives_way_and_the_label_stays_whole(extra: int) -> None:
    labelled = add_sample_label("x" * (ROOM_FOR_THE_NOTE + extra))
    assert len(labelled) <= MAX_NOTE_CHARS
    assert labelled.endswith(f"... {SAMPLE_NOTE}")
