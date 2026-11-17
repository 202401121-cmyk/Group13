"""The smart interview (US-04, D-69): the next question is the criterion the app is least sure of that also matters
most, and the interview stops once the top 3 are settled or after 10 questions. A planner error never stops the turn:
it goes on in the fixed order (criterion 5).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import pytest

from app.contracts import (
    Budget,
    CriteriaSet,
    Criterion,
    Intent,
    InterviewState,
    InterviewStep,
    InterviewTurn,
    LLMTask,
    Money,
    RoughLevel,
    RubricEdit,
    StageContext,
    WeightBelief,
)
from app.contracts import examples as ex
from app.interview import AdaptiveInterviewStage, InterviewStage
from app.interview.adaptive import next_key, settled
from app.interview.beliefs import LEVEL_SPREADS, BeliefSettings, beliefs_for, updated
from app.interview.parsing import RawReply
from app.interview.rubric_builder import apply_edits, with_edits_of
from tests.fakes.llm import FakeLLMClient

CTX = StageContext(search_id="srch_smartinterview")
# D-69's own numbers: one rough answer settles a criterion (sd under 1.2), so the stop shows after 3 answers.
STOPS_AT_TOP_3 = BeliefSettings(prior_sd=2.5, exact_sd=1.5, settled_sd=1.2)
# Settles only an answer surer than "medium", so the interview runs on to its limits.
RUNS_ON = BeliefSettings(prior_sd=2.5, exact_sd=1.5, settled_sd=0.5)
# ex.criteria() default weights: cpu 8, battery 7, keyboard 6, value 6, display 5, weight 5, build 4
FIXED = [
    "cpu_performance",
    "battery_life",
    "keyboard_comfort",
    "value_for_money",
    "display_quality",
    "weight",
    "build_durability",
]


def unclear_llm() -> FakeLLMClient:
    """A reply classifier (US-39) that reads every reply code can't place as unclear, so the stage rephrases."""
    return FakeLLMClient({LLMTask.CLASSIFY_REPLY: RawReply(reply_type="unclear")})


def belief(mean: float, sd: float) -> WeightBelief:
    return WeightBelief(mean=mean, sd=sd)


def criterion(key: str, label: str, weight: float, *, value: bool = False) -> Criterion:
    return Criterion(
        key=key,
        label=label,
        definition=f"How good the {label.lower()} is.",
        default_weight=weight,
        is_value_for_money=value,
    )


def state_for(criteria: CriteriaSet | None = None, budget_inr: float | None = None) -> InterviewState:
    criteria = criteria or ex.criteria()
    intent = ex.intent()
    changes: dict[str, object] = {}
    if criteria.category != intent.category:
        changes |= {"category": criteria.category, "category_label": criteria.category.replace("_", " ").capitalize()}
    if budget_inr is not None:
        changes["budget"] = Budget(max=Money(amount=budget_inr)).model_dump()
    if changes:
        intent = Intent.model_validate({**intent.model_dump(), **changes})
    return InterviewState(session_id=ex.SESSION_ID, intent=intent, criteria=criteria)


def start(stage: InterviewStage, state: InterviewState | None = None) -> InterviewStep:
    return stage.run(InterviewTurn(state=state or state_for()), CTX)


def reply(stage: InterviewStage, step: InterviewStep, text: str) -> InterviewStep:
    return stage.run(InterviewTurn(state=step.state, reply_text=text), CTX)


def run_through(stage: InterviewStage, step: InterviewStep, replies: Sequence[str]) -> list[InterviewStep]:
    steps = [step]
    for i in range(40):
        if steps[-1].kind == "finished":
            return steps
        steps.append(reply(stage, steps[-1], replies[i % len(replies)]))
    raise AssertionError("the interview did not finish")


def asked(step: InterviewStep) -> list[str]:
    return [e.question.criterion_key for e in step.state.history]


# --- choosing the next question -------------------------------------------------------------------------------------


