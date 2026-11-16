"""What each explanation may say, picked by code (US-20, DECIDED 1). The LLM only puts these facts into words.

Per ranked product:
- its top 3: the 3 largest contributions that have a score, ties by key. A contribution is score x weight; out of the
  0-100 score it is worth `contribution x 10` points;
- one trade-off: its weakest criterion with weight 2 or more, or a Disputed value when nothing is weaker than its
  strengths (see `_trade_off`);
- what it could not verify: every criterion that is Unknown or estimated (DR-09), most weight first;
- the claims behind each fact, so each sentence can cite them.

Claims name a product as written, not by id. A claim is this product's when the match stage's own id rule gives its
name this product's id (`product_id_for`): exact, but it misses a claim written under another of the product's names.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from app.contracts import Claim, Contribution, RankedProduct, ValueState
from app.entities.clustering import product_id_for

from .numbers import numbers_in

MAX_TOP = 3
MAX_CITES = 3  # claims cited per fact: enough to check, few enough to read
# "Weight 2 or more" on the shopper's 0-10 scale. Explain sees only each weight's share, so a criterion counts when its
# share is at least 0.2 of the product's largest share: weight 2 against a top weight taken as 10.
WEIGHT_LINE = 0.2
OUT_OF = (10.0, 100.0)  # "8 out of 10", "73 out of 100"

Basis = Literal["opinion", "compared", "estimated"]


@dataclass(frozen=True)
class Fact:
    """One criterion of one product, as code scored it."""

    key: str
    label: str
    score: float  # 0-10, one decimal at most
    points: int  # its share of the 0-100 score, as shown
    contribution: float  # score x weight, exact: the top 3 never depend on rounding
    weight: float
    state: ValueState
    basis: Basis | None
    claim_ids: tuple[str, ...]

    @property
    def estimated(self) -> bool:
        return self.state is ValueState.ESTIMATED or self.basis == "estimated"

    @property
    def disputed(self) -> bool:
        return self.state is ValueState.DISPUTED


@dataclass(frozen=True)
class TradeOff:
    fact: Fact
    kind: Literal["weakest", "disputed"]


@dataclass(frozen=True)
class ProductFacts:
    product_id: str
    rank: int
    score: int | None  # the 0-100 score as shown: a whole number. None when nothing could be ranked (1.8.0)
    top: tuple[Fact, ...]
    trade_off: TradeOff | None
    unverified: tuple[str, ...]  # criterion keys
    labels: Mapping[str, str]  # every criterion's label, for naming the unverified ones
    tied: bool = False  # too close to call with another shown product (US-19): its rank alone would overstate it

    def facts(self) -> tuple[Fact, ...]:
        """The top 3, then the trade-off if it isn't one of them."""
        extra = () if self.trade_off is None or self.trade_off.fact in self.top else (self.trade_off.fact,)
        return (*self.top, *extra)

    def claim_ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(c for fact in self.facts() for c in fact.claim_ids))


def label(key: str) -> str:
    """A criterion key as words: "battery_life" -> "battery life"."""
    return key.replace("_", " ").replace("-", " ")


def shown_score(score: float) -> str:
    """A 0-10 score as shown: one decimal at most, rounded half up, and none when it is whole ("8", "7.5")."""
    return f"{Decimal(str(score)).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP):f}".removesuffix(".0")


def linked_claims(product_id: str, claims: Iterable[Claim]) -> dict[str, tuple[str, ...]]:
    """Criterion key -> the ids of the claims about this product and that criterion, sorted (see the module note)."""
    found: dict[str, list[str]] = defaultdict(list)
    for claim in claims:
        if claim.is_product_type or claim.criterion_key is None:
            continue
        if product_id_for(claim.product_mention) == product_id:
            found[claim.criterion_key].append(claim.id)
    return {key: tuple(sorted(set(ids))) for key, ids in found.items()}


