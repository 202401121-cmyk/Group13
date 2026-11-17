"""Fault test (US-04): if the smart interview's run() crashes, the runner's fallback still gives a valid step, in the
fixed order and with no beliefs (empty beliefs mean the fixed order was used)."""

from __future__ import annotations

import pytest

from app.contracts import (
    STAGE_TIME_LIMITS_S,
    InterviewState,
    InterviewStep,
    InterviewTurn,
    StageContext,
    StageName,
    StageStatus,
)
from app.contracts import examples as ex
from app.interview import AdaptiveInterviewStage
from app.pipeline import run_stage

CTX = StageContext(search_id="srch_fault00004")
LIMIT = STAGE_TIME_LIMITS_S[StageName.INTERVIEW]


def _broken_plan(*args: object, **kwargs: object) -> object:
    raise RuntimeError("plan exploded")


def _state() -> InterviewState:
    return InterviewState(session_id=ex.SESSION_ID, intent=ex.intent(), criteria=ex.criteria())


def test_run_raises_and_the_fixed_fallback_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    first = AdaptiveInterviewStage().run(InterviewTurn(state=_state()), CTX)
    assert first.state.beliefs  # the smart turn carried them
    # the plan breaks for both orders, so run() itself raises and the runner calls the stage's fallback
    monkeypatch.setattr("app.interview.stage.plan_questions", _broken_plan)
    monkeypatch.setattr("app.interview.adaptive.plan_questions", _broken_plan)
    step, report = run_stage(AdaptiveInterviewStage(), InterviewTurn(state=first.state, reply_text="High"), CTX, LIMIT)
    assert report.status is StageStatus.FALLBACK
    assert isinstance(step, InterviewStep)
    assert InterviewStep.model_validate_json(step.model_dump_json()) == step
    assert (step.ordering, step.state.beliefs) == ("fixed_order", {})
    assert step.question is not None and step.question.criterion_key == "battery_life"


def test_the_fallback_finishes_with_a_rubric_when_everything_was_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    stage = AdaptiveInterviewStage()
    step = stage.run(InterviewTurn(state=_state()), CTX)
    for _ in range(6):
        step = stage.run(InterviewTurn(state=step.state, reply_text="Medium"), CTX)
    monkeypatch.setattr("app.interview.stage.plan_questions", _broken_plan)
    monkeypatch.setattr("app.interview.adaptive.plan_questions", _broken_plan)
    done, report = run_stage(stage, InterviewTurn(state=step.state, reply_text="Medium"), CTX, LIMIT)
    assert report.status is StageStatus.FALLBACK
    assert done is not None and done.kind == "finished" and done.rubric is not None
    assert (done.ordering, done.state.beliefs) == ("fixed_order", {})
