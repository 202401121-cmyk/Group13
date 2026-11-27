"""Search pipeline runner (US-52): research, extract, match, verify, enrich, decide, explain, commerce.

Every stage runs through `run_stage`, which enforces its time limit and swaps in its fallback when it fails,
so no stage can hang or crash a search (build rule 9). State stays in memory and is saved once at the end, after
the result is complete and outside the search's 150 seconds, with a time limit of its own (P-38, replacing D-21's save
inside the limit): a slow save never cuts the search short. `saved` is true only when the row was committed (D-27), and
a save that does not happen only sets `saved=False` (build rule 10).

A search run (US-12, P-07) also passes a progress callback, which hears each stage start and finish, and a cancel
flag, which stops the search before its next stage. The CLI and POST /api/search pass neither.

Verify and decide are real (S3-10b, US-16 to US-18): TrustVerifyStage and WeightedDecideStage, with the Sprint 1 stubs
kept only as their fallbacks. A product verify drops as unsourced is left out of decide and of the result, and the
result cites every claim its values rest on (SearchResult.citations).
"""

from __future__ import annotations

import inspect
import json
import logging
import os
import re
import secrets
import threading
from collections.abc import Callable, Collection, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from time import monotonic
from typing import Literal, Protocol, TypeVar
from uuid import UUID

import httpx
from pydantic import ValidationError

from app import db
from app.commerce import default_commerce_stage
from app.contracts import (
    SEARCH_TIME_LIMIT_S,
    STAGE_TIME_LIMITS_S,
    Citation,
    Claim,
    CommerceInput,
    CommerceResult,
    DecideInput,
    DecideResult,
    EnrichInput,
    EnrichResult,
    EnrichTarget,
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
    ValueState,
    VerifyInput,
    VerifyResult,
    examples,
)
from app.contracts.enrich import ENRICH_TOP_N
from app.decide import PRICE_KEY, WeightedDecideStage
from app.decide.features import feature_key, known_feature
from app.decide.rules import NO_SINGLE_CHANGE
from app.enrich import default_enrich_stage, price_targets
from app.enrich.facts import FactAsk, other_site, spec_label, stand_in_label
from app.enrich.stage import LATER_S as ENRICH_LATER_S
from app.enrich.stage import MIN_LOOKUP_S as ENRICH_MIN_LOOKUP_S
from app.enrich.stage import facts_note, prices_note
from app.enrich.targets import (
    fact_targets,
    filled_targets,
    held_keys,
    provisional_ranking,
    readable_price,
    shared_targets,
)
from app.entities import ModelMatchStage, alias_keys
from app.explain import ExplainStage
from app.extract import default_extract_stage
from app.extract.schedule import SendWindow, public_page, read_in_lanes
from app.extract.settings import load_extract_settings
from app.llm.errors import AuthFailed, InvalidOutput, ProviderTimeout, RateLimited, ServerError
from app.research import default_research_stage
from app.research.readers import FIRECRAWL, JINA
from app.research.reddit.recommenders import count_recommenders
from app.research.retention import without_reddit_text
from app.research.sites import SKIPPED_DETAIL, skipped_sentence
from app.verify import TrustVerifyStage

log = logging.getLogger(__name__)

In = TypeVar("In")
Out = TypeVar("Out")

