"""Checks behind the safety floors (S3-18): rule breaks among shown products, unsourced shown products, and
popularity beating fit. Each is written apart from the stage it checks, so a bug in that stage can't hide itself:
- rule breaks: read the rubric and the values directly, not through app/decide/rules.py (as tests/unit/decide/
  test_briefs.py does for the five briefs);
- unsourced: a shown product must be named in a source, or cite a claim from one, not just "verify kept it";
- popularity: "fits worst" is the rubric's weights times each value's own score (0 to 10), not decide's score, which
  decides the rank itself.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence

from app.contracts import Citation, HardRule, Product, ResolvedValue, Rubric, Source, ValueState

# Units to a base, written apart from app/decide/units.py on purpose. A unit not here makes a value "can't check".
TO_BASE = {"gb": 1.0, "tb": 1000.0, "mb": 0.001, "g": 1.0, "kg": 1000.0, "oz": 28.349523125, "mm": 0.1, "cm": 1.0}
TO_BASE |= {"inch": 2.54, "in": 2.54, "hz": 1.0, "khz": 1000.0, "mah": 1.0, "w": 1.0, "h": 1.0, "hours": 1.0}
PRICE_KEY = "price_inr"
_AMOUNT = re.compile(r"\s*([\d][\d,]*(?:\.\d+)?)\s*([a-zA-Z\"]*)\s*")
_EVIDENCE = (ValueState.KNOWN, ValueState.DISPUTED)

ByKey = Mapping[str, ResolvedValue]
Ranked = tuple[str, int]  # product id, rank


def _base(value: object, unit: str | None) -> float | None:
    """A number in base units, or None when it can't be read for sure."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        if unit is None:
            return float(value)
        return float(value) * TO_BASE[unit] if unit in TO_BASE else None
    found = _AMOUNT.fullmatch(str(value))
    if found is None:
        return None
    number, written = float(found[1].replace(",", "")), (found[2].lower() or unit)
    if written is None:
        return number
    return number * TO_BASE[written] if written in TO_BASE else None


