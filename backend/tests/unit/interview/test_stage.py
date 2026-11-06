"""The interview stage (US-05): one question per turn, rephrase and skip, topic rules, go back and correct."""

from __future__ import annotations

import math
from time import monotonic

import pytest

from app.contracts import (
    MAX_UNCLEAR_PER_QUESTION,
    CriteriaInput,
    CriteriaSet,
    Criterion,
    InterviewState,
    InterviewStep,
    InterviewTurn,
    ReplyType,
    StageContext,
)
from app.contracts import examples as ex
from app.criteria import fallback_set
from app.interview import InterviewStage
from app.interview.stage import DONE_MESSAGE, REPHRASE_MESSAGE

CTX = StageContext(search_id="srch_interview01")
STAGE = InterviewStage()


def state_for(criteria: CriteriaSet | None = None) -> InterviewState:
    criteria = criteria or ex.criteria()
    intent = ex.intent()
    if criteria.category != intent.category:
        label = criteria.category.replace("_", " ").capitalize()
        intent = intent.model_validate({**intent.model_dump(), "category": criteria.category, "category_label": label})
    return InterviewState(session_id=ex.SESSION_ID, intent=intent, criteria=criteria)


def start(criteria: CriteriaSet | None = None) -> InterviewStep:
    return STAGE.run(InterviewTurn(state=state_for(criteria)), CTX)


def reply(step: InterviewStep, text: str) -> InterviewStep:
    return STAGE.run(InterviewTurn(state=step.state, reply_text=text), CTX)


def finish(step: InterviewStep, text: str = "Medium") -> InterviewStep:
    for _ in range(25):
        if step.kind == "finished":
            return step
        step = reply(step, text)
    raise AssertionError("the interview did not finish")


def entry(step: InterviewStep, key: str) -> object:
    return next(e for e in step.state.history if e.question.criterion_key == key)


# ex.criteria() default weights: cpu 8, battery 7, keyboard 6, value 6, display 5, weight 5, build 4
ORDER = [
    "cpu_performance",
    "battery_life",
    "keyboard_comfort",
    "value_for_money",
    "display_quality",
    "weight",
    "build_durability",
]


def test_the_first_turn_asks_the_most_important_question() -> None:
    step = start()
    assert step.kind == "question"
    assert step.question is not None
    assert step.question.criterion_key == "cpu_performance"
    assert step.message == step.question.text == "CPU performance: how much does it matter to you?"
    assert step.question.quick_replies == ["Low", "Medium", "High", "Skip"]
    assert step.questions_asked == 1
    assert [e.status for e in step.state.history] == ["pending"]
    assert step.ordering == "fixed_order"


def test_an_answer_is_recorded_and_the_next_question_follows() -> None:
    step = reply(start(), "High")
    assert step.question is not None and step.question.criterion_key == "battery_life"
    first = step.state.history[0]
    assert (first.status, first.reply_text) == ("answered", "High")
    assert first.classification is not None and first.classification.weight == 8.0
    assert step.questions_asked == 2


def test_questions_follow_the_default_order() -> None:
    step, asked = start(), []
    while step.kind != "finished":
        assert step.question is not None
        asked.append(step.question.criterion_key)
        step = reply(step, "7")
    assert asked == ORDER


def test_skip_moves_on() -> None:
    step = reply(start(), "Skip")
    assert step.state.history[0].status == "skipped"
    assert step.question is not None and step.question.criterion_key == "battery_life"


def test_unclear_rephrases_then_gives_up_after_two() -> None:
    first = start()
    again = reply(first, "hmm not sure")
    assert again.kind == "rephrase"
    assert again.message == REPHRASE_MESSAGE
    assert again.question == first.question
    assert again.state.history[0].unclear_count == 1
    assert again.state.history[0].status == "pending"

    moved_on = reply(again, "whatever you think")
    gave_up = moved_on.state.history[0]
    assert (gave_up.status, gave_up.unclear_count) == ("skipped", MAX_UNCLEAR_PER_QUESTION)
    assert moved_on.kind == "question"
    assert moved_on.question is not None and moved_on.question.criterion_key == "battery_life"


def test_the_finished_step_carries_a_valid_rubric_for_every_criterion() -> None:
    step = start()
    step = reply(step, "High")  # cpu: answered 8
    step = reply(step, "Skip")  # battery: skipped
    step = finish(step, "3")  # the rest: answered 3
    assert step.kind == "finished"
    assert step.message == DONE_MESSAGE
    assert step.question is None
    assert step.rubric is not None
    assert InterviewStep.model_validate_json(step.model_dump_json()) == step
    weights = {w.key: w for w in step.rubric.weights}
    assert set(weights) == set(ORDER)
    assert math.isclose(sum(w.normalized for w in step.rubric.weights), 1.0)
    assert (weights["cpu_performance"].weight, weights["cpu_performance"].origin) == (8.0, "answered")
    assert weights["cpu_performance"].uncertainty == 0.2
    assert (weights["battery_life"].weight, weights["battery_life"].origin) == (7.0, "skipped")
    assert weights["battery_life"].uncertainty == 0.5
    assert step.questions_asked == len(ORDER)
    assert step.rubric.budget == ex.intent().budget


