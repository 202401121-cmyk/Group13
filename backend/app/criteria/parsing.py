"""Turn the LLM's criteria into a validated CriteriaSet, or refuse them (US-03).

The reply is parsed into the lenient RawCriteria first (never straight into the contract), then cleaned one
criterion at a time. A criterion that is vague, has no real definition or has an impossible weight is dropped
and logged, never repaired with invented text. If fewer than 6 survive, the result is None and the stage asks
again or uses the family's fallback set.
"""

from __future__ import annotations

import logging
import math
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.contracts import CriteriaInput, CriteriaSet, Criterion, Direction
from app.contracts.criteria import VAGUE_CRITERIA
from app.llm.output import looks_like_instruction

from .fallback import fallback_criteria

log = logging.getLogger(__name__)

MIN_CRITERIA = 6  # CriteriaSet minimum; fewer means ask again, then fall back
MAX_CRITERIA = 10
MIN_DEFINITION_WORDS = 5
MAX_DEFINITION_CHARS = 300  # Criterion.definition limit
MAX_LABEL_CHARS = 60
MAX_UNIT_CHARS = 16
DEFAULT_WEIGHT = 5.0
VALUE_FOR_MONEY_KEY = "value_for_money"

# A key made only of these words says nothing measurable ("overall_quality", "design_and_looks").
VAGUE_WORDS = {
    *("quality", "overall", "performance", "good", "value", "general", "and"),
    *("feature", "features", "design", "designs", "look", "looks", "brand", "brands"),
    *("spec", "specs", "specification", "specifications"),
}
_ALIASES = {"name": "label", "description": "definition", "weight": "default_weight", "importance": "default_weight"}


class RawCriterion(BaseModel):
    """One criterion as the LLM wrote it. A wrong type becomes None instead of an error."""

    model_config = ConfigDict(extra="ignore")

    key: str | None = None
    label: str | None = None
    definition: str | None = None
    direction: str | None = None
    unit: str | None = None
    default_weight: float | None = None
    is_value_for_money: bool = False

    @model_validator(mode="before")
    @classmethod
    def _aliases(cls, data: Any) -> Any:
        if isinstance(data, dict):
            data = dict(data)
            for alias, name in _ALIASES.items():
                if alias in data and name not in data:
                    data[name] = data[alias]
        return data

    @field_validator("key", "label", "definition", "direction", "unit", mode="before")
    @classmethod
    def _text(cls, value: object) -> str | None:
        return " ".join(value.split()) or None if isinstance(value, str) else None

    @field_validator("default_weight", mode="before")
    @classmethod
    def _number(cls, value: object) -> float | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int | float):
            try:
                return float(value)
            except OverflowError:
                return math.inf  # too big to be a weight, so cleaning drops this criterion and no other
        if isinstance(value, str):
            try:
                return float(value.strip())
            except ValueError:
                return None
        return None

    @field_validator("is_value_for_money", mode="before")
    @classmethod
    def _flag(cls, value: object) -> bool:
        return value is True or (isinstance(value, str) and value.strip().lower() in {"true", "yes"})


class RawCriteria(BaseModel):
    """The LLM's whole answer: {"criteria": [...]}."""

    model_config = ConfigDict(extra="ignore")

    criteria: list[RawCriterion] = Field(default_factory=list)

    @field_validator("criteria", mode="before")
    @classmethod
    def _objects(cls, value: object) -> list[Any]:
        return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def to_criteria_set(raw: RawCriteria, inp: CriteriaInput) -> CriteriaSet | None:
    """A generated CriteriaSet, or None if fewer than 6 usable criteria remain after cleaning."""
    criteria = clean_criteria(raw, fallback_criteria(inp.category).value_for_money)
    if len(criteria) < MIN_CRITERIA:
        log.info("criteria: only %d usable criteria for %s", len(criteria), inp.category)
        return None
    try:
        return CriteriaSet(category=inp.category, use_case=inp.use_case, criteria=criteria, source="generated")
    except ValidationError as exc:  # cleaning should make this impossible; never crash on it
        log.warning("criteria: cleaned set still invalid (%d errors)", exc.error_count())
        return None


