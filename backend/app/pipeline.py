"""Search pipeline runner (US-52): research, extract, match, verify, enrich, decide, explain, commerce.

Every stage runs through `run_stage`, which enforces its time limit and swaps in its fallback when it fails,
so no stage can hang or crash a search (build rule 9). State stays in memory and is saved once at the end, inside
the search's time limit (D-21). `saved` is true only when the row was committed (D-27), and a save that does not
happen only sets `saved=False` (build rule 10).

A search run (US-12, P-07) also passes a progress callback, which hears each stage start and finish, and a cancel
flag, which stops the search before its next stage. The CLI and POST /api/search pass neither.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import threading
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic
from typing import Literal, Protocol, TypeVar

import httpx
from pydantic import ValidationError

from app import db
from app.commerce import CommerceStage
from app.contracts import (
    SEARCH_TIME_LIMIT_S,
    STAGE_TIME_LIMITS_S,
    Claim,
    CommerceInput,
    CommerceResult,
    DecideInput,
    DecideResult,
    EnrichInput,
    EnrichResult,
    ExplainInput,
    ExplainResult,
    ExtractInput,
    ExtractionResult,
    MatchInput,
    MatchResult,
    Product,
    ProductMention,
    ProductSummary,
    ProgressEvent,
    RecommenderCount,
    ResearchInput,
    ResearchResult,
    SearchRequest,
    SearchResult,
    Source,
    SourceFailure,
    SourceType,
    Stage,
    StageContext,
    StageName,
    StageReport,
    StageStatus,
    VerifyInput,
    VerifyResult,
    examples,
)
from app.decide import PRICE_KEY, DecideStage
from app.enrich import EnrichStage
from app.entities import FuzzyMatchStage, alias_keys
from app.explain import ExplainStage
from app.extract import default_extract_stage
from app.llm.errors import AuthFailed, InvalidOutput, ProviderTimeout, RateLimited, ServerError
from app.research import default_research_stage
from app.research.reddit.recommenders import count_recommenders
from app.research.retention import without_reddit_text
from app.verify import VerifyStage

log = logging.getLogger(__name__)

In = TypeVar("In")
Out = TypeVar("Out")

MAX_REASON_CHARS = 300  # StageReport.fallback_reason limit
MAX_NOTE_CHARS = 400  # SearchResult.coverage_note limit
MAX_CATEGORY_LABEL = 60  # ExtractInput.category_label limit, under the 64 a rubric's category may hold
MAX_MENTIONS = 1000  # MatchInput.mentions limit
_PRODUCT_ID = re.compile(r"^prd_[A-Za-z0-9_-]{3,64}$")  # a stored alias must point at a valid ProductId
MAX_CLAIMS = 5000  # VerifyInput.claims limit
MAX_VERIFY_KEYS = 30  # VerifyInput.criterion_keys limit
MAX_EXPLAINED = 10  # ExplainInput.ranked limit
MAX_PRICED = 10  # CommerceInput.products limit
TOP_PRIORITIES = 3
EXTRACT_WORKERS = 8  # sources read at once. Each has its own time limit, so more only means more waiting calls

OUT_OF_TIME = "the search ran out of time"
EARLIER_FAILURE = "an earlier step failed"
SAMPLE_NOTE = "These are sample results while live research is being built. They are not real recommendations."

# What the progress bar says about each stage (P-07): plain words, about Grade 8.
STAGE_STARTED = {
    StageName.RESEARCH: "Looking for reviews, videos and discussions.",
    StageName.EXTRACT: "Reading what each source says.",
    StageName.MATCH: "Working out which products the sources mean.",
    StageName.VERIFY: "Checking the facts against the sources.",
    StageName.ENRICH: "Filling in missing details.",
    StageName.DECIDE: "Scoring the products on what matters to you.",
    StageName.EXPLAIN: "Writing a short reason for each pick.",
    StageName.COMMERCE: "Checking prices and where to buy.",
}
STAGE_DONE = {
    StageName.RESEARCH: "Finished looking for sources.",
    StageName.EXTRACT: "Finished reading the sources.",
    StageName.MATCH: "Matched the products.",
    StageName.VERIFY: "Checked the facts.",
    StageName.ENRICH: "Finished filling in details.",
    StageName.DECIDE: "Scored and ranked the products.",
    StageName.EXPLAIN: "Wrote the reasons.",
    StageName.COMMERCE: "Checked the prices.",
}
STEP_TOOK_TOO_LONG = "That step took too long, so a simpler way was used."
# The other outcomes name their fixed reason (below): "That step had a problem: the service was busy. ..."
MAX_MESSAGE_CHARS = 200  # ProgressEvent.message limit

# Why a stage fell back, in fixed plain words chosen by the error's kind (US-12). An error's own text never leaves the
# server: an HTTP error's can hold the request URL (some APIs take the key in the query string), and others can hold
# what the shopper typed. These reach StageReport.fallback_reason, the coverage note, the saved row and the CLI.
TOOK_TOO_LONG = "took too long"
SERVICE_REFUSED = "the service refused the request"
SERVICE_BUSY = "the service was busy"
SERVICE_UNREACHABLE = "the service could not be reached"
REPLY_UNREADABLE = "the reply could not be read"
SOMETHING_WENT_WRONG = "something went wrong"

ProgressStatus = Literal["started", "done", "fallback", "failed"]
ProgressCallback = Callable[[ProgressEvent], None]


def new_search_id() -> str:
    """A new search's ID: "srch_" plus 24 hex characters, the form a search run needs (P-07)."""
    return "srch_" + secrets.token_hex(12)