def test_a_low_answer_settles_its_topic_in_the_rubric() -> None:
    def crit(key: str, weight: float, vfm: bool = False) -> Criterion:
        return Criterion(
            key=key,
            label=key.replace("_", " ").capitalize(),
            definition=f"How the laptop does on {key.replace('_', ' ')} in daily use at home.",
            default_weight=weight,
            is_value_for_money=vfm,
        )

    criteria = CriteriaSet(
        category="laptop",
        source="generated",
        criteria=[
            crit("battery_life", 9),
            crit("charging_speed", 7),
            crit("cpu_speed", 6),
            crit("keyboard_feel", 5),
            crit("speakers", 4),
            crit("value_for_money", 3, vfm=True),
        ],
    )
    step = reply(start(criteria), "Low")  # battery_life: 2, which settles charging_speed
    assert step.question is not None and step.question.criterion_key == "cpu_speed"
    done = finish(step)
    assert done.rubric is not None
    charging = next(w for w in done.rubric.weights if w.key == "charging_speed")
    assert (charging.weight, charging.origin, charging.uncertainty) == (2.0, "answered", 0.6)
    assert "charging_speed" not in {e.question.criterion_key for e in done.state.history}


def test_go_back_and_correct_updates_the_rubric() -> None:
    done = finish(start(), "High")
    battery = next(w for w in done.rubric.weights if w.key == "battery_life")  # type: ignore[union-attr]
    assert battery.weight == 8.0

    back = STAGE.go_back(done.state, "q_battery_life")
    assert back.kind == "question"
    assert back.question is not None and back.question.id == "q_battery_life"
    assert entry(back, "battery_life").status == "pending"  # type: ignore[attr-defined]

    corrected = reply(back, "Low")
    fixed = entry(corrected, "battery_life")
    assert fixed.status == "answered"  # type: ignore[attr-defined]
    assert fixed.classification.reply_type is ReplyType.CORRECTION  # type: ignore[attr-defined]
    assert fixed.classification.corrects_question_id == "q_battery_life"  # type: ignore[attr-defined]
    assert corrected.kind == "finished"
    battery = next(w for w in corrected.rubric.weights if w.key == "battery_life")  # type: ignore[union-attr]
    assert (battery.weight, battery.origin) == (2.0, "answered")


def test_going_back_drops_the_unanswered_question_and_it_returns_later() -> None:
    step = reply(reply(start(), "High"), "High")  # cpu, battery answered; keyboard pending
    back = STAGE.go_back(step.state, "q_cpu_performance")
    assert [e.question.criterion_key for e in back.state.history] == ["cpu_performance", "battery_life"]
    after = reply(back, "Medium")
    assert after.question is not None and after.question.criterion_key == "keyboard_comfort"


def test_two_unclear_replies_while_correcting_keep_the_old_answer() -> None:
    step = reply(start(), "High")
    back = STAGE.go_back(step.state, "q_cpu_performance")
    kept = reply(reply(back, "???"), "no idea")
    cpu = entry(kept, "cpu_performance")
    assert cpu.status == "answered"  # type: ignore[attr-defined]
    assert cpu.classification.weight == 8.0  # type: ignore[attr-defined]


def test_go_back_to_a_question_not_answered_yet_is_refused() -> None:
    step = start()
    with pytest.raises(ValueError, match="not been answered"):
        STAGE.go_back(step.state, "q_cpu_performance")  # still pending
    with pytest.raises(ValueError, match="not been answered"):
        STAGE.go_back(step.state, "q_display_quality")  # never asked


def test_a_reload_asks_the_pending_question_again() -> None:
    step = reply(start(), "High")
    again = STAGE.run(InterviewTurn(state=step.state), CTX)
    assert again.question == step.question
    assert again.state == step.state


def test_cooking_results_reads_correctly() -> None:
    inp = CriteriaInput(category="mixer_grinder", category_label="Mixer grinder")
    step = start(fallback_set(inp))
    assert step.question is not None
    assert step.question.text == "Cooking results: how much does it matter to you?"


def test_every_step_uses_fixed_order() -> None:
    step = start()
    while step.kind != "finished":
        assert step.ordering == "fixed_order"
        step = reply(step, "Skip")
    assert step.ordering == "fixed_order"


def test_a_ten_question_interview_is_fast() -> None:
    criteria = CriteriaSet(
        category="laptop",
        source="generated",
        criteria=[
            Criterion(
                key=f"criterion_{i}",
                label=f"Criterion {i}",
                definition=f"How well the product does on measure number {i} in daily use.",
                default_weight=10 - i,
                is_value_for_money=i == 9,
            )
            for i in range(10)
        ],
    )
    started = monotonic()
    step = start(criteria)
    turns = 0
    while step.kind != "finished":
        step = reply(step, "6")
        turns += 1
    elapsed = monotonic() - started
    assert turns == 10 and step.questions_asked == 10
    assert elapsed < 0.1, f"a full 10-question interview took {elapsed * 1000:.0f} ms"
