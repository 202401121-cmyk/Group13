"""Contract test for every stage (US-52): valid input in -> valid output out, from run() and from fallback()."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

import app.contracts as C
from app.commerce import CommerceStage
from app.contracts import examples as ex
from app.criteria import CriteriaStage
from app.decide import DecideStage
from app.enrich import EnrichStage
from app.entities import MatchStage
from app.explain import ExplainStage
from app.extract import ExtractStage
from app.intake import IntakeStage
from app.interview import InterviewStage
from app.research import ResearchStage
from app.verify import VerifyStage

WEIGHT_KEYS = [w.key for w in ex.rubric().weights]


def _state() -> C.InterviewState:
    return C.InterviewState(session_id=ex.SESSION_ID, intent=ex.intent(), criteria=ex.criteria())


# (stage, a valid input, the output contract)
CASES: list[tuple[Any, BaseModel, type[BaseModel]]] = [
    (IntakeStage(), C.IntakeInput(raw_query=ex.QUERY), C.Intent),
    (CriteriaStage(), C.CriteriaInput(category="laptop", category_label="Laptop", use_case="coding"), C.CriteriaSet),
    (InterviewStage(), C.InterviewTurn(state=_state()), C.InterviewStep),
    (ResearchStage(), C.ResearchInput(rubric=ex.rubric()), C.ResearchResult),
    (
        ExtractStage(),
        C.ExtractInput(source=ex.sources()[0], criterion_keys=WEIGHT_KEYS, category_label="laptop"),
        C.ExtractionResult,
    ),
    (MatchStage(), C.MatchInput(mentions=ex.mentions()), C.MatchResult),
    (
        VerifyStage(),
        C.VerifyInput(match=ex.match(), claims=ex.claims(), sources=ex.sources(), criterion_keys=WEIGHT_KEYS),
        C.VerifyResult,
    ),
    (EnrichStage(), C.EnrichInput(targets=[]), C.EnrichResult),
    (
        DecideStage(),
        C.DecideInput(rubric=ex.rubric(), products=ex.match().products, values=ex.verify().values),
        C.DecideResult,
    ),
    (
        ExplainStage(),
        C.ExplainInput(ranked=ex.decision().ranked, claims=ex.claims(), user_priorities=WEIGHT_KEYS[:3]),
        C.ExplainResult,
    ),
    (
        CommerceStage(),
        C.CommerceInput(products=ex.match().products[:3], budget=ex.rubric().budget),
        C.CommerceResult,
    ),
]
IDS = [type(stage).__name__ for stage, _, _ in CASES]


def _still_valid(out: BaseModel) -> None:
    """Re-validating from JSON proves the object obeys every rule of its contract."""
    assert type(out).model_validate_json(out.model_dump_json()) == out


def test_every_stage_has_a_case() -> None:
    assert {stage.name for stage, _, _ in CASES} == set(C.StageName)


@pytest.mark.parametrize(("stage", "inp", "out_type"), CASES, ids=IDS)
def test_run_returns_its_output_contract(stage: Any, inp: BaseModel, out_type: type[BaseModel]) -> None:
    out = stage.run(inp, C.StageContext(search_id="srch_test00001"))
    assert isinstance(out, out_type)
    _still_valid(out)


@pytest.mark.parametrize(("stage", "inp", "out_type"), CASES, ids=IDS)
def test_fallback_returns_its_output_contract(stage: Any, inp: BaseModel, out_type: type[BaseModel]) -> None:
    out = stage.fallback(inp, C.StageContext(search_id="srch_test00001"), RuntimeError("boom"))
    assert isinstance(out, out_type)
    _still_valid(out)


def test_extract_keeps_only_claims_from_its_own_source() -> None:
    source = ex.sources()[2]
    inp = C.ExtractInput(source=source, criterion_keys=WEIGHT_KEYS, category_label="laptop")
    out = ExtractStage().run(inp, C.StageContext(search_id="srch_test00001"))
    assert out.claims
    assert all(c.source_id == source.id for c in out.claims)


def test_interview_fallback_asks_the_next_question_in_default_order() -> None:
    # US-05: the fallback keeps the interview going in default order instead of stopping at once.
    step = InterviewStage().fallback(
        C.InterviewTurn(state=_state()), C.StageContext(search_id="srch_test00001"), RuntimeError("boom")
    )
    assert step.kind == "question"
    assert step.question is not None
    assert step.question.criterion_key == "cpu_performance"  # the highest default weight


def test_interview_fallback_builds_a_default_rubric() -> None:
    # Every question already asked (and skipped): the fallback finishes with the default weights.
    state = _state()
    skipped = [
        C.AskedQuestion(
            question=C.InterviewQuestion(id=f"q_{c.key}", criterion_key=c.key, text=f"About {c.label}?"),
            status="skipped",
        )
        for c in state.criteria.criteria
    ]
    state = C.InterviewState.model_validate({**state.model_dump(), "history": [q.model_dump() for q in skipped]})
    step = InterviewStage().fallback(
        C.InterviewTurn(state=state), C.StageContext(search_id="srch_test00001"), RuntimeError("boom")
    )
    assert step.kind == "finished"
    assert step.rubric is not None
    defaults = {c.key: c.default_weight for c in state.criteria.criteria}
    assert {w.key: w.weight for w in step.rubric.weights} == defaults
    assert step.rubric.hard_rules == ex.intent().hard_rules


def test_research_can_be_told_to_fail() -> None:
    with pytest.raises(RuntimeError, match="simulated research failure"):
        ResearchStage(fail=True).run(C.ResearchInput(rubric=ex.rubric()), C.StageContext(search_id="srch_test00001"))
