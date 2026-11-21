"""What a finished interview leaves to remember (US-06, DECIDED 2), for a signed-in shopper only.

- A weight the shopper gave: answered, remembered and kept, or set in the rubric editor. Never a skipped one, one left
  at its default, or one a topic rule settled without asking.
- A must-have the shopper kept active. Never a rule on the price: a budget is never stored, so a laptop budget can't
  fill a headphone one.
- A leave-out marked Always. A brand holds for every category (no category); a model, feature or seller for its own.

Each is said in plain words ("Battery life: high importance") and has a topic, the text that is embedded: a weight's
is its criterion's "label: definition", which is what recall compares a new session's criteria with; a must-have's is
its criterion's label and the shopper's words when the rule names a criterion, else its own words; a leave-out's is its
own words. Every item is checked against the PreferenceItem contract before it is saved, as the table also checks.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from app.contracts import (
    AskedQuestion,
    Criterion,
    Exclusion,
    HardRule,
    InterviewState,
    PreferenceItem,
    PreferenceKind,
    ReplyType,
    Rubric,
)
from app.interview.answers import understand_reply

from .text import clean_text, exclusion_text, round_weight, rule_text, weight_text, without_nul

log = logging.getLogger(__name__)

BUDGET_WORDS = ("price", "budget", "cost")  # a rule on any of these is a budget, and never stored
KEPT_STATUSES = ("answered", "remembered")


@dataclass(frozen=True)
class Lasting:
    """One preference to save, as the table holds it."""

    kind: PreferenceKind
    category: str | None
    criterion_key: str | None
    text: str
    weight: float | None
    rule: dict[str, Any] | None  # a must-have's HardRule or a leave-out's Exclusion, as the contract's JSON
    always: bool
    topic: str  # the text that is embedded
    # It came from memory in this interview, unchanged: saving it again only renews a row that still exists. One the
    # shopper deleted meanwhile (US-11) is never made again by an interview that recalled it before.
    refresh_only: bool = False

    @property
    def key(self) -> tuple[str, ...]:
        """What makes two items the same preference: saving one again updates the other."""
        rule = self.rule or {}
        if self.kind == "weight":
            return ("weight", self.category or "", self.criterion_key or "")
        if self.kind == "hard_rule":
            return ("hard_rule", self.category or "", str(rule.get("id", "")))
        return ("exclusion", self.category or "", str(rule.get("kind", "")), str(rule.get("value", "")).casefold())

    def as_preference(self, item_id: uuid.UUID | None = None, at: datetime | None = None) -> PreferenceItem:
        """The contract's item. Raises ValueError (pydantic's ValidationError) if it breaks one of its rules."""
        when = at or datetime.now(UTC)
        return PreferenceItem.model_validate(
            {
                "id": item_id or uuid.uuid4(),
                "kind": self.kind,
                "category": self.category,
                "criterion_key": self.criterion_key,
                "text": self.text,
                "weight": self.weight,
                "rule": self.rule if self.kind == "hard_rule" else None,
                "exclusion": self.rule if self.kind == "exclusion" else None,
                "always": self.always,
                "created_at": when,
                "updated_at": when,
            }
        )


def lasting_items(state: InterviewState, rubric: Rubric) -> list[Lasting]:
    """The preferences the finished rubric holds that last. Any item that would break the contract is left out, with a
    log line naming only its kind."""
    criteria = {c.key: c for c in state.criteria.criteria}
    found = [
        _marked(state, i) for i in [*_weights(state, rubric, criteria), *_rules(rubric, criteria), *_leave_outs(rubric)]
    ]
    kept: list[Lasting] = []
    for item in found:
        try:
            item.as_preference()
        except ValueError as exc:
            log.warning("memory: a %s was not saved: it breaks the contract (%s)", item.kind, type(exc).__name__)
            continue
        kept.append(item)
    return kept


def _marked(state: InterviewState, item: Lasting) -> Lasting:
    """The item marked refresh-only when it came from memory in this interview and the shopper didn't change it: a
    weight still in the history as "remembered" (not set in the rubric editor), or a must-have or leave-out whose words
    are those of a remembered one."""
    if item.kind == "weight":
        kept = {e.question.criterion_key for e in state.history if e.status == "remembered"} - set(state.manual_weights)
        from_memory = item.criterion_key in kept
    else:
        from_memory = item.text in {r.text for r in state.remembered if r.kind == item.kind}
    return replace(item, refresh_only=True) if from_memory else item


def guessed(entry: AskedQuestion) -> bool:
    """An answer the shopper never gave: their words couldn't be read (by code, then by the classifier, which failed),
    so the interview used the usual weight and said so. It is never remembered as theirs."""
    found = entry.classification
    if found is None or found.classified_by != "fallback" or not (entry.reply_text or "").strip():
        return False
    return understand_reply(entry.reply_text or "", entry.question).reply_type is ReplyType.UNCLEAR


def _weights(state: InterviewState, rubric: Rubric, criteria: dict[str, Criterion]) -> list[Lasting]:
    given = {
        e.question.criterion_key
        for e in state.history
        if e.status in KEPT_STATUSES
        and e.classification is not None
        and e.classification.weight is not None
        and not guessed(e)
    }
    given |= set(state.manual_weights)
    items: list[Lasting] = []
    for weighted in rubric.weights:
        criterion = criteria.get(weighted.key)
        if criterion is None or weighted.key not in given or weighted.origin in ("default", "skipped"):
            continue
        weight = round_weight(weighted.weight)
        items.append(
            Lasting(
                kind="weight",
                category=rubric.category,
                criterion_key=weighted.key,
                text=weight_text(criterion.label, weight),
                weight=weight,
                rule=None,
                always=False,
                topic=clean_text(f"{criterion.label}: {criterion.definition}", 1000),
            )
        )
    return items


def _value_without_nul(value: Any) -> Any:
    if isinstance(value, str):
        return without_nul(value)
    if isinstance(value, list):
        return [without_nul(str(v)) for v in value]
    return value


def _is_budget(rule: HardRule) -> bool:
    attribute = rule.attribute.lower()
    return any(word in attribute for word in BUDGET_WORDS) or (rule.unit or "").upper() in ("INR", "USD", "EUR")


def _rules(rubric: Rubric, criteria: dict[str, Criterion]) -> list[Lasting]:
    items: list[Lasting] = []
    for rule in rubric.hard_rules:
        if not rule.active or _is_budget(rule):
            continue
        words = clean_text(rule.source_text)
        if not words:
            continue
        data = rule.model_dump(mode="json")
        data["source_text"] = words
        # The value keeps its exact words: only NUL goes, which the database can't hold. The rule's id is made from its
        # value (HardRule), so it is made again only when NUL went.
        value = data.get("value")
        cleaned = _value_without_nul(value)
        if cleaned != value:
            data["value"] = cleaned
            data.pop("id", None)
            data = HardRule.model_validate(data).model_dump(mode="json")
        criterion = criteria.get(rule.attribute)
        topic = f"{criterion.label}: {words}" if criterion is not None else rule_text(words)
        items.append(
            Lasting(
                kind="hard_rule",
                category=rubric.category,
                criterion_key=criterion.key if criterion is not None else None,
                text=rule_text(words),
                weight=None,
                rule=data,
                always=False,
                topic=topic,
            )
        )
    return items


def _leave_outs(rubric: Rubric) -> list[Lasting]:
    items: list[Lasting] = []
    for exclusion in rubric.exclusions:
        if not exclusion.always:
            continue
        value = clean_text(exclusion.value, 80)
        if not value:
            continue
        data = Exclusion(kind=exclusion.kind, value=value, always=True).model_dump(mode="json")
        text = exclusion_text(value, exclusion.kind)
        items.append(
            Lasting(
                kind="exclusion",
                category=None if exclusion.kind == "brand" else rubric.category,
                criterion_key=None,
                text=text,
                weight=None,
                rule=data,
                always=True,
                topic=text,
            )
        )
    return items
