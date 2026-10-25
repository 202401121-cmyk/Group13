"""The Sprint 1 rubric (US-05): a weight for every criterion, with the right origin and uncertainty."""

from __future__ import annotations

import math

from app.contracts import AskedQuestion, InterviewState, ReplyClassification, ReplyType, Rubric
from app.contracts import examples as ex
from app.interview.plan import Plan
from app.interview.questions import question_for
from app.interview.rubric_builder import build_rubric, default_rubric

CRITERIA = {c.key: c for c in ex.criteria().criteria}


def state(*history: AskedQuestion) -> InterviewState:
    return InterviewState(session_id=ex.SESSION_ID, intent=ex.intent(), criteria=ex.criteria(), history=list(history))


def answered(key: str, weight: float, reply_type: ReplyType = ReplyType.ANSWER) -> AskedQuestion:
    extra = {"corrects_question_id": f"q_{key}"} if reply_type is ReplyType.CORRECTION else {}
    reply = ReplyClassification(reply_type=reply_type, weight=weight, classified_by="fallback", **extra)
    return AskedQuestion(question=question_for(CRITERIA[key]), status="answered", classification=reply)


def skipped(key: str) -> AskedQuestion:
    return AskedQuestion(question=question_for(CRITERIA[key]), status="skipped")


def rows(rubric: Rubric) -> dict[str, tuple[float, str, float]]:
    return {w.key: (w.weight, w.origin, w.uncertainty) for w in rubric.weights}


def test_origins_and_uncertainties() -> None:
    rubric = build_rubric(
        state(
            answered("cpu_performance", 9),
            answered("battery_life", 3, ReplyType.CORRECTION),
            skipped("keyboard_comfort"),
        ),
        Plan(to_ask=(), inferred={"display_quality": 3.0}),
    )
    got = rows(rubric)
    assert got["cpu_performance"] == (9.0, "answered", 0.2)
    assert got["battery_life"] == (3.0, "answered", 0.2)  # a correction counts as an answer
    assert got["display_quality"] == (3.0, "answered", 0.6)  # settled by a topic rule
    assert got["keyboard_comfort"] == (6.0, "skipped", 0.5)  # default weight
    assert got["weight"] == (5.0, "default", 0.5)  # never asked
    assert set(got) == set(CRITERIA)
    assert math.isclose(sum(w.normalized for w in rubric.weights), 1.0)


def test_shares_follow_the_weights() -> None:
    rubric = build_rubric(state(answered("cpu_performance", 10)))
    shares = {w.key: w.normalized for w in rubric.weights}
    total = 10 + sum(c.default_weight for k, c in CRITERIA.items() if k != "cpu_performance")
    assert math.isclose(shares["cpu_performance"], 10 / total)


def test_all_zero_weights_still_add_up_to_one() -> None:
    rubric = build_rubric(state(*(answered(k, 0) for k in CRITERIA)))
    assert all(math.isclose(w.normalized, 1 / len(CRITERIA)) for w in rubric.weights)


def test_hard_rules_exclusions_budget_and_use_come_from_the_intent() -> None:
    intent = ex.intent()
    rubric = build_rubric(state())
    assert rubric.hard_rules == intent.hard_rules
    assert rubric.exclusions == intent.exclusions
    assert rubric.budget == intent.budget
    assert (rubric.use_case, rubric.region, rubric.category) == (intent.use_case, intent.region, "laptop")


def test_default_rubric_ignores_answers() -> None:
    rubric = default_rubric(state(answered("cpu_performance", 1)))
    assert {o for _, o, _ in rows(rubric).values()} == {"default"}
    assert rows(rubric)["cpu_performance"][0] == CRITERIA["cpu_performance"].default_weight