MAX_REASON_CHARS = 300  # StageReport.fallback_reason limit
MAX_NOTE_CHARS = 400  # SearchResult.coverage_note limit
MAX_CATEGORY_LABEL = 60  # ExtractInput.category_label limit, under the 64 a rubric's category may hold
MAX_MENTIONS = 1000  # MatchInput.mentions limit
_PRODUCT_ID = re.compile(r"^prd_[A-Za-z0-9_-]{3,64}$")  # a stored alias must point at a valid ProductId
MAX_CLAIMS = 5000  # VerifyInput.claims limit
MAX_VERIFY_SOURCES = 60  # VerifyInput.sources limit: research's own sources and enrich's must fit together
FIRST_RANKING = "_first"  # the first ranking's search ID ends so (US-41): never the real search's own
SEARCH_SNIPPET = "search_snippet"  # Source.via for a search result never read in full
MAX_VERIFY_KEYS = 30  # VerifyInput.criterion_keys limit
MAX_REPORT_NOTES = 20  # StageReport.notes limit
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
SAVING = "Saving your results."  # said once, on the last stage's line, while a finished search is saved (P-38)
# The save has its store's own limit (DB_SAVE_TIMEOUT_SECONDS), and the store answers within it. This grace only stops
# a store that never answers; a store that gives no limit gets the default one. It is defined beside the save limit,
# because the Reddit cache's serve_limit() allows for both.
SAVE_GRACE_S = db.SAVE_GRACE_S

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
# US-34 (D-94): a search taking over reading begun during the interview waits this long past its own window for the
# reads still going, each of which ends within its own limit.
JOIN_GRACE_S = 1.0
EARLY_UNFINISHED = "the reading begun during the interview did not finish in time"
READ_HERE = "the reading begun during the interview did not finish, so this search read its sources"


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
# Every LLM call the search made failed on every provider (US-31). First in the note: it explains the rest.
LLM_UNAVAILABLE_NOTE = (
    "The AI services we use were all unavailable, so no source could be read. Please try again later."
)
# A page reader that failed or was capped is named in the note (US-38): its name starts the failure's detail.
READER_NAMES = (JINA, FIRECRAWL)
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

    A store whose save can block also has `time_limit_s`, the longest one save may take. The runner waits that long
    after the search, outside its limit (P-38); for a store without it, the database's default save limit. A store
    that can write a row also says `saves`, so the progress stream can say a save is happening.
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

    save_search() answers True only when the row was committed, never raises, and gives up after
    DB_SAVE_TIMEOUT_SECONDS (P-38). A database that is off, down, slow or failing answers False, and the search still
    finishes (build rule 10).
    """

    def __init__(self, requests: db.RequestStore, time_limit_s: float | None = None) -> None:
        self.requests = requests
        # os.environ, because the default also loads .env: default_request_store() has done that if it was needed.
        self.time_limit_s = db.load_settings(os.environ).save_timeout_s if time_limit_s is None else time_limit_s

    @property
    def saves(self) -> bool:
        """Will a save be tried? Not with the database off, nor while its breaker skips it: then no "Saving your
        results." line is sent, and the answer False comes at once."""
        if isinstance(self.requests, db.NullRequestStore):
            return False
        breaker = getattr(self.requests, "breaker", None)
        return not isinstance(breaker, db.CircuitBreaker) or breaker.state == "closed"

    def save(self, result: SearchResult) -> bool:
        entered = monotonic()
        request = result.request
        try:  # the signed-in owner, which the API sets from the checked token (US-30); None for a guest
            owner = UUID(request.user_id) if request.user_id is not None else None
        except ValueError:
            log.warning("search not saved: its user_id is not a user's id")
            return False
        # mode="json": a plain dump holds datetimes and URLs, which the database module refuses.
        # A saved row stays, and Reddit data may be kept at most 24 hours (D-53), so its text never reaches the row.
        rubric, row = request.rubric.model_dump(mode="json"), without_reddit_text(result.model_dump(mode="json"))
        # The runner waits time_limit_s from the call: what building the row took is no longer the database's.
        left = self.time_limit_s - (monotonic() - entered)
        if left <= 0:
            log.warning("search not saved: building its row took all of the save's time")
            return False
        return self.requests.save_search(request.session_id, rubric, row, user_id=owner, within_s=left)


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
        match=ModelMatchStage(),  # the trained matcher, with fuzzy matching as its baseline (US-15, S3-15)
        verify=TrustVerifyStage(),
        enrich=default_enrich_stage(),  # prices from store listings (US-41); the stub is its fallback
        decide=WeightedDecideStage(),
        explain=ExplainStage(),  # code's paragraph, with checked evidence sentences from public quotes (D-91)
        commerce=default_commerce_stage(),  # real prices (US-24); the stub is its fallback
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
    """Did example data reach this output? Follows the class that ran, never the stage's name (D-20).

    The stage's own class speaks for run(). A fallback may hand over to the class it replaced, kept in `baseline`.
    A live stage whose baseline is a stub hands over to the stub's fallback() (tests/unit/test_sample_label.py holds
    every such class to it), so its output is a sample only when the stub says its fallback gives example data
    (`fallback_is_example`, S4-35): research's one query and no sources is not; decide's example ranking is. A class
    that does not say is real; a placeholder that does not say gives example data.
    """
    if report.status is StageStatus.OK:
        return bool(getattr(stage, "is_placeholder", False))
    maker = getattr(stage, "baseline", stage)
    return bool(getattr(maker, "is_placeholder", False)) and bool(getattr(maker, "fallback_is_example", True))


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
        early: EarlyHandover | None = None,
    ) -> SearchResult:
        """Run the search, save it and return it. The CLI and POST /api/search pass only `request`.

        A search run (US-12) passes its own `search_id`, a `progress` callback for each stage's start and outcome, and
        a `cancel` flag. Cancelling is cooperative: the flag is read before each stage, before each source extract
        starts, and before the save, so a stage already running finishes first. Then SearchCancelled is raised, and
        nothing is saved. Once the save has started, a cancel is refused (CancelFlag.request answers False).

        `early`: research and extraction begun during the interview (US-34, D-94). Research is taken over as it is, and
        extraction once it ends, within this search's own window; both still report, as stages already done. The
        search's 150 seconds count from here, as before.
        """
        # P-38: the stages have the whole limit; the save comes after it, with its own (so a search that runs out of
        # time is still saved, and a slow save never cuts the search short).
        ctx = StageContext(search_id=search_id or new_search_id(), time_limit_s=self.search_time_limit_s)
        started_at = datetime.now(UTC)
        search = _Search(
            self.stages, self.time_limits, request, ctx, cancel, self.extract_workers, self.aliases, early=early
        )
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
        if getattr(self.store, "saves", False):
            events.saving(search.reports, search.sources_found())
        return self._save(result)

    def _save(self, result: SearchResult) -> SearchResult:
        """`result` says saved=True, so the stored row and the response agree. If no row was committed, say so."""
        if self._committed(result, self.save_wait_s):
            return result
        return SearchResult.model_validate({**result.model_dump(), "saved": False})

    @property
    def save_wait_s(self) -> float:
        """The longest the run waits for its save: the store's own limit (or the default one), and SAVE_GRACE_S."""
        return (self.save_time_s if self.save_time_s > 0 else db.DEFAULT_SAVE_TIMEOUT_S) + SAVE_GRACE_S

    def _committed(self, result: SearchResult, wait_s: float) -> bool:
        """True only when the store reports the row committed. Never raises, and never waits past `wait_s`.

        The store answers within its own limit (P-38); this wait only stops a store that never answers. Like a
        timed-out stage, a save cut off here finishes in the background, and nobody waits for it.
        """
        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="result-save")
        future = pool.submit(self.store.save, result)
        try:
            done, _ = wait([future], timeout=wait_s)
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


class EarlyHandover(Protocol):
    """Research and extraction begun while the shopper answered the interview (US-34, D-94), for the confirmed search
    to take over: app/early_search.py's job. The search never runs research again, and reads no source twice."""

    def researched(self) -> tuple[_Search, StageReport]:
        """The early search, its research done, and research's report."""
        ...

    def extracted(self, until: float, wait_s: float, stopped: Callable[[], bool]) -> StageReport | None:
        """Extraction's report once it has ended. It sends nothing new after `until` (the monotonic clock), and is
        waited for at most `wait_s`, or until `stopped` (the search's cancel) says so. None when it didn't end with a
        report: the search then reads the sources itself, if its window is still open."""
        ...


