"""US-06: what lasts from a finished interview, in plain words, and the three limits of S5-1's table (NUL, tiny
weights, weights read back exactly)."""

from __future__ import annotations

from typing import Any

import pytest

from app.contracts import (
    AskedQuestion,
    CriteriaInput,
    Exclusion,
    HardRule,
    Intent,
    InterviewState,
    ReplyClassification,
    ReplyType,
    Rubric,
)
from app.criteria import fallback_set
from app.interview.questions import question_for
from app.interview.rubric_builder import build_rubric
from app.memory.lasting import Lasting, lasting_items
from app.memory.text import MAX_TEXT, clean_text, round_weight, weight_text, weight_words

RAM = HardRule(attribute="ram_gb", operator=">=", value=16, unit="GB", source_text="at least 16 GB of RAM")
LIGHT = HardRule(attribute="weight_kg", operator="<=", value=1.5, unit="kg", source_text="under 1.5 kg", active=False)
PRICE = HardRule(attribute="price_inr", operator="<=", value=70000, unit="INR", source_text="under 70k")


def state_for(category: str = "laptop", **intent: Any) -> InterviewState:
    criteria = fallback_set(CriteriaInput(category=category, category_label=category.capitalize()))
    made = Intent(
        raw_query=f"a made-up {category} request",
        category=category,
        category_label=category.capitalize(),
        detected_by="rules",
        **intent,
    )
    return InterviewState(session_id="sess_memorytest01", intent=made, criteria=criteria)


def answered(state: InterviewState, key: str, weight: float, status: str = "answered") -> InterviewState:
    criterion = next(c for c in state.criteria.criteria if c.key == key)
    classification = (
        ReplyClassification(reply_type=ReplyType.ANSWER, weight=weight, classified_by="fallback")
        if status != "skipped"
        else ReplyClassification(reply_type=ReplyType.SKIP, classified_by="fallback")
    )
    entry = AskedQuestion(question=question_for(criterion), status=status, classification=classification)  # type: ignore[arg-type]
    return InterviewState.model_validate(
        {**state.model_dump(), "history": [*state.model_dump()["history"], entry.model_dump()]}
    )


def items_of(state: InterviewState, rubric: Rubric | None = None) -> list[Lasting]:
    return lasting_items(state, rubric or build_rubric(state))


# --- plain words ------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("weight", "words"),
    [
        (8.0, "high importance"),
        (5.0, "medium importance"),
        (2.0, "low importance"),
        (3.0, "good enough will do"),
        (7.0, "7 out of 10"),
        (0.0, "0 out of 10"),
    ],
)
def test_a_weight_in_plain_words(weight: float, words: str) -> None:
    assert weight_words(weight) == words
    assert weight_text("Battery life", weight) == f"Battery life: {words}"


def test_text_loses_nul_and_control_characters_and_keeps_to_200() -> None:
    assert clean_text("Battery\x00 life\t\n matters ") == "Battery life matters"
    assert clean_text("\x00\x01  ") == ""
    long = clean_text("word " * 100)
    assert len(long) <= MAX_TEXT and long == long.strip()
    assert clean_text("₹" * 300) == "₹" * MAX_TEXT


@pytest.mark.parametrize(("raw", "kept"), [(1e-46, 0.0), (-0.0, 0.0), (7.3, 7.3), (7.333333, 7.33), (10.004, 10.0)])
def test_a_weight_is_rounded_to_two_places_so_real_can_hold_it(raw: float, kept: float) -> None:
    assert round_weight(raw) == kept
    assert str(round_weight(raw)) != "-0.0"


# --- what lasts -------------------------------------------------------------------------------------------------------


def test_an_answered_weight_lasts_with_its_criterion_as_the_topic() -> None:
    state = answered(state_for(), "processor_speed", 8.0)
    [item] = [i for i in items_of(state) if i.kind == "weight"]
    criterion = next(c for c in state.criteria.criteria if c.key == "processor_speed")
    assert (item.category, item.criterion_key, item.weight, item.always) == ("laptop", "processor_speed", 8.0, False)
    assert item.text == f"{criterion.label}: high importance"
    assert item.topic == f"{criterion.label}: {criterion.definition}"
    assert item.rule is None


def test_skipped_and_default_weights_never_last() -> None:
    state = answered(answered(state_for(), "processor_speed", 8.0), "memory_and_storage", 0.0, status="skipped")
    keys = {i.criterion_key for i in items_of(state) if i.kind == "weight"}
    assert keys == {"processor_speed"}


def test_a_weight_settled_by_a_topic_rule_is_not_an_answer_and_never_lasts() -> None:
    state = answered(state_for(), "processor_speed", 8.0)
    rubric = build_rubric(state)
    inferred = Rubric.model_validate(
        {
            **rubric.model_dump(),
            "weights": [
                {**w.model_dump(), "origin": "answered", "uncertainty": 0.6}
                if w.key == "memory_and_storage"
                else w.model_dump()
                for w in rubric.weights
            ],
        }
    )
    assert {i.criterion_key for i in lasting_items(state, inferred) if i.kind == "weight"} == {"processor_speed"}


def test_a_weight_the_shopper_set_and_a_remembered_one_last() -> None:
    state = answered(state_for(), "memory_and_storage", 5.0, status="remembered")
    state = InterviewState.model_validate({**state.model_dump(), "manual_weights": {"keyboard_comfort": 9.0}})
    found = {i.criterion_key: i.weight for i in items_of(state) if i.kind == "weight"}
    assert found == {"memory_and_storage": 5.0, "keyboard_comfort": 9.0}


