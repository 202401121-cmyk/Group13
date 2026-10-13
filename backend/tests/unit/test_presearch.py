"""The pre-search flow: intake -> criteria -> interview (US-52)."""

from __future__ import annotations

import pytest

import app.contracts as C
from app.contracts import examples as ex
from app.presearch import interview_turn, make_criteria, start_request


def test_presearch_flow_ends_with_a_rubric() -> None:
    intent = start_request("best laptop for coding under 70k")
    assert intent.raw_query == "best laptop for coding under 70k"
    assert intent.ready_for_research

    criteria = make_criteria(intent)
    assert criteria.category == intent.category

    state = C.InterviewState(session_id=ex.SESSION_ID, intent=intent, criteria=criteria)
    first = interview_turn(C.InterviewTurn(state=state))
    assert first.kind == "question"
    assert first.question is not None
    assert first.question.criterion_key == criteria.criteria[0].key
    assert first.message == first.question.text
    assert first.questions_asked == 1

    # US-05: "medium" records weight 5 and the interview moves on, one question per turn, until it finishes.
    second = interview_turn(C.InterviewTurn(state=first.state, reply_text="medium"))
    assert second.kind == "question"
    assert second.question is not None and second.question.id != first.question.id

    last = second
    for _ in range(len(criteria.criteria)):
        if last.kind == "finished":
            break
        last = interview_turn(C.InterviewTurn(state=last.state, reply_text="medium"))
    assert last.kind == "finished"
    assert last.question is None
    assert last.rubric is not None
    assert {w.key for w in last.rubric.weights} == {c.key for c in criteria.criteria}
    assert {(w.weight, w.origin) for w in last.rubric.weights} == {(5.0, "answered")}


def test_criteria_need_a_known_category() -> None:
    with pytest.raises(ValueError, match="no product category"):
        make_criteria(ex.unclear_intent())
