"""Stage 4, research (US-12, US-13, US-38): rubric in, sources out."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, HttpUrl, model_validator

from .common import AwareDatetime, Contract, Fraction, SourceId, SourceTier, SourceType, UtcDatetime
from .llm import DataClass
from .rubric import Rubric

MAX_SOURCE_CHARS = 12_000  # about 3,000 tokens; longer text is trimmed before it gets here
MAX_COMMENT_CHARS = 2_000  # one Reddit comment, trimmed the same way
MAX_SOURCE_COMMENTS = 200  # comments kept per thread
MAX_COMMENT_DEPTH = 3  # US-13 reads comment trees three levels deep

# Where a Reddit source's text came from (US-12): one of the two archives, or Reddit's own API.
RedditOrigin = Literal["arctic_shift", "pullpush", "official"]

# A source is never private: it is text we fetched, not something the shopper typed (build rule 8).
SourceDataClass = Literal[DataClass.PUBLIC, DataClass.REDDIT]

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


class SourceComment(Contract):
    """One Reddit comment in a thread (US-13)."""

    id: str = Field(min_length=1, max_length=32)
    parent_id: str | None = Field(
        default=None, min_length=1, max_length=32, description="The comment or thread it replies to"
    )
    depth: int = Field(ge=1, le=MAX_COMMENT_DEPTH, description="1 is a direct reply to the thread")
    upvotes: int = Field(description="Can be negative")
    text: str = Field(min_length=1, max_length=MAX_COMMENT_CHARS)
    quality: Fraction = Field(default=0.5, description="US-13 comment quality score")
    author_hash: str | None = Field(
        default=None,
        max_length=128,
        description=(
            "A salted hash of the author's username, so one person counts once. Never the username itself (D-53). "
            "The code that builds a comment makes the hash; this model can't tell a hash from a name. "
            "None when the author is deleted or unknown."
        ),
    )

    @model_validator(mode="after")
    def _check(self) -> SourceComment:
        if self.depth > 1 and self.parent_id is None:
            raise ValueError("a reply below the top level must name its parent")
        return self


class Source(Contract):
    id: SourceId
    url: HttpUrl
    type: SourceType
    tier: SourceTier
    title: str = Field(max_length=300)
    text: str = Field(min_length=1, max_length=MAX_SOURCE_CHARS, description="Cleaned, HTML stripped, capped")
    text_hash: str = Field(pattern=r"^[a-f0-9]{64}$", description="sha256 of text, used for caching")
    fetched_at: UtcDatetime
    published_at: AwareDatetime | None = None
    community: str | None = Field(default=None, min_length=1, max_length=80, description="e.g. 'r/SuggestALaptop'")
    site: str | None = Field(
        default=None, min_length=1, max_length=120, description="The site's domain, for a web page"
    )
    data_class: SourceDataClass = Field(
        default=DataClass.PUBLIC,
        description="What this text may be sent to (US-46). Follows the type: Reddit is reddit, the rest public",
    )
    quality: Fraction = Field(default=0.5, description="US-13 thread or page quality score")
    via: Literal["api", "direct", "jina", "search_snippet", "firecrawl"] = Field(
        description="How the text was read: an API, a plain fetch, Jina Reader, a search snippet or Firecrawl (D-62)"
    )
    source_detail: RedditOrigin | None = Field(
        default=None, description="Reddit only: which archive or API the text came from (US-12)"
    )
    author_hash: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
        description="Salted sha256 of the author's username, so US-13 can count distinct people. Never the name",
    )
    upvotes: int | None = Field(default=None, description="Reddit only. Can be negative")
    comment_count: int | None = Field(default=None, ge=0, description="Reddit only: the thread's total")
    comments: tuple[SourceComment, ...] = Field(
        default=(), max_length=MAX_SOURCE_COMMENTS, description="Reddit only: the comments we read"
    )

    @model_validator(mode="before")
    @classmethod
    def _set_data_class(cls, data: Any) -> Any:
        """Left out, the data class follows the type, so a builder can't forget to mark Reddit text."""
        if isinstance(data, dict) and data.get("data_class") is None:
            reddit = data.get("type") == SourceType.REDDIT
            return {**data, "data_class": DataClass.REDDIT if reddit else DataClass.PUBLIC}
        return data

    @model_validator(mode="after")
    def _check(self) -> Source:
        if self.type == SourceType.REDDIT and not self.community:
            raise ValueError("Reddit sources must record their subreddit")
        if self.type == SourceType.REDDIT and self.tier != SourceTier.COMMUNITY:
            raise ValueError("Reddit is community evidence, never editorial (US-16)")
        if self.source_detail is not None and self.type != SourceType.REDDIT:
            raise ValueError("source_detail says which Reddit archive or API was used, so it is for Reddit only")
        if (self.type == SourceType.REDDIT) != (self.data_class == DataClass.REDDIT):
            raise ValueError("a source's data class must match its type: Reddit is reddit, the rest are public")
        if (self.site is None) == (self.community is None):
            raise ValueError("a source has a site or a community: exactly one of the two")
        if self.type != SourceType.REDDIT and (self.upvotes, self.comment_count, self.comments) != (None, None, ()):
            raise ValueError("upvotes, comment_count and comments are for Reddit only")
        ids = [c.id for c in self.comments]
        if len(ids) != len(set(ids)):
            raise ValueError("each comment appears once in a source")
        return self


class SourceFailure(Contract):
    source_type: SourceType
    reason: Literal["timeout", "rate_limited", "blocked", "not_found", "quota_exhausted", "error"]
    detail: str = Field(default="", max_length=300)


class RecommenderCount(Contract):
    """How many different people recommend one product (US-13). Counted by code, never by an LLM."""

    product_mention: str = Field(min_length=1, max_length=150, description="The product's name as written")
    distinct_recommenders: int = Field(ge=0, description="One person posting ten times counts once")
    threads: int = Field(ge=0, description="How many threads those people posted in")

    @model_validator(mode="after")
    def _check(self) -> RecommenderCount:
        if (self.distinct_recommenders == 0) != (self.threads == 0):
            raise ValueError("a count has both people and threads, or neither")
        return self


class ResearchResult(Contract):
    queries: list[str] = Field(min_length=1, max_length=12)
    sources: list[Source] = Field(default_factory=list, max_length=60)
    failures: list[SourceFailure] = Field(default_factory=list)
    recommender_counts: list[RecommenderCount] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def _check(self) -> ResearchResult:
        names = [c.product_mention.casefold() for c in self.recommender_counts]
        if len(names) != len(set(names)):
            raise ValueError("each product is counted once")
        return self

    @property
    def source_types_reached(self) -> set[SourceType]:
        return {s.type for s in self.sources}
