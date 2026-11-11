"""Stage 1, intake (US-01, US-02): request text in, Intent out."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import Field, model_validator

from .common import Contract, Money, Region, Slug

Operator = Literal["<=", ">=", "==", "!=", "in", "not_in", "contains"]

# The word each operator gives a hard rule's id, so the id reads like the rule (S3-0).
OPERATOR_WORDS: dict[str, str] = {
    "<=": "max",
    ">=": "min",
    "==": "is",
    "!=": "not",
    "in": "in",
    "not_in": "not_in",
    "contains": "has",
}


def _canonical(value: object) -> object:
    """The value as the id sees it: a number as a float, text trimmed and case-folded, a list sorted."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return float(value) + 0.0  # + 0.0 turns -0.0 into 0.0, as JSON readers such as the browser do
    if isinstance(value, str):
        return value.strip().casefold()
    if isinstance(value, list | tuple):
        return sorted(str(item).strip().casefold() for item in value)
    return repr(value)


def hard_rule_id(attribute: str, operator: str, value: object) -> str:
    """A hard rule's stable id (US-09): the same attribute, operator and value always give the same id.

    It reads as "<attribute>_<operator word>_<10 hex>", such as "ram_gb_min_…". The hex is the start of the sha256 of
    the three, so 16 and 16.0, "HP" and "hp", or a list in another order give the same id. Unit and source_text are not
    part of it. Two rules with the same id are the same rule, so a switch applies to both. Saved results and the
    shopper's rule switches keep these ids, so changing how one is made is a contract change.
    """
    canonical = json.dumps([attribute, operator, _canonical(value)], ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:10]
    return f"{attribute[:40].rstrip('_-')}_{OPERATOR_WORDS[operator]}_{digest}"


def _id_from(data: dict[str, Any]) -> str:
    """Make the id from the fields already validated. If one of them failed, the rule is refused anyway."""
    attribute, operator = data.get("attribute"), data.get("operator")
    if not isinstance(attribute, str) or operator not in OPERATOR_WORDS or "value" not in data:
        return "invalid"
    return hard_rule_id(attribute, str(operator), data["value"])


class HardRule(Contract):
    """A must-have. Hard rules only filter products; they never change the order (DR-08)."""

    attribute: Slug = Field(description="Criterion or spec key, e.g. 'weight_kg' or 'has_ois'")
    operator: Operator
    value: float | bool | str | list[str]
    unit: str | None = Field(default=None, max_length=16)
    source_text: str = Field(min_length=1, max_length=200, description="What the user actually said")
    id: Slug = Field(
        default_factory=_id_from,
        description=(
            "Stable id made from attribute, operator and value (hard_rule_id), so the same rule keeps its id across a "
            "rebuild (US-09). Left out, it is made; given, it must match"
        ),
    )
    active: bool = Field(
        default=True,
        description="False when the shopper switched this rule to a preference: it then no longer filters (US-09)",
    )

    @model_validator(mode="after")
    def _check_id(self) -> HardRule:
        if self.id != hard_rule_id(self.attribute, self.operator, self.value):
            raise ValueError(
                "a hard rule's id is made from its attribute, operator and value; leave it out to have it made"
            )
        return self


class Exclusion(Contract):
    """Something the user never wants to see (FR-10)."""

    kind: Literal["brand", "model", "feature", "seller"]
    value: str = Field(min_length=1, max_length=80)
    always: bool = Field(default=False, description="True = save to memory for future searches")


class Budget(Contract):
    min: Money | None = None
    max: Money | None = None

    @model_validator(mode="after")
    def _check_range(self) -> Budget:
        if self.min is None and self.max is None:
            raise ValueError("a budget needs a min, a max, or both; use budget=None for no budget")
        if self.min and self.max:
            if self.min.currency != self.max.currency:
                raise ValueError("budget min and max must use the same currency")
            if self.min.amount > self.max.amount:
                raise ValueError("budget min is above budget max")
        return self


class IntakeInput(Contract):
    raw_query: str = Field(min_length=1, max_length=1000)
    previous_clarification_reply: str | None = Field(default=None, max_length=500)
    unclear_attempts: int = Field(default=0, ge=0, le=2)


class Intent(Contract):
    """Everything understood from the request. Research never starts without a category."""

    raw_query: str = Field(min_length=1, max_length=1000)
    category: Slug | None = None
    category_label: str | None = Field(default=None, max_length=60)
    use_case: str | None = Field(default=None, max_length=120)
    budget: Budget | None = None
    region: Region = "IN"
    hard_rules: list[HardRule] = Field(default_factory=list, max_length=20)
    exclusions: list[Exclusion] = Field(default_factory=list, max_length=30)
    needs_clarification: bool = False
    clarification_question: str | None = Field(default=None, max_length=300)
    clarification_options: list[str] = Field(default_factory=list, max_length=6)
    show_example_requests: bool = Field(
        default=False, description="US-01: second unclear reply -> show examples, never guess"
    )
    detected_by: Literal["llm", "rules"]

    @model_validator(mode="after")
    def _check_clarification(self) -> Intent:
        if self.category is None and not self.needs_clarification:
            raise ValueError("no category detected, so needs_clarification must be True")
        if self.needs_clarification and not (self.clarification_question or self.show_example_requests):
            raise ValueError("needs_clarification requires a clarification_question or example requests")
        if (self.category is None) != (self.category_label is None):
            raise ValueError("category and category_label must be set together")
        return self

    @property
    def ready_for_research(self) -> bool:
        return self.category is not None and not self.needs_clarification
