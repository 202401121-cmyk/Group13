"""Fault test (US-05): if the interview's run() crashes, its fallback still gives a valid step."""

from __future__ import annotations

import pytest

from app.contracts import (
    STAGE_TIME_LIMITS_S,
    InterviewState,
    InterviewStep,
    InterviewTurn,
    RoughLevel,
    StageContext,
    StageName,
    StageStatus,
)
from app.contracts import examples as ex
from app.interview import InterviewStage
from app.pipeline import run_stage

CTX = StageContext(search_id="srch_fault00001")
LIMIT = STAGE_TIME_LIMITS_S[StageName.INTERVIEW]


def _broken_plan(*args: object, **kwargs: object) -> object:
    raise RuntimeError("plan exploded")


def _state() -> InterviewState:
    return InterviewState(session_id=ex.SESSION_ID, intent=ex.intent(), criteria=ex.criteria())


def test_run_raises_and_the_fallback_asks_the_next_question(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.interview.stage.plan_questions", _broken_plan)
    step, report = run_stage(InterviewStage(), InterviewTurn(state=_state()), CTX, LIMIT)
    assert report.status is StageStatus.FALLBACK
    assert report.fallback_reason == "something went wrong"  # a fixed reason, never the error's own text
    assert isinstance(step, InterviewStep)
    assert InterviewStep.model_validate_json(step.model_dump_json()) == step
    assert step.kind == "question"
    assert step.question is not None and step.question.criterion_key == "cpu_performance"


def test_the_fallback_skips_the_reply_it_could_not_use_and_moves_on(monkeypatch: pytest.MonkeyPatch) -> None:
    first = InterviewStage().run(InterviewTurn(state=_state()), CTX)  # cpu_performance pending
    monkeypatch.setattr("app.interview.stage.plan_questions", _broken_plan)
    step, report = run_stage(InterviewStage(), InterviewTurn(state=first.state, reply_text="High"), CTX, LIMIT)
    assert report.status is StageStatus.FALLBACK
    assert step is not None and step.kind == "question"
    assert step.state.history[0].status == "skipped"
    assert step.question is not None and step.question.criterion_key == "battery_life"


@pytest.mark.parametrize("fields", [{"level": RoughLevel.HIGH}, {"exact_weight": 8.0}], ids=["level", "exact_weight"])
def test_the_fallback_counts_a_level_or_a_weight_as_a_reply(
    monkeypatch: pytest.MonkeyPatch, fields: dict[str, object]
) -> None:
    """Otherwise it would ask the same question again, as if the page had been reloaded."""
    first = InterviewStage().run(InterviewTurn(state=_state()), CTX)
    monkeypatch.setattr("app.interview.stage.plan_questions", _broken_plan)
    turn = InterviewTurn(state=first.state, **fields)  # type: ignore[arg-type]
    step, report = run_stage(InterviewStage(), turn, CTX, LIMIT)
    assert report.status is StageStatus.FALLBACK
    assert step is not None and step.question is not None
    assert step.question.criterion_key == "battery_life"


def test_the_fallback_keeps_an_exact_0_that_was_being_corrected(monkeypatch: pytest.MonkeyPatch) -> None:
    stage = InterviewStage()
    first = stage.run(InterviewTurn(state=_state()), CTX)
    answered = stage.run(InterviewTurn(state=first.state, reply_text="0", exact_weight=0), CTX)
    reopened = stage.go_back(answered.state, "q_cpu_performance")
    monkeypatch.setattr("app.interview.stage.plan_questions", _broken_plan)
    step, _ = run_stage(stage, InterviewTurn(state=reopened.state, reply_text="High"), CTX, LIMIT)
    assert step is not None
    kept = next(e for e in step.state.history if e.question.criterion_key == "cpu_performance")
    assert (kept.status, kept.classification.weight if kept.classification else None) == ("answered", 0.0)


def test_the_fallback_finishes_when_every_question_was_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    step = InterviewStage().run(InterviewTurn(state=_state()), CTX)
    while step.kind != "finished":
        step = InterviewStage().run(InterviewTurn(state=step.state, reply_text="High"), CTX)
    monkeypatch.setattr("app.interview.stage.plan_questions", _broken_plan)
    done, report = run_stage(InterviewStage(), InterviewTurn(state=step.state, reply_text="High"), CTX, LIMIT)
    assert report.status is StageStatus.FALLBACK
    assert done is not None and done.kind == "finished" and done.rubric is not None
    assert {w.weight for w in done.rubric.weights} == {8.0}  # the answers are kept


def test_the_fallback_never_raises_even_if_its_own_steps_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*args: object, **kwargs: object) -> object:
        raise RuntimeError("everything is broken")

    monkeypatch.setattr("app.interview.stage.default_order", broken)
    step = InterviewStage().fallback(InterviewTurn(state=_state()), CTX, RuntimeError("first failure"))
    assert step.kind == "finished"
    assert step.rubric is not None
    assert {w.origin for w in step.rubric.weights} == {"default"}