def facts_for(ranked: RankedProduct, claims: Sequence[Claim]) -> ProductFacts:
    linked = linked_claims(ranked.product_id, claims)
    labels = {c.criterion_key: label(c.criterion_key) for c in ranked.contributions}
    scored = [_fact(c, linked) for c in ranked.contributions if c.score is not None]
    unverified = tuple(
        c.criterion_key
        for c in sorted(ranked.contributions, key=lambda c: (-c.normalized_weight, c.criterion_key))
        if c.score is None or c.state in (ValueState.UNKNOWN, ValueState.ESTIMATED) or c.basis == "estimated"
    )
    tied = bool(ranked.tied_with)
    if ranked.score is None:
        return ProductFacts(ranked.product_id, ranked.rank, None, (), None, unverified, labels, tied)
    # A criterion that adds nothing (the shopper gave it no weight, or it scored 0) is no strength.
    top = tuple(sorted((f for f in scored if f.contribution > 0), key=lambda f: (-f.contribution, f.key))[:MAX_TOP])
    return ProductFacts(
        ranked.product_id, ranked.rank, whole(ranked.score), top, _trade_off(scored, top), unverified, labels, tied
    )


def allowed_numbers(facts: ProductFacts, about: Iterable[Fact] | None = None) -> list[float]:
    """The numbers a sentence about these facts may name: theirs, the product's rank and score, and the "out of" scales.

    With no `about`, every fact's. A sentence names the facts it is about by citing their claims, so a number from a
    fact it doesn't cite ("12" points, written as "12 kilos") is caught. A fact with no claim to cite can't be cited, so
    its numbers are always allowed.
    """
    allowed: list[float] = [*OUT_OF, float(facts.rank)]
    if facts.score is not None:
        allowed.append(float(facts.score))
    chosen = list(facts.facts() if about is None else about)
    for fact in [*chosen, *(f for f in facts.facts() if not f.claim_ids and f not in chosen)]:
        allowed += [round(fact.score, 1), float(fact.points)]
    for name in [*facts.labels.values()]:  # a criterion's own name may hold a number ("usb 4 ports")
        allowed += [n.value for n in numbers_in(name)]
    return allowed


def whole(value: float) -> int:
    """Rounded half up, as people round ("72.5" is 73), never to the even number as round() does."""
    return int(Decimal(str(value)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _fact(c: Contribution, linked: Mapping[str, tuple[str, ...]]) -> Fact:
    return Fact(
        key=c.criterion_key,
        label=label(c.criterion_key),
        score=float(Decimal(str(c.score or 0.0)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)),
        points=whole(c.contribution * 10),
        contribution=c.contribution,
        weight=c.normalized_weight,
        state=c.state,
        basis=c.basis,
        claim_ids=linked.get(c.criterion_key, ())[:MAX_CITES],
    )


def _trade_off(scored: Sequence[Fact], top: Sequence[Fact]) -> TradeOff | None:
    """Its weakest criterion with weight 2 or more, or a Disputed value when nothing is weaker than its strengths.

    The weakest is the lowest score; a tie goes to one outside the top 3 (a strength is a poor "weak point"), then to
    the larger weight. When even that scores as well as the weakest strength, the shopper gives nothing up there, so a
    Disputed value, the risk of a value sources disagree on, is the trade-off instead, if there is one.

    An estimate is never the trade-off: it is the peers' average, not something known about this product, and it is
    named as unverified instead.
    """
    if not scored:
        return None
    line = WEIGHT_LINE * max(f.weight for f in scored)
    candidates = [f for f in scored if f.weight >= line and not f.estimated]
    if not candidates:
        return None
    weakest = min(candidates, key=lambda f: (f.score, f in top, -f.weight, f.key))
    disputed = sorted((f for f in candidates if f.disputed), key=lambda f: (-f.weight, f.key))
    nothing_weaker = weakest in top or not top or weakest.score >= min(f.score for f in top)
    if disputed and nothing_weaker:
        return TradeOff(disputed[0], "disputed")
    return TradeOff(weakest, "weakest")