def early_search(runner: PipelineRunner, request: SearchRequest, cancel: CancelFlag, limit_s: float) -> _Search:
    """A search's research and extraction, begun before the shopper confirms it (US-34, D-94): the same stages, limits,
    caps and data-class rules as the search's own, on `request`'s early rubric, with `limit_s` for the reading."""
    ctx = StageContext(search_id=new_search_id(), time_limit_s=limit_s)
    return _Search(runner.stages, runner.time_limits, request, ctx, cancel, runner.extract_workers, runner.aliases)


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

    def saving(self, reports: list[StageReport], sources: dict[SourceType, int]) -> None:
        """Once, while the finished search is saved, on the line of the last stage that ran OK, as "done": the
        contract has no event for a save. A stage that fell back, failed or was skipped keeps its own words on its line,
        so when no stage ran OK nothing is sent."""
        ran_ok = [report for report in reports if report.status is StageStatus.OK]
        if ran_ok:
            self._send(ran_ok[-1].stage, "done", SAVING, sources)

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
        early: EarlyHandover | None = None,
    ) -> None:
        self.stages = stages
        self.cancel = cancel
        self.early = early  # reading begun during the interview, taken over by research and extract (US-34)
        self._early: _Search | None = None
        self.extract_workers = extract_workers
        self.aliases = aliases if aliases is not None else db.shared_memory_aliases()
        self.decide_is_sample = False  # set when the decide stub made the ranking: decide fell back to it
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
        self.llm_unavailable = False  # every LLM call this search made failed on every provider (US-31)
        self.decide_note: str | None = None
        self.enrich_note: str | None = None  # how many prices came from store listings (US-41)
        self.enrich_sources: list[Source] = []  # the listings enrich's claims came from, once verify took them
        self._fact_ranking: list[str] = []  # the first ranking's product ids, best first (D-101)
        self.enrich_other_facts = 0  # of the facts verify took, those from another checked page only (D-102)
        self.research_note: str | None = None  # which readers read the hard pages (D-100 item 1)
        self.commerce_note: str | None = None  # how many products couldn't be priced (US-24, S4-26)
        self.recommender_counts: list[
            RecommenderCount
        ] = []  # counted after extract, from the products it found (US-13)
        self.match_out: MatchResult | None = None
        self.verify_out: VerifyResult | None = None
        self.verify_ok = False  # verify ran and finished OK: only then are its values looked past (US-41)
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

    @property
    def rule_keys(self) -> list[str]:
        return [r.attribute for r in self.rubric.hard_rules]

    @property
    def features(self) -> list[str]:
        """The features the shopper ruled out (S3-9), each by its own name in config/exclusions.yaml. A feature that
        file doesn't list is left out, so the shopper's own words never go to extract or verify."""
        named = (known_feature(e.value) for e in self.rubric.exclusions if e.kind == "feature")
        return list(dict.fromkeys(name for name in named if name is not None))

    @property
    def feature_keys(self) -> list[str]:
        """The keys decide reads a ruled-out feature under ("has_touchscreen"), so verify resolves a value for each."""
        keys = (feature_key(name) for name in self.features)
        return list(dict.fromkeys(key for key in keys if key is not None))

    def sources_found(self) -> dict[SourceType, int]:
        """Sources found so far, per type, for the progress bar. All zero until a real research stage has run."""
        counts = dict.fromkeys(SourceType, 0)
        if self.research_out is not None and not self.sample_sources:
            for source in self.research_out.sources:
                counts[source.type] += 1
        return counts

    # Each step runs one stage, keeps its output and returns its report.

    def research(self) -> StageReport:
        if self.early is not None:  # done during the interview, on what it couldn't change (US-34, D-94)
            self._early, report = self.early.researched()
            self.research_out, self.sample_sources = self._early.research_out, self._early.sample_sources
            self.used_placeholder = self.used_placeholder or self._early.used_placeholder  # the sample note shows
            self.research_note = self._early.research_note
            return report
        self.research_out, report = self._run(self.stages.research, ResearchInput(rubric=self.rubric))
        self.sample_sources = made_by_placeholder(self.stages.research, report)
        self.research_note = self._stage_note(self.stages.research)  # which readers read hard pages (D-100)
        return report

    def extract(self) -> StageReport:
        if self.early is not None:
            return self._extract_early(self.early)
        return self._extract_sources()

    def _extract_sources(self) -> StageReport:
        sources = _require(self.research_out, "research").sources
        if not sources:
            return _skipped(StageName.EXTRACT, "there were no sources to read")
        started = monotonic()
        failed = threading.Event()
        # Public calls ask for the known spec keys among the hard rules, and the price with a budget (US-14), and each
        # ruled-out feature config/exclusions.yaml knows, by its own name. A stub stage has no such method, so nothing
        # changes for it, and one written before features were asked for gets the rest. Keys and names from our config
        # only: the rules' and the exclusions' own words are never passed.
        ask_for_specs = getattr(self.stages.extract, "ask_for_specs", None)
        if ask_for_specs is not None:
            has_budget = self.rubric.budget is not None
            if _takes(ask_for_specs, "features"):
                _hook(
                    "ask_for_specs",
                    ask_for_specs,
                    self.ctx.search_id,
                    self.rule_keys,
                    has_budget,
                    features=self.features,
                )
            else:
                _hook("ask_for_specs", ask_for_specs, self.ctx.search_id, self.rule_keys, has_budget)

        label = self.rubric.category[:MAX_CATEGORY_LABEL]

        def input_for(source: Source) -> ExtractInput:
            return ExtractInput(source=source, criterion_keys=self.weight_keys, category_label=label)

        def halted() -> bool:
            """A fallback failed too, or the shopper cancelled: what is left is not "not reached" for time."""
            return failed.is_set() or (self.cancel is not None and self.cancel.requested)

        def stopped() -> bool:
            return halted() or self.out_of_time or self.ctx.seconds_left <= 0

        def read(source: Source) -> tuple[ExtractionResult | None, StageReport] | None:
            """One source, each under its own time limit from when it is sent. None if it was never started."""
            if stopped():
                return None  # a fallback failed too, the search is out of time, or the shopper cancelled it
            out, report = self._run(self.stages.extract, input_for(source))
            if out is None:
                failed.set()  # merge() reports FAILED and the runner stops
            return out, report

        # A public page is sent when a public provider has room, maker, store and review pages first, and only while
        # the time left covers its own limit and every later stage's, which keep their time (US-46, S4-29).
        window = SendWindow(
            seconds_left=lambda: self.ctx.seconds_left,
            later_stages_s=self._later_stages_s(),
            source_s=self.limits[StageName.EXTRACT],
        )
        stage = self.stages.extract
        open_window = getattr(stage, "open_window", None)
        if open_window is not None:
            _hook("open_window", open_window, self.ctx.search_id, monotonic() + window.seconds_open())
        room_for = getattr(stage, "room_for", None)

        def room(source: Source, within_s: float) -> bool | None:
            # None (can't tell, or the check raised) sends the page at once, as before: the call keeps every limit.
            verdict = _hook("room_for", room_for, self.ctx.search_id, input_for(source), within_s)
            return verdict if isinstance(verdict, bool) else None

        workers = min(self.extract_workers, len(sources))
        checks_room = room_for is not None
        sent = read_in_lanes(
            sources, read, workers=workers, window=window, room=room if checks_room else None, stop=halted
        )
        done = sent.results  # in the sources' order
        count_not_reached = getattr(stage, "count_not_reached", None)
        if count_not_reached is not None:
            _hook("count_not_reached", count_not_reached, self.ctx.search_id, sent.not_reached)
        for out, _ in done:
            if out is not None:
                self.claims += out.claims
                self.mentions += out.mentions
        # Read before the note: collecting the note clears the stage's counts for this search.
        counts = _hook("counts_for", getattr(self.stages.extract, "counts_for", None), self.ctx.search_id)
        # A source whose read ran out of time got no answer either: every provider can hang as well as fail.
        timed_out = sum(1 for _, report in done if report.status is StageStatus.TIMEOUT)
        answered = bool(self.claims or self.mentions)  # any claim or product named means a provider answered
        self.llm_unavailable = not answered and every_llm_call_failed(counts, timed_out)
        self.extract_note = self._stage_note(self.stages.extract)
        self.recommender_counts = self._counted_recommenders()
        if not done:
            return _skipped(StageName.EXTRACT, "the search stopped before any source was read")
        return _merge(StageName.EXTRACT, [report for _, report in done], int((monotonic() - started) * 1000))

    def _extract_early(self, early: EarlyHandover) -> StageReport:
        """Extraction begun during the interview (US-34, D-94): waited for inside this search's own send window, then
        taken over as it is. A rule key or a ruled-out feature the interview added, with no claim yet, is then asked of
        the pages that gave claims, in the time left (_read_added_keys); without time it stays Unknown (DR-09)."""
        started = monotonic()
        window = self._send_window()
        open_s = window.seconds_open()

        def cancelled() -> bool:
            return self.cancel is not None and self.cancel.requested

        wait_s = open_s + self.limits[StageName.EXTRACT] + JOIN_GRACE_S
        report = early.extracted(monotonic() + open_s, wait_s, cancelled)
        done = self._early
        if report is None or done is None:
            if cancelled() or not self._send_window().is_open():
                return _skipped(StageName.EXTRACT, EARLY_UNFINISHED)
            # The job failed outside its stage: this search reads its sources in the time left (build rule 9).
            own = self._extract_sources()
            notes = [*own.notes, READ_HERE]
            return StageReport.model_validate({**own.model_dump(), "notes": notes})
        self.used_placeholder = self.used_placeholder or done.used_placeholder
        self.claims, self.mentions = list(done.claims), list(done.mentions)
        self.extract_note, self.llm_unavailable = done.extract_note, done.llm_unavailable
        self.recommender_counts = list(done.recommender_counts)
        _hook("keep_claims", getattr(self.stages.extract, "keep_claims", None), self.claims)  # their sources open
        more = self._read_added_keys()
        if not more:
            return report
        asked = _merge(StageName.EXTRACT, more, int((monotonic() - started) * 1000))
        worst = max((report, asked), key=lambda r: _SEVERITY[r.status])
        reasons = [r.fallback_reason for r in (report, asked) if r.fallback_reason]
        return StageReport(
            stage=StageName.EXTRACT,
            status=worst.status,
            duration_ms=report.duration_ms + asked.duration_ms,
            fallback_reason="; ".join(reasons)[:MAX_REASON_CHARS] if reasons else None,
            notes=[*report.notes, f"asked {len(more)} sources again for keys added in the interview"],
        )

    def _read_added_keys(self) -> list[StageReport]:
        """The rule keys and ruled-out features the final rubric has and no claim covers yet (US-34, D-94 item 3),
        asked of the public pages that gave claims, within this search's window. Only a key a public call can ask for
        counts (a known spec key, as extraction asks: app/extract/prompts.py), so no page is read again for nothing,
        and only claims on those keys are kept: the criteria were read already. Keys and names from our config only."""
        ask_for_specs = getattr(self.stages.extract, "ask_for_specs", None)
        if ask_for_specs is None:
            return []
        settings = getattr(self.stages.extract, "settings", None)
        askable = set(getattr(settings, "public_spec_keys", None) or load_extract_settings().public_spec_keys)
        claimed = {claim.criterion_key for claim in self.claims}
        own = set(self.weight_keys)  # read under the criteria already
        rules = [key for key in self.rule_keys if key in askable and key not in own and key not in claimed]
        takes_features = _takes(ask_for_specs, "features")
        features = [n for n in self.features if feature_key(n) not in claimed] if takes_features else []
        wanted = set(rules) | {key for key in (feature_key(name) for name in features) if key is not None}
        if not wanted:
            return []
        cited = {claim.source_id for claim in self.claims}
        research = _require(self.research_out, "research")
        sources = [source for source in research.sources if source.id in cited and public_page(source)]
        if not sources:
            return []
        if takes_features:
            _hook("ask_for_specs", ask_for_specs, self.ctx.search_id, rules, False, features=features)
        else:
            _hook("ask_for_specs", ask_for_specs, self.ctx.search_id, rules, False)
        label = self.rubric.category[:MAX_CATEGORY_LABEL]
        window = self._send_window()
        stage = self.stages.extract
        _hook(
            "open_window", getattr(stage, "open_window", None), self.ctx.search_id, monotonic() + window.seconds_open()
        )

        def read(source: Source) -> tuple[ExtractionResult | None, StageReport] | None:
            if self.out_of_time or self.ctx.seconds_left <= 0 or (self.cancel is not None and self.cancel.requested):
                return None
            inp = ExtractInput(source=source, criterion_keys=self.weight_keys, category_label=label)
            return self._run(stage, inp)

        sent = read_in_lanes(sources, read, workers=min(self.extract_workers, len(sources)), window=window, room=None)
        added = [
            claim for out, _ in sent.results if out is not None for claim in out.claims if claim.criterion_key in wanted
        ]
        self.claims += added
        _hook("keep_claims", getattr(stage, "keep_claims", None), added)
        note = self._stage_note(stage)  # the asking again's own counts, for this search
        if note:
            self.extract_note = f"{self.extract_note} {note}" if self.extract_note else note
        return [report for _, report in sent.results]

    def _send_window(self) -> SendWindow:
        """New sends while the time left covers one source's limit and every later stage's (build rule 9)."""
        return SendWindow(
            seconds_left=lambda: self.ctx.seconds_left,
            later_stages_s=self._later_stages_s(),
            source_s=self.limits[StageName.EXTRACT],
        )

    def _later_stages_s(self) -> float:
        """Every stage after extraction, at its own limit: what the send window leaves them (build rule 9)."""
        names = [name for name, _ in self.steps()]
        return sum(self.limits[name] for name in names[names.index(StageName.EXTRACT) + 1 :])

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
        inp = self._verify_input(self.claims, research.sources)
        try:
            self.verify_out, report = self._run(self.stages.verify, inp)
            self.verify_ok = report.status is StageStatus.OK
            report = self._with_report_notes(self.stages.verify, report)  # before forget, which clears them
        finally:
            self._forget_filter_keys()
        return report

    def _verify_input(self, claims: Sequence[Claim], sources: Sequence[Source]) -> VerifyInput:
        """Verify's input for these claims and sources. Its filter keys are handed to the stage first."""
        rule_keys = self.rule_keys + self.feature_keys  # a ruled-out feature is checked as a rule is (S3-9)
        price = [PRICE_KEY] if self.rubric.budget else []  # decide checks the budget on it (US-17), before the rules
        keys = list(dict.fromkeys(self.weight_keys + price + rule_keys))[:MAX_VERIFY_KEYS]
        # The keys decide filters on: the price, every hard rule's attribute and every ruled-out feature's key, a
        # criterion too or not. Community claims give them no value (S3-10b, DR-01). Keys only, like extract's specs.
        # A stage without the method (the stub) has no use for them; one that keeps them is told to forget them once
        # verify is done, run or fallback.
        use_filter_keys = getattr(self.stages.verify, "use_filter_keys", None)
        _hook("use_filter_keys", use_filter_keys, self.ctx.search_id, list(dict.fromkeys(price + rule_keys)))
        return VerifyInput(
            match=_require(self.match_out, "match"),
            claims=list(claims[:MAX_CLAIMS]),
            sources=list(sources),
            criterion_keys=keys,
            category=self.rubric.category,
        )

    def _with_report_notes(self, stage: object, report: StageReport) -> StageReport:
        """The report, with the stage's own notes for this search (`report_notes(search_id)`), such as how many
        products verify dropped as unsourced (US-40). Never stops the search."""
        collect = getattr(stage, "report_notes", None)
        if collect is None:
            return report
        try:
            notes = [note for note in collect(self.ctx.search_id) if isinstance(note, str) and note]
        except Exception as exc:  # the type only, never its text (build rule 9)
            log.warning("a stage's report notes were not read: %s", type(exc).__name__)
            return report
        if not notes:
            return report
        merged = [*report.notes, *notes][:MAX_REPORT_NOTES]
        return StageReport.model_validate({**report.model_dump(), "notes": merged})

    def _forget_filter_keys(self) -> None:
        forget = getattr(self.stages.verify, "forget", None)
        if forget is None:
            return
        try:
            forget(self.ctx.search_id)
        except Exception as exc:  # the type only, never its text (build rule 9)
            log.warning("verify's filter keys were not forgotten: %s", type(exc).__name__)

    @property
    def dropped(self) -> set[str]:
        """Products verify found no source for (FR-25): left out of decide and of the result."""
        return set(self.verify_out.dropped_unsourced) if self.verify_out else set()

    def _kept_products(self) -> list[Product]:
        dropped = self.dropped
        return [p for p in (self.match_out.products if self.match_out else []) if p.id not in dropped]

    def enrich(self) -> StageReport:
        """Prices from store listings for the kept products whose price is unknown (US-41, P-34), and the top products'
        missing facts from checked public pages, makers' and review sites' first (US-41, S5-6, D-102). The targets are
        picked here, in code: the facts after a first ranking, filled from "can't tell" when few rank. New claims go
        through verify again, so decide sees them; any failure changes nothing."""
        stage = self.stages.enrich
        products = self._kept_products()
        targets = self._enrich_targets(products)
        _hook("use_products", getattr(stage, "use_products", None), self.ctx.search_id, products)
        _hook("use_search", getattr(stage, "use_search", None), self.ctx.search_id, self._fact_ask())
        self.enrich_out, report = self._run(stage, EnrichInput(targets=targets))
        found = _hook("sources_for", getattr(stage, "sources_for", None), self.ctx.search_id)
        sources = [s for s in found if isinstance(s, Source)] if isinstance(found, list) else []
        claims = self.enrich_out.new_claims if self.enrich_out is not None else []
        prices, facts = self._reverified(claims, sources)
        # What verify took, never what was only found: else the note would say what didn't happen.
        notes = [
            *([prices_note(prices)] if prices else []),
            *([facts_note(facts, self.enrich_other_facts)] if facts else []),
        ]
        if notes:
            self.enrich_note = " ".join(notes)
        return report

    def _fact_ask(self) -> FactAsk:
        """What the fact look-up may know of this search: its category and region, the pages research read, and the
        first ranking's order, which splits the look-ups between Serper and Firecrawl (D-101)."""
        research = self.research_out
        read = frozenset(str(s.url) for s in research.sources) if research is not None else frozenset()
        ranked = tuple(self._fact_ranking)
        return FactAsk(category=self.rubric.category, region=self.rubric.region, known_urls=read, ranked=ranked)

    def _enrich_targets(self, products: list[Product]) -> list[EnrichTarget]:
        """None unless verify finished OK with values of its own. A stub's are examples, which a price can't change;
        after a fallback, verify run again would give values its report says it didn't. Prices and facts share enrich's
        searches (DECIDED 1)."""
        stub = bool(getattr(self.stages.verify, "is_placeholder", False))
        if self.verify_out is None or self.match_out is None or stub or not self.verify_ok:
            return []
        source_of = {claim.id: claim.source_id for claim in self.claims}
        prices = price_targets(self.rubric, products, self.verify_out.values, self.match_out.alias_map, source_of)
        return self._fact_targets(products, prices)

    def _fact_targets(self, products: list[Product], prices: list[EnrichTarget]) -> list[EnrichTarget]:
        """The top products' criteria with no evidence, after a first ranking (US-41, DECIDED 1), sharing enrich's
        searches with the prices. Only a criterion with a fixed public stand-in can be searched for (D-51). When fewer
        than ENRICH_TOP_N products rank, the rest come from decide's "can't tell" group, each with the unknown that held
        it back first (D-102 item 1, filled_targets)."""
        match = _require(self.match_out, "match")
        verified = _require(self.verify_out, "verify")
        first = self._first_ranking(products)
        ranked = list(first.ranked) if first is not None else []
        self._fact_ranking = [item.product_id for item in ranked]
        if first is None:
            return shared_targets(prices, [])
        settings = load_extract_settings()
        category = self.rubric.category
        known: dict[str, bool] = {}

        def searchable(key: str) -> bool:
            if key not in known:
                known[key] = stand_in_label(category, key, settings) is not None
            return known[key]

        by_id = {p.id: p for p in products}
        held = [each for each in first.filtered if each.group == "cant_tell"]
        usual = shared_targets(prices, fact_targets(self.rubric, ranked, by_id, match.alias_map, searchable))
        if len(ranked) >= ENRICH_TOP_N or not held:
            return usual

        def askable(key: str) -> bool:
            return spec_label(key, settings) is not None

        try:
            holding = [by_id[each.product_id] for each in held if each.product_id in by_id]
            return filled_targets(
                self.rubric,
                ranked,
                held,
                provisional_ranking(self.rubric, holding, verified.values),
                by_id,
                verified.values,
                match.alias_map,
                searchable,
                askable,
                prices,
                held_keys(self.rubric, first.checked_values),
            )
        except Exception as exc:  # build rule 9: the usual look-ups, and the search goes on
            log.warning("enrich's look-ups from can't tell were not picked: %s", type(exc).__name__)
            return usual

    def _first_ranking(self, products: list[Product]) -> DecideResult | None:
        """Decide's ranking on what verify found so far, for the fact look-ups' targets only (US-41): its ranked and its
        "can't tell" products. Its report is never added and its note is read away, so the result shows only the real
        decide's. A stub decide, a fallback or no time gives none: then no fact is looked up."""
        verified = self.verify_out
        decide_s = self.limits[StageName.DECIDE]
        if verified is None or bool(getattr(self.stages.decide, "is_placeholder", False)) or self.out_of_time:
            return None
        if self.ctx.seconds_left < decide_s + ENRICH_LATER_S + ENRICH_MIN_LOOKUP_S:  # enrich would have no time
            return None
        inp = DecideInput(rubric=self.rubric, products=products, values=verified.values, community=verified.community)
        # Under a search ID of its own: a note it keeps, even late after a time-out, is never the real decide's.
        first = replace(self.ctx, search_id=f"{self.ctx.search_id}{FIRST_RANKING}")
        try:
            out, report = run_stage(self.stages.decide, inp, first, min(decide_s, self.ctx.seconds_left))
        except Exception as exc:  # build rule 9: no first ranking, so no fact look-ups
            log.warning("the first ranking for enrich did not run: %s", type(exc).__name__)
            return None
        finally:
            _hook("coverage_note", getattr(self.stages.decide, "coverage_note", None), first.search_id)
        if out is None or report.status is not StageStatus.OK:
            return None
        return without_products(out, self.dropped)

    def _reverified(self, claims: Sequence[Claim], sources: Sequence[Source]) -> tuple[int, int]:
        """Verify again with enrich's claims (US-41, P-34 item 4). How many products now have a price from them, and
        how many facts (a criterion other than the price, for one product) now rest on them.

        Only a claim whose source enrich handed back is used, and only a source research didn't find. Verify's own
        limit holds. A run that doesn't finish OK, or in which no listing's price is taken, keeps the first values, and
        its report is never added: the search goes on as if enrich had found nothing. A product the first run dropped
        as unsourced stays dropped: a listing for another product can't bring it back.
        """
        research = self.research_out
        first = self.verify_out
        have = {source.id for source in research.sources} if research is not None else set()
        known = {source.id: source for source in sources if source.id not in have}
        new = [claim for claim in claims if claim.source_id in known]
        if research is None or not new or first is None:
            return 0, 0
        kept, added = fit_sources(research.sources, list(known.values()), self.claims)
        new = [claim for claim in new if claim.source_id in {source.id for source in added}]
        if not new:
            return 0, 0
        all_claims = [*self.claims[: MAX_CLAIMS - len(new)], *new]  # the new ones always reach verify
        try:
            inp = self._verify_input(all_claims, [*kept, *added])
            limit = min(self.limits[StageName.VERIFY], self.ctx.seconds_left)
            out, report = run_stage(self.stages.verify, inp, self.ctx, limit)
            self._with_report_notes(self.stages.verify, report)  # read, so none is kept for this search
        except Exception as exc:  # build rule 9: the first values stand
            log.warning("verify with enrich's claims did not run: %s", type(exc).__name__)
            return 0, 0
        finally:
            self._forget_filter_keys()
        if out is None or report.status is not StageStatus.OK:
            log.info("search %s: enrich's claims left out, as verify did not finish again", self.ctx.search_id)
            return 0, 0
        out = _still_dropped(out, first.dropped_unsourced)
        ids = {claim.id for claim in new}
        resting = [value for value in out.values if ids.intersection(value.supporting_claim_ids)]
        taken = sum(1 for value in resting if value.criterion_key == PRICE_KEY and readable_price(value))
        facts = sum(
            1
            for value in resting
            if value.criterion_key != PRICE_KEY and value.state in (ValueState.KNOWN, ValueState.DISPUTED)
        )
        if not taken and not facts:
            log.info("search %s: verify took none of enrich's claims", self.ctx.search_id)
            return 0, 0
        # D-102: a fact resting on other checked pages only gets its own words in the note: it counts for less.
        others = {source.id for source in added if other_site(source)}
        source_of = {claim.id: claim.source_id for claim in new}
        self.enrich_other_facts = sum(
            1
            for value in resting
            if value.criterion_key != PRICE_KEY
            and value.state in (ValueState.KNOWN, ValueState.DISPUTED)
            and all(source_of[claim] in others for claim in ids.intersection(value.supporting_claim_ids))
        )
        self.verify_out = out
        self.claims = [*self.claims, *new]
        self.enrich_sources = added
        _hook("keep_claims", getattr(self.stages.extract, "keep_claims", None), new)  # so the source drawer has them
        return taken, facts

    def decide(self) -> StageReport:
        verified = _require(self.verify_out, "verify")
        _require(self.match_out, "match")
        inp = DecideInput(
            rubric=self.rubric,
            products=self._kept_products(),
            values=verified.values,
            community=verified.community,
        )
        self.decide_out, report = self._run(self.stages.decide, inp)
        if self.decide_out is not None:
            # Whatever made it (the stub's sample decision lists the example products), a dropped product is never
            # shown or held: it would appear with no product behind it (US-40).
            self.decide_out = without_products(self.decide_out, self.dropped)
        self.decide_is_sample = self.decide_out is not None and made_by_placeholder(self.stages.decide, report)
        self.decide_note = self._stage_note(self.stages.decide)
        return report

    def _stage_note(self, stage: object) -> str | None:
        """A stage's own note for this search, if it keeps one (`coverage_note(search_id)`). Never stops the search."""
        collect = getattr(stage, "coverage_note", None)
        if collect is None:
            return None
        try:
            note = collect(self.ctx.search_id)
        except Exception as exc:  # the type only, never its text (build rule 9)
            log.warning("a stage's note was not read: %s", type(exc).__name__)
            return None
        return note if isinstance(note, str) and note else None

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
        self.commerce_note = self._stage_note(self.stages.commerce)
        return report

    # Assembling the result.

    def _sample_products(self) -> list[Product]:
        """When decide fell back to its stub, it ranks and filters the example products, whatever match found. Those
        sample products join the result, so it stays valid and shows names, not ids, under the sample note."""
        if not self.decide_is_sample or self.decide_out is None:
            return []
        have = {p.id for p in self._kept_products()}
        named = {r.product_id for r in self.decide_out.ranked} | {f.product_id for f in self.decide_out.filtered}
        wanted = named - have - self.dropped  # filtered ones need their names too; a dropped one never comes back
        return [p for p in examples.match().products if p.id in wanted]

    def result(self, started_at: datetime, partial: bool) -> SearchResult:
        values = self.verify_out.values if self.verify_out else []
        community = {c.product_id: c for c in self.verify_out.community} if self.verify_out else {}
        products = [
            ProductSummary(product=p, values=[v for v in values if v.product_id == p.id], community=community.get(p.id))
            for p in [*self._kept_products(), *self._sample_products()]
        ]
        failures = self.research_out.failures if self.research_out else []
        found = [*(self.research_out.sources if self.research_out else []), *self.enrich_sources]
        older_archive = any(s.source_detail == "pullpush" for s in found)
        note = build_coverage_note(
            self.reports,
            failures,
            partial,
            older_archive=older_archive,
            extract_note=self.extract_note,
            research_note=self.research_note,
            decide_note=self.decide_note,
            enrich_note=self.enrich_note,
            commerce_note=self.commerce_note,
            llm_unavailable=self.llm_unavailable,
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
            sources=found,
            citations=self._citations(products, found),
            stage_reports=self.reports,
            coverage_note=add_sample_label(note) if self.used_placeholder else note,
            llm_unavailable=self.llm_unavailable,
            saved=True,  # what the stored row must say; the runner returns a saved=False copy if it is not committed
        )

    def _citations(self, products: Sequence[ProductSummary], sources: Sequence[Source]) -> list[Citation]:
        """Each claim a value cites, once, with its source and quote (US-21): its supporting and conflicting claims,
        which hold every alternative's too.

        All or none, as the contract asks: if one cited claim is not among the run's claims, or its source is not among
        the result's, there are none (as from a stub that cites example claims). Explanation sentences cite claims too,
        but a citation must belong to a value, so only those a value also cites are listed.
        """
        cited = list(
            dict.fromkeys(
                claim_id
                for summary in products
                for value in summary.values
                for claim_id in (*value.supporting_claim_ids, *value.conflicting_claim_ids)
            )
        )
        by_id = {claim.id: claim for claim in self.claims}
        known_sources = {source.id for source in sources}
        found = [by_id.get(claim_id) for claim_id in cited]
        if not all(claim is not None and claim.source_id in known_sources for claim in found):
            if cited:
                log.warning("search %s: citations left out, as a cited claim is not the run's", self.ctx.search_id)
            return []
        # What the claim said and when, too (US-29, 1.11.0): a saved result reopens with the same evidence.
        return [
            Citation(
                claim_id=c.id,
                source_id=c.source_id,
                quote=c.quote,
                value=c.value,
                unit=c.unit,
                score=c.score,
                polarity=c.polarity,
                claim_type=c.claim_type,
                retrieved_at=c.retrieved_at,
            )
            for c in found
            if c is not None
        ]