def test_an_active_rule_lasts_and_an_inactive_one_does_not() -> None:
    state = state_for(hard_rules=[RAM, LIGHT])
    [rule] = [i for i in items_of(state) if i.kind == "hard_rule"]
    assert rule.rule is not None and rule.rule["id"] == RAM.id
    assert (rule.category, rule.weight, rule.text) == ("laptop", None, "Must have: at least 16 GB of RAM")
    switched = InterviewState.model_validate({**state.model_dump(), "manual_rule_switches": {RAM.id: False}})
    assert [i for i in items_of(switched) if i.kind == "hard_rule"] == []


def test_a_budget_is_never_stored() -> None:
    """Neither the request's budget nor a rule on the price: a laptop budget never fills a headphone one."""
    from app.contracts import Budget, Money

    state = state_for(hard_rules=[PRICE], budget=Budget(max=Money(amount=70000, currency="INR")))
    found = items_of(state)
    assert all(i.kind != "hard_rule" for i in found)
    assert all("70" not in i.text and "budget" not in i.text.lower() for i in found)


def test_a_leave_out_lasts_only_marked_always_and_a_brand_holds_for_every_category() -> None:
    state = state_for(
        exclusions=[
            Exclusion(kind="brand", value="Acme", always=True),
            Exclusion(kind="model", value="Acme Book 14", always=True),
            Exclusion(kind="brand", value="Zenith", always=False),
        ]
    )
    found = {(i.rule or {}).get("value"): i for i in items_of(state) if i.kind == "exclusion"}
    assert set(found) == {"Acme", "Acme Book 14"}
    assert (found["Acme"].category, found["Acme"].always, found["Acme"].text) == (None, True, "Never Acme")
    assert found["Acme Book 14"].category == "laptop"


def test_nul_in_a_rule_or_a_leave_out_is_cleaned_before_it_is_saved() -> None:
    rule = HardRule(attribute="ram_gb", operator=">=", value=16, unit="GB", source_text="at least\x00 16 GB")
    state = state_for(hard_rules=[rule], exclusions=[Exclusion(kind="brand", value="Ac\x00me", always=True)])
    for item in items_of(state):
        assert "\x00" not in item.text and "\\u0000" not in str(item.rule)
    texts = {i.text for i in items_of(state)}
    assert {"Must have: at least 16 GB", "Never Acme"} <= texts


def test_every_lasting_item_fits_the_contract() -> None:
    state = answered(
        state_for(hard_rules=[RAM], exclusions=[Exclusion(kind="brand", value="Acme", always=True)]),
        "processor_speed",
        8.0,
    )
    for item in items_of(state):
        item.as_preference()  # a PreferenceItem: what the table's checks also hold it to


# --- the review's cases -----------------------------------------------------------------------------------------------


def _with_reply(state: InterviewState, key: str, reply_text: str | None, weight: float, by: str) -> InterviewState:
    criterion = next(c for c in state.criteria.criteria if c.key == key)
    entry = AskedQuestion(
        question=question_for(criterion),
        status="answered",
        reply_text=reply_text,
        classification=ReplyClassification(reply_type=ReplyType.ANSWER, weight=weight, classified_by=by),  # type: ignore[arg-type]
    )
    history = [*state.model_dump()["history"], entry.model_dump()]
    return InterviewState.model_validate({**state.model_dump(), "history": history})


def test_a_reply_the_classifier_could_not_read_is_never_remembered() -> None:
    """With no LLM to read it, the reply counts at the usual weight (replies.fallback_answer): the shopper never said
    it, so it is never saved as theirs. A button, a typed level and a classified reply are theirs, and are saved."""
    from app.interview.replies import fallback_answer

    state = state_for()
    battery = next(c for c in state.criteria.criteria if c.key == "battery_life")
    guess = fallback_answer(battery)
    state = _with_reply(state, "battery_life", "kind of matters i guess", guess.weight or 0.0, "fallback")
    state = _with_reply(state, "processor_speed", None, 8.0, "fallback")  # the High button
    state = _with_reply(state, "display_quality", "High", 8.0, "fallback")  # typed, read by code
    state = _with_reply(state, "keyboard_comfort", "I type all day, it matters a lot", 8.0, "llm")
    kept = {i.criterion_key for i in items_of(state) if i.kind == "weight"}
    assert kept == {"processor_speed", "display_quality", "keyboard_comfort"}


def test_a_rules_value_keeps_its_exact_words_and_its_id() -> None:
    rule = HardRule(attribute="processor", operator="==", value="Intel  Core i7", source_text="Intel  Core i7")
    [item] = [i for i in items_of(state_for(hard_rules=[rule])) if i.kind == "hard_rule"]
    assert item.rule is not None and (item.rule["value"], item.rule["id"]) == ("Intel  Core i7", rule.id)


def test_nul_in_a_rules_value_is_dropped_and_its_id_made_again() -> None:
    rule = HardRule(attribute="processor", operator="==", value="Intel\x00 Core", source_text="Intel Core")
    [item] = [i for i in items_of(state_for(hard_rules=[rule])) if i.kind == "hard_rule"]
    assert item.rule is not None and item.rule["value"] == "Intel Core"
    assert item.rule["id"] == HardRule(attribute="processor", operator="==", value="Intel Core", source_text="x").id


def test_a_leave_outs_words_name_its_kind_except_a_brands() -> None:
    state = state_for(
        exclusions=[
            Exclusion(kind="brand", value="Acme", always=True),
            Exclusion(kind="model", value="Acme", always=True),
        ]
    )
    assert sorted(i.text for i in items_of(state) if i.kind == "exclusion") == ["Never Acme", "Never Acme (model)"]
