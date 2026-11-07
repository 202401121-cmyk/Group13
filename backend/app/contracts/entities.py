"""Stage 6, match (US-13, US-15, DR-02, DR-06): raw names in, canonical products out."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .claims import ProductMention
from .common import Contract, ProductId, VariantId
from .research import RecommenderCount


class MatchInput(Contract):
    mentions: list[ProductMention] = Field(max_length=1000)
    known_aliases: dict[str, ProductId] = Field(
        default_factory=dict, description="Saved aliases from earlier searches (DR-06)"
    )
    recommender_counts: list[RecommenderCount] = Field(
        default_factory=list, max_length=200, description="From research (US-13), for Product.recommender_count"
    )


class Variant(Contract):
    """DR-02: versions that differ in price or specs are separate variants of one product."""

    id: VariantId
    label: str = Field(max_length=120, description="e.g. '16 GB RAM / 512 GB SSD / India'")
    attributes: dict[str, str] = Field(default_factory=dict, description="e.g. {'ram': '16GB', 'region': 'IN'}")


class Product(Contract):
    id: ProductId
    name: str = Field(min_length=1, max_length=150, description="Canonical display name")
    brand: str | None = Field(default=None, max_length=60)
    kind: Literal["product", "product_type"] = Field(
        default="product", description="product_type for commodities like 'ring-shank nails'"
    )
    aliases: list[str] = Field(default_factory=list, max_length=50)
    variants: list[Variant] = Field(default_factory=list, max_length=20)
    recommender_count: int = Field(default=0, ge=0, description="Distinct authors who recommended it")
    weighted_mentions: float = Field(default=0.0, ge=0, description="Mentions after the 180-day half-life")

    @model_validator(mode="after")
    def _check(self) -> Product:
        ids = [v.id for v in self.variants]
        if len(ids) != len(set(ids)):
            raise ValueError("variant ids must be unique")
        return self


class MatchResult(Contract):
    products: list[Product] = Field(max_length=200)
    alias_map: dict[str, ProductId] = Field(description="Every raw name -> its canonical product")
    unmatched: list[str] = Field(default_factory=list, description="Names kept apart because matching was unsure")
    method: Literal["model", "fuzzy"]

    @model_validator(mode="after")
    def _check(self) -> MatchResult:
        ids = {p.id for p in self.products}
        if len(ids) != len(self.products):
            raise ValueError("product ids must be unique")
        dangling = set(self.alias_map.values()) - ids
        if dangling:
            raise ValueError(f"alias_map points to unknown products: {sorted(dangling)}")
        return self
