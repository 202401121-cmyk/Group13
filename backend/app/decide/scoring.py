"""The ranking (US-18, DR-09, DR-10): shown products scored on the shopper's own weights. Plain code.

- A product's score is the weighted sum of its criterion scores (0 to 10) times 10, so 0 to 100.
- A criterion score is a source's opinion (ResolvedValue.score). With none, a stated value is scored by comparing
  the shown products (US-18, P-20): a number in one unit, or yes/no, on a criterion with a direction
  (directions.py), linear from the lowest to the highest value onto 1 to 9, the direction deciding which end is 9.
  At least two shown products must have a value to compare. An opinion always wins, and the value's own state
  (Known or Disputed) is kept, so coverage counts it as evidence.
- A criterion a product has neither for takes the average of the other shown products that have one, marked
  "estimated" (DR-09). Never zero.
- A criterion no shown product has is left out for every product: its contribution says "unknown" with weight 0, and
  the other weights are renormalised. notes.left_out_note() names it.
- Mention counts (recommender_count, weighted_mentions) show how strong the evidence is; they are never read here
  (DR-10).
- Coverage is the share of the score that comes from Known and Disputed values.
- With no evidence on any criterion for any shown product, nothing is ranked by score (S3-10b): every score is None,
  nobody is the best match, and the products are listed by name. notes.NOT_RANKED_NOTE says so. Never a 0.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from app.contracts import Contribution, Product, RankedProduct, ResolvedValue, Rubric, ValueState

from .directions import Better
from .units import Quantity, attribute_unit, in_base_roughly, known_unit_roughly, quantity

MAX_RANKED = 50  # DecideResult.ranked holds at most 50
HIGH_COVERAGE = 0.8
LOW_COVERAGE = 0.5
CLEAR_LEAD = 5.0  # points ahead of the next product, for high confidence

_EVIDENCE = (ValueState.KNOWN, ValueState.DISPUTED)
_TINY = 1e-9
LOWEST, MIDDLE, HIGHEST = 1.0, 5.0, 9.0  # a compared value's score: never 0, as Unknown is never scored 0 (DR-09)
MIN_TO_COMPARE = 2
SAME_SHARE = 0.01  # values within 1% are the same: 1 year (8,766 h on average) and 365 days (8,760 h)
YES_NO = "yes_no"  # the dimension of a yes/no value
_YES, _NO = {"yes", "true"}, {"no", "false"}

Values = Mapping[tuple[str, str], ResolvedValue]
Confidence = Literal["high", "medium", "low"]
Label = Literal["best_match", "runner_up"]


def _label(position: int) -> Label | None:
    """Best match for the first product, runner-up for the second (US-18)."""
    if position == 0:
        return "best_match"
    return "runner_up" if position == 1 else None


@dataclass(frozen=True)
class _Scored:
    product: Product
    contributions: list[Contribution]
    score: float
    coverage: float


@dataclass(frozen=True)
class Evidence:
    """One product's score on one criterion: a source's opinion, or its stated value compared with the others'."""

    score: float
    state: ValueState
    compared: bool = False


Scores = dict[str, dict[str, Evidence]]


def _opinions(products: Sequence[Product], keys: Sequence[str], values: Values) -> Scores:
    """Each product's values that carry a score. Unknown, or a value with no score, is missing."""
    out: Scores = {}
    for product in products:
        out[product.id] = {}
        for key in keys:
            value = values.get((product.id, key))
            if value is not None and value.state is not ValueState.UNKNOWN and value.score is not None:
                out[product.id][key] = Evidence(value.score, value.state)
    return out


