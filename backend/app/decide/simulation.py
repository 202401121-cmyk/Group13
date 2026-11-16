"""Honest confidence and ties from simulations (US-19, S4-7). Plain code with numpy: no model ranks anything.

After the filter, the shown products are scored SIMULATIONS times from the input's seed, each time with every weight and
every score drawn within its uncertainty:
- a weight: w x (1 + u x z), u its uncertainty (WeightedCriterion.uncertainty) and z a standard normal draw. Never below
  0, and renormalised in each simulation;
- a score: its value's score plus normal noise of SPREAD x (1 - the value's confidence), clipped to 0 to 10. An
  estimated score (verify's peer average or ours, DR-09) varies the widest, SPREAD. A Disputed value first draws its
  side: verify's own with the value's confidence, the other sides sharing the rest. A score side is taken as it is. A
  stated value is scored against the other shown products', so when one is Disputed, each simulation draws every
  disputed product's side and scores the whole criterion again as scoring.py does, peer averages included: a side
  moves the other products' scores too.

A product's chance of being first is its share of the simulations it tops; products level at the top share it. Its
confidence is high at a chance of 0.6 or more with coverage of 0.8 or more, low under a chance of 0.3 or coverage under
0.5, and medium otherwise. Two products whose chance of beating each other lies between 0.4 and 0.6 are "too close to
call" (tied_with), and the criterion with the largest contribution gap between them separates them.

The order follows the chance of being first (the contract asks for it), then the weighted score, coverage and id. The
score shown, the contributions and the coverage stay the weighted ones (scoring.py), so they still add up. The same
inputs and seed always give the same output.

The simulations stop at a deadline inside decide's time limit (TIME_LIMIT_S): past it they raise SimulationTimeout,
and decision.py keeps the plain weighted ranking, as on any other error (build rule 9).
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from app.contracts import STAGE_TIME_LIMITS_S, Product, RankedProduct, Rubric, StageName, ValueState

from .directions import Better
from .rules import sides, value_disputed
from .scoring import (
    HIGH_COVERAGE,
    HIGHEST,
    LOW_COVERAGE,
    LOWEST,
    MAX_RANKED,
    MIDDLE,
    MIN_TO_COMPARE,
    SAME_SHARE,
    YES_NO,
    Confidence,
    Reading,
    Scores,
    Values,
    _label,
    _readings,
    _score_one,
    _Scored,
    _weights,
    evidence,
    reading,
)

SIMULATIONS = 1000
SPREAD = 2.0  # a score's widest spread, in score points (0 to 10): an estimate's, or a value no source is sure of
HIGH_CHANCE = 0.6
LOW_CHANCE = 0.3
TIE_LOW, TIE_HIGH = 0.4, 0.6  # a chance of beating the other within this is "too close to call"
TIME_LIMIT_S = 0.75 * STAGE_TIME_LIMITS_S[StageName.DECIDE]  # from decide's start: the rest is the weighted ranking's
_TINY = 1e-9


class SimulationTimeout(TimeoutError):
    """The simulations ran past their deadline; the plain weighted ranking stands."""


@dataclass(frozen=True)
class Draws:
    """What the simulations drew, for the criteria some shown product has (in the rubric's order) and the products:
    weights (simulations x criteria, each row adding up to 1), scores (simulations x products x criteria, 0 to 10) and
    totals (simulations x products, 0 to 100)."""

    keys: list[str]
    product_ids: list[str]
    weights: NDArray[np.float64]
    scores: NDArray[np.float64]
    totals: NDArray[np.float64]


@dataclass(frozen=True)
class _Cell:
    """One product's score on one criterion: the sides it may take (its own first), their chances, and its spread."""

    sides: list[float]
    chances: list[float]
    spread: float


@dataclass(frozen=True)
class _Dispute:
    """A product whose stated value on one criterion is Disputed: its sides as the comparison reads them (its own
    first, None when its own can't be read) and their chances."""

    product_id: str
    sides: list[Reading | None]
    chances: list[float]


def confidence_for(chance_first: float, coverage: float) -> Confidence:
    """High at a 0.6 chance of being first with 0.8 coverage; low under a 0.3 chance or 0.5 coverage; else medium."""
    if chance_first < LOW_CHANCE - _TINY or coverage < LOW_COVERAGE - _TINY:
        return "low"
    if chance_first >= HIGH_CHANCE - _TINY and coverage >= HIGH_COVERAGE - _TINY:
        return "high"
    return "medium"


# --- the draws ----------------------------------------------------------------------------------------------------


def _in_time(deadline: float | None) -> None:
    if deadline is not None and time.monotonic() > deadline:
        raise SimulationTimeout("the simulations ran past their deadline")


def _generator(seed: int) -> np.random.Generator:
    """numpy's generator for the seed. numpy takes no negative seed, so one is wrapped onto 0 to 2**64 - 1."""
    return np.random.default_rng(seed if seed >= 0 else seed % (1 << 64))


def _chances(sure: float, others: int) -> list[float]:
    """The own side's chance is the value's confidence; the other sides share the rest."""
    sure = min(1.0, max(0.0, sure))
    return [sure, *([(1 - sure) / others] * others)]


def _cell(product: Product, key: str, item: _Scored, scores: Scores, values: Values) -> _Cell:
    """A product's score on one criterion. A Disputed opinion draws from its sides here; a Disputed stated value is
    drawn with its whole criterion (_column), since its score depends on the other products' values."""
    contribution = next(c for c in item.contributions if c.criterion_key == key)
    base = float(contribution.score or 0.0)
    own = scores[product.id].get(key)
    value = values.get((product.id, key))
    if contribution.basis == "estimated" or own is None or value is None:
        return _Cell([base], [1.0], SPREAD)  # a peer average, verify's or ours: the widest
    sure = min(1.0, max(0.0, value.confidence))
    others = [] if own.compared else [a.score for a in value.alternatives if a.score is not None]
    if value.state is not ValueState.DISPUTED or not others:
        return _Cell([base], [1.0], SPREAD * (1 - sure))
    return _Cell([base, *others], _chances(sure, len(others)), SPREAD * (1 - sure))


def _disputes(products: Sequence[Product], key: str, values: Values, scores: Scores) -> list[_Dispute]:
    """The products whose stated value on this criterion is Disputed, with sides that can be read. An opinion always
    wins over a stated value, so a product with one is left as it is."""
    own = _readings(products, key, values)
    out: list[_Dispute] = []
    for product in products:
        found = scores[product.id].get(key)
        value = values.get((product.id, key))
        if (found is not None and not found.compared) or value is None or not value_disputed(value):
            continue
        others = [read for read in (reading(raw, key) for raw in sides(value)[1:]) if read is not None]
        if others:
            sides_read: list[Reading | None] = [own.get(product.id), *[(d, a, value.state) for d, a in others]]
            out.append(_Dispute(product.id, sides_read, _chances(value.confidence, len(others))))
    return out


def _column(
    products: Sequence[Product],
    key: str,
    better: Better,
    values: Values,
    scores: Scores,
    base: NDArray[np.float64],
    rng: np.random.Generator,
    sims: int,
    deadline: float | None,
) -> NDArray[np.float64] | None:
    """Each simulation's scores on one compared criterion (simulations x products), or None with no Disputed stated
    value on it. Each simulation draws every disputed product's side, then the stated values are compared again by
    scoring.py's rules (_compare_readings), all simulations at once, and a product with no score takes its peers'
    average (DR-09). An opinion always wins over a stated value."""
    disputes = _disputes(products, key, values, scores)
    if not disputes:
        return None
    where = {p.id: i for i, p in enumerate(products)}
    readings = _readings(products, key, values)
    dims = sorted({d for d, _, _ in readings.values()} | {s[0] for x in disputes for s in x.sides if s is not None})
    code = {d: i for i, d in enumerate(dims)}

    # each product's reading in each simulation: its dimension's code (-1 for none) and its amount
    codes = np.full((sims, len(products)), -1, dtype=np.int64)
    amounts = np.zeros((sims, len(products)), dtype=np.float64)
    for pid, (dim, amount, _) in readings.items():
        codes[:, where[pid]], amounts[:, where[pid]] = code[dim], amount
    for dispute in disputes:
        picks = rng.choice(len(dispute.sides), size=sims, p=np.array(dispute.chances) / sum(dispute.chances))
        side_codes = np.array([-1 if s is None else code[s[0]] for s in dispute.sides], dtype=np.int64)
        side_amounts = np.array([0.0 if s is None else s[1] for s in dispute.sides], dtype=np.float64)
        codes[:, where[dispute.product_id]] = side_codes[picks]
        amounts[:, where[dispute.product_id]] = side_amounts[picks]
    _in_time(deadline)

    # the dimension most values are in; a tie goes to a real unit before bare numbers (""), then to the name
    preference = sorted(range(len(dims)), key=lambda c: (dims[c] == "", dims[c]))
    place = np.empty(len(dims), dtype=np.int64)
    place[preference] = np.arange(len(dims))
    counts = np.stack([(codes == c).sum(axis=1) for c in range(len(dims))], axis=1)
    chosen = (counts * (len(dims) + 1) - place).argmax(axis=1)
    kept = codes == chosen[:, None]
    compares = kept.sum(axis=1) >= MIN_TO_COMPARE
    low = np.where(compares, np.where(kept, amounts, np.inf).min(axis=1), 0.0)
    high = np.where(compares, np.where(kept, amounts, -np.inf).max(axis=1), 1.0)
    same = high - low <= np.maximum(_TINY, SAME_SHARE * np.maximum(np.abs(high), np.abs(low)))
    share = (amounts - low[:, None]) / np.where(same, 1.0, high - low)[:, None]
    linear = LOWEST + (HIGHEST - LOWEST) * (share if better == "higher" else 1 - share)
    good = amounts > 0.5 if better == "higher" else amounts < 0.5
    yes_no = (np.array(dims, dtype=object)[chosen] == YES_NO)[:, None]
    compared = np.round(np.where(yes_no, np.where(good, HIGHEST, LOWEST), np.where(same[:, None], MIDDLE, linear)), 2)

    opinion = np.full(len(products), np.nan)
    for pid, s in scores.items():
        if key in s and not s[key].compared:
            opinion[where[pid]] = s[key].score
    has_opinion = ~np.isnan(opinion)
    found = (kept & compares[:, None]) | has_opinion[None, :]
    score = np.where(has_opinion[None, :], opinion[None, :], compared)
    count = found.sum(axis=1)
    peers = np.round(np.where(found, score, 0.0).sum(axis=1) / np.maximum(count, 1), 2)
    out = np.where(found, score, peers[:, None])
    return np.where((count == 0)[:, None], base[None, :], out)  # nothing compares: as the weighted ranking scored it


def _draw(
    products: Sequence[Product],
    rubric: Rubric,
    values: Values,
    directions: Mapping[str, Better],
    seed: int,
    sims: int,
    deadline: float | None = None,
) -> tuple[Draws, list[_Scored]]:
    products = sorted(products, key=lambda p: p.id)  # drawn in one fixed order, whatever order they came in
    keys = [w.key for w in rubric.weights]
    scores = evidence(products, keys, values, directions)
    present = [key for key in keys if any(key in s for s in scores.values())]
    if not present:
        raise ValueError("nothing to simulate: no shown product has a score")
    shares = _weights(rubric, present)
    scored = [_score_one(p, keys, shares, scores) for p in products]
    _in_time(deadline)
    rng = _generator(seed)

    # the weights: each by its own uncertainty, never below 0, renormalised in each simulation
    unsure = {w.key: w.uncertainty for w in rubric.weights}
    base_w = np.array([shares[key] for key in present], dtype=np.float64)
    spread_w = np.array([unsure[key] for key in present], dtype=np.float64)
    weights = np.clip(base_w * (1 + spread_w * rng.standard_normal((sims, len(present)))), 0.0, None)
    totals_w = weights.sum(axis=1, keepdims=True)
    weights = np.where(totals_w > _TINY, weights / np.where(totals_w > _TINY, totals_w, 1.0), 1.0 / len(present))

    # the scores: each value's own side (or a drawn side of a dispute) plus noise of its spread, within 0 and 10
    cells: list[list[_Cell]] = []
    for item in scored:
        _in_time(deadline)
        cells.append([_cell(item.product, key, item, scores, values) for key in present])
    chosen = np.broadcast_to(
        np.array([[c.sides[0] for c in row] for row in cells], dtype=np.float64), (sims, len(products), len(present))
    ).copy()
    for k, key in enumerate(present):  # a Disputed stated value: its whole criterion scored again per side
        better = directions.get(key)
        if better is None:
            continue
        column = _column(products, key, better, values, scores, chosen[0, :, k].copy(), rng, sims, deadline)
        if column is not None:
            chosen[:, :, k] = column
    for p, row in enumerate(cells):  # a Disputed opinion: one of its scores
        for k, cell in enumerate(row):
            if len(cell.sides) > 1:
                picks = rng.choice(len(cell.sides), size=sims, p=np.array(cell.chances) / sum(cell.chances))
                chosen[:, p, k] = np.array(cell.sides, dtype=np.float64)[picks]
    _in_time(deadline)
    spread = np.array([[c.spread for c in row] for row in cells], dtype=np.float64)
    drawn = np.clip(chosen + rng.standard_normal(chosen.shape) * spread, 0.0, 10.0)
    totals = np.clip(np.einsum("sk,spk->sp", weights, drawn) * 10.0, 0.0, 100.0)
    return Draws(present, [p.id for p in products], weights, drawn, totals), scored


def draw(
    products: Sequence[Product],
    rubric: Rubric,
    values: Values,
    directions: Mapping[str, Better],
    seed: int,
    sims: int = SIMULATIONS,
    deadline: float | None = None,
) -> Draws:
    """The simulations' draws for the shown products. Raises ValueError when no product has a score to draw, and
    SimulationTimeout past the deadline (a time.monotonic() reading)."""
    return _draw(products, rubric, values, directions, seed, sims, deadline)[0]


# --- the ranking --------------------------------------------------------------------------------------------------


def _separating(n: int, partners: Sequence[int], ahead: NDArray[np.float64], items: Sequence[_Scored]) -> str | None:
    """The criterion with the largest contribution gap against the tightest tie (the chance nearest 0.5, then the
    nearer rank). None when nothing separates them. On equal gaps, the first in the rubric's order."""
    m = min(partners, key=lambda j: (abs(float(ahead[n, j]) - 0.5), abs(j - n)))
    gaps = [
        abs(a.contribution - b.contribution)
        for a, b in zip(items[n].contributions, items[m].contributions, strict=True)
    ]
    widest = max(gaps, default=0.0)
    if widest <= _TINY:
        return None
    return items[n].contributions[next(i for i, gap in enumerate(gaps) if gap >= widest - _TINY)].criterion_key


def simulate(
    products: Sequence[Product],
    rubric: Rubric,
    values: Values,
    directions: Mapping[str, Better],
    seed: int,
    sims: int = SIMULATIONS,
    deadline: float | None = None,
) -> list[RankedProduct]:
    """The shown products ranked by their chance of being first, with confidence and ties (US-19). Raises
    SimulationTimeout past the deadline (a time.monotonic() reading)."""
    drawn, scored = _draw(products, rubric, values, directions, seed, sims, deadline)
    tops = drawn.totals.max(axis=1, keepdims=True)
    first = drawn.totals >= tops - _TINY
    chance = (first / first.sum(axis=1, keepdims=True)).mean(axis=0)
    chances = [round(float(c), 3) for c in chance]
    order = sorted(
        range(len(scored)),
        key=lambda i: (-chances[i], -scored[i].score, -scored[i].coverage, scored[i].product.id),
    )[:MAX_RANKED]

    shown = drawn.totals[:, order]
    ahead = (shown[:, :, None] > shown[:, None, :]).mean(axis=0) + 0.5 * (shown[:, :, None] == shown[:, None, :]).mean(
        axis=0
    )
    items = [scored[i] for i in order]
    leader = items[0].score
    ranked: list[RankedProduct] = []
    for n, (i, item) in enumerate(zip(order, items, strict=True)):
        partners = [
            m for m in range(len(order)) if m != n and TIE_LOW - _TINY <= float(ahead[n, m]) <= TIE_HIGH + _TINY
        ]
        ranked.append(
            RankedProduct(
                product_id=item.product.id,
                rank=n + 1,
                score=item.score,
                contributions=item.contributions,
                coverage=item.coverage,
                chance_first=chances[i],
                confidence=confidence_for(chances[i], item.coverage),
                label=_label(n),
                gap_to_leader=round(max(0.0, leader - item.score), 2),
                tied_with=[items[m].product.id for m in partners],
                separating_criterion=_separating(n, partners, ahead, items) if partners else None,
            )
        )
    return ranked
