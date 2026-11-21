"""US-06 in the interview (DECIDED 4): a remembered weight enters as an answer, so the planner need not ask it; it shows
as "remembered" and can be changed or forgotten. A remembered must-have or leave-out joins the rubric, switchable like
the shopper's own. With nothing remembered the interview is exactly as before."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.contracts import (
    CriteriaInput,
    Exclusion,
    HardRule,
    Intent,
    InterviewState,
    InterviewTurn,
    RememberedPreference,
    RoughLevel,
    RubricEdit,
    StageContext,
)
from app.criteria import fallback_set
from app.interview import AdaptiveInterviewStage, InterviewStage
from app.interview.beliefs import beliefs_for
from app.interview.remembered import REMEMBERED_WHY, forget, with_remembered
from app.interview.rubric_builder import apply_edits, build_rubric
from app.memory import RememberedItem

CTX = StageContext(search_id="srch_remembered")
RAM = HardRule(attribute="ram_gb", operator=">=", value=16, unit="GB", source_text="at least 16 GB of RAM")


def state_for(category: str = "laptop", **intent: Any) -> InterviewState:
    criteria = fallback_set(CriteriaInput(category=category, category_label=category.capitalize()))
    made = Intent(
        raw_query=f"a made-up {category} request",
        category=category,
        category_label=category.capitalize(),
        detected_by="rules",
        **intent,
    )
    return InterviewState(session_id="sess_rememberedtest", intent=made, criteria=criteria)


def weight(key: str, value: float, similarity: float = 0.95) -> RememberedItem:
    return RememberedItem(
        RememberedPreference(
            preference_id=uuid.uuid4(),
            kind="weight",
            text=f"{key}: remembered",
            criterion_key=key,
            weight=value,
            similarity=similarity,
            age_days=3,
        )
    )


def rule(found: HardRule = RAM) -> RememberedItem:
    return RememberedItem(
        RememberedPreference(
            preference_id=uuid.uuid4(),
            kind="hard_rule",
            text=f"Must have: {found.source_text}",
            similarity=0.8,
            age_days=1,
        ),
        rule=found,
    )


def leave_out(value: str = "Acme") -> RememberedItem:
    return RememberedItem(
        RememberedPreference(
            preference_id=uuid.uuid4(), kind="exclusion", text=f"Never {value}", similarity=1.0, age_days=2
        ),
        exclusion=Exclusion(kind="brand", value=value, always=True),
    )


def finish(stage: Any, state: InterviewState, level: RoughLevel = RoughLevel.MEDIUM) -> Any:
    step = stage.run(InterviewTurn(state=state), CTX)
    while step.kind != "finished":
        step = stage.run(InterviewTurn(state=step.state, level=level), CTX)
    return step


# --- a remembered weight is an answer ---------------------------------------------------------------------------------


def test_a_remembered_weight_is_in_the_history_marked_remembered_and_never_asked() -> None:
    state = with_remembered(state_for(), [weight("battery_life", 8.0)])
    [entry] = state.history
    assert (entry.status, entry.question.criterion_key, entry.classification.weight) == (
        "remembered",
        "battery_life",
        8.0,
    )  # type: ignore[union-attr]
    assert entry.question.why_asking == REMEMBERED_WHY
    assert [r.criterion_key for r in state.remembered] == ["battery_life"]
    for stage in (InterviewStage(), AdaptiveInterviewStage()):
        step = finish(stage, state)
        asked = [e.question.criterion_key for e in step.state.history if e.status != "remembered"]
        assert "battery_life" not in asked
        assert step.questions_asked == len(asked)


def test_the_belief_moves_as_for_an_answer_of_that_level() -> None:
    from app.contracts import AskedQuestion, ReplyClassification, ReplyType
    from app.interview.questions import question_for

    plain = state_for()
    remembered = with_remembered(plain, [weight("battery_life", 8.0)])
    criterion = next(c for c in plain.criteria.criteria if c.key == "battery_life")
    answer = AskedQuestion(
        question=question_for(criterion),
        status="answered",
        classification=ReplyClassification(reply_type=ReplyType.ANSWER, weight=8.0, classified_by="fallback"),
    )
    answered = InterviewState.model_validate({**plain.model_dump(), "history": [answer.model_dump()]})
    assert beliefs_for(remembered)["battery_life"] == beliefs_for(answered)["battery_life"]
    assert beliefs_for(remembered)["battery_life"].sd < beliefs_for(plain)["battery_life"].sd


def test_remembered_weights_mean_fewer_questions() -> None:
    plain = finish(AdaptiveInterviewStage(), state_for())
    keys = [e.question.criterion_key for e in plain.state.history][:3]
    remembered = with_remembered(state_for(), [weight(key, 5.0) for key in keys])
    step = finish(AdaptiveInterviewStage(), remembered)
    assert step.questions_asked <= plain.questions_asked - len(keys)


def test_the_rubric_marks_a_remembered_weight_remembered() -> None:
    rubric = build_rubric(with_remembered(state_for(), [weight("battery_life", 8.0)]))
    found = next(w for w in rubric.weights if w.key == "battery_life")
    assert (found.weight, found.origin) == (8.0, "remembered")


def test_a_remembered_weight_for_a_criterion_this_session_lacks_is_left_out() -> None:
    state = with_remembered(state_for(), [weight("sound_quality", 8.0)])
    assert state.history == [] and state.remembered == []


def test_nothing_remembered_leaves_the_interview_exactly_as_before() -> None:
    plain = state_for()
    assert with_remembered(plain, []) == plain
    assert finish(AdaptiveInterviewStage(), with_remembered(plain, [])) == finish(AdaptiveInterviewStage(), plain)


def test_at_most_five_are_kept() -> None:
    keys = [c.key for c in state_for().criteria.criteria]
    state = with_remembered(state_for(), [weight(key, 5.0) for key in keys])
    assert len(state.remembered) == 5 and len(state.history) == 5


# --- changed or forgotten in one click --------------------------------------------------------------------------------


def test_going_back_to_a_remembered_answer_changes_it() -> None:
    stage = AdaptiveInterviewStage()
    state = with_remembered(state_for(), [weight("battery_life", 8.0)])
    question_id = state.history[0].question.id
    back = stage.go_back(stage.run(InterviewTurn(state=state), CTX).state, question_id)
    assert back.question is not None and back.question.criterion_key == "battery_life"
    changed = stage.run(InterviewTurn(state=back.state, level=RoughLevel.LOW), CTX)
    entry = next(e for e in changed.state.history if e.question.id == question_id)
    assert (entry.status, entry.classification.weight) == ("answered", 2.0)  # type: ignore[union-attr]


def test_going_back_and_then_away_keeps_it_remembered() -> None:
    stage = AdaptiveInterviewStage()
    state = with_remembered(state_for(), [weight("battery_life", 8.0)])
    first = stage.run(InterviewTurn(state=state), CTX)
    answered = stage.run(InterviewTurn(state=first.state, level=RoughLevel.HIGH), CTX)
    remembered_id = state.history[0].question.id
    other_id = first.question.id if first.question else ""
    back = stage.go_back(answered.state, remembered_id)
    away = stage.go_back(back.state, other_id)
    entry = next(e for e in away.state.history if e.question.id == remembered_id)
    assert (entry.status, entry.classification.weight) == ("remembered", 8.0)  # type: ignore[union-attr]


def test_a_shopper_can_set_a_remembered_weight_in_the_rubric_editor() -> None:
    state = with_remembered(state_for(), [weight("battery_life", 8.0)])
    edited = apply_edits(state, [RubricEdit(kind="set_weight", criterion_key="battery_life", weight=3)])
    found = next(w for w in build_rubric(edited).weights if w.key == "battery_life")
    assert (found.weight, found.origin) == (3.0, "manual")


def test_forgetting_a_remembered_weight_takes_it_out_so_it_is_asked() -> None:
    item = weight("battery_life", 8.0)
    state = forget(with_remembered(state_for(), [item]), item.preference.preference_id)
    assert state.history == [] and state.remembered == []
    step = finish(InterviewStage(), state)
    assert "battery_life" in {e.question.criterion_key for e in step.state.history}


def test_forgetting_what_was_never_remembered_is_refused() -> None:
    with pytest.raises(KeyError):
        forget(state_for(), uuid.uuid4())


# --- a remembered rule or leave-out joins the rubric ------------------------------------------------------------------


def test_a_remembered_rule_joins_the_rubric_and_switches_like_the_shoppers_own() -> None:
    item = rule()
    state = with_remembered(state_for(), [item])
    assert [r.id for r in build_rubric(state).hard_rules] == [RAM.id]
    switched = apply_edits(state, [RubricEdit(kind="switch_rule", rule_id=RAM.id, active=False)])
    assert [r.active for r in build_rubric(switched).hard_rules] == [False]
    forgotten = forget(state, item.preference.preference_id)
    assert build_rubric(forgotten).hard_rules == [] and forgotten.remembered == []


def test_a_rule_the_request_already_has_is_not_added_twice() -> None:
    state = with_remembered(state_for(hard_rules=[RAM]), [rule()])
    assert [r.id for r in build_rubric(state).hard_rules] == [RAM.id] and state.remembered == []


def test_a_remembered_leave_out_joins_the_rubric_and_can_be_removed() -> None:
    item = leave_out()
    state = with_remembered(state_for(), [item])
    assert build_rubric(state).exclusions == [Exclusion(kind="brand", value="Acme", always=True)]
    removed = apply_edits(state, [RubricEdit(kind="remove_exclusion", exclusion=Exclusion(kind="brand", value="Acme"))])
    assert build_rubric(removed).exclusions == []
    forgotten = forget(state, item.preference.preference_id)
    assert build_rubric(forgotten).exclusions == [] and forgotten.remembered == []


def test_a_leave_out_the_shopper_removed_in_this_session_is_not_brought_back() -> None:
    own = Exclusion(kind="brand", value="Acme", always=False)
    removed = apply_edits(state_for(exclusions=[own]), [RubricEdit(kind="remove_exclusion", exclusion=own)])
    state = with_remembered(removed, [leave_out()])
    assert build_rubric(state).exclusions == [] and state.remembered == []


# --- the review's cases -----------------------------------------------------------------------------------------------


def test_a_remembered_rule_in_the_shoppers_own_words_is_not_added_so_forgetting_never_takes_theirs() -> None:
    own = HardRule(attribute="ram", operator=">=", value=16, unit="GB", source_text=RAM.source_text)
    state = with_remembered(state_for(hard_rules=[own]), [rule()])
    assert [r.id for r in state.intent.hard_rules] == [own.id] and state.remembered == []


def test_forgetting_a_remembered_brand_keeps_the_shoppers_own_model_of_the_same_name() -> None:
    item = leave_out("Acme")
    state = with_remembered(state_for(), [item])
    mine = Exclusion(kind="model", value="Acme", always=True)
    state = apply_edits(state, [RubricEdit(kind="add_exclusion", exclusion=mine)])
    forgotten = forget(state, item.preference.preference_id)
    assert build_rubric(forgotten).exclusions == [mine]


def test_one_remembered_item_that_does_not_fit_is_left_out_alone() -> None:
    full = [Exclusion(kind="brand", value=f"Brand {i}") for i in range(30)]  # the request's 30 leave-outs
    state = with_remembered(state_for(exclusions=full), [leave_out("Acme"), weight("battery_life", 8.0)])
    assert [r.kind for r in state.remembered] == ["weight"]
    assert len(build_rubric(state).exclusions) == 30


def test_the_fixed_order_also_keeps_a_remembered_answer_when_the_shopper_goes_back_and_away() -> None:
    stage = InterviewStage()
    state = with_remembered(state_for(), [weight("battery_life", 8.0)])
    first = stage.run(InterviewTurn(state=state), CTX)
    answered = stage.run(InterviewTurn(state=first.state, level=RoughLevel.HIGH), CTX)
    remembered_id = state.history[0].question.id
    back = stage.go_back(answered.state, remembered_id)
    assert back.question is not None and back.question.criterion_key == "battery_life"
    away = stage.go_back(back.state, first.question.id if first.question else "")
    entry = next(e for e in away.state.history if e.question.id == remembered_id)
    assert entry.status == "remembered"


def test_the_stage_fallback_puts_a_remembered_answer_being_changed_back_as_remembered() -> None:
    stage = InterviewStage()
    state = with_remembered(state_for(), [weight("battery_life", 8.0)])
    back = stage.go_back(stage.run(InterviewTurn(state=state), CTX).state, state.history[0].question.id)
    step = stage.fallback(InterviewTurn(state=back.state, reply_text="High"), CTX, RuntimeError("made up"))
    entry = next(e for e in step.state.history if e.question.criterion_key == "battery_life")
    assert (entry.status, entry.classification.weight) == ("remembered", 8.0)  # type: ignore[union-attr]


def test_forgetting_a_remembered_answer_while_it_is_being_changed() -> None:
    stage = AdaptiveInterviewStage()
    item = weight("battery_life", 8.0)
    state = with_remembered(state_for(), [item])
    back = stage.go_back(stage.run(InterviewTurn(state=state), CTX).state, state.history[0].question.id)
    forgotten = forget(back.state, item.preference.preference_id)
    assert all(e.question.criterion_key != "battery_life" for e in forgotten.history)
    assert forgotten.remembered == []


def test_without_remembered_takes_out_all_memory_added_and_nothing_else() -> None:
    from app.interview.remembered import without_remembered

    mine = Exclusion(kind="brand", value="Zenith", always=True)
    plain = apply_edits(state_for(), [RubricEdit(kind="add_exclusion", exclusion=mine)])
    state = with_remembered(plain, [weight("battery_life", 8.0), rule(), leave_out("Acme")])
    assert len(state.remembered) == 3
    assert without_remembered(state) == plain


def test_a_switch_on_a_rule_is_kept_once_it_is_back() -> None:
    from app.interview.remembered import with_switches

    state = with_remembered(state_for(), [rule()])
    assert with_switches(state, {RAM.id: False}).manual_rule_switches == {RAM.id: False}
    assert with_switches(state_for(), {RAM.id: False}).manual_rule_switches == {}  # no such rule: nothing kept


def test_a_remembered_leave_out_the_shopper_removes_is_no_longer_remembered() -> None:
    from app.interview.remembered import dropped_leave_outs

    item = leave_out("Acme")
    state = with_remembered(state_for(), [item, weight("battery_life", 8.0)])
    removed = apply_edits(state, [RubricEdit(kind="remove_exclusion", exclusion=Exclusion(kind="brand", value="Acme"))])
    after, ids = dropped_leave_outs(removed)
    assert ids == [item.preference.preference_id]
    assert [r.kind for r in after.remembered] == ["weight"]
    assert dropped_leave_outs(state) == (state, [])