def _still_dropped(out: VerifyResult, dropped: Sequence[str]) -> VerifyResult:
    """Verify's result with the products dropped before as unsourced dropped again: their values and community
    left out, and their ids listed. The same object when it names none of them."""
    gone = set(dropped)
    if not gone or (gone <= set(out.dropped_unsourced) and not any(v.product_id in gone for v in out.values)):
        return out
    data = out.model_dump()
    data["values"] = [v for v in data["values"] if v["product_id"] not in gone]
    data["community"] = [c for c in data["community"] if c["product_id"] not in gone]
    data["dropped_unsourced"] = sorted(gone | set(out.dropped_unsourced))
    return VerifyResult.model_validate(data)


def _label_at(position: int) -> Literal["best_match", "runner_up"] | None:
    return "best_match" if position == 0 else "runner_up" if position == 1 else None


def fit_sources(
    research: Sequence[Source], added: Sequence[Source], claims: Sequence[Claim]
) -> tuple[list[Source], list[Source]]:
    """Research's sources and enrich's, within verify's MAX_VERIFY_SOURCES (US-41). A search snippet no claim cites
    makes room first: it was never read for claims, and every product is named by a source that was. If that is not
    enough room, only the enrich sources that fit go, in enrich's order (prices first)."""
    over = len(research) + len(added) - MAX_VERIFY_SOURCES
    if over <= 0:
        return list(research), list(added)
    cited = {claim.source_id for claim in claims}
    spare = [s.id for s in reversed(research) if s.via == SEARCH_SNIPPET and s.id not in cited][:over]
    kept = [s for s in research if s.id not in set(spare)]
    return kept, list(added)[: max(0, MAX_VERIFY_SOURCES - len(kept))]


