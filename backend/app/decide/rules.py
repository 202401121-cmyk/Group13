"""The filter (US-17, DR-01, DR-08): the budget, the active hard rules and the exclusions, checked in plain code.

Each product gets one check per rule: pass, fail or unknown, with a plain reason. Any fail puts it in "filtered out";
otherwise any unknown puts it in "can't tell"; otherwise it is shown. A rule switched to a preference (active false)
never filters. Missing evidence is never a fail: it is "can't tell", so the shopper can look it up.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from app.contracts import Budget, Exclusion, FilteredProduct, HardRule, Product, ResolvedValue, ValueState

from .units import (
    Quantity,
    attribute_unit,
    attribute_words,
    in_base,
    known_unit,
    money,
    number_text,
    quantity,
    rupees,
    unit_key,
    unit_label,
)

PRICE_KEY = "price_inr"
BUDGET = "budget"
MAX_REASONS = 10
MAX_HINT = 300
NO_SINGLE_CHANGE = "No single change to your rules would let a product through. Try relaxing more than one."

Values = Mapping[tuple[str, str], ResolvedValue]
Verdict = Literal["pass", "fail", "unknown"]

_EVIDENCE = (ValueState.KNOWN, ValueState.DISPUTED)
_TRUE = frozenset({"yes", "true", "y"})
_FALSE = frozenset({"no", "false", "n"})
_WORD = re.compile(r"[a-z0-9]+")
_DIGIT = re.compile(r"\d")


@dataclass(frozen=True)
class Check:
    rule: str  # "budget", a hard rule's id, or "exclusion:<n>"
    verdict: Verdict
    reason: str = ""
    attribute: str | None = None


@dataclass(frozen=True)
class FilterOutcome:
    shown: list[Product]
    filtered: list[FilteredProduct]
    relaxation_hint: str | None


# --- reading values --------------------------------------------------------------------------------------------------


def index_values(values: Sequence[ResolvedValue], product_ids: set[str]) -> dict[tuple[str, str], ResolvedValue]:
    """One value per product and key, for the products being decided. If a key repeats, the first one counts."""
    out: dict[tuple[str, str], ResolvedValue] = {}
    for value in values:
        if value.product_id in product_ids:
            out.setdefault((value.product_id, value.criterion_key), value)
    return out


def _words(text: str) -> list[str]:
    return _WORD.findall(text.casefold())


def _truth(raw: object) -> bool | None:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        text = raw.strip().casefold()
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
    return None


def _value_text(raw: object, attribute: str) -> str:
    """A value as the shopper reads it: 8 for "ram_gb" -> "8 GB"; text as it was given."""
    if isinstance(raw, bool):
        return "yes" if raw else "no"
    if isinstance(raw, int | float):
        unit = attribute_unit(attribute)
        if unit == "inr":
            return rupees(raw)
        return f"{number_text(raw)} {unit_label(unit)}".strip()
    return str(raw).strip()[:80]


# --- the budget ------------------------------------------------------------------------------------------------------


def check_budget(budget: Budget, value: ResolvedValue | None) -> Check:
    def unknown(reason: str) -> Check:
        return Check(BUDGET, "unknown", reason, PRICE_KEY)

    currencies = {m.currency for m in (budget.min, budget.max) if m is not None}
    if currencies != {"INR"}:
        return unknown(f"We can't compare its price with a budget in {', '.join(sorted(currencies))}")
    if value is None or value.state not in _EVIDENCE:
        return unknown("We couldn't find its price")
    price = quantity(value.value)
    if price is None or price.unit not in (None, "inr"):
        return unknown("We couldn't find its price")
    if value.state is ValueState.DISPUTED:
        return unknown(f"Sources disagree on its price: one says {rupees(price.number)}")
    if budget.max is not None and price.number > budget.max.amount:
        return Check(
            BUDGET, "fail", f"{rupees(price.number)} is above your {rupees(budget.max.amount)} budget", PRICE_KEY
        )
    if budget.min is not None and price.number < budget.min.amount:
        return Check(
            BUDGET, "fail", f"{rupees(price.number)} is below your {rupees(budget.min.amount)} minimum", PRICE_KEY
        )
    return Check(BUDGET, "pass", attribute=PRICE_KEY)


# --- hard rules ------------------------------------------------------------------------------------------------------


def _numbers(rule: HardRule, raw: object, target: object) -> tuple[float, float] | None:
    """The value and `target` (the rule's value, or one of its options) in the same base unit, or None when they
    can't be compared as numbers.

    The rule's unit is its own, else the one its attribute's name ends with ("ram_gb"). A bare value takes the
    attribute's unit, else the rule's, but only a unit we know: "6E" lends nothing to "6". When the rule has no unit and
    the value has one, the shopper's meaning is unknown, so the two are not compared.
    """
    have = quantity(raw)
    want = quantity(target)
    if have is None or want is None:
        return None
    from_name = attribute_unit(rule.attribute)
    want_unit = want.unit or unit_key(rule.unit) or from_name
    have_unit = have.unit or from_name or (want_unit if known_unit(want_unit) else None)
    if (want_unit is None) != (have_unit is None):
        return None
    have_dim, have_base = in_base(Quantity(have.number, have_unit), None)
    want_dim, want_base = in_base(Quantity(want.number, want_unit), None)
    return (have_base, want_base) if have_dim == want_dim else None


def _plain(value: object) -> bool:
    """Only a number and its unit ("16 GB", 16), not text with other words ("Wi-Fi 6")."""
    amount = quantity(value)
    return amount is not None and amount.plain


def _close(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)


def _text(raw: object) -> str | None:
    return " ".join(_words(raw)) if isinstance(raw, str) and _words(raw) else None


def _member(rule: HardRule, raw: object, options: list[str]) -> bool | None:
    """Is the value one of the options? As numbers when the value and every option are plain numbers, else as text."""
    if isinstance(raw, bool):
        return None
    if _plain(raw) and all(_plain(o) for o in options):
        pairs = [_numbers(rule, raw, option) for option in options]
        if any(pair is None for pair in pairs):
            return None
        return any(_close(*pair) for pair in pairs if pair is not None)
    return _text_match(raw, options)


def _flip(result: bool | None, negate: bool) -> bool | None:
    """`result`, turned round for "!=" and "not_in". None stays None: what can't be compared stays unknown."""
    if result is None:
        return None
    return not result if negate else result


def _contains(raw: object, target: str | list[str]) -> bool | None:
    """Does the text hold every item, as whole words ("USB-C" is in "2x USB-C, 1x HDMI")?"""
    text = _text(raw)
    items = target if isinstance(target, list) else [target]
    if text is None or not all(_words(i) for i in items):
        return None
    return all(f" {' '.join(_words(i))} " in f" {text} " for i in items)


def _equal(rule: HardRule, raw: object, target: float | str) -> bool | None:
    """Equal as numbers (after units) when both sides are plain numbers, else as text: "Wi-Fi 6" is not "Wi-Fi 6E"."""
    if _plain(raw) and _plain(target):
        pair = _numbers(rule, raw, target)
        return None if pair is None else _close(*pair)
    return _text_match(raw, [target]) if isinstance(target, str) else None


def _text_match(raw: object, options: list[str]) -> bool | None:
    """Is the text one of the options, word for word? A mismatch where numbers are involved ("16 GB DDR5" against
    "16 GB") may still be the same thing said another way, so it is unknown rather than a fail."""
    text = _text(raw)
    if text is None:
        return None
    if text in {" ".join(_words(o)) for o in options}:
        return True
    return None if any(_DIGIT.search(t) for t in [text, *options]) else False


def _at_least(rule: HardRule, raw: object, target: float | str, op: str) -> bool | None:
    pair = _numbers(rule, raw, target)
    if pair is None:
        return None
    have, want = pair
    if _close(have, want):
        return True
    return have > want if op == ">=" else have < want


def compare(rule: HardRule, raw: object) -> bool | None:
    """Does `raw` keep the rule? None when the two can't be compared (different units, text against a number...)."""
    op, target = rule.operator, rule.value
    if isinstance(target, bool):
        have = _truth(raw)
        return _flip(have == target, op == "!=") if have is not None and op in ("==", "!=") else None
    if op in ("in", "not_in"):
        return _flip(_member(rule, raw, target if isinstance(target, list) else [str(target)]), op == "not_in")
    if op == "contains":
        return _contains(raw, target if isinstance(target, list) else str(target))
    if isinstance(target, list):
        return None
    if op in ("==", "!="):
        return _flip(_equal(rule, raw, target), op == "!=")
    return _at_least(rule, raw, target, op)


def check_rule(rule: HardRule, value: ResolvedValue | None) -> Check:
    words, said = attribute_words(rule.attribute), rule.source_text
    if value is None or value.state not in _EVIDENCE or value.value is None:
        return Check(rule.id, "unknown", f"We couldn't find its {words}, so we can't check \"{said}\"", rule.attribute)
    shown = _value_text(value.value, rule.attribute)
    if value.state is ValueState.DISPUTED:
        return Check(rule.id, "unknown", f"Sources disagree on its {words}: one says {shown}", rule.attribute)
    kept = compare(rule, value.value)
    if kept is None:
        reason = f'We couldn\'t compare its {words} ("{shown}") with "{said}"'
        return Check(rule.id, "unknown", reason, rule.attribute)
    if kept:
        return Check(rule.id, "pass", attribute=rule.attribute)
    if isinstance(value.value, bool) or (_truth(value.value) is not None and isinstance(rule.value, bool)):
        has = "it does" if _truth(value.value) else "it doesn't"
    else:
        has = f"it has {shown}"
    return Check(rule.id, "fail", f'It fails "{said}": {has}', rule.attribute)


# --- exclusions ------------------------------------------------------------------------------------------------------


def _runs_in(words: list[str], within: list[str]) -> bool:
    """Do `words` appear together, in order, in `within`?"""
    n = len(words)
    return n > 0 and any(within[i : i + n] == words for i in range(len(within) - n + 1))


def _feature_keys(feature: list[str]) -> set[str]:
    key = "_".join(feature)
    return {key, f"has_{key}", f"is_{key}"}


def _variant_has(attributes: Mapping[str, str], feature: list[str]) -> bool:
    keys = _feature_keys(feature)
    for name, text in attributes.items():
        if _words(text) == feature:
            return True
        if "_".join(_words(name)) in keys and _truth(text) is True:
            return True
    return False


def excluded(exclusion: Exclusion, product: Product, values: Values) -> bool:
    target = _words(exclusion.value)
    if not target:
        return False
    name = _words(product.name)
    if exclusion.kind == "brand":
        return _words(product.brand or "") == target or name[: len(target)] == target
    if exclusion.kind == "model":
        return any(_runs_in(target, _words(text)) for text in [product.name, *product.aliases])
    if exclusion.kind == "feature":
        for key in _feature_keys(target):
            value = values.get((product.id, key))
            if value is not None and value.state is ValueState.KNOWN and _truth(value.value) is True:
                return True
        variants = product.variants
        return bool(variants) and all(_variant_has(v.attributes, target) for v in variants)
    return False  # a seller is ruled out among the offers (US-24): decide has no seller data


def _unique_exclusions(exclusions: Sequence[Exclusion]) -> list[tuple[str, Exclusion]]:
    seen: set[tuple[str, tuple[str, ...]]] = set()
    out: list[tuple[str, Exclusion]] = []
    for n, exclusion in enumerate(exclusions):
        key = (exclusion.kind, tuple(_words(exclusion.value)))
        if key not in seen:
            seen.add(key)
            out.append((f"exclusion:{n}", exclusion))
    return out


def _active_rules(rules: Sequence[HardRule]) -> list[HardRule]:
    """Active rules, once each: two rules with the same id are the same rule (US-09)."""
    seen: set[str] = set()
    out: list[HardRule] = []
    for rule in rules:
        if rule.active and rule.id not in seen:
            seen.add(rule.id)
            out.append(rule)
    return out


# --- groups and the hint ---------------------------------------------------------------------------------------------


def checks_for(
    product: Product,
    budget: Budget | None,
    rules: Sequence[HardRule],
    exclusions: Sequence[tuple[str, Exclusion]],
    values: Values,
) -> list[Check]:
    checks: list[Check] = []
    if budget is not None:
        checks.append(check_budget(budget, values.get((product.id, PRICE_KEY))))
    checks += [check_rule(rule, values.get((product.id, rule.attribute))) for rule in rules]
    for key, exclusion in exclusions:
        if excluded(exclusion, product, values):
            checks.append(Check(key, "fail", f"You ruled out {exclusion.value}", exclusion.kind))
    return checks


def _held(product: Product, group: Literal["filtered_out", "cant_tell"], hits: list[Check]) -> FilteredProduct:
    reasons = list(dict.fromkeys(c.reason for c in hits))[:MAX_REASONS]
    return FilteredProduct(product_id=product.id, group=group, reasons=reasons, rule_attribute=hits[0].attribute)


def _grouped(product: Product, checks: list[Check]) -> FilteredProduct | None:
    """Any fail: filtered out, with every fail's reason. Else any unknown: can't tell. Else None: it is shown."""
    fails = [c for c in checks if c.verdict == "fail"]
    if fails:
        return _held(product, "filtered_out", fails)
    unknowns = [c for c in checks if c.verdict == "unknown"]
    return _held(product, "cant_tell", unknowns) if unknowns else None


def _budget_words(budget: Budget) -> str:
    if budget.min is not None and budget.max is not None:
        low, high = money(budget.min.amount, budget.min.currency), money(budget.max.amount, budget.max.currency)
        return f"your {low} to {high} budget"
    if budget.max is not None:
        return f"your {money(budget.max.amount, budget.max.currency)} budget"
    assert budget.min is not None  # a Budget has a min, a max or both
    return f"your {money(budget.min.amount, budget.min.currency)} minimum"


def relaxation_hint(
    blocked: Sequence[set[str]],
    budget: Budget | None,
    rules: Sequence[HardRule],
    exclusions: Sequence[tuple[str, Exclusion]],
) -> str:
    """The one rule whose removal lets the most products through (US-17), from each product's blocking rules.

    A product gets through when the removed rule was its only block. A tie goes to the budget, then the hard rules,
    then the exclusions, each in the rubric's order.
    """
    candidates: list[tuple[str, str]] = []
    if budget is not None:
        candidates.append((BUDGET, f"Removing {_budget_words(budget)}"))
    candidates += [(rule.id, f'Dropping your rule "{rule.source_text}"') for rule in rules]
    candidates += [(key, f"Allowing {exclusion.value} again") for key, exclusion in exclusions]
    best_count, best_words = 0, ""
    for key, words in candidates:
        count = sum(1 for blocks in blocked if blocks == {key})
        if count > best_count:
            best_count, best_words = count, words
    if best_count == 0:
        return NO_SINGLE_CHANGE
    hint = f"{best_words} would let {best_count} product{'' if best_count == 1 else 's'} through."
    return hint if len(hint) <= MAX_HINT else hint[: MAX_HINT - 3].rstrip() + "..."


def filter_products(
    products: Sequence[Product],
    budget: Budget | None,
    rules: Sequence[HardRule],
    exclusions: Sequence[Exclusion],
    values: Values,
) -> FilterOutcome:
    active = _active_rules(rules)
    unique = _unique_exclusions(exclusions)
    shown: list[Product] = []
    held: list[FilteredProduct] = []
    blocked: list[set[str]] = []
    for product in products:
        checks = checks_for(product, budget, active, unique, values)
        group = _grouped(product, checks)
        if group is None:
            shown.append(product)
        else:
            held.append(group)
            blocked.append({c.rule for c in checks if c.verdict != "pass"})
    hint = relaxation_hint(blocked, budget, active, unique) if products and not shown else None
    return FilterOutcome(shown=shown, filtered=held, relaxation_hint=hint)
