"""Stage 9, decide (US-17 to US-19, DR-01, DR-08 to DR-10). Plain code only: the LLM never filters or ranks."""

from __future__ import annotations

from typing import Annotated, Literal

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
    basis: Literal["opinion", "compared", "estimated"] | None = Field(
        default=None,
        description=(
            "How the score was made: a source's opinion, a stated value compared with the other shown products' "
            "(P-20), or the peers' average (DR-09). Decide leaves it None where it made no score; None on a result "
            "from before 1.9.0"
        ),
    )


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
    rule_notes: list[Annotated[str, Field(min_length=1, max_length=400)]] = Field(
        default_factory=list,
        max_length=5,
        description=(
            "How a RAM or storage must-have passed through one version of the product, such as 'It meets \"at least "
            "8 GB RAM\" in its 8 GB version' (US-09, S4-24). Empty when no version decided a rule; before 1.10.0"
        ),
    )


class CheckedValue(Contract):
    """A value the filter read for one product (US-23, P-42): its price, or a hard rule's attribute, as decide used it.

    With these the browser can filter again, for a changed budget or rule, with no server call, keeping a rule as decide
    does (docs/frontend-api.md, "Sprint 5 contracts"):
    - no values: the filter had nothing to read, so it can't tell;
    - one value: the rule keeps it or not;
    - several, not versions (the sides of a disputed value): every side keeps it, it passes; every side breaks it, it
      fails; else it can't tell. A disputed value with one side alone can't tell either;
    - versions (any_version): any version keeping the rule is enough. If none does and one can't be compared, it can't
      tell; if none does and the claims gave a disputed value with one side alone, it can't tell; else it fails.
    """

    product_id: ProductId
    attribute: Slug = Field(description="'price_inr', or a hard rule's attribute such as 'ram_gb'")
    state: ValueState = Field(
        description=(
            "The state as the filter read it: unknown when verify gave no value, and known for a value disputed in "
            "scores only, which the filter checks as an agreed one"
        )
    )
    values: list[float | bool | str] = Field(
        default_factory=list,
        max_length=40,
        description=(
            "What the filter compared: the price in rupees; a size as a number in `unit`; else the value as its claims "
            "gave it. Every side of a disputed value, or every version of a RAM or storage size, claims' first"
        ),
    )
    unit: str | None = Field(
        default=None, max_length=16, description="The numbers' unit: 'INR' for a price, the rule's ('gb'), else None"
    )
    any_version: bool = Field(
        default=False,
        description="True when values are the versions the product comes in (DR-02): any one keeping a rule is enough",
    )
    from_names: int = Field(
        default=0,
        ge=0,
        le=40,
        description="How many of values, the last ones, are versions only the names state; the rest are the claims.",
    )

    @model_validator(mode="after")
    def _check(self) -> CheckedValue:
        if self.from_names > len(self.values):
            raise ValueError("from_names counts some of the values")
        if self.from_names and not self.any_version:
            raise ValueError("only versions come from a product's names")
        claims = len(self.values) - self.from_names
        if claims and self.state in (ValueState.UNKNOWN, ValueState.ESTIMATED):
            raise ValueError("a value the filter could not read has no values, unless the names state versions")
        numbers = any(isinstance(v, int | float) and not isinstance(v, bool) for v in self.values)
        if self.unit is not None and not numbers:
            raise ValueError("a unit goes with a number in values")
        return self


class DecideResult(Contract):
    ranked: list[RankedProduct] = Field(max_length=50)
    filtered: list[FilteredProduct] = Field(default_factory=list)
    relaxation_hint: str | None = Field(
        default=None, max_length=300, description="When nothing passes: the one rule whose relaxation helps most"
    )
    method: Literal["monte_carlo", "weighted"]
    checked_values: list[CheckedValue] = Field(
        default_factory=list,
        max_length=5000,
        description=(
            "For every product decided, shown, can't tell or filtered out: its price and each hard rule's attribute, "
            "as the filter read them (US-23, P-42). Empty before 1.10.0"
        ),
    )

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
        read = [(v.product_id, v.attribute) for v in self.checked_values]
        if len(read) != len(set(read)):
            raise ValueError("each product's attribute is in checked_values once")
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
