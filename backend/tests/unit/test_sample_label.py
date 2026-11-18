"""The sample label (US-52, known gap 3): results made by a stub say so, and real results never do.

The label follows the class that made the output, not the stage's name, because a stub lives on as the fallback of
the real class that replaces it (D-20). tests/faults/test_sample_label_faults.py covers the stages that fail.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from dataclasses import fields
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app
import app.contracts as C
from app.cli import main as cli_main
from app.commerce import CommerceStage
from app.contracts import examples as ex
from app.criteria import CriteriaStage
from app.decide import DecideStage, WeightedDecideStage
from app.enrich import EnrichStage
from app.entities import FuzzyMatchStage, MatchStage
from app.explain import ExplainStage, TemplateExplainStage
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
# US-20 replaced explain's Sprint 1 stub, which gave the example explanations: no stub of it is left. Its baseline, the
# template, states code's own numbers, so it is real and never labels a result as a sample.
STUBBED_STAGES = [name for name in SEARCH_STAGES if name != "explain"]
STUBS = [
    ResearchStage,
    ExtractStage,
    MatchStage,
    VerifyStage,
    EnrichStage,
    DecideStage,
    CommerceStage,
]
# The rule (S4-34): a stage class whose output can carry example data says it is a placeholder, so the result is
# labelled a sample; one that returns only empty, researched or code-made output does not. Every stage class in app/
# is named here with its reason, so a new one (a stub above all) can't go unnoticed.
PLACEHOLDER_RULE: dict[str, tuple[bool, str]] = {
    "app.research.stage.ResearchStage": (True, "returns the three example sources"),
    "app.extract.stage.ExtractStage": (True, "returns the example claims and mentions"),
    "app.entities.stage.MatchStage": (True, "returns the five example products"),
    "app.verify.stage.VerifyStage": (True, "returns the example resolved values"),
    "app.decide.stage.DecideStage": (True, "returns the example ranking"),
    "app.commerce.stage.CommerceStage": (True, "returns the two example offers"),
    "app.enrich.stage.EnrichStage": (False, "returns an empty EnrichResult: it adds nothing to the result"),
    "app.explain.stage.TemplateExplainStage": (False, "states code's own numbers in fixed words"),
    "app.intake.stage.IntakeStage": (False, "the shopper's request read by the LLM or the keyword rules"),
    "app.criteria.stage.CriteriaStage": (False, "generated, saved or the fixed sets: no example data"),
    "app.interview.stage.InterviewStage": (False, "the shopper's own answers"),
    "app.interview.adaptive.AdaptiveInterviewStage": (False, "the shopper's own answers, in another order"),
    "app.research.stage.LiveResearchStage": (False, "real searches"),
    "app.extract.stage.GuardedExtractStage": (False, "claims read from the real sources"),
    "app.entities.stage.FuzzyMatchStage": (False, "matches the names research really found"),
    "app.entities.stage.ModelMatchStage": (False, "matches the names research really found"),
    "app.verify.stage.TrustVerifyStage": (False, "values settled from the real claims"),
    "app.decide.stage.WeightedDecideStage": (False, "ranks the real values in code"),
    "app.explain.stage.ExplainStage": (False, "explains code's own numbers"),
    "app.commerce.stage.ShoppingCommerceStage": (False, "real prices, or store search links"),
}
# Wrappers take the flag of the stage they wrap, per instance, so they are not stages of their own here.
WRAPPERS = {
    "app.cli._SeesCounts": "wraps match for the command-line tool's counts",
    "app.extract.recorder.RecordingStage": "wraps extract to record what each source gave",
}


def _stage_classes() -> dict[str, type]:
    """Every class in app/ with a stage name and a run(): what the runner could be given."""
    found: dict[str, type] = {}
    for module in pkgutil.walk_packages(app.__path__, "app."):
        loaded = importlib.import_module(module.name)
        for _, cls in inspect.getmembers(loaded, inspect.isclass):
            ours = cls.__module__ == loaded.__name__
            if ours and isinstance(getattr(cls, "name", None), C.StageName) and callable(getattr(cls, "run", None)):
                found[f"{cls.__module__}.{cls.__qualname__}"] = cls
    return found


# The stubs that put example data into a result: one of them alone among real stages is enough for the note.
EXAMPLE_STUBBED_STAGES = [name for name in STUBBED_STAGES if name != "enrich"]
REAL_STAGES = [
    IntakeStage,
    CriteriaStage,
    InterviewStage,
    FuzzyMatchStage,
    WeightedDecideStage,
    TrustVerifyStage,
    ExplainStage,
    TemplateExplainStage,
]
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
    """Every search stage as its Sprint 1 stub, built one by one: the defaults are real now for match (US-15), verify
    and decide (S3-10b), and the stubs live on as their fallbacks. Explain has no stub left (US-20): its template."""
    return SearchStages(
        research=ResearchStage(),
        extract=ExtractStage(),
        match=MatchStage(),
        verify=VerifyStage(),
        enrich=EnrichStage(),
        decide=DecideStage(),
        explain=TemplateExplainStage(),
        commerce=CommerceStage(),
    )


def all_real() -> SearchStages:
    stubs = stub_stages()
    return SearchStages(**{name: FakeRealStage(getattr(stubs, name)) for name in SEARCH_STAGES})


# --- which classes are placeholders --------------------------------------------------------------


def test_a_stage_is_a_placeholder_only_when_its_output_can_carry_example_data() -> None:
    """Every stage class in app/, by name, with why it is or isn't a placeholder (S4-34)."""
    classes = _stage_classes()
    assert set(classes) == set(PLACEHOLDER_RULE) | set(WRAPPERS)  # a new stage class must be named above
    unset = [
        name for name in PLACEHOLDER_RULE if not any("is_placeholder" in vars(k) for k in classes[name].__mro__[:-1])
    ]
    assert unset == []  # each says it, never left to the runner's default
    wrong = {
        name: reason
        for name, (placeholder, reason) in PLACEHOLDER_RULE.items()
        if classes[name].is_placeholder is not placeholder
    }
    assert wrong == {}
    assert {name for name, (placeholder, _) in PLACEHOLDER_RULE.items() if placeholder} == {
        f"{stub.__module__}.{stub.__qualname__}" for stub in STUBS if stub is not EnrichStage
    }


