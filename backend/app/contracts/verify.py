"""Stage 7, verify (US-16, US-40, US-42, DR-03, DR-09): claims in, one resolved value per product and criterion out."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from .claims import Claim
from .common import ClaimId, Contract, Fraction, ProductId, Score10, Slug
from .entities import MatchResult
from .research import Source


class ValueState(StrEnum):
    KNOWN = "known"  # trustworthy evidence agrees
    DISPUTED = "disputed"  # independent sources disagree strongly; show both
    UNKNOWN = "unknown"  # no trustworthy claim; never scored as zero (DR-09)
    ESTIMATED = "estimated"  # filled with the peer average after enrichment failed (US-18)


class VerifyInput(Contract):
    match: MatchResult
    claims: list[Claim] = Field(max_length=5000)
    sources: list[Source] = Field(max_length=60)
    criterion_keys: list[Slug] = Field(
        min_length=1, max_length=30, description="Criteria plus any hard-rule attributes to resolve"
    )
    category: Slug | None = Field(
        default=None, description="The rubric's product category, which moves a site's trust (trust.yaml). 1.8.0"
    )


class AlternativeValue(Contract):
    """The other side of a disputed value (DR-03): what the outvoted sources say, and the claims that say it. 1.8.0"""

    value: float | bool | str | None = None
    unit: str | None = Field(default=None, max_length=16)
    score: Score10 | None = None
    claim_ids: list[ClaimId] = Field(min_length=1, description="Among the value's conflicting claims")

    @model_validator(mode="after")
    def _check(self) -> AlternativeValue:
        if self.value is None and self.score is None:
            raise ValueError("an alternative needs a value or a score")
        return self


class ResolvedValue(Contract):
    product_id: ProductId
    criterion_key: Slug = Field(description="A criterion key, or a hard-rule attribute such as 'ram_gb'")
    state: ValueState
    score: Score10 | None = None
    value: float | bool | str | None = None
    confidence: Fraction
    supporting_claim_ids: list[ClaimId] = Field(default_factory=list)
    conflicting_claim_ids: list[ClaimId] = Field(default_factory=list)
    alternatives: list[AlternativeValue] = Field(
        default_factory=list, max_length=20, description="A disputed value's other side, each with its claims. 1.8.0"
    )

    @model_validator(mode="after")
    def _check(self) -> ResolvedValue:
        s = self.state
        if s in (ValueState.KNOWN, ValueState.DISPUTED) and not self.supporting_claim_ids:
            raise ValueError(f"a {s} value must cite the claims behind it")
        if s == ValueState.DISPUTED and not self.conflicting_claim_ids:
            raise ValueError("a disputed value must cite the conflicting claims")
        if s == ValueState.UNKNOWN and self.score is not None:
            raise ValueError("an unknown value has no score; it must never become zero")
        if s == ValueState.ESTIMATED and (self.score is None or self.supporting_claim_ids):
            raise ValueError("an estimated value has a peer-average score and no claims of its own")
        if s in (ValueState.KNOWN, ValueState.DISPUTED) and self.score is None and self.value is None:
            raise ValueError(f"a {s} value needs a score or a value")
        if self.alternatives and s != ValueState.DISPUTED:
            raise ValueError("only a disputed value has alternatives")
        conflicting = set(self.conflicting_claim_ids)
        if any(set(a.claim_ids) - conflicting for a in self.alternatives):
            raise ValueError("an alternative's claims must be among the conflicting claims")
        return self


class CommunitySignal(Contract):
    """US-42: whether communities agree about a product."""

    product_id: ProductId
    label: Literal["consistent", "split", "single_source"]
    communities: list[str] = Field(default_factory=list, max_length=20)
    note: str | None = Field(default=None, max_length=300)


class VerifyResult(Contract):
    values: list[ResolvedValue]
    community: list[CommunitySignal] = Field(default_factory=list)
    dropped_unsourced: list[ProductId] = Field(
        default_factory=list, description="FR-25: products with no source, removed before scoring"
    )
    trust_method: Literal["learned", "tier_order"]

    @model_validator(mode="after")
    def _check(self) -> VerifyResult:
        pairs = [(v.product_id, v.criterion_key) for v in self.values]
        if len(pairs) != len(set(pairs)):
            raise ValueError("one resolved value per product and criterion")
        dropped = set(self.dropped_unsourced)
        if any(v.product_id in dropped for v in self.values):
            raise ValueError("a dropped product cannot still have resolved values")
        return self