def without_products(decision: DecideResult, dropped: Collection[str]) -> DecideResult:
    """The decision with these products in neither its ranked nor its filtered list (US-40): verify dropped them as
    unsourced, so they have no product to show. Ranks run 1, 2, 3 again, each tie list loses them, and the labels and
    gaps follow the new order. With nothing left shown but something held, the contract asks for a hint: it then says
    no single change is known. Unchanged, the same object, when it names none of them."""
    gone = set(dropped)
    ranked = [r for r in decision.ranked if r.product_id not in gone]
    filtered = [f for f in decision.filtered if f.product_id not in gone]
    if len(ranked) == len(decision.ranked) and len(filtered) == len(decision.filtered):
        return decision
    labelled = any(r.label is not None for r in decision.ranked)
    leader = ranked[0].score if ranked else None
    renumbered = [
        {
            **r.model_dump(),
            "rank": n + 1,
            "tied_with": [t for t in r.tied_with if t not in gone],
            "label": _label_at(n) if labelled else None,
            "gap_to_leader": round(max(0.0, leader - r.score), 2)
            if leader is not None and r.score is not None
            else 0.0,
        }
        for n, r in enumerate(ranked)
    ]
    hint = decision.relaxation_hint
    if not ranked and filtered and hint is None:
        hint = NO_SINGLE_CHANGE
    return DecideResult.model_validate(
        {
            **decision.model_dump(),
            "ranked": renumbered,
            "filtered": [f.model_dump() for f in filtered],
            "relaxation_hint": hint,
        }
    )


