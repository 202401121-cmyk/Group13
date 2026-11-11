"""Evidence (US-21): the claims behind one resolved value, and the citations a result keeps for them.

A citation is what a saved result keeps of a claim a value cites: its id, its source and, if it may be kept, its quote.
Reddit's words are never kept past 24 hours (D-53), so a saved citation from Reddit has no quote
(app/research/retention.py). The evidence route then says why the quote is missing.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, HttpUrl, model_validator

from .claims import ClaimValue
from .common import (
    ClaimId,
    ClaimType,
    Contract,
    ProductId,
    SearchId,
    Slug,
    SourceId,
    SourceTier,
    SourceType,
    StrictScore10,
    UtcDatetime,
)
from .verify import ValueState


class Citation(Contract):
    """One claim that a value in the result cites, with where it came from."""

    claim_id: ClaimId
    source_id: SourceId = Field(description="One of the result's sources")
    quote: str | None = Field(
        default=None,
        min_length=3,
        max_length=600,
        description="The claim's exact words. None when not kept: Reddit's words are not saved (D-53)",
    )


class EvidenceSource(Contract):
    """The source of a claim, as the evidence panel shows it."""

    id: SourceId
    url: HttpUrl
    name: str = Field(min_length=1, max_length=120, description="The site's domain, or the community, e.g. 'r/...'")
    type: SourceType
    tier: SourceTier
    fetched_at: UtcDatetime


class ClaimEvidence(Contract):
    """One claim behind a value, with its source (build rule 4)."""

    claim_id: ClaimId
    quote: str | None = Field(
        default=None,
        min_length=3,
        max_length=600,
        description="Exact words from the source. None when withheld; the response then says why",
    )
    value: ClaimValue | None = Field(default=None, description="Text or a number, e.g. 1.4 for kg. Or a score")
    unit: str | None = Field(default=None, max_length=16)
    score: StrictScore10 | None = Field(default=None, description="0 to 10 judgement for this criterion. Or a value")
    polarity: Literal["positive", "negative", "neutral"]
    claim_type: ClaimType
    retrieved_at: UtcDatetime = Field(description="When the source was fetched (build rule 4)")
    source: EvidenceSource

    @model_validator(mode="after")
    def _check(self) -> ClaimEvidence:
        if (self.value is None) == (self.score is None):
            raise ValueError("a claim has exactly one of a value and a score")
        return self


class EvidenceResponse(Contract):
    """What the evidence route answers for one product and criterion (US-21): the value's state and its claims."""

    search_id: SearchId
    product_id: ProductId
    criterion_key: Slug
    state: ValueState
    winning_claim_ids: list[ClaimId] = Field(
        default_factory=list, description="The claims the value rests on. A disputed value's other side is the rest"
    )
    claims: list[ClaimEvidence] = Field(default_factory=list, max_length=200)
    quote_withheld_reason: str | None = Field(
        default=None, min_length=1, max_length=200, description="Why some quotes are missing, set exactly when one is"
    )

    @model_validator(mode="after")
    def _check(self) -> EvidenceResponse:
        ids = [c.claim_id for c in self.claims]
        if len(ids) != len(set(ids)):
            raise ValueError("each claim appears once")
        if set(self.winning_claim_ids) - set(ids):
            raise ValueError("every winning claim must be among the claims")
        state = self.state
        if state == ValueState.UNKNOWN and self.claims:
            raise ValueError("an unknown value has no claims to show")
        if state == ValueState.ESTIMATED and self.winning_claim_ids:
            # Like ResolvedValue: a peer average has no claims of its own, but may show the ones that conflicted.
            raise ValueError("an estimated value has no claims of its own, so no winning claims")
        if state in (ValueState.KNOWN, ValueState.DISPUTED) and not self.winning_claim_ids:
            raise ValueError(f"a {state} value names its winning claims")
        if state == ValueState.DISPUTED and set(ids) <= set(self.winning_claim_ids):
            raise ValueError("a disputed value lists both sides: some claim must not be winning")
        withheld = any(c.quote is None for c in self.claims)
        if withheld != (self.quote_withheld_reason is not None):
            raise ValueError("quote_withheld_reason is set exactly when a quote is withheld")
        return self
