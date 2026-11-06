"""The pipeline (US-52): how stages plug in, and what one search returns to the browser."""

from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic
from typing import Literal, Protocol, TypeVar

from pydantic import Field, model_validator

from .commerce import CommerceResult
from .common import (
    SCHEMA_VERSION,
    SEARCH_TIME_LIMIT_S,
    AwareDatetime,
    Contract,
    SearchId,
    SessionId,
    StageName,
    StageReport,
    StageStatus,
)
from .decide import DecideResult
from .entities import Product
from .explain import Explanation
from .research import Source
from .rubric import Rubric
from .verify import CommunitySignal, ResolvedValue

InT = TypeVar("InT", contravariant=True)
OutT = TypeVar("OutT", covariant=True)


@dataclass
class StageContext:
    """Passed to every stage. Not a contract: it never leaves the process."""

    search_id: str
    started_at: float = field(default_factory=monotonic)
    time_limit_s: float = SEARCH_TIME_LIMIT_S

    @property
    def seconds_left(self) -> float:
        return max(0.0, self.time_limit_s - (monotonic() - self.started_at))


class Stage(Protocol[InT, OutT]):
    """Every stage implements this. 'Replace, never rewrite': a better version is a new class
    with the same input and output types, and the old one becomes its fallback."""

    name: StageName

    def run(self, inp: InT, ctx: StageContext) -> OutT:
        """The best available implementation. May raise; the runner then calls fallback()."""
        ...

    def fallback(self, inp: InT, ctx: StageContext, error: Exception) -> OutT:
        """The simple baseline. Must not call an LLM or the network unless the stage has no other way."""
        ...


class SearchRequest(Contract):
    session_id: SessionId
    user_id: str | None = Field(
        default=None,
        description=(
            "The signed-in owner's ID once login exists (US-30). None for guests: a guest's request and search are "
            "stored only under the anonymous session ID and are never linked to an account."
        ),
    )
    raw_query: str = Field(min_length=1, max_length=1000)
    rubric: Rubric


class ProductSummary(Contract):
    """What a results card needs besides the ranking itself."""

    product: Product
    values: list[ResolvedValue]
    community: CommunitySignal | None = None


class SearchResult(Contract):
    """Everything the results page needs, including what went wrong. Saved at the end (build rule 10)."""

    schema_version: str = SCHEMA_VERSION
    search_id: SearchId
    request: SearchRequest
    started_at: AwareDatetime
    finished_at: AwareDatetime
    partial: bool = Field(default=False, description="True if the 150-second limit was hit")
    products: list[ProductSummary] = Field(default_factory=list)
    decision: DecideResult | None = None
    explanations: list[Explanation] = Field(default_factory=list)
    commerce: CommerceResult | None = None
    sources: list[Source] = Field(default_factory=list)
    stage_reports: list[StageReport] = Field(default_factory=list)
    coverage_note: str | None = Field(default=None, max_length=400, description="e.g. 'Reddit was unavailable'")
    saved: bool = Field(
        default=False,
        description=(
            "True only when this result's row was committed to the database. False when the database is off, down, "
            "slow or failing (the UI then says the result isn't saved)."
        ),
    )

    @model_validator(mode="after")
    def _check(self) -> SearchResult:
        if self.finished_at < self.started_at:
            raise ValueError("finished_at is before started_at")
        degraded = any(r.status is not StageStatus.OK for r in self.stage_reports)
        if (degraded or self.partial) and not self.coverage_note:
            raise ValueError("when any stage fell back, failed or timed out, say so in coverage_note")
        if self.decision:
            known = {p.product.id for p in self.products}
            missing = {r.product_id for r in self.decision.ranked} - known
            if missing:
                raise ValueError(f"ranked products missing from products: {sorted(missing)}")
        return self


class ProgressEvent(Contract):
    """One Server-Sent Event for the live progress bar."""

    search_id: SearchId
    stage: StageName
    status: Literal["started", "finished", "fallback", "skipped", "timeout", "failed"]
    message: str = Field(max_length=200, description="Plain words for the user, e.g. 'Reading 12 Reddit threads'")
    at: AwareDatetime
