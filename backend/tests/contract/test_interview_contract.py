"""Contract test for the interview (US-05): the example InterviewTurn gives a valid InterviewStep."""

from __future__ import annotations

from app.contracts import InterviewState, InterviewStep, InterviewTurn, StageContext
from app.contracts import examples as ex
from app.interview import InterviewStage

CTX = StageContext(search_id="srch_contract001")


def _valid(step: InterviewStep) -> None:
    assert InterviewStep.model_validate_json(step.model_dump_json()) == step


def test_the_example_turn_gives_a_valid_step_every_turn_until_the_rubric() -> None:
    state = InterviewState(session_id=ex.SESSION_ID, intent=ex.intent(), criteria=ex.criteria())
    step = InterviewStage().run(InterviewTurn(state=state), CTX)
    _valid(step)
    assert step.kind == "question"
    for reply in ["High", "huh?", "7", "Skip", "Low", "Medium", "10", "2", "5"]:
        if step.kind == "finished":
            break
        step = InterviewStage().run(InterviewTurn(state=step.state, reply_text=reply), CTX)
        _valid(step)
    assert step.kind == "finished"
    assert step.rubric is not None
    assert step.rubric.category == ex.intent().category
    assert {w.key for w in step.rubric.weights} == {c.key for c in ex.criteria().criteria}