def _truth(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    return True if text in {"yes", "true"} else False if text in {"no", "false"} else None


def _rule_break(rule: HardRule, value: ResolvedValue | None) -> bool | None:
    """True: the value breaks the rule. False: it keeps it. None: it can't be checked (unknown or unreadable)."""
    if value is None or value.state not in _EVIDENCE or value.value is None:
        return None
    have, want, op = value.value, rule.value, rule.operator
    if isinstance(want, bool):
        truth = _truth(have)
        return None if truth is None else (truth != want if op in ("==", "in") else truth == want)
    if isinstance(want, int | float):
        written = rule.unit.lower() if rule.unit else rule.attribute.rsplit("_", 1)[-1]
        unit = written if written in TO_BASE else None
        left, right = _base(have, unit), _base(want, unit)
        if left is None or right is None:
            return None
        if op == ">=":
            return left < right - 1e-6
        if op == "<=":
            return left > right + 1e-6
        if op == "==":
            return abs(left - right) > 1e-6
        if op == "!=":
            return abs(left - right) <= 1e-6
        return None
    text = str(have).strip().lower()
    wanted = [w.lower() for w in want] if isinstance(want, list) else [str(want).lower()]
    if op in ("==", "in"):
        return text not in wanted
    if op in ("!=", "not_in"):
        return text in wanted
    if op == "contains":
        return not any(w in text for w in wanted)
    return None


def breaks(rubric: Rubric, product: Product, values: ByKey) -> tuple[list[str], list[str]]:
    """The rules this product breaks, and those it can't be shown to keep (a value unknown or unreadable)."""
    broken: list[str] = []
    unknown: list[str] = []
    budget = rubric.budget
    if budget is not None:
        price = values.get(PRICE_KEY)
        amount = _base(price.value, None) if price is not None and price.state in _EVIDENCE else None
        if amount is None:
            unknown.append("price")
        else:
            if budget.max is not None and amount > budget.max.amount + 1e-6:
                broken.append("above the budget")
            if budget.min is not None and amount < budget.min.amount - 1e-6:
                broken.append("below the minimum")
    for rule in rubric.hard_rules:
        if not rule.active:
            continue
        verdict = _rule_break(rule, values.get(rule.attribute))
        if verdict is None:
            unknown.append(rule.attribute)
        elif verdict:
            broken.append(rule.attribute)
    name = product.name.lower()
    for exclusion in rubric.exclusions:
        target = exclusion.value.lower()
        if exclusion.kind == "brand" and (target == (product.brand or "").lower() or name.split()[:1] == [target]):
            broken.append(f"brand {exclusion.value}")
        elif exclusion.kind == "model" and target in name:
            broken.append(f"model {exclusion.value}")
        elif exclusion.kind == "feature":
            key = target.replace(" ", "_")
            for feature in (key, f"has_{key}", f"is_{key}"):
                found = values.get(feature)
                if found is not None and found.state in _EVIDENCE and _truth(found.value) is True:
                    broken.append(f"feature {exclusion.value}")
    return broken, unknown


def by_product(values: Iterable[ResolvedValue]) -> dict[str, dict[str, ResolvedValue]]:
    """Each product's values by key; the first one counts when a key repeats."""
    out: dict[str, dict[str, ResolvedValue]] = {}
    for value in values:
        out.setdefault(value.product_id, {}).setdefault(value.criterion_key, value)
    return out


def shown_violations(rubric: Rubric, shown: Sequence[Product], values: Sequence[ResolvedValue]) -> tuple[int, int]:
    """How many shown products break a rule, and how many have a rule that can't be checked (decide would hold
    both back: filtered out, or "can't tell")."""
    indexed = by_product(values)
    broken = unknown = 0
    for product in shown:
        hit, unsure = breaks(rubric, product, indexed.get(product.id, {}))
        broken += bool(hit)
        unknown += bool(unsure) and not hit
    return broken, unknown


def unsourced(
    shown: Sequence[Product], values: Sequence[ResolvedValue], sources: Sequence[Source], citations: Sequence[Citation]
) -> int:
    """Shown products with no source behind them: none of their names is in any source's title or text, and none of
    their values cites a claim from one of the result's sources (build rule 5)."""
    texts = [f"{s.title or ''} {s.text}".lower() for s in sources]
    source_ids = {s.id for s in sources}
    cited = {c.claim_id for c in citations if c.source_id in source_ids}
    indexed = by_product(values)
    count = 0
    for product in shown:
        names = [n.lower() for n in (product.name, *product.aliases) if n.strip()]
        named = any(name in text for name in names for text in texts)
        claims = {cid for v in indexed.get(product.id, {}).values() for cid in v.supporting_claim_ids}
        count += not named and not (claims & cited)
    return count


def fit(rubric: Rubric, values: ByKey) -> float | None:
    """How well a product fits: the rubric's weights times each value's own score (0 to 10), over the criteria it has
    a score for. None when it has none."""
    weighted = [(w.normalized, values[w.key].score) for w in rubric.weights if w.key in values]
    scored = [(weight, score) for weight, score in weighted if score is not None]
    total = sum(weight for weight, _ in scored)
    if not scored or total <= 0:
        return None
    return sum(weight * float(score) for weight, score in scored) / total


def most_mentioned_wins_fitting_worst(
    ranked: Sequence[Ranked], fits: Mapping[str, float | None], mentions: Mapping[str, int]
) -> bool:
    """True when the one most-mentioned shown product ranks first while it fits worst (lowest fit, ties included)
    and another shown product fits better: mentions bought the rank (US-17, build rule 2). Fewer than two products
    with a fit, or a tie for most mentioned, is never such a case."""
    known = [(pid, rank) for pid, rank in ranked if fits.get(pid) is not None]
    if len(known) < 2:
        return False
    counts = sorted((mentions.get(pid, 0) for pid, _ in known), reverse=True)
    if counts[0] == counts[1]:
        return False
    most, rank = max(known, key=lambda item: mentions.get(item[0], 0))
    lowest = min(fits[pid] or 0.0 for pid, _ in known)
    better = any((fits[pid] or 0.0) > lowest + 1e-9 for pid, _ in known)
    return rank == 1 and (fits[most] or 0.0) <= lowest + 1e-9 and better
