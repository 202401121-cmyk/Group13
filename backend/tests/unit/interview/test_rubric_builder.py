"""The Sprint 1 rubric (US-05): a weight for every criterion, with the right origin and uncertainty."""

from __future__ import annotations

import math
import random

import pytest

from app.contracts import (
    ROUGH_LEVEL_WEIGHTS,
    AskedQuestion,
    InterviewState,
    InterviewStep,
    InterviewTurn,
    ReplyClassification,
    ReplyType,
    RoughLevel,
    Rubric,
    StageContext,
)
from app.contracts import examples as ex
from app.interview import InterviewStage
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


# --- rough and exact answers together (US-08) ------------------------------------------------------

STAGE = InterviewStage()
CTX = StageContext(search_id="srch_rubric00001")


def interview(pick: list[dict[str, object]]) -> InterviewStep:
    """Run the interview to the end, taking each answer from `pick` in turn."""
    step = STAGE.run(InterviewTurn(state=state()), CTX)
    for turn in range(25):
        if step.kind == "finished":
            return step
        fields = pick[turn % len(pick)]
        step = STAGE.run(InterviewTurn(state=step.state, reply_text="x", **fields), CTX)
    raise AssertionError("the interview did not finish")


@pytest.mark.parametrize("level", list(RoughLevel))
def test_each_level_reaches_the_rubric_with_its_table_weight(level: RoughLevel) -> None:
    done = interview([{"level": level}])
    assert done.rubric is not None
    assert rows(done.rubric)["cpu_performance"] == (ROUGH_LEVEL_WEIGHTS[level], "answered", 0.2)


@pytest.mark.parametrize("seed", range(40))
def test_the_rubric_sums_to_1_after_any_mix_of_rough_and_exact_answers(seed: int) -> None:
    chooser = random.Random(seed)
    answers: list[dict[str, object]] = [{"level": level} for level in RoughLevel]
    answers += [{"exact_weight": weight} for weight in (0.0, 0.5, 3.3, 7.5, 10.0)]
    done = interview([chooser.choice(answers) for _ in range(10)])
    assert done.rubric is not None
    assert math.isclose(sum(w.normalized for w in done.rubric.weights), 1.0)


def test_the_rubric_sums_to_1_when_every_answer_is_zero() -> None:
    done = interview([{"exact_weight": 0.0}])
    assert done.rubric is not None
    assert math.isclose(sum(w.normalized for w in done.rubric.weights), 1.0)


def test_an_exact_0_reaches_the_rubric_as_an_answer() -> None:
    done = interview([{"exact_weight": 0.0}, {"level": RoughLevel.HIGH}])
    assert done.rubric is not None
    assert rows(done.rubric)["cpu_performance"] == (0.0, "answered", 0.2)