def test_the_enrich_stub_gives_an_empty_result() -> None:
    """Why it isn't a placeholder: run and fallback both add nothing, so no example data can come from it."""
    context = C.StageContext(search_id="srch_test")
    inp = C.EnrichInput(targets=[])
    assert EnrichStage().run(inp, context) == C.EnrichResult()
    assert EnrichStage().fallback(inp, context, RuntimeError("made up")) == C.EnrichResult()


@pytest.mark.parametrize("stage", REAL_STAGES)
def test_the_real_stages_say_they_are_not(stage: type) -> None:
    assert vars(stage)["is_placeholder"] is False


# --- the label -----------------------------------------------------------------------------------


def test_stub_stages_give_only_the_note() -> None:
    assert run(stub_stages()).coverage_note == SAMPLE_NOTE


def test_stub_stages_are_every_stub() -> None:
    assert [type(getattr(stub_stages(), name)) for name in STUBBED_STAGES] == STUBS
    assert type(stub_stages().explain) is TemplateExplainStage


def test_the_default_stages_with_stub_research_still_give_the_note() -> None:
    """Research and extract are stubs in every test (tests/conftest.py), so their data is a sample however real the
    later stages are: the note says so whatever else it says."""
    note = run(default_search_stages()).coverage_note
    assert note is not None and note.endswith(SAMPLE_NOTE)


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


@pytest.mark.parametrize("field", EXAMPLE_STUBBED_STAGES)
def test_one_stub_among_real_stages_is_enough_for_the_note(field: str) -> None:
    stages = all_real()
    setattr(stages, field, getattr(stub_stages(), field))
    note = run(stages).coverage_note
    assert note is not None and note.endswith(SAMPLE_NOTE)


def test_real_stages_with_the_enrich_stub_give_no_label() -> None:
    """S4-34: every other stage real, enrich still the stub (US-41 is to come): the result is not a sample."""
    stages = all_real()
    stages.enrich = EnrichStage()
    result = run(stages)
    assert SAMPLE_NOTE not in (result.coverage_note or "")


def test_the_same_run_with_the_decide_stub_is_still_labelled() -> None:
    stages = all_real()
    stages.enrich = EnrichStage()
    stages.decide = DecideStage()  # the example ranking: a sample
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
    notes = [line for line in capsys.readouterr().out.splitlines() if line.startswith("Coverage note: ")]
    assert len(notes) == 1 and notes[0].endswith(SAMPLE_NOTE)  # after any note of the real stages (S3-10b)


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