def test_the_next_question_is_the_largest_mean_times_sd() -> None:
    beliefs = {"a": belief(5, 2.5), "b": belief(8, 1.0), "c": belief(6, 2.5)}  # 12.5, 8 and 15
    order = ["b", "c", "a"]
    assert next_key(beliefs, ["a", "b", "c"], order) == "c"
    assert next_key(beliefs, ["a", "b"], order) == "a"  # only among the criteria the plan still allows
    assert next_key(beliefs, [], order) is None


def test_a_tie_goes_to_the_fixed_order() -> None:
    beliefs = {"a": belief(6, 2.5), "c": belief(6, 2.5)}
    assert next_key(beliefs, ["a", "c"], ["c", "a"]) == "c"
    assert next_key(beliefs, ["a", "c"], ["a", "c"]) == "a"


# --- when to stop ---------------------------------------------------------------------------------------------------


def test_the_interview_is_settled_when_each_of_the_top_3_is_under_the_bar() -> None:
    order = ["a", "b", "c", "d", "e"]
    beliefs = {"a": belief(9, 1.0), "b": belief(8, 1.1), "c": belief(7, 1.0), "d": belief(6, 2.5), "e": belief(1, 2.5)}
    assert settled(beliefs, order, order, STOPS_AT_TOP_3)  # d and e are not in the top 3, so they may stay unsure
    assert not settled({**beliefs, "b": belief(8, 1.3)}, order, order, STOPS_AT_TOP_3)  # one of the 3 over the bar
    risen = {**beliefs, "d": belief(7.5, 2.5)}  # d now has a higher mean than c, and nobody asked about it
    assert not settled(risen, order, order, STOPS_AT_TOP_3)


def test_a_criterion_the_interview_can_not_ask_never_blocks_the_stop() -> None:
    order = ["a", "b", "c", "d"]
    beliefs = {"a": belief(9, 1.0), "b": belief(8, 1.1), "c": belief(7, 1.0), "d": belief(7.5, 2.5)}
    assert settled(beliefs, ["a", "b", "c"], order, STOPS_AT_TOP_3)  # d was skipped or settled by a topic rule


def test_fewer_than_3_criteria_in_play_are_judged_on_their_own() -> None:
    beliefs = {"a": belief(9, 1.0), "b": belief(8, 2.5)}
    assert settled(beliefs, ["a"], ["a", "b"], STOPS_AT_TOP_3)
    assert not settled(beliefs, ["a", "b"], ["a", "b"], STOPS_AT_TOP_3)
    assert settled(beliefs, [], ["a", "b"], STOPS_AT_TOP_3)  # nothing left to ask about


# --- through the stage ----------------------------------------------------------------------------------------------


def test_every_step_says_information_gain_and_carries_the_beliefs_of_its_answers() -> None:
    stage = AdaptiveInterviewStage(llm=unclear_llm())
    first = start(stage)
    assert (first.kind, first.ordering) == ("question", "information_gain")
    assert first.question is not None and first.question.criterion_key == "cpu_performance"  # mean 8 x sd: the largest
    assert first.state.beliefs == beliefs_for(first.state, stage.settings)

    second = reply(stage, first, "High")
    assert second.ordering == "information_gain"
    assert second.state.beliefs == beliefs_for(second.state, stage.settings)
    assert second.state.beliefs["cpu_performance"].sd < stage.settings.prior_sd

    unclear = reply(stage, second, "hmm, no idea")
    assert (unclear.kind, unclear.ordering) == ("rephrase", "information_gain")
    assert unclear.state.beliefs == second.state.beliefs

    back = stage.go_back(unclear.state, "q_cpu_performance")
    assert (back.kind, back.ordering) == ("question", "information_gain")
    assert back.state.beliefs["cpu_performance"] == belief(8.0, stage.settings.prior_sd)  # its answer is reopened

    done = run_through(stage, back, ["Medium"])[-1]
    assert (done.kind, done.ordering) == ("finished", "information_gain")
    assert done.state.beliefs == beliefs_for(done.state, stage.settings)


