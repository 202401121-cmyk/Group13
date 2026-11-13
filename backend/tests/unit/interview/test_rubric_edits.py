"""Must-haves kept apart from preferences (US-09, DR-08): two lists, and the shopper's edits applied last.

Hard rules only filter (and only active ones); weights only order, and their shares add up to exactly 1. A rebuild,
after a new answer, a step back or a changed intent, keeps the shopper's edits.
"""

from __future__ import annotations

import itertools
import math
from typing import Any

import pytest

from app.contracts import (
    AskedQuestion,
    Exclusion,
    HardRule,
    InterviewState,
    ReplyClassification,
    ReplyType,
    RubricEdit,
)
from app.contracts import examples as ex
from app.interview.plan import Plan
from app.interview.questions import question_for
from app.interview.rubric_builder import (
    ALL_ZERO_NOTE,
    UnknownEdit,
    apply_edits,
    build_rubric,
    carry_edits,
    default_rubric,
    rubric_note,
)

CRITERIA = {c.key: c for c in ex.criteria().criteria}
RULE = ex.intent().hard_rules[0]  # ram_gb at least 16, "at least 16 GB RAM"
ZENITH = ex.intent().exclusions[0]


def state(*history: AskedQuestion, **edits: Any) -> InterviewState:
    return InterviewState(
        session_id=ex.SESSION_ID, intent=ex.intent(), criteria=ex.criteria(), history=list(history), **edits
    )


def answered(key: str, weight: float) -> AskedQuestion:
    reply = ReplyClassification(reply_type=ReplyType.ANSWER, weight=weight, classified_by="fallback")
    return AskedQuestion(question=question_for(CRITERIA[key]), status="answered", classification=reply)


def skipped(key: str) -> AskedQuestion:
    return AskedQuestion(question=question_for(CRITERIA[key]), status="skipped")


def edit(**fields: Any) -> RubricEdit:
    return RubricEdit.model_validate(fields)


def weights(rubric_state: InterviewState, plan: Plan | None = None) -> dict[str, tuple[float, str]]:
    return {w.key: (w.weight, w.origin) for w in build_rubric(rubric_state, plan).weights}


# --- two lists, and shares that add up to exactly 1 ------------------------------------------------------------------


@pytest.mark.parametrize("manual", [{}, {"weight": 10.0}, {"weight": 0.0, "battery_life": 3.3}])
def test_weights_add_to_exactly_1_for_every_mix_of_origins(manual: dict[str, float]) -> None:
    keys = list(CRITERIA)
    for picked in itertools.combinations(keys, 3):
        history = [answered(picked[0], 7.7), skipped(picked[1])]
        plan = Plan(to_ask=(), inferred={picked[2]: 3.1})
        rubric = build_rubric(state(*history, manual_weights=manual), plan)
        assert abs(sum(w.normalized for w in rubric.weights) - 1.0) <= 1e-9
        assert math.fsum(w.normalized for w in rubric.weights) == pytest.approx(1.0, abs=1e-9)
    mixed = state(answered("cpu_performance", 9), skipped("keyboard_comfort"), manual_weights=manual)
    origins = {w.origin for w in build_rubric(mixed, Plan(to_ask=(), inferred={"display_quality": 3.0})).weights}
    assert {"answered", "skipped", "default"} <= origins
    assert ("manual" in origins) is bool(manual)


def test_a_never_asked_criterion_keeps_its_default() -> None:
    got = weights(state(answered("cpu_performance", 9)))
    assert got["display_quality"] == (CRITERIA["display_quality"].default_weight, "default")


def test_identical_hard_rules_share_one_id_and_one_row() -> None:
    twin = HardRule.model_validate({**RULE.model_dump(), "source_text": "16 GB of RAM at least"})
    intent = ex.intent().model_validate(
        {**ex.intent().model_dump(), "hard_rules": [RULE.model_dump(), twin.model_dump()]}
    )
    rubric = build_rubric(InterviewState(session_id=ex.SESSION_ID, intent=intent, criteria=ex.criteria()))
    assert [r.id for r in rubric.hard_rules] == [RULE.id]
    assert rubric.hard_rules[0].source_text == RULE.source_text  # the first one the shopper said


def test_a_switched_rule_is_kept_with_its_source_text_and_never_filters() -> None:
    switched = apply_edits(state(), [edit(kind="switch_rule", rule_id=RULE.id, active=False)])
    [rule] = build_rubric(switched).hard_rules
    assert (rule.id, rule.active, rule.source_text) == (RULE.id, False, RULE.source_text)
    assert all(w.key != rule.attribute for w in build_rubric(switched).weights)  # a weight never becomes a filter


def test_all_weights_zero_fall_back_to_the_defaults_with_a_note() -> None:
    zero = state(manual_weights=dict.fromkeys(CRITERIA, 0.0))
    got = weights(zero)
    assert got == {key: (c.default_weight, "default") for key, c in CRITERIA.items()}
    assert rubric_note(zero) == ALL_ZERO_NOTE
    assert rubric_note(state()) is None
    raised = zero.model_validate({**zero.model_dump(), "manual_weights": {**zero.manual_weights, "weight": 4.0}})
    assert weights(raised)["weight"] == (4.0, "manual")  # the edits were kept, so one raised weight counts again