class CancelFlag:
    """Lets a search be cancelled until it starts saving its result (US-12). Safe to use from any thread.

    The runner reads it before each stage and between sources, and closes it just before the save. From then on a
    request to cancel is refused, so a search is either cancelled with nothing saved, or finished and saved.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requested = False
        self._saving = False

    def request(self) -> bool:
        """Ask the search to stop. False when it is too late: the search has started saving its result."""
        with self._lock:
            if self._saving:
                return False
            self._requested = True
            return True

    @property
    def requested(self) -> bool:
        with self._lock:
            return self._requested

    def start_saving(self) -> bool:
        """Called by the runner just before its save. False when a cancel came first, so nothing may be saved."""
        with self._lock:
            if self._requested:
                return False
            self._saving = True
            return True


class SearchCancelled(Exception):
    """The cancel flag was set, so the search stopped before its next stage, or before its save. Nothing was saved."""

    def __init__(self, search_id: str) -> None:
        super().__init__(f"search {search_id} was cancelled")
        self.search_id = search_id


# Worst status wins when several reports are merged into one (extract runs once per source).
_SEVERITY = {
    StageStatus.OK: 0,
    StageStatus.SKIPPED: 1,
    StageStatus.FALLBACK: 2,
    StageStatus.TIMEOUT: 3,
    StageStatus.FAILED: 4,
}

OLDER_ARCHIVE_NOTE = "Reddit results came from an older archive (to May 2025)."
SOURCE_LABELS = {
    SourceType.REDDIT: "Reddit",
    SourceType.YOUTUBE: "YouTube",
    SourceType.WEB_SEARCH: "Web search",
    SourceType.REVIEW_SITE: "Review sites",
    SourceType.MANUFACTURER: "Maker pages",
    SourceType.LAB: "Lab tests",
    SourceType.RETAILER: "Store pages",
}
FAILURE_WORDS = {
    "timeout": "timed out",
    "rate_limited": "hit a rate limit",
    "blocked": "could not be read",
    "not_found": "found nothing",
    "quota_exhausted": "ran out of quota",
    "error": "had an error",
}


# --- storage ------------------------------------------------------------------------------------


class ResultStore(Protocol):
    """Where a finished search is saved. Returns False (or raises) when saving did not work.

    A store whose save can block also has `time_limit_s`, the longest one save may take. The runner sets that time
    aside from the search's limit. A store without it is taken to be instant.
    """

    def save(self, result: SearchResult) -> bool: ...


class InMemoryResultStore:
    """Keeps results in a list, for tests. The API and the CLI use DatabaseResultStore."""

    def __init__(self) -> None:
        self.results: list[SearchResult] = []

    def save(self, result: SearchResult) -> bool:
        self.results.append(result)
        return True


class DatabaseResultStore:
    """Saves a finished search through the database module, so `saved` tells the truth (D-27).

    save_search() answers True only when the row was committed, never raises, and gives up after DB_TIMEOUT_SECONDS.
    A database that is off, down, slow or failing answers False, and the search still finishes (build rule 10).
    """

    def __init__(self, requests: db.RequestStore, time_limit_s: float | None = None) -> None:
        self.requests = requests
        # os.environ, because the default also loads .env: default_request_store() has done that if it was needed.
        self.time_limit_s = db.load_settings(os.environ).timeout_s if time_limit_s is None else time_limit_s

    def save(self, result: SearchResult) -> bool:
        request = result.request
        # mode="json": a plain dump holds datetimes and URLs, which the database module refuses.
        # A saved row stays, and Reddit data may be kept at most 24 hours (D-53), so its text never reaches the row.
        return self.requests.save_search(
            request.session_id,
            request.rubric.model_dump(mode="json"),
            without_reddit_text(result.model_dump(mode="json")),
        )


# --- stages -------------------------------------------------------------------------------------


@dataclass
class SearchStages:
    """The eight search stages. Tests swap one field for a failing or slow version."""

    research: Stage[ResearchInput, ResearchResult]
    extract: Stage[ExtractInput, ExtractionResult]
    match: Stage[MatchInput, MatchResult]
    verify: Stage[VerifyInput, VerifyResult]
    enrich: Stage[EnrichInput, EnrichResult]
    decide: Stage[DecideInput, DecideResult]
    explain: Stage[ExplainInput, ExplainResult]
    commerce: Stage[CommerceInput, CommerceResult]


def default_search_stages() -> SearchStages:
    return SearchStages(
        research=default_research_stage(),
        extract=default_extract_stage(),
        match=FuzzyMatchStage(),
        verify=VerifyStage(),
        enrich=EnrichStage(),
        decide=DecideStage(),
        explain=ExplainStage(),
        commerce=CommerceStage(),
    )


# --- running one stage --------------------------------------------------------------------------


def run_stage(stage: Stage[In, Out], inp: In, ctx: StageContext, limit_s: float) -> tuple[Out | None, StageReport]:
    """Run one stage with a time limit, falling back on any error or timeout.

    Returns (output, report). The output is None only when the fallback failed too (status FAILED).
    """
    start = monotonic()
    timeout = max(0.0, min(limit_s, ctx.seconds_left))
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"stage-{stage.name}")
    future = pool.submit(stage.run, inp, ctx)
    try:
        done, _ = wait([future], timeout=timeout)
    finally:
        # Python cannot kill a thread. A timed-out run() keeps going in the background until it returns;
        # we just stop waiting for it. Stages should respect ctx.seconds_left themselves.
        pool.shutdown(wait=False, cancel_futures=True)

    error: Exception
    if not done:
        status, reason = StageStatus.TIMEOUT, TOOK_TOO_LONG
        error = TimeoutError(reason)
    else:
        exc = future.exception()
        if exc is None:
            return future.result(), _report(stage.name, StageStatus.OK, start)
        if not isinstance(exc, Exception):
            raise exc  # KeyboardInterrupt and friends are not stage failures
        status, reason, error = StageStatus.FALLBACK, plain_reason(exc), exc

    # The stage and the error's type only, never its message: an HTTP error's text can hold the request URL (some APIs
    # take the key in the query string), and other errors can hold what the shopper typed.
    log.warning("stage %s: %s; using its fallback", stage.name, type(error).__name__)
    try:
        output = stage.fallback(inp, ctx, error)
    except Exception as fallback_exc:
        log.error("stage %s: fallback failed too: %s", stage.name, type(fallback_exc).__name__)
        return None, _report(stage.name, StageStatus.FAILED, start, f"{reason}; the fallback failed too")
    return output, _report(stage.name, status, start, reason)


def made_by_placeholder(stage: Stage[In, Out], report: StageReport) -> bool:
    """Did a stub make this output? Follows the class that ran, never the stage's name (D-20).

    The stage's own class speaks for run(). A fallback may hand over to the class it replaced, kept in `baseline`.
    A class that does not say is real.
    """
    maker = stage if report.status is StageStatus.OK else getattr(stage, "baseline", stage)
    return bool(getattr(maker, "is_placeholder", False))


def plain_reason(error: BaseException) -> str:
    """Why a stage failed, as one of the fixed reasons above, from the error's kind. Never the error's own text.

    The error and what explicitly caused it (`raise ... from`) are checked in turn, so a stage's own error raised from
    an HTTP 429 still says the service was busy. An error that merely happened while another was being handled is not
    taken as its cause. Never raises (build rule 9): an error that cannot be read is "something went wrong".
    """
    try:
        seen: set[int] = set()
        current: BaseException | None = error
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            reason = _reason_for(current)
            if reason is not None:
                return reason
            current = current.__cause__
    except Exception:  # an odd error object must not break the search
        return SOMETHING_WENT_WRONG
    return SOMETHING_WENT_WRONG


def as_clause(reason: str | None) -> str:
    """A reason that reads as a clause after a colon: "took too long" becomes "it took too long"."""
    parts = (reason or SOMETHING_WENT_WRONG).rstrip(".").split("; ")
    return "; ".join(f"it {part}" if part == TOOK_TOO_LONG else part for part in parts)


def _reason_for(error: BaseException) -> str | None:
    if isinstance(error, TimeoutError | httpx.TimeoutException | ProviderTimeout):
        return TOOK_TOO_LONG
    status = _status_code(error)
    if status in (401, 403) or isinstance(error, AuthFailed):
        return SERVICE_REFUSED
    if status == 429 or isinstance(error, RateLimited):
        return SERVICE_BUSY
    if (status is not None and 500 <= status <= 599) or isinstance(
        error, ServerError | httpx.TransportError | ConnectionError
    ):
        return SERVICE_UNREACHABLE
    if isinstance(error, ValidationError | json.JSONDecodeError | InvalidOutput):
        return REPLY_UNREADABLE
    return None


def _status_code(error: BaseException) -> int | None:
    """The HTTP status of an error that carries a response (httpx, and the Reddit client), else None."""
    status = getattr(getattr(error, "response", None), "status_code", None)
    return status if isinstance(status, int) else None


def _report(stage: StageName, status: StageStatus, start: float, reason: str | None = None) -> StageReport:
    return StageReport(
        stage=stage,
        status=status,
        duration_ms=int((monotonic() - start) * 1000),
        fallback_reason=reason[:MAX_REASON_CHARS] if reason else None,
    )


def _skipped(stage: StageName, reason: str) -> StageReport:
    return StageReport(stage=stage, status=StageStatus.SKIPPED, duration_ms=0, fallback_reason=reason)


def _require(value: Out | None, what: str) -> Out:
    """The runner stops at the first FAILED stage, so a later stage always has its inputs."""
    if value is None:
        raise RuntimeError(f"{what} is missing; the runner should have stopped earlier")
    return value


# --- the runner ---------------------------------------------------------------------------------


class PipelineRunner:
    """Runs the eight search stages in order, saves the result and always returns a valid SearchResult."""

    def __init__(
        self,
        stages: SearchStages,
        store: ResultStore,
        time_limits: dict[StageName, float] | None = None,
        search_time_limit_s: float = SEARCH_TIME_LIMIT_S,
        extract_workers: int = EXTRACT_WORKERS,
        aliases: db.AliasStore | None = None,
    ) -> None:
        self.stages = stages
        self.store = store
        # Product aliases (DR-06): Postgres with memory underneath when the result store saves to Postgres, else memory.
        self.aliases = aliases if aliases is not None else db.alias_store_for(getattr(store, "requests", None))
        self.time_limits = {**STAGE_TIME_LIMITS_S, **(time_limits or {})}
        self.search_time_limit_s = search_time_limit_s
        self.extract_workers = extract_workers
        self.save_time_s: float = getattr(store, "time_limit_s", 0.0)

    def run(
        self,
        request: SearchRequest,
        *,
        search_id: str | None = None,
        progress: ProgressCallback | None = None,
        cancel: CancelFlag | None = None,
    ) -> SearchResult:
        """Run the search, save it and return it. The CLI and POST /api/search pass only `request`.

        A search run (US-12) passes its own `search_id`, a `progress` callback for each stage's start and outcome, and
        a `cancel` flag. Cancelling is cooperative: the flag is read before each stage, before each source extract
        starts, and before the save, so a stage already running finishes first. Then SearchCancelled is raised, and
        nothing is saved. Once the save has started, a cancel is refused (CancelFlag.request answers False).
        """
        # D-21: the limit covers the save too, so the stages stop early enough to leave the store its time. That way
        # a search that runs out of time still gets saved.
        stage_time_s = max(0.0, self.search_time_limit_s - self.save_time_s)
        ctx = StageContext(search_id=search_id or new_search_id(), time_limit_s=stage_time_s)
        started_at = datetime.now(UTC)
        search = _Search(self.stages, self.time_limits, request, ctx, cancel, self.extract_workers, self.aliases)
        search.prefetch_aliases()  # read in the background while research runs: match never waits for the database
        events = _Progress(ctx.search_id, progress)

        partial = False
        steps = search.steps()
        for i, (name, step) in enumerate(steps):
            _stop_if_cancelled(cancel, ctx.search_id)
            if search.out_of_time or ctx.seconds_left <= 0:
                search.reports += [_skipped(n, OUT_OF_TIME) for n, _ in steps[i:]]
                partial = True
                break
            events.started(name, search.sources_found())
            report = step()
            search.reports.append(report)
            events.finished(report, search.sources_found())
            if report.status is StageStatus.FAILED:
                search.reports += [_skipped(n, EARLIER_FAILURE) for n, _ in steps[i + 1 :]]
                partial = True
                break

        if cancel is not None and not cancel.start_saving():  # from here on, a cancel comes too late
            _stop_if_cancelled(cancel, ctx.search_id)
        result = search.result(started_at, partial or search.out_of_time)
        return self._save(result, self.search_time_limit_s - (monotonic() - ctx.started_at))

    def _save(self, result: SearchResult, seconds_left: float) -> SearchResult:
        """`result` says saved=True, so the stored row and the response agree. If no row was committed, say so."""
        if self._committed(result, seconds_left):
            return result
        return SearchResult.model_validate({**result.model_dump(), "saved": False})

    def _committed(self, result: SearchResult, seconds_left: float) -> bool:
        """True only when the store reports the row committed. Never raises, and never waits past the search's limit.

        The store has its own, shorter time limit (D-28). This wait is what holds the 150 seconds whatever the store
        does; like a timed-out stage, a save cut off here finishes in the background, and nobody waits for it.
        """
        if seconds_left <= 0:
            log.warning("search %s not saved: no time was left", result.search_id)
            return False
        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="result-save")
        future = pool.submit(self.store.save, result)
        try:
            done, _ = wait([future], timeout=seconds_left)
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        if not done:
            log.warning("search %s not saved: the save ran past the time limit", result.search_id)
            return False
        try:
            return bool(future.result())
        except Exception as exc:  # only the error's type: its text could hold a connection string
            log.error("could not save search %s: %s", result.search_id, type(exc).__name__)
            return False


def _stop_if_cancelled(cancel: CancelFlag | None, search_id: str) -> None:
    if cancel is not None and cancel.requested:
        log.info("search %s cancelled", search_id)
        raise SearchCancelled(search_id)


class _Progress:
    """Numbers one search's progress events from 1 and hands each to the callback. Never raises (build rule 9)."""

    def __init__(self, search_id: str, callback: ProgressCallback | None) -> None:
        self.search_id = search_id
        self.callback = callback
        self.sent = 0

    def started(self, stage: StageName, sources: dict[SourceType, int]) -> None:
        self._send(stage, "started", STAGE_STARTED[stage], sources)

    def finished(self, report: StageReport, sources: dict[SourceType, int]) -> None:
        status, message = _outcome(report, sources)
        self._send(report.stage, status, message, sources)

    def _send(self, stage: StageName, status: ProgressStatus, message: str, sources: dict[SourceType, int]) -> None:
        if self.callback is None:
            return
        try:
            event = ProgressEvent(
                event_id=self.sent + 1,
                search_id=self.search_id,
                stage=stage,
                status=status,
                at=datetime.now(UTC),
                message=message[:MAX_MESSAGE_CHARS],
                sources_found=sources,
            )
        except ValueError as exc:  # e.g. an ID that is not a run's; the search itself carries on
            log.warning("search %s: progress not sent: %s", self.search_id, type(exc).__name__)
            return
        self.sent += 1
        try:
            self.callback(event)
        except Exception as exc:  # only the type: the text could hold anything
            log.warning("search %s: the progress callback failed: %s", self.search_id, type(exc).__name__)


