"""Stage 4, research (US-12, US-13, US-38): rubric in, sources out."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, HttpUrl, model_validator

from .common import AwareDatetime, Contract, Fraction, SourceId, SourceTier, SourceType
from .rubric import Rubric

MAX_SOURCE_CHARS = 12_000  # about 3,000 tokens; longer text is trimmed before it gets here

# Where a Reddit source's text came from (US-12): one of the two archives, or Reddit's own API.
RedditOrigin = Literal["arctic_shift", "pullpush", "official"]

# Which tier a source type gets by default (US-16 can refine per site).
DEFAULT_TIER: dict[SourceType, SourceTier] = {
    SourceType.MANUFACTURER: SourceTier.MANUFACTURER,
    SourceType.LAB: SourceTier.LAB,
    SourceType.REVIEW_SITE: SourceTier.EDITORIAL,
    SourceType.YOUTUBE: SourceTier.EDITORIAL,
    SourceType.WEB_SEARCH: SourceTier.EDITORIAL,
    SourceType.REDDIT: SourceTier.COMMUNITY,
    SourceType.RETAILER: SourceTier.RETAILER,
}


class ResearchInput(Contract):
    rubric: Rubric
    known_candidates: list[str] = Field(
        default_factory=list, max_length=20, description="Product names already known, for 'X vs Y' queries"
    )


class Source(Contract):
    id: SourceId
    url: HttpUrl
    type: SourceType
    tier: SourceTier
    title: str = Field(max_length=300)
    text: str = Field(min_length=1, max_length=MAX_SOURCE_CHARS, description="Cleaned, HTML stripped, capped")
    text_hash: str = Field(pattern=r"^[a-f0-9]{64}$", description="sha256 of text, used for caching")
    fetched_at: AwareDatetime
    published_at: AwareDatetime | None = None
    community: str | None = Field(default=None, max_length=80, description="e.g. 'r/SuggestALaptop'")
    quality: Fraction = Field(default=0.5, description="US-13 thread or page quality score")
    via: Literal["api", "direct", "jina", "search_snippet"]
    source_detail: RedditOrigin | None = Field(
        default=None, description="Reddit only: which archive or API the text came from (US-12)"
    )
    author_hash: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
        description="Salted sha256 of the author's username, so US-13 can count distinct people. Never the name",
    )

    @model_validator(mode="after")
    def _check(self) -> Source:
        if self.type == SourceType.REDDIT and not self.community:
            raise ValueError("Reddit sources must record their subreddit")
        if self.type == SourceType.REDDIT and self.tier != SourceTier.COMMUNITY:
            raise ValueError("Reddit is community evidence, never editorial (US-16)")
        if self.source_detail is not None and self.type != SourceType.REDDIT:
            raise ValueError("source_detail says which Reddit archive or API was used, so it is for Reddit only")
        return self


class SourceFailure(Contract):
    source_type: SourceType
    reason: Literal["timeout", "rate_limited", "blocked", "not_found", "quota_exhausted", "error"]
    detail: str = Field(default="", max_length=300)


class ResearchResult(Contract):
    queries: list[str] = Field(min_length=1, max_length=12)
    sources: list[Source] = Field(default_factory=list, max_length=60)
    failures: list[SourceFailure] = Field(default_factory=list)

    @property
    def source_types_reached(self) -> set[SourceType]:
        return {s.type for s in self.sources}
