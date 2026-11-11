"""Small builders for the decide tests. Every product and request here is made up."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from typing import Any

from app.contracts import (
    Budget,
    DecideInput,
    DecideResult,
    Exclusion,
    HardRule,
    Money,
    Product,
    ResolvedValue,
    Rubric,
    ValueState,
    WeightedCriterion,
    normalize,
)

RULE_RAM = HardRule(attribute="ram_gb", operator=">=", value=16, unit="GB", source_text="at least 16 GB RAM")


def claim_id(*parts: str) -> str:
    return "clm_" + hashlib.sha256("|".join(parts).encode()).hexdigest()[:12]


def known(pid: str, key: str, *, score: float | None = None, value: Any = None) -> ResolvedValue:
    return ResolvedValue(
        product_id=pid,
        criterion_key=key,
        state=ValueState.KNOWN,
        score=score,
        value=value,
        confidence=0.8,
        supporting_claim_ids=[claim_id(pid, key)],
    )


def disputed(pid: str, key: str, *, score: float | None = None, value: Any = None) -> ResolvedValue:
    return ResolvedValue(
        product_id=pid,
        criterion_key=key,
        state=ValueState.DISPUTED,
        score=score,
        value=value,
        confidence=0.5,
        supporting_claim_ids=[claim_id(pid, key, "a")],
        conflicting_claim_ids=[claim_id(pid, key, "b")],
    )


def unknown(pid: str, key: str) -> ResolvedValue:
    return ResolvedValue(product_id=pid, criterion_key=key, state=ValueState.UNKNOWN, confidence=0.0)


def estimated(pid: str, key: str, score: float) -> ResolvedValue:
    return ResolvedValue(product_id=pid, criterion_key=key, state=ValueState.ESTIMATED, score=score, confidence=0.3)


def product(pid: str, name: str | None = None, brand: str | None = None, **extra: Any) -> Product:
    words = pid.removeprefix("prd_").replace("_", " ").title()
    return Product(id=pid, name=name or words, brand=brand, **extra)


def criteria(pairs: Sequence[tuple[str, float]]) -> list[WeightedCriterion]:
    shares = normalize(dict(pairs))
    return [WeightedCriterion(key=k, weight=w, normalized=shares[k], origin="default") for k, w in pairs]


def rubric(
    *,
    weights: Sequence[tuple[str, float]] = (("battery_life", 5.0),),
    budget_max: float | None = None,
    budget_min: float | None = None,
    currency: str = "INR",
    rules: Iterable[HardRule] = (),
    exclusions: Iterable[Exclusion] = (),
) -> Rubric:
    budget = None
    if budget_max is not None or budget_min is not None:
        budget = Budget(
            min=Money(amount=budget_min, currency=currency) if budget_min is not None else None,
            max=Money(amount=budget_max, currency=currency) if budget_max is not None else None,
        )
    return Rubric(
        category="laptop",
        budget=budget,
        hard_rules=list(rules),
        exclusions=list(exclusions),
        weights=criteria(weights),
    )


def decide_input(rb: Rubric, products: Sequence[Product], values: Sequence[ResolvedValue]) -> DecideInput:
    return DecideInput(rubric=rb, products=list(products), values=list(values))


def shown(result: DecideResult) -> list[str]:
    return [r.product_id for r in result.ranked]


def group_of(result: DecideResult, pid: str) -> str:
    if pid in shown(result):
        return "shown"
    return next(f.group for f in result.filtered if f.product_id == pid)


def filtered(result: DecideResult, pid: str) -> tuple[str, list[str], str | None]:
    """(group, reasons, rule_attribute) of a product that did not pass."""
    item = next(f for f in result.filtered if f.product_id == pid)
    return item.group, list(item.reasons), item.rule_attribute