def reading(value: object, key: str) -> tuple[str, float] | None:
    """A stated value as (dimension, amount in the dimension's base unit), or (yes_no, 1 or 0). None for text, or a
    number with other words around it and no unit we know, read the way verify reads a claim."""
    if isinstance(value, bool):
        return YES_NO, 1.0 if value else 0.0
    if isinstance(value, str) and value.strip().casefold() in _YES | _NO:
        return YES_NO, 1.0 if value.strip().casefold() in _YES else 0.0
    amount = quantity(value)
    if amount is None:
        return None
    if isinstance(value, str) and not (amount.plain or known_unit_roughly(amount.unit)):
        return None
    # months and years at their average length: fine for ranking, never used for a rule (units.py)
    dimension, base = in_base_roughly(Quantity(amount.number, amount.unit), attribute_unit(key))
    return (dimension, base) if math.isfinite(base) else None


def _compared(products: Sequence[Product], key: str, better: Better, values: Values) -> dict[str, Evidence]:
    """The shown products' stated values on one criterion, scored against each other (US-18, P-20). The most common
    unit's values compare (ties go to the dimension's name); a value in a unit that can't be converted to it is left
    out. A disputed value compares on its winning side, verify's value. Fewer than two values: none."""
    readings: dict[str, tuple[str, float, ValueState]] = {}
    for product in products:
        value = values.get((product.id, key))
        if value is None or value.state not in _EVIDENCE or value.value is None:
            continue
        read = reading(value.value, key)
        if read is not None:
            readings[product.id] = (read[0], read[1], value.state)
    if len(readings) < MIN_TO_COMPARE:
        return {}
    counts = Counter(dimension for dimension, _, _ in readings.values())
    # the most values; a tie goes to a real unit before bare numbers (""), then to the name, so it is always the same
    dimension = min(counts, key=lambda d: (-counts[d], d == "", d))
    kept = {pid: (amount, state) for pid, (d, amount, state) in readings.items() if d == dimension}
    if len(kept) < MIN_TO_COMPARE:
        return {}
    low, high = min(a for a, _ in kept.values()), max(a for a, _ in kept.values())
    out: dict[str, Evidence] = {}
    for pid, (amount, state) in kept.items():
        if dimension == YES_NO:
            good = amount > 0.5 if better == "higher" else amount < 0.5
            score = HIGHEST if good else LOWEST
        elif high - low <= max(_TINY, SAME_SHARE * max(abs(high), abs(low))):
            score = MIDDLE
        else:
            share = (amount - low) / (high - low)
            score = LOWEST + (HIGHEST - LOWEST) * (share if better == "higher" else 1 - share)
        out[pid] = Evidence(round(score, 2), state, compared=True)
    return out


def evidence(
    products: Sequence[Product], keys: Sequence[str], values: Values, directions: Mapping[str, Better]
) -> Scores:
    """Each product's score per criterion: its opinion, else its stated value compared with the other shown
    products' (an opinion always wins), else nothing (DR-09 estimates it later). Mentions are never read (DR-10)."""
    scores = _opinions(products, keys, values)
    for key in keys:
        better = directions.get(key)
        if better is None:
            continue
        for pid, found in _compared(products, key, better, values).items():
            scores[pid].setdefault(key, found)
    return scores


def compared_keys(keys: Sequence[str], scores: Scores) -> list[str]:
    """The criteria at least one shown product was scored on by comparing stated values, in the rubric's order."""
    return [key for key in keys if any(key in s and s[key].compared for s in scores.values())]


def _weights(rubric: Rubric, present: Sequence[str]) -> dict[str, float]:
    """The rubric's shares over the criteria some shown product has, renormalised to add up to 1."""
    if not present:
        return {}
    total = sum(rubric.weight_of(key) for key in present)
    if total <= 0:
        return {key: 1 / len(present) for key in present}
    return {key: rubric.weight_of(key) / total for key in present}


def _coverage(contributions: Sequence[Contribution]) -> float:
    evidenced = [c for c in contributions if c.state in _EVIDENCE]
    total = sum(c.contribution for c in contributions)
    if total > _TINY:
        share = sum(c.contribution for c in evidenced) / total
    else:  # a score of 0 has no share to split: use the weight backed by evidence instead
        share = sum(c.normalized_weight for c in evidenced)
    return round(min(1.0, max(0.0, share)), 3)