def _outcome(report: StageReport, sources: dict[SourceType, int]) -> tuple[ProgressStatus, str]:
    """A finished stage's event: its status and a plain message with its fixed reason, never the error's text."""
    if report.status is StageStatus.FAILED:
        return "failed", f"That step did not work: {as_clause(report.fallback_reason)}. These results will be partial."
    if report.status is StageStatus.TIMEOUT:
        return "fallback", STEP_TOOK_TOO_LONG
    if report.status is StageStatus.FALLBACK:
        return "fallback", f"That step had a problem: {as_clause(report.fallback_reason)}. A simpler way was used."
    if report.status is StageStatus.SKIPPED:
        return "done", f"Skipped because {report.fallback_reason or 'there was nothing to do'}."
    found = sum(sources.values())
    if report.stage is StageName.RESEARCH and found:
        return "done", f"Found {found} source{'' if found == 1 else 's'}."
    return "done", STAGE_DONE[report.stage]


class _Search:
    """The in-memory state of one search: each stage's output, filled in as the stages run."""

    def __init__(
        self,
        stages: SearchStages,
        time_limits: dict[StageName, float],
        request: SearchRequest,
        ctx: StageContext,
        cancel: CancelFlag | None = None,
        extract_workers: int = EXTRACT_WORKERS,
        aliases: db.AliasStore | None = None,
    ) -> None:
        self.stages = stages
        self.cancel = cancel
        self.extract_workers = extract_workers
        self.aliases = aliases if aliases is not None else db.shared_memory_aliases()
        self.decide_is_sample = False  # set when the decide stub made the ranking (until US-17)
        self.limits = time_limits
        self.request = request
        self.rubric = request.rubric
        self.ctx = ctx
        self.out_of_time = False  # set when the search's own deadline cut a stage off
        self.used_placeholder = False  # set when a stub made any part of the result
        self.sample_sources = False  # set when a stub made the research output, so its sources are not counted
        self.reports: list[StageReport] = []
        self.research_out: ResearchResult | None = None
        self.claims: list[Claim] = []
        self.mentions: list[ProductMention] = []
        self.extract_note: str | None = None
        self.recommender_counts: list[
            RecommenderCount
        ] = []  # counted after extract, from the products it found (US-13)
        self.match_out: MatchResult | None = None
        self.verify_out: VerifyResult | None = None
        self.enrich_out: EnrichResult | None = None
        self.decide_out: DecideResult | None = None
        self.explain_out: ExplainResult | None = None
        self.commerce_out: CommerceResult | None = None

    def steps(self) -> list[tuple[StageName, Callable[[], StageReport]]]:
        return [
            (StageName.RESEARCH, self.research),
            (StageName.EXTRACT, self.extract),
            (StageName.MATCH, self.match),
            (StageName.VERIFY, self.verify),
            (StageName.ENRICH, self.enrich),
            (StageName.DECIDE, self.decide),
            (StageName.EXPLAIN, self.explain),
            (StageName.COMMERCE, self.commerce),
        ]

    def _run(self, stage: Stage[In, Out], inp: In) -> tuple[Out | None, StageReport]:
        limit = self.limits[stage.name]
        budget = self.ctx.seconds_left  # read once, so the decision below matches the wait that ran
        out, report = run_stage(stage, inp, self.ctx, min(limit, budget))
        if report.status is StageStatus.TIMEOUT and budget < limit:
            # The search's deadline, not the stage's own limit, cut this stage off: the search is out of time.
            # Don't rely on seconds_left reading exactly 0 afterwards; a timed wait can wake a moment before
            # its deadline (seen on GitHub's Windows runners), which used to let the next stages start.
            self.out_of_time = True
        if out is not None and made_by_placeholder(stage, report):
            self.used_placeholder = True
        return out, report

    @property
    def weight_keys(self) -> list[str]:
        return [w.key for w in self.rubric.weights]

    def sources_found(self) -> dict[SourceType, int]:
        """Sources found so far, per type, for the progress bar. All zero until a real research stage has run."""
        counts = dict.fromkeys(SourceType, 0)
        if self.research_out is not None and not self.sample_sources:
            for source in self.research_out.sources:
                counts[source.type] += 1
        return counts

    # Each step runs one stage, keeps its output and returns its report.

    def research(self) -> StageReport:
        self.research_out, report = self._run(self.stages.research, ResearchInput(rubric=self.rubric))
        self.sample_sources = made_by_placeholder(self.stages.research, report)
        return report

    def extract(self) -> StageReport:
        sources = _require(self.research_out, "research").sources
        if not sources:
            return _skipped(StageName.EXTRACT, "there were no sources to read")
        started = monotonic()
        failed = threading.Event()

        def read(source: Source) -> tuple[ExtractionResult | None, StageReport] | None:
            """One source, each under its own time limit. None if it was never started."""
            cancelled = self.cancel is not None and self.cancel.requested
            if failed.is_set() or self.out_of_time or cancelled or self.ctx.seconds_left <= 0:
                return None  # a fallback failed too, the search is out of time, or the shopper cancelled it
            label = self.rubric.category[:MAX_CATEGORY_LABEL]
            inp = ExtractInput(source=source, criterion_keys=self.weight_keys, category_label=label)
            out, report = self._run(self.stages.extract, inp)
            if out is None:
                failed.set()  # merge() reports FAILED and the runner stops
            return out, report

        workers = min(self.extract_workers, len(sources))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="extract-source") as pool:
            done = [item for item in pool.map(read, sources) if item is not None]  # in the sources' order
        for out, _ in done:
            if out is not None:
                self.claims += out.claims
                self.mentions += out.mentions
        collect_note = getattr(self.stages.extract, "coverage_note", None)
        self.extract_note = collect_note(self.ctx.search_id) if collect_note is not None else None
        self.recommender_counts = self._counted_recommenders()
        if not done:
            return _skipped(StageName.EXTRACT, "the search stopped before any source was read")
        return _merge(StageName.EXTRACT, [report for _, report in done], int((monotonic() - started) * 1000))

    def _counted_recommenders(self) -> list[RecommenderCount]:
        """How many people name each product the run found, by code alone (US-13). Sample sources keep their own."""
        research = _require(self.research_out, "research")
        if self.sample_sources:
            return list(research.recommender_counts)
        try:
            names = [mention.raw_name for mention in self.mentions if mention.is_product]
            return count_recommenders(research.sources, names)
        except Exception as exc:  # a bug here must not stop the search (build rule 9). The type only, never its text
            log.warning("recommender counting failed: %s", type(exc).__name__)
            return []

    def match(self) -> StageReport:
        category = self.rubric.category
        inp = MatchInput(
            mentions=self.mentions[:MAX_MENTIONS],
            known_aliases=self._known_aliases(category),
            recommender_counts=self.recommender_counts,
        )
        self.match_out, report = self._run(self.stages.match, inp)
        real = report.status is StageStatus.OK and not made_by_placeholder(self.stages.match, report)
        if self.match_out is not None and real:  # a fallback merges nothing, and a stub's aliases are examples
            self._keep_aliases(category, self.match_out)
        return report

    def prefetch_aliases(self) -> None:
        try:
            self.aliases.prefetch(self.rubric.category)
        except Exception as exc:  # the store never raises, but a bug in one must not reach the search
            log.warning("aliases not read ahead: %s", type(exc).__name__)

    def _known_aliases(self, category: str) -> dict[str, str]:
        """Aliases kept by earlier searches (DR-06). Never stops the search: a failure means none are known."""
        try:
            known = self.aliases.load(category)
            return {alias: product_id for alias, product_id in known.items() if _PRODUCT_ID.match(product_id)}
        except Exception as exc:  # the store never raises, but a bug in one must not reach the search
            log.warning("known aliases not loaded: %s", type(exc).__name__)
            return {}

    def _keep_aliases(self, category: str, result: MatchResult) -> None:
        try:
            self.aliases.save(category, alias_keys(result))
        except Exception as exc:
            log.warning("aliases not kept: %s", type(exc).__name__)

    def verify(self) -> StageReport:
        research = _require(self.research_out, "research")
        rule_keys = [r.attribute for r in self.rubric.hard_rules]
        price = [PRICE_KEY] if self.rubric.budget else []  # decide checks the budget on it (US-17), before the rules
        keys = list(dict.fromkeys(self.weight_keys + price + rule_keys))[:MAX_VERIFY_KEYS]
        inp = VerifyInput(
            match=_require(self.match_out, "match"),
            claims=self.claims[:MAX_CLAIMS],
            sources=research.sources,
            criterion_keys=keys,
        )
        self.verify_out, report = self._run(self.stages.verify, inp)
        return report

    def enrich(self) -> StageReport:
        # Choosing enrichment targets is US-41; until then nothing is looked up.
        self.enrich_out, report = self._run(self.stages.enrich, EnrichInput(targets=[]))
        return report

    def decide(self) -> StageReport:
        verified = _require(self.verify_out, "verify")
        inp = DecideInput(
            rubric=self.rubric,
            products=_require(self.match_out, "match").products,
            values=verified.values,
            community=verified.community,
        )
        self.decide_out, report = self._run(self.stages.decide, inp)
        self.decide_is_sample = self.decide_out is not None and made_by_placeholder(self.stages.decide, report)
        return report

    def explain(self) -> StageReport:
        top = sorted(self.rubric.weights, key=lambda w: w.normalized, reverse=True)[:TOP_PRIORITIES]
        inp = ExplainInput(
            ranked=_require(self.decide_out, "decide").ranked[:MAX_EXPLAINED],
            claims=self.claims,
            user_priorities=[w.key for w in top],
        )
        self.explain_out, report = self._run(self.stages.explain, inp)
        return report

    def commerce(self) -> StageReport:
        matched = _require(self.match_out, "match").products
        by_id = {p.id: p for p in [*matched, *self._sample_products()]}
        ranked = _require(self.decide_out, "decide").ranked
        products = [by_id[r.product_id] for r in ranked if r.product_id in by_id][:MAX_PRICED]
        if not products:
            return _skipped(StageName.COMMERCE, "no product was ranked, so there was nothing to price")
        inp = CommerceInput(products=products, budget=self.rubric.budget)
        self.commerce_out, report = self._run(self.stages.commerce, inp)
        return report

    # Assembling the result.

    def _sample_products(self) -> list[Product]:
        """While decide is a stub (until US-17), it ranks and filters the example products, whatever match found.
        Those sample products join the result, so it stays valid and shows names, not ids; the stub already puts the
        sample note on it."""
        if not self.decide_is_sample or self.decide_out is None:
            return []
        have = {p.id for p in self.match_out.products} if self.match_out else set()
        named = {r.product_id for r in self.decide_out.ranked} | {f.product_id for f in self.decide_out.filtered}
        return [p for p in examples.match().products if p.id in named - have]  # filtered ones need their names too

    def result(self, started_at: datetime, partial: bool) -> SearchResult:
        values = self.verify_out.values if self.verify_out else []
        community = {c.product_id: c for c in self.verify_out.community} if self.verify_out else {}
        products = [
            ProductSummary(product=p, values=[v for v in values if v.product_id == p.id], community=community.get(p.id))
            for p in [*(self.match_out.products if self.match_out else []), *self._sample_products()]
        ]
        failures = self.research_out.failures if self.research_out else []
        found = self.research_out.sources if self.research_out else []
        older_archive = any(s.source_detail == "pullpush" for s in found)
        note = build_coverage_note(
            self.reports, failures, partial, older_archive=older_archive, extract_note=self.extract_note
        )
        return SearchResult(
            search_id=self.ctx.search_id,
            request=self.request,
            started_at=started_at,
            finished_at=datetime.now(UTC),
            partial=partial,
            products=products,
            decision=self.decide_out,
            explanations=self.explain_out.explanations if self.explain_out else [],
            commerce=self.commerce_out,
            sources=self.research_out.sources if self.research_out else [],
            stage_reports=self.reports,
            coverage_note=add_sample_label(note) if self.used_placeholder else note,
            saved=True,  # what the stored row must say; the runner returns a saved=False copy if it is not committed
        )


