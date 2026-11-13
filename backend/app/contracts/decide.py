"""Stage 9, decide (US-17 to US-19, DR-01, DR-08 to DR-10). Plain code only: the LLM never filters or ranks."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .common import Contract, Fraction, ProductId, Score10, Slug
from .entities import Product
from .rubric import Rubric
from .verify import CommunitySignal, ResolvedValue, ValueState


class DecideInput(Contract):
    rubric: Rubric
    products: list[Product] = Field(max_length=200)
    values: list[ResolvedValue]
    community: list[CommunitySignal] = Field(default_factory=list)
    seed: int = Field(default=42, description="Same inputs + same seed = same output (US-19)")


class FilteredProduct(Contract):
    product_id: ProductId
    group: Literal["filtered_out", "cant_tell"]
    reasons: list[str] = Field(min_length=1, max_length=10, description="e.g. '₹94,990 is above your ₹80,000 budget'")
    rule_attribute: Slug | None = None


class Contribution(Contract):
    criterion_key: Slug
    score: Score10 | None
    normalized_weight: Fraction
    contribution: float = Field(ge=0, description="score x weight, the share of the final score")
    state: ValueState


class RankedProduct(Contract):
    product_id: ProductId
    rank: int = Field(ge=1)
    score: float | None = Field(
        ge=0, le=100, description="0 to 100. None for every product when none had evidence on any criterion (1.8.0)"
    )
    contributions: list[Contribution] = Field(min_length=1)
    coverage: Fraction = Field(description="Share of the score backed by real evidence, not estimates")
    chance_first: Fraction | None = Field(default=None, description="From simulations; None with the plain score")
    confidence: Literal["high", "medium", "low"]
    label: Literal["best_match", "runner_up"] | None = None
    gap_to_leader: float = Field(ge=0)
    tied_with: list[ProductId] = Field(default_factory=list, description="'Too close to call'")
    separating_criterion: Slug | None = None


class DecideResult(Contract):
    ranked: list[RankedProduct] = Field(max_length=50)
    filtered: list[FilteredProduct] = Field(default_factory=list)
    relaxation_hint: str | None = Field(
        default=None, max_length=300, description="When nothing passes: the one rule whose relaxation helps most"
    )
    method: Literal["monte_carlo", "weighted"]

    @model_validator(mode="after")
    def _check(self) -> DecideResult:
        ranks = [r.rank for r in self.ranked]
        if ranks != list(range(1, len(ranks) + 1)):
            raise ValueError("ranks must run 1, 2, 3 ... in order")
        shown = {r.product_id for r in self.ranked}
        if shown & {f.product_id for f in self.filtered}:
            raise ValueError("a product cannot be both ranked and filtered")
        if not self.ranked and self.relaxation_hint is None and self.filtered:
            raise ValueError("when nothing passes the rules, give a relaxation hint (US-17)")
        unscored = [r.score is None for r in self.ranked]
        if any(unscored) and not all(unscored):
            raise ValueError("every ranked product has no score, or none does")
        if any(unscored) and any(r.label is not None for r in self.ranked):
            raise ValueError("with no score, no product is labelled best match or runner-up")
        pairs = list(zip(self.ranked, self.ranked[1:], strict=False))
        if self.method == "weighted":
            if any(a.score is not None and b.score is not None and a.score < b.score - 1e-9 for a, b in pairs):
                raise ValueError("with the plain weighted score, ranks follow the score")
        else:
            if any(r.chance_first is None for r in self.ranked):
                raise ValueError("with simulations, every ranked product needs chance_first")
            if any((a.chance_first or 0) < (b.chance_first or 0) - 1e-9 for a, b in pairs):
                raise ValueError("with simulations, ranks follow the chance of being first")
        return self