def test_the_last_resort_rubric_keeps_the_edits_too() -> None:
    edited = apply_edits(state(), [edit(kind="set_weight", criterion_key="weight", weight=9.0)])
    assert {w.key: (w.weight, w.origin) for w in default_rubric(edited).weights}["weight"] == (9.0, "manual")


# --- edits survive a rebuild -----------------------------------------------------------------------------------------


def test_a_manual_weight_survives_a_new_answer() -> None:
    edited = apply_edits(state(answered("weight", 2)), [edit(kind="set_weight", criterion_key="weight", weight=9.0)])
    later = edited.model_validate(
        {**edited.model_dump(), "history": [h.model_dump() for h in [*edited.history, answered("weight", 1)]]}
    )
    assert weights(later)["weight"] == (9.0, "manual")


def test_a_manual_weight_survives_going_back() -> None:
    from app.interview import InterviewStage

    edited = apply_edits(
        state(answered("cpu_performance", 9)), [edit(kind="set_weight", criterion_key="battery_life", weight=1.0)]
    )
    back = InterviewStage().go_back(edited, edited.history[0].question.id)
    assert back.state.manual_weights == {"battery_life": 1.0}
    assert weights(back.state)["battery_life"] == (1.0, "manual")


def test_a_switched_rule_stays_switched_after_a_rebuild() -> None:
    switched = apply_edits(state(), [edit(kind="switch_rule", rule_id=RULE.id, active=False)])
    later = switched.model_validate({**switched.model_dump(), "history": [answered("cpu_performance", 9).model_dump()]})
    assert build_rubric(later).hard_rules[0].active is False
    carried = carry_edits(later, ex.intent(), ex.criteria())
    assert carried is not None and build_rubric(carried).hard_rules[0].active is False


def test_edits_are_carried_to_a_changed_intent_but_not_a_switch_for_a_rule_it_lost() -> None:
    edited = apply_edits(
        state(),
        [
            edit(kind="switch_rule", rule_id=RULE.id, active=False),
            edit(kind="set_weight", criterion_key="weight", weight=9.0),
        ],
    )
    no_rules = ex.intent().model_validate({**ex.intent().model_dump(), "hard_rules": []})
    carried = carry_edits(edited, no_rules, ex.criteria())
    assert carried is not None
    assert carried.manual_rule_switches == {} and carried.manual_weights == {"weight": 9.0}
    assert carried.history == []
    assert carry_edits(state(), ex.intent(), ex.criteria()) is None  # no edits: nothing to carry


def test_an_exclusion_added_then_removed_is_gone() -> None:
    nova = Exclusion(kind="brand", value="Nova")
    edited = apply_edits(
        state(),
        [
            edit(kind="add_exclusion", exclusion=nova.model_dump()),
            edit(kind="remove_exclusion", exclusion=nova.model_dump()),
        ],
    )
    assert [e.value for e in build_rubric(edited).exclusions] == [ZENITH.value]
    again = apply_edits(edited, [edit(kind="add_exclusion", exclusion={"kind": "brand", "value": "NOVA"})])
    assert [e.value.casefold() for e in build_rubric(again).exclusions] == ["zenith", "nova"]


def test_removing_a_requests_exclusion_keeps_it_removed() -> None:
    edited = apply_edits(state(), [edit(kind="remove_exclusion", exclusion={"kind": "brand", "value": "zenith"})])
    assert build_rubric(edited).exclusions == []
    later = edited.model_validate({**edited.model_dump(), "history": [answered("cpu_performance", 9).model_dump()]})
    assert build_rubric(later).exclusions == []
    back = apply_edits(edited, [edit(kind="add_exclusion", exclusion=ZENITH.model_dump())])
    assert [e.value for e in build_rubric(back).exclusions] == [ZENITH.value]


# --- unknown names ---------------------------------------------------------------------------------------------------


def test_an_unknown_rule_id_is_refused_with_its_name() -> None:
    with pytest.raises(UnknownEdit, match="no_such_rule"):
        apply_edits(state(), [edit(kind="switch_rule", rule_id="no_such_rule", active=False)])


def test_an_unknown_criterion_key_is_refused_with_its_name() -> None:
    with pytest.raises(UnknownEdit, match="not_a_criterion"):
        apply_edits(state(), [edit(kind="set_weight", criterion_key="not_a_criterion", weight=5.0)])


def test_the_requests_own_exclusions_pass_through_as_they_were() -> None:
    """Only the shopper's edits are matched and merged; what the request held is unchanged (no behaviour change)."""
    twice = ex.intent().model_validate(
        {**ex.intent().model_dump(), "exclusions": [ZENITH.model_dump(), {"kind": "brand", "value": "zenith"}]}
    )
    rubric = build_rubric(InterviewState(session_id=ex.SESSION_ID, intent=twice, criteria=ex.criteria()))
    assert [e.value for e in rubric.exclusions] == ["Zenith", "zenith"]
