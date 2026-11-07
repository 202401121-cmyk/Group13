"""Stage 5, extract (US-14, US-42): one source in, mentions and claims out."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, HttpUrl, model_validator

from .common import ClaimId, ClaimType, Contract, Fraction, Slug, SourceId, StrictScore10, UtcDatetime
from .research import Source

# Text or a real number. Strict, so a boolean is refused instead of becoming 1.0. NaN and infinity are refused
# because they turn into null in JSON, and the claim then no longer has its value.
ClaimValue = (
    Annotated[float, Field(strict=True, allow_inf_nan=False)]
    | Annotated[str, Field(strict=True, min_length=1, max_length=200)]
)


class ExtractInput(Contract):
    source: Source
    criterion_keys: list[Slug] = Field(min_length=1, max_length=10)
    category_label: str = Field(max_length=60)


class ProductMention(Contract):
    raw_name: str = Field(min_length=1, max_length=150)
    is_product: bool = Field(description="False for product types such as 'memory foam' (US-14)")
    brand: str | None = Field(default=None, max_length=60)
    author_hash: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
        description="For distinct-recommender counts. A salted sha256, never a username",
    )
    recommends: bool | None = None


class Claim(Contract):
    """One statement about one product, always with its exact quote (build rule 4)."""

    id: ClaimId
    source_id: SourceId
    source_url: HttpUrl = Field(description="The page the quote is from (build rule 4)")
    product_mention: str = Field(min_length=1, max_length=150, description="The product's name as written")
    is_product_type: bool = Field(
        default=False, description="True for things like 'memory foam' that are not buyable products (US-14)"
    )
    criterion_key: Slug | None = Field(default=None, description="None = general opinion")
    value: ClaimValue | None = Field(default=None, description="Text or a number, e.g. 1.4 for kg. Or a score")
    unit: str | None = Field(default=None, max_length=16)
    score: StrictScore10 | None = Field(default=None, description="0 to 10 judgement for this criterion. Or a value")
    quote: str = Field(min_length=3, max_length=600, description="Exact words from the source")
    claim_type: ClaimType
    polarity: Literal["positive", "negative", "neutral"]
    weight: Fraction = Field(default=1.0, description="Lowered by sarcasm, weak complaints (US-42)")
    retrieved_at: UtcDatetime = Field(description="When the source was fetched (build rule 4)")
    extracted_at: UtcDatetime = Field(description="When this claim was read from the source")

    @model_validator(mode="after")
    def _check(self) -> Claim:
        if (self.value is None) == (self.score is None):
            raise ValueError("a claim has exactly one of a value and a score")
        if self.score is not None and self.criterion_key is None:
            raise ValueError("a scored claim must name its criterion")
        return self


class ExtractionResult(Contract):
    source_id: SourceId
    mentions: list[ProductMention] = Field(default_factory=list, max_length=100)
    claims: list[Claim] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def _check(self) -> ExtractionResult:
        if any(c.source_id != self.source_id for c in self.claims):
            raise ValueError("every claim must come from this result's source")
        return self
