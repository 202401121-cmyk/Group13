"""The ranking (US-18, DR-09, DR-10): shown products scored on the shopper's own weights. Plain code.

- A product's score is the weighted sum of its criterion scores (0 to 10) times 10, so 0 to 100.
- A criterion a product has no score for takes the average of the other shown products that have one, marked
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

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from app.contracts import Contribution, Product, RankedProduct, ResolvedValue, Rubric, ValueState

MAX_RANKED = 50  # DecideResult.ranked holds at most 50
HIGH_COVERAGE = 0.8
LOW_COVERAGE = 0.5
CLEAR_LEAD = 5.0  # points ahead of the next product, for high confidence

_EVIDENCE = (ValueState.KNOWN, ValueState.DISPUTED)
_TINY = 1e-9

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


def _own_scores(
    products: Sequence[Product], keys: Sequence[str], values: Values
) -> dict[str, dict[str, ResolvedValue]]:
    """Each product's values that carry a score. Unknown, or a value with no score, is missing."""
    out: dict[str, dict[str, ResolvedValue]] = {}
    for product in products:
        out[product.id] = {}
        for key in keys:
            value = values.get((product.id, key))
            if value is not None and value.state is not ValueState.UNKNOWN and value.score is not None:
                out[product.id][key] = value
    return out


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
    scores: Mapping[str, Mapping[str, ResolvedValue]],
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
        if own is not None and own.score is not None:
            score, state = own.score, own.state
        else:  # DR-09: the average of the other shown products that have it, never zero
            peers = [s[key].score for pid, s in scores.items() if pid != product.id and key in s]
            score, state = round(sum(p for p in peers if p is not None) / len(peers), 2), ValueState.ESTIMATED
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


def rank(products: Sequence[Product], rubric: Rubric, values: Values) -> list[RankedProduct]:
    """The shown products, best first: by score, then coverage, then id. The same input always gives the same order.

    With no evidence on any criterion, none is ranked by score: they are listed by name with no score (S3-10b)."""
    keys = [w.key for w in rubric.weights]
    scores = _own_scores(products, keys, values)
    present = [key for key in keys if any(key in s for s in scores.values())]
    if not present:
        return _unranked(products, keys)
    weights = _weights(rubric, present)
    scored = sorted(
        (_score_one(p, keys, weights, scores) for p in products),
        key=lambda s: (-s.score, -s.coverage, s.product.id),
    )
    if not scored:
        return []
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
    return ranked
