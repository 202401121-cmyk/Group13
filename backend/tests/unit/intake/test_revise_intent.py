"""Editing a detected Intent (US-02, failure F-2): only the use, budget and region can change, and code decides.

The product needs a new request, because the criteria and the interview depend on it. Must-haves and leave-outs
wait for US-09 and US-10. A value that repeats the current one is not a change, so it is never refused.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from app.contracts import Intent
from app.contracts import examples as ex
from app.intake import revise_intent

PRODUCT = "To change the product, start a new request."
RULES = "Must-haves and leave-outs can't be changed here yet. Start a new request with the change instead."
NEW_RULE = {"attribute": "ram_gb", "operator": ">=", "value": 32, "unit": "GB", "source_text": "32 GB RAM"}
NEW_EXCLUSION = {"kind": "brand", "value": "Acme", "always": False}
INTENT = ex.intent()
UNCLEAR = ex.unclear_intent()  # still asks which kind of product


def refusal(intent: Intent, changes: dict[str, Any]) -> str:
    with pytest.raises(ValidationError) as caught:
        revise_intent(intent, changes)
    return str(caught.value.errors()[0]["msg"])


@pytest.mark.parametrize(
    "changes",
    [
        {"category": "smartphone"},  # the F-2 edit: it left category smartphone with the label "Laptop"
        {"category_label": "Smartphone"},
        {"category": "smartphone", "category_label": "Smartphone"},
        {"category": None},
        {"category_label": None},
        {"category": None, "category_label": None},
        {"category": "Not A Slug!"},
        {"category": "laptop", "category_label": "Smartphone"},  # one repeated, one changed
    ],
)
def test_changing_the_product_is_refused(changes: dict[str, Any]) -> None:
    assert refusal(INTENT, changes) == PRODUCT


@pytest.mark.parametrize(
    "changes",
    [
        {"hard_rules": [NEW_RULE]},
        {"hard_rules": []},  # taking every must-have away is a change too
        {"hard_rules": ["32 GB RAM"]},  # the shape a shopper types
        {"exclusions": [NEW_EXCLUSION]},
        {"exclusions": []},
        {"exclusions": ["Acme"]},
    ],
)
def test_changing_must_haves_or_leave_outs_is_refused(changes: dict[str, Any]) -> None:
    assert refusal(INTENT, changes) == RULES


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"use_case": "gaming", "category": "smartphone"}, PRODUCT),
        ({"region": "US", "category_label": "Smartphone"}, PRODUCT),
        ({"budget": None, "hard_rules": [NEW_RULE]}, RULES),
        ({"region": "US", "exclusions": [NEW_EXCLUSION]}, RULES),
    ],
)
def test_a_valid_change_never_goes_through_beside_a_refused_one(changes: dict[str, Any], message: str) -> None:
    assert refusal(INTENT, changes) == message


def test_the_product_message_wins_whatever_order_the_fields_come_in() -> None:
    assert refusal(INTENT, {"exclusions": [NEW_EXCLUSION], "category": "smartphone"}) == PRODUCT


def test_a_refused_edit_leaves_the_intent_as_it_was() -> None:
    before = INTENT.model_dump()
    refusal(INTENT, {"category": "smartphone", "use_case": "gaming"})
    assert INTENT.model_dump() == before


def test_an_answer_to_the_question_is_not_an_edit() -> None:
    """A category for an unclear request comes from the reply to our question, through intake."""
    assert refusal(UNCLEAR, {"category": "smartwatch", "category_label": "Smartwatch"}) == PRODUCT


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("raw_query", "a phone"),
        ("detected_by", "rules"),
        ("needs_clarification", True),
        ("clarification_question", "Which one?"),
        ("clarification_options", ["One", "Two"]),
        ("show_example_requests", True),
    ],
)
def test_the_fields_only_code_sets_are_refused(field: str, value: object) -> None:
    message = refusal(INTENT, {field: value})
    assert message.startswith("That change doesn't work: ") and field in message


@pytest.mark.parametrize(
    "changes",
    [
        {"category": "laptop"},
        {"category_label": "Laptop"},
        {"category": "laptop", "category_label": "Laptop"},
        {"category": " laptop ", "category_label": " Laptop "},  # the contract trims spaces
        {"hard_rules": [r.model_dump() for r in INTENT.hard_rules]},
        {"hard_rules": [r.model_dump(exclude_none=True) for r in INTENT.hard_rules]},  # defaults left out
        {"exclusions": [e.model_dump() for e in INTENT.exclusions]},
        {"exclusions": [e.model_dump(exclude={"always"}) for e in INTENT.exclusions]},
        {"raw_query": INTENT.raw_query, "detected_by": INTENT.detected_by, "needs_clarification": False},
        INTENT.model_dump(),  # the whole Intent sent back
    ],
)
def test_repeating_the_current_values_is_not_refused(changes: dict[str, Any]) -> None:
    assert revise_intent(INTENT, changes) == INTENT


def test_repeating_an_empty_category_is_not_refused() -> None:
    assert revise_intent(UNCLEAR, {"category": None, "category_label": None}) == UNCLEAR


def test_a_repeated_product_does_not_stop_a_real_edit() -> None:
    edited = revise_intent(INTENT, {**INTENT.model_dump(), "use_case": "gaming"})
    assert (edited.use_case, edited.category, edited.category_label) == ("gaming", "laptop", "Laptop")


def test_the_use_budget_and_region_can_all_change() -> None:
    edited = revise_intent(
        INTENT,
        {"use_case": "gaming", "budget": {"max": {"amount": 60_000, "currency": "INR"}}, "region": "US"},
    )
    assert (edited.use_case, edited.region) == ("gaming", "US")
    assert edited.budget is not None and edited.budget.max is not None and edited.budget.max.amount == 60_000
    assert (edited.category, edited.category_label) == ("laptop", "Laptop")
    assert (edited.hard_rules, edited.exclusions) == (INTENT.hard_rules, INTENT.exclusions)


def test_the_use_and_budget_can_be_cleared() -> None:
    edited = revise_intent(INTENT, {"use_case": None, "budget": None})
    assert edited.use_case is None and edited.budget is None


@pytest.mark.parametrize(
    "changes",
    [
        {"budget": {"max": {"amount": -1, "currency": "INR"}}},
        {"budget": {"min": {"amount": 90_000, "currency": "INR"}, "max": {"amount": 60_000, "currency": "INR"}}},
        {"budget": {}},
        {"region": "India"},
        {"use_case": "x" * 121},
    ],
)
def test_a_bad_value_in_an_editable_field_is_still_refused_by_the_contract(changes: dict[str, Any]) -> None:
    message = refusal(INTENT, changes)
    assert message not in (PRODUCT, RULES)


def test_an_unknown_field_is_still_refused_by_the_contract() -> None:
    with pytest.raises(ValidationError) as caught:
        revise_intent(INTENT, {"colour": "red"})
    assert caught.value.errors()[0]["type"] == "extra_forbidden"