def _merge(stage: StageName, reports: list[StageReport], elapsed_ms: int) -> StageReport:
    """One report for a stage that ran several times at once (extract runs once per source), and the time it took."""
    worst = max(reports, key=lambda r: _SEVERITY[r.status])
    reasons = list(dict.fromkeys(r.fallback_reason for r in reports if r.fallback_reason))
    problems = sum(r.status is not StageStatus.OK for r in reports)
    notes = [f"tried {len(reports)} sources"] + ([f"{problems} of them fell back"] if problems else [])
    return StageReport(
        stage=stage,
        status=worst.status,
        duration_ms=elapsed_ms,
        fallback_reason="; ".join(reasons)[:MAX_REASON_CHARS] if reasons else None,
        notes=notes,
    )


# --- the coverage note --------------------------------------------------------------------------


def build_coverage_note(
    reports: Iterable[StageReport],
    failures: Iterable[SourceFailure],
    partial: bool,
    *,
    older_archive: bool = False,
    extract_note: str | None = None,
) -> str | None:
    """Plain-English summary of everything that did not go to plan. None when everything did."""
    parts: list[str] = []
    skipped: dict[str, list[str]] = {}
    for r in reports:
        label = r.stage.value.capitalize()
        reason = (r.fallback_reason or "no reason given").rstrip(".")
        if r.status is StageStatus.FALLBACK:
            parts.append(f"{label} used a simpler fallback: {as_clause(reason)}.")
        elif r.status is StageStatus.TIMEOUT:
            parts.append(f"{label} took too long, so a simpler fallback was used.")
        elif r.status is StageStatus.FAILED:
            parts.append(f"{label} failed, so the steps after it did not run.")
        elif r.status is StageStatus.SKIPPED:
            skipped.setdefault(reason, []).append(r.stage.value)
    for f in failures:
        parts.append(f"{SOURCE_LABELS[f.source_type]} {FAILURE_WORDS[f.reason]}.")
    if older_archive:
        parts.append(OLDER_ARCHIVE_NOTE)
    for reason, stages in skipped.items():
        parts.append(f"Skipped {', '.join(stages)} because {reason}.")
    if partial:
        parts.append("These results are partial.")
    if extract_note:  # last, so a note too long to fit never costs the sentences above
        parts.append(extract_note)

    note = " ".join(parts)
    return _fit(note, MAX_NOTE_CHARS) if note else None


def add_sample_label(note: str | None) -> str:
    """Add the sample sentence to the note. When both do not fit the note gives way: the label must always be seen."""
    if not note:
        return SAMPLE_NOTE
    return f"{_fit(note, MAX_NOTE_CHARS - len(SAMPLE_NOTE) - 1)} {SAMPLE_NOTE}"


def _fit(note: str, limit: int) -> str:
    return note if len(note) <= limit else note[: limit - 3].rstrip() + "..."
