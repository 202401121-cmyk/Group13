"""The pipeline (US-52): how stages plug in, and what one search returns to the browser."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from time import monotonic
from typing import Annotated, Literal, Protocol, TypeVar

from pydantic import Field, model_validator

from .commerce import CommerceResult
from .common import (
    SCHEMA_VERSION,
    SEARCH_TIME_LIMIT_S,
    AwareDatetime,
    Contract,
    SearchId,
    SearchRunId,
    SessionId,
    SourceType,
    StageName,
    StageReport,
    StageStatus,
    UtcDatetime,
)
from .decide import DecideResult
from .entities import Product
from .evidence import Citation
from .explain import Explanation
from .research import Source
from .rubric import Rubric
from .verify import CommunitySignal, ResolvedValue

MAX_RUN_EVENTS = 200  # far more than the stages send: a run's progress can't grow without limit

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
    with the same input and output types, and the old one becomes its fallback.

    `is_placeholder` is True on a stub, whose output is example data and not research: the runner then labels the
    result as a sample. A real class whose fallback hands over to the class it replaced keeps that class's instance in
    `self.baseline`, so the label follows the class that really made the output."""

    name: StageName
    is_placeholder: bool = False

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
    citations: list[Citation] = Field(
        default_factory=list,
        description=(
            "For each claim a value cites: its id, its source and its quote. Saved, a Reddit quote is dropped (D-53). "
            "All or none: empty in a result from before 1.7.0"
        ),
    )
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
        self._check_citations()
        return self

    def _check_citations(self) -> None:
        """Every claim a value cites is cited once, from one of the result's sources (US-21).

        All or nothing: a result from before 1.7.0, or from a pipeline that does not fill them yet, has none.
        """
        if not self.citations:
            return
        ids = [c.claim_id for c in self.citations]
        if len(ids) != len(set(ids)):
            raise ValueError("each claim is cited once")
        cited = {
            claim_id
            for summary in self.products
            for value in summary.values
            for claim_id in (*value.supporting_claim_ids, *value.conflicting_claim_ids)
        }
        stray = sorted(set(ids) - cited)
        if stray:
            raise ValueError(f"citations name claims that no value cites: {stray}")
        missing = sorted(cited - set(ids))
        if missing:
            raise ValueError(f"citations leave out claims a value cites: {missing}")
        sources = {s.id for s in self.sources}
        unknown = sorted({c.source_id for c in self.citations} - sources)
        if unknown:
            raise ValueError(f"citations name sources not in this result's sources: {unknown}")


class ProgressEvent(Contract):
    """One Server-Sent Event for the live progress bar (P-07)."""

    event_id: int = Field(ge=1, description="Counts up from 1 within a run, so a client can resume")
    search_id: SearchRunId
    stage: StageName
    status: Literal["started", "done", "fallback", "failed"]
    at: UtcDatetime
    message: str = Field(
        min_length=1, max_length=200, description="Plain words for the user, e.g. 'Reading 12 Reddit threads'"
    )
    sources_found: dict[SourceType, Annotated[int, Field(ge=0)]] = Field(
        default_factory=dict, description="Sources found so far, per source type"
    )


class SearchRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    PARTIAL = "partial"  # cut short; SearchResult.partial is true, and the result holds what was found
    FAILED = "failed"
    CANCELLED = "cancelled"


_RUN_ENDED = {SearchRunStatus.DONE, SearchRunStatus.PARTIAL, SearchRunStatus.FAILED, SearchRunStatus.CANCELLED}
_RUN_HAS_RESULT = {SearchRunStatus.DONE, SearchRunStatus.PARTIAL}
_RUN_MAY_SKIP_START = {SearchRunStatus.QUEUED, SearchRunStatus.FAILED, SearchRunStatus.CANCELLED}


class SearchRunStarted(Contract):
    """What starting a run answers, at once, before the work is done (P-07). The body sent is a SearchRequest."""

    search_id: SearchRunId


class SearchRun(Contract):
    """A run's state at any moment (P-07): how far it has got and, once finished, its SearchResult."""

    search_id: SearchRunId
    status: SearchRunStatus
    started_at: AwareDatetime | None = Field(default=None, description="None until the run leaves the queue")
    finished_at: AwareDatetime | None = Field(default=None, description="Set once the run has ended")
    progress: list[ProgressEvent] = Field(default_factory=list, max_length=MAX_RUN_EVENTS)
    result: SearchResult | None = Field(default=None, description="Set when the status is done or partial")

    @model_validator(mode="after")
    def _check(self) -> SearchRun:
        self._check_times()
        self._check_result()
        if any(e.search_id != self.search_id for e in self.progress):
            raise ValueError("every progress event must belong to this run")
        if [e.event_id for e in self.progress] != list(range(1, len(self.progress) + 1)):
            raise ValueError("progress event ids must count up from 1")
        return self

    def _check_times(self) -> None:
        if (self.status in _RUN_ENDED) != (self.finished_at is not None):
            raise ValueError("a run has finished_at exactly when it has ended")
        if self.status is SearchRunStatus.QUEUED and self.started_at is not None:
            raise ValueError("a queued run has not started yet")
        if self.status not in _RUN_MAY_SKIP_START and self.started_at is None:
            raise ValueError("a run that ran needs started_at")
        if self.started_at and self.finished_at and self.finished_at < self.started_at:
            raise ValueError("finished_at is before started_at")

    def _check_result(self) -> None:
        if (self.status in _RUN_HAS_RESULT) != (self.result is not None):
            raise ValueError("a run has a result exactly when it is done or partial")
        if self.result is None:
            return
        if self.result.search_id != self.search_id:
            raise ValueError("the result must belong to this run")
        if self.result.partial != (self.status is SearchRunStatus.PARTIAL):
            raise ValueError("the run's partial status and its result must agree")