def _score_one(
    product: Product,
    keys: Sequence[str],
    weights: Mapping[str, float],
    scores: Mapping[str, Mapping[str, Evidence]],
) -> _Scored:
    contributions: list[Contribution] = []
    for key in keys:
        if key not in weights:
            contributions.append(
                Contribution(
                    criterion_key=key, score=None, normalized_weight=0.0, contribution=0.0, state=ValueState.UNKNOWN
                )
            )
            continue
        own = scores[product.id].get(key)
        if own is not None:
            score, state = own.score, own.state
        else:  # DR-09: the average of the other shown products that have it, never zero
            peers = [s[key].score for pid, s in scores.items() if pid != product.id and key in s]
            score, state = round(sum(peers) / len(peers), 2), ValueState.ESTIMATED
        weight = weights[key]
        contributions.append(
            Contribution(
                criterion_key=key,
                score=score,
                normalized_weight=weight,
                contribution=round(score * weight, 4),
                state=state,
            )
        )
    exact = sum((c.score or 0.0) * c.normalized_weight for c in contributions)
    return _Scored(product, contributions, round(min(100.0, exact * 10), 2), _coverage(contributions))


def _confidence(coverage: float, lead: float | None) -> Confidence:
    """Low under 0.5 coverage; high at 0.8 or more and at least 5 points ahead of the next product; else medium."""
    if coverage < LOW_COVERAGE:
        return "low"
    if coverage >= HIGH_COVERAGE and lead is not None and lead >= CLEAR_LEAD - _TINY:
        return "high"
    return "medium"


def _unranked(products: Sequence[Product], keys: Sequence[str]) -> list[RankedProduct]:
    """Nothing to rank on: every product listed by name (then id), with no score and no label. Unknown is never 0."""
    left_out = [
        Contribution(criterion_key=key, score=None, normalized_weight=0.0, contribution=0.0, state=ValueState.UNKNOWN)
        for key in keys
    ]
    ordered = sorted(products, key=lambda p: (p.name.casefold(), p.id))
    return [
        RankedProduct(
            product_id=product.id,
            rank=n + 1,
            score=None,
            contributions=left_out,
            coverage=0.0,
            confidence="low",
            label=None,
            gap_to_leader=0.0,
        )
        for n, product in enumerate(ordered[:MAX_RANKED])
    ]


def rank(
    products: Sequence[Product], rubric: Rubric, values: Values, directions: Mapping[str, Better] | None = None
) -> list[RankedProduct]:
    """The shown products, best first: by score, then coverage, then id. The same input always gives the same order.

    With no evidence on any criterion, none is ranked by score: they are listed by name with no score (S3-10b).
    `directions` names the criteria a stated value can score (directions.py); with none, opinions only."""
    return rank_with_notes(products, rubric, values, directions)[0]


def rank_with_notes(
    products: Sequence[Product], rubric: Rubric, values: Values, directions: Mapping[str, Better] | None = None
) -> tuple[list[RankedProduct], list[str]]:
    """rank(), and the criteria scored by comparing stated values, for the decide note."""
    keys = [w.key for w in rubric.weights]
    scores = evidence(products, keys, values, directions or {})
    present = [key for key in keys if any(key in s for s in scores.values())]
    if not present:
        return _unranked(products, keys), []
    weights = _weights(rubric, present)
    scored = sorted(
        (_score_one(p, keys, weights, scores) for p in products),
        key=lambda s: (-s.score, -s.coverage, s.product.id),
    )
    if not scored:
        return [], []
    leader = scored[0].score
    ranked: list[RankedProduct] = []
    for n, item in enumerate(scored[:MAX_RANKED]):
        lead = item.score - scored[n + 1].score if n + 1 < len(scored) else None
        ranked.append(
            RankedProduct(
                product_id=item.product.id,
                rank=n + 1,
                score=item.score,
                contributions=item.contributions,
                coverage=item.coverage,
                confidence=_confidence(item.coverage, lead),
                label=_label(n),
                gap_to_leader=round(max(0.0, leader - item.score), 2),
            )
        )
    return ranked, compared_keys(keys, scores)