def clean_criteria(raw: RawCriteria, family_value_for_money: Criterion) -> list[Criterion]:
    """The usable criteria in the LLM's order, with exactly one value-for-money criterion and at most 10."""
    kept: list[Criterion] = []
    has_value_for_money = False
    for item in raw.criteria:
        criterion = _clean_one(item)
        if criterion is None:
            continue
        if any(c.key == criterion.key for c in kept):
            log.info("criteria: dropped duplicate key %s", criterion.key)
            continue
        if criterion.is_value_for_money:
            if has_value_for_money:
                log.info("criteria: dropped a second value-for-money criterion")
                continue
            has_value_for_money = True
        kept.append(criterion)
    if not has_value_for_money:
        kept.append(family_value_for_money)  # value for money is always one of the criteria (US-03)
    if len(kept) > MAX_CRITERIA:
        head = kept[:MAX_CRITERIA]
        if not any(c.is_value_for_money for c in head):
            value = next(c for c in kept if c.is_value_for_money)
            head = [*[c for c in kept if not c.is_value_for_money][: MAX_CRITERIA - 1], value]
        kept = head
    return kept


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:64].strip("_")


def is_vague(key: str) -> bool:
    """True for a key that names nothing measurable. An empty key is not vague; it is handled as missing."""
    return bool(key) and (key in VAGUE_CRITERIA or all(word in VAGUE_WORDS for word in key.split("_")))


def _clean_one(item: RawCriterion) -> Criterion | None:
    key = slugify(item.key or item.label or "")
    if not key:
        return _drop("no key or label")
    value_for_money = item.is_value_for_money or key == VALUE_FOR_MONEY_KEY
    if value_for_money and is_vague(key):
        key = VALUE_FOR_MONEY_KEY  # "value" means value for money here; give it the standard key
    elif not value_for_money and (is_vague(key) or is_vague(slugify(item.label or ""))):
        return _drop(f"vague criterion {key}")

    definition = item.definition or ""
    if len(definition.split()) < MIN_DEFINITION_WORDS or len(definition) > MAX_DEFINITION_CHARS:
        return _drop(f"{key} has a missing, too-short or too-long definition")
    if any(looks_like_instruction(text) for text in (definition, item.label or "", key.replace("_", " "))):
        return _drop("a criterion reads like an instruction, not a definition")

    weight = DEFAULT_WEIGHT if item.default_weight is None else item.default_weight
    if not (math.isfinite(weight) and 0 <= weight <= 10):
        return _drop(f"{key} has a weight outside 0-10")

    label = item.label if item.label and 2 <= len(item.label) <= MAX_LABEL_CHARS else _label_from(key)
    unit = item.unit if item.unit and len(item.unit) <= MAX_UNIT_CHARS else None
    try:
        return Criterion(
            key=key,
            label=label,
            definition=definition,
            direction=_direction(item.direction),
            unit=unit,
            default_weight=weight,
            is_value_for_money=value_for_money,
        )
    except ValidationError as exc:
        return _drop(f"{key} failed validation ({exc.error_count()} errors)")


def _direction(value: str | None) -> Direction:
    return Direction.LOWER_IS_BETTER if value and value.lower().startswith("lower") else Direction.HIGHER_IS_BETTER


def _label_from(key: str) -> str:
    label = key.replace("_", " ")[:MAX_LABEL_CHARS].strip()
    return label[:1].upper() + label[1:] if len(label) >= 2 else "Criterion"


def _drop(reason: str) -> Criterion | None:
    """Log why a criterion was dropped; always None, so `return _drop(...)` reads as one step."""
    log.info("criteria: dropped a criterion: %s", reason)
    return None