def test_the_interview_stops_once_the_top_3_are_settled() -> None:
    stage = AdaptiveInterviewStage(settings=STOPS_AT_TOP_3)
    steps = run_through(stage, start(stage), ["High"])
    done = steps[-1]
    assert done.kind == "finished" and done.rubric is not None
    assert done.questions_asked == 3
    assert asked(done) == ["cpu_performance", "battery_life", "keyboard_comfort"]
    # keyboard (6) came before value for money (6): equal mean x sd, so the fixed order broke the tie
    assert [s.question.criterion_key for s in steps[:-1] if s.question] == asked(done)


def test_going_back_and_answering_low_reopens_the_interview_and_asks_the_next_criterion() -> None:
    stage = AdaptiveInterviewStage(settings=STOPS_AT_TOP_3)
    done = run_through(stage, start(stage), ["High"])[-1]
    back = stage.go_back(done.state, "q_cpu_performance")
    step = reply(stage, back, "Low")
    # cpu is now about 2.9, so value for money (6, still unsure) is in the top 3 and is asked
    assert step.kind == "question" and step.question is not None
    assert step.question.criterion_key == "value_for_money"
    expected = updated(belief(8.0, 2.5), 2.0, LEVEL_SPREADS[RoughLevel.LOW])
    assert step.state.beliefs["cpu_performance"] == expected
    assert step.state.beliefs == beliefs_for(step.state, STOPS_AT_TOP_3)
    corrected = step.state.history[0]
    assert corrected.classification is not None and corrected.classification.reply_type == "correction"
    final = reply(stage, step, "High")
    assert final.kind == "finished" and final.questions_asked == 4


def test_the_tuned_numbers_still_stop_once_the_top_3_are_good_enough_answers() -> None:
    """With the shipped numbers only a "good enough" answer settles a criterion (sd 0.49 under 0.7), so the stop fires
    when the 3 highest means are all such answers: here every other default is lower than "good enough"."""
    keys = [("hinge", 3.6), ("fan", 3.5), ("ports", 3.4), ("speaker", 1.0), ("webcam", 1.0)]
    criteria = [criterion(f"{k}_quality", f"{k.capitalize()} quality", w) for k, w in keys]
    criteria.append(criterion("value_for_money", "Value for money", 1.0, value=True))
    stage = AdaptiveInterviewStage()
    state = state_for(CriteriaSet(category="laptop", criteria=criteria, source="fallback"))
    done = run_through(stage, start(stage, state), ["Good enough"])[-1]
    assert done.kind == "finished" and done.questions_asked == 3
    assert asked(done) == ["hinge_quality", "fan_quality", "ports_quality"]
    assert all(done.state.beliefs[k].sd < stage.settings.settled_sd for k in asked(done))


def test_a_reload_asks_the_pending_question_again_with_the_same_beliefs() -> None:
    stage = AdaptiveInterviewStage()
    step = reply(stage, start(stage), "High")
    again = stage.run(InterviewTurn(state=step.state), CTX)  # no reply: the page was reloaded
    assert (again.kind, again.ordering) == ("question", "information_gain")
    assert again.question == step.question
    assert again.state == step.state
    assert again.questions_asked == step.questions_asked


def test_with_the_tuned_numbers_the_questions_and_rubric_are_the_fixed_orders() -> None:
    """Accuracy first (Om, 17 Nov 2026): the tuned stop keeps every question the fixed order asks."""
    replies = ["High", "Medium", "Low", "7", "Skip", "Good enough", "3"]
    smart = run_through(AdaptiveInterviewStage(), start(AdaptiveInterviewStage()), replies)[-1]
    fixed = run_through(InterviewStage(), start(InterviewStage()), replies)[-1]
    assert asked(smart) == asked(fixed) == FIXED
    assert smart.rubric == fixed.rubric
    assert fixed.ordering == "fixed_order" and fixed.state.beliefs == {}