def _takes(method: Callable[..., object], parameter: str) -> bool:
    """Does this method take a parameter of this name? A stage written before it was added doesn't."""
    try:
        return parameter in inspect.signature(method).parameters
    except (TypeError, ValueError):  # a builtin or a mock with no signature to read
        return False


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
    decide_note: str | None = None,
    enrich_note: str | None = None,
    commerce_note: str | None = None,
    llm_unavailable: bool = False,
    research_note: str | None = None,
) -> str | None:
    """Plain-English summary of everything that did not go to plan. None when everything did.

    Every LLM provider failing comes first (US-31): it explains the rest. The stages' own notes come last: decide's
    (criteria left out, or nothing ranked), then research's (which readers read the hard pages, D-100), then extract's
    (sources not read), so a note too long to fit never costs the sentences above."""
    parts: list[str] = [LLM_UNAVAILABLE_NOTE] if llm_unavailable else []
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
        if f.detail.startswith(SKIPPED_DETAIL):  # sites that kept failing, skipped for 24 hours (US-31)
            parts.append(skipped_sentence(f.detail))
            continue
        reader = next((name for name in READER_NAMES if f.detail.startswith(f"{name}:")), None)
        if reader is not None and f.source_type is SourceType.WEB_SEARCH:  # Firecrawl as Serper's backup
            parts.append(f"{reader} search {FAILURE_WORDS[f.reason]}.")
        elif reader is not None:
            parts.append(f"{reader} {FAILURE_WORDS[f.reason]}, so some hard-to-read pages may be missing.")
        else:
            parts.append(f"{SOURCE_LABELS[f.source_type]} {FAILURE_WORDS[f.reason]}.")
    if older_archive:
        parts.append(OLDER_ARCHIVE_NOTE)
    for reason, stages in skipped.items():
        parts.append(f"Skipped {', '.join(stages)} because {reason}.")
    if partial:
        parts.append("These results are partial.")
    if decide_note:
        parts.append(decide_note)
    if enrich_note:  # how many prices came from store listings (US-41)
        parts.append(enrich_note)
    if commerce_note:
        parts.append(commerce_note)
    if research_note:  # which readers read the hard pages (D-100 item 1)
        parts.append(research_note)
    if extract_note:  # last, so a note too long to fit never costs the sentences above
        parts.append(extract_note)

    note = " ".join(parts)
    return _fit(note, MAX_NOTE_CHARS) if note else None