def test_the_same_inputs_give_the_same_questions_and_beliefs() -> None:
    replies = ["High", "Low", "Medium", "Good enough", "7", "Skip"]
    runs = []
    for _ in range(2):
        stage = AdaptiveInterviewStage(settings=STOPS_AT_TOP_3)
        runs.append([s.model_dump() for s in run_through(stage, start(stage), replies)])
    assert runs[0] == runs[1]


def test_the_question_limit_still_ends_the_interview(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.interview.adaptive.MAX_QUESTIONS", 4)
    stage = AdaptiveInterviewStage(settings=RUNS_ON)
    done = run_through(stage, start(stage), ["Medium"])[-1]
    assert done.kind == "finished" and done.questions_asked == 4


def test_ten_criteria_end_after_ten_questions_when_nothing_settles() -> None:
    keys = ["hinge", "fan", "ports", "speaker", "webcam", "trackpad", "lid", "bezel", "keys"]
    criteria = [criterion(f"{k}_quality", f"{k.capitalize()} quality", 9 - i * 0.5) for i, k in enumerate(keys)]
    criteria.append(criterion("value_for_money", "Value for money", 4, value=True))
    stage = AdaptiveInterviewStage(settings=RUNS_ON)
    state = state_for(CriteriaSet(category="laptop", criteria=criteria, source="fallback"))
    done = run_through(stage, start(stage, state), ["Medium"])[-1]
    assert done.kind == "finished" and done.questions_asked == 10


def test_a_topic_settled_by_a_low_answer_is_never_asked_and_never_blocks_the_stop() -> None:
    phone = CriteriaSet(
        category="smartphone",
        criteria=[
            criterion("battery_life", "Battery life", 8),
            criterion("charging_speed", "Charging speed", 7),
            criterion("camera_quality", "Camera quality", 6),
            criterion("value_for_money", "Value for money", 6, value=True),
            criterion("display_quality", "Display quality", 5),
            criterion("storage_space", "Storage space", 4),
        ],
        source="fallback",
    )
    stage = AdaptiveInterviewStage(settings=STOPS_AT_TOP_3)
    first = start(stage, state_for(phone))
    done = run_through(stage, reply(stage, first, "Low"), ["High"])[-1]
    # charging (default 7) would top the means, but "Low" for battery settled its topic (interview_rules.yaml)
    assert asked(done) == ["battery_life", "camera_quality", "value_for_money", "display_quality"]
    assert done.rubric is not None
    charging = next(w for w in done.rubric.weights if w.key == "charging_speed")
    assert charging.weight == 2.0


def test_a_premium_question_under_a_low_budget_is_still_skipped() -> None:
    laptop = CriteriaSet(
        category="laptop",
        criteria=[
            criterion("cpu_performance", "CPU performance", 8),
            criterion("oled_display", "OLED display", 7),
            criterion("battery_life", "Battery life", 6),
            criterion("value_for_money", "Value for money", 6, value=True),
            criterion("keyboard_comfort", "Keyboard comfort", 5),
            criterion("weight", "Weight", 4),
        ],
        source="fallback",
    )
    stage = AdaptiveInterviewStage(settings=RUNS_ON)
    done = run_through(stage, start(stage, state_for(laptop, budget_inr=40_000)), ["Medium"])[-1]
    assert "oled_display" not in asked(done)
    assert done.questions_asked == 5


# --- the planner failing: the turn goes on in the fixed order (criterion 5) ----------------------------------------


@pytest.mark.parametrize("broken", ["next_key", "settled", "beliefs_for"])
def test_a_planner_error_continues_the_turn_in_the_fixed_order(
    broken: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    stage = AdaptiveInterviewStage()
    first = start(stage)

    def explode(*_: object, **__: object) -> object:
        raise RuntimeError("the shopper's secret words")

    monkeypatch.setattr(f"app.interview.adaptive.{broken}", explode)
    with caplog.at_level(logging.DEBUG, logger="app.interview"):
        step = reply(stage, first, "High")
    assert step.kind == "question" and step.question is not None
    assert step.question.criterion_key == "battery_life"  # the fixed order's next question
    assert (step.ordering, step.state.beliefs) == ("fixed_order", {})
    assert step.state.history[0].status == "answered"  # the reply was kept, never dropped
    records = [r for r in caplog.records if r.name.startswith("app.interview")]
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING and records[0].exc_info is None
    assert "fixed order" in records[0].getMessage() and "RuntimeError" in records[0].getMessage()
    assert "secret" not in caplog.text

    monkeypatch.undo()
    after = reply(stage, step, "Medium")
    assert after.ordering == "information_gain"  # the next turn tries the planner again
    assert after.state.beliefs == beliefs_for(after.state, stage.settings)


def test_a_planner_error_on_the_first_turn_asks_the_fixed_orders_first_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(*_: object, **__: object) -> object:
        raise ValueError("bad numbers")

    monkeypatch.setattr("app.interview.adaptive.next_key", explode)
    step = start(AdaptiveInterviewStage())
    assert step.question is not None and step.question.criterion_key == "cpu_performance"
    assert (step.ordering, step.state.beliefs) == ("fixed_order", {})


def test_beliefs_that_cannot_be_worked_out_on_a_rephrase_say_fixed_order(monkeypatch: pytest.MonkeyPatch) -> None:
    stage = AdaptiveInterviewStage(llm=unclear_llm())
    first = start(stage)

    def explode(*_: object, **__: object) -> object:
        raise ArithmeticError("no")

    monkeypatch.setattr("app.interview.adaptive.beliefs_for", explode)
    step = reply(stage, first, "hmm")
    assert (step.kind, step.ordering, step.state.beliefs) == ("rephrase", "fixed_order", {})


# --- the beliefs survive every rebuild (D-69 #6) --------------------------------------------------------------------


def test_a_rubric_edit_leaves_the_beliefs_as_the_answers_give_them() -> None:
    stage = AdaptiveInterviewStage()
    step = reply(stage, reply(stage, start(stage), "High"), "Low")
    edit = RubricEdit(kind="set_weight", criterion_key="cpu_performance", weight=1)
    edited = apply_edits(step.state, [edit])
    assert edited.manual_weights == {"cpu_performance": 1.0}
    assert edited.beliefs == step.state.beliefs == beliefs_for(step.state, stage.settings)


def test_a_restart_that_carries_the_edits_keeps_the_new_interviews_beliefs() -> None:
    stage = AdaptiveInterviewStage()
    old = apply_edits(
        reply(stage, start(stage), "High").state,
        [RubricEdit(kind="set_weight", criterion_key="weight", weight=2)],
    )
    fresh = reply(stage, start(stage), "Low").state
    kept = with_edits_of(old, fresh)
    assert kept.manual_weights == {"weight": 2.0}
    assert kept.history == fresh.history
    assert kept.beliefs == fresh.beliefs == beliefs_for(fresh, stage.settings)


def test_a_correction_leaves_the_beliefs_as_the_answers_give_them() -> None:
    stage = AdaptiveInterviewStage()
    step = reply(stage, reply(stage, start(stage), "High"), "Medium")
    back = stage.go_back(step.state, "q_battery_life")
    corrected = reply(stage, back, "2")
    assert corrected.state.beliefs == beliefs_for(corrected.state, stage.settings)
    battery = updated(belief(7.0, stage.settings.prior_sd), 2.0, stage.settings.exact_sd)  # "2" typed: exact
    assert corrected.state.beliefs["battery_life"] == battery


def test_the_stages_fallback_has_no_beliefs_and_says_fixed_order() -> None:
    stage = AdaptiveInterviewStage()
    first = start(stage)
    step = stage.fallback(InterviewTurn(state=first.state, reply_text="High"), CTX, RuntimeError("x"))
    assert (step.ordering, step.state.beliefs) == ("fixed_order", {})
    assert step.question is not None and step.question.criterion_key == "battery_life"