def every_llm_call_failed(counts: object, timed_out: int = 0) -> bool:
    """True when the search sent sources to the LLM and none came back with an answer: no provider could answer,
    or the source's read ran out of time while they tried (`timed_out`).

    `counts` is the extract stage's counts for the search (`counts_for`), or None. A search that sent nothing to the
    LLM did not need it, so it never says the LLM was unavailable."""
    if not isinstance(counts, dict):
        return False
    sent, unreadable = counts.get("sent", 0), counts.get("unreadable", 0)
    if not (isinstance(sent, int) and isinstance(unreadable, int)) or sent <= 0:
        return False
    return unreadable + timed_out >= sent


def _hook(name: str, hook: Callable[..., object] | None, *args: object, **kwargs: object) -> object:
    """A stage's helper method, called outside run_stage: if it raises, the search carries on without it (US-31,
    build rule 9). Only the error's type is logged, never its text."""
    if hook is None:
        return None
    try:
        return hook(*args, **kwargs)
    except Exception as exc:
        log.warning("a stage's %s failed (%s); the search carries on", name, type(exc).__name__)
        return None


def add_sample_label(note: str | None) -> str:
    """Add the sample sentence to the note. When both do not fit the note gives way: the label must always be seen."""
    if not note:
        return SAMPLE_NOTE
    return f"{_fit(note, MAX_NOTE_CHARS - len(SAMPLE_NOTE) - 1)} {SAMPLE_NOTE}"


def _fit(note: str, limit: int) -> str:
    return note if len(note) <= limit else note[: limit - 3].rstrip() + "..."
