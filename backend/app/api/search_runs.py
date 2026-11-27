"""Search runs with live progress (US-12, P-07): start a search, follow it stage by stage, cancel it, fetch it by ID.

    POST   /api/search/runs                     202 {"search_id"}; 503 with Retry-After when every worker is busy
    GET    /api/search/runs/{search_id}         the run's state (SearchRun), with its result once it has finished
    GET    /api/search/runs/{search_id}/events  Server-Sent Events: each ProgressEvent, then one final "run" event
    DELETE /api/search/runs/{search_id}         cancel; 202 while it stops, 200 (and no change) when it is too late
    GET    /api/search/runs/{search_id}/evidence?product_id=&criterion_key=
                                                the claims behind one value, with their sources (US-21)

Runs execute on a small, bounded set of worker threads, so a request thread is never held for a whole search. They
are daemon threads, so a forced stop of the server never waits for a whole search. A progress stream holds no thread
either: it is an async generator that looks for new events every POLL_SECONDS. Each run's state lives in memory
(build rule 10) and is dropped once its keep-time has passed: at the next request or stream read, and within ten
minutes even when nobody asks (drop_expired, called by the app's housekeeping in app/main.py). The result itself is
saved at the end by the runner, as POST /api/search does: after the search's 150 seconds, with its own limit
(DB_SAVE_TIMEOUT_SECONDS, P-38). While it saves, the stream says so once, and the run's result and its `saved` flag
become final only once the save has answered. The stream sends its own keep-alive comment, so tests can make it
short.

A finished run also keeps the claims its result's values cite (never the rest), so the evidence route can show each
value's sources (app/api/evidence.py). They are never saved, and go with the run.

A signed-in shopper's run is theirs (US-30): its state, its progress stream, its evidence and cancelling it need that
user's token. Anyone else, with another token or none, gets the same 404 as for an ID that never existed, never 403, and
such a run leaves no "expired" trace when it goes. Its result is saved with the user's id, then kept without it, so no
state or event ever shows a user_id. A guest's run works exactly as before.

A run confirmed for a session whose research and extraction began during the interview takes them over (US-34,
D-94, app/early_search.py): the same owner and brief only, joined if still going, never repeated, with the job's
worker. The early jobs share this pool, and go with this registry's housekeeping and shutdown.

A finished run goes at its keep-time even while a progress stream reads it, and on an idle server the app's housekeeping
drops it at the next tick, so the Reddit text in its result and claims is gone before D-53's 24 hours (serve_limit(),
app/research/reddit/cache.py, leaves room for both, and for the search's save before them). A stream still open then
sends the run's final event, with its result as it is saved (no Reddit text, app/research/retention.py), and closes.
The keep-time runs out on the registry's own clock or on the wall clock, whichever is first: a machine that sleeps can
stop the first, but Reddit data keeps ageing on the second.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import logging
import threading
from collections import deque
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import monotonic
from typing import Annotated, Any, NamedTuple

import anyio
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.sse import EventSourceResponse, ServerSentEvent

from app import db
from app.auth import OptionalUser
from app.cache import default_result_cache
from app.contracts import (
    Claim,
    EvidenceResponse,
    ProgressEvent,
    SearchRequest,
    SearchResult,
    SearchRun,
    SearchRunStarted,
    SearchRunStatus,
)
from app.contracts.pipeline import MAX_RUN_EVENTS
from app.early_search import EarlyJob, EarlySearches, early_on
from app.pipeline import (
    CancelFlag,
    DatabaseResultStore,
    PipelineRunner,
    SearchCancelled,
    default_search_stages,
    new_search_id,
)
from app.research.retention import without_reddit_text
from app.search_run_limits import DEFAULT_KEEP_MINUTES, environment, keep_minutes, number

from .account import owned_by
from .evidence import (
    NO_PRODUCT,
    NO_VALUE,
    SOURCES_GONE,
    ClaimRecorder,
    EvidenceUnavailable,
    cited_claims,
    evidence_for,
    saved_evidence_for,
)

log = logging.getLogger(__name__)

RETRY_AFTER_SECONDS = 30
BUSY = "ShopSense is busy with other searches right now. Please try again in a moment."
NOT_FOUND = "We couldn't find that search. Finished searches are only kept for a while."
# The evidence route tells a search that has gone from one that never was, and says why it can't answer.
NO_SUCH_SEARCH = "We couldn't find that search."
RUN_EXPIRED = "This search has expired. Run it again to see its sources."
NOT_FINISHED = "This search hasn't finished yet."
NO_RESULTS = "This search has no results to show sources for."

DEFAULT_WORKERS = 4
MAX_WORKERS = 16
DEFAULT_PING_SECONDS = 15.0
# FastAPI sends its own keep-alive after 15 s of silence, so a longer setting would have no effect.
MIN_PING_SECONDS, MAX_PING_SECONDS = 0.01, 15.0
DEFAULT_MAX_KEPT = 500  # finished runs kept in memory at once; the oldest go first, unless a stream is reading one
MIN_MAX_KEPT, MAX_MAX_KEPT = 1, 10_000
POLL_SECONDS = 0.1  # how often an open stream looks for new events
STREAM_START_GRACE_S = 10.0  # a run that passed a stream's start check is kept this long, until the stream reads it
MAX_FORGOTTEN_IDS = 1000  # dropped runs remembered by ID, so the evidence route can say "expired"; the oldest go first

_ENDED = {SearchRunStatus.DONE, SearchRunStatus.PARTIAL, SearchRunStatus.FAILED, SearchRunStatus.CANCELLED}


# --- settings -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class RunSettings:
    workers: int = DEFAULT_WORKERS  # searches that may run at once; one more gets 503
    keep_s: float = DEFAULT_KEEP_MINUTES * 60  # how long a finished run's state stays in memory
    ping_s: float = DEFAULT_PING_SECONDS  # the longest a stream stays silent; then it sends a keep-alive
    max_kept: int = DEFAULT_MAX_KEPT  # finished runs kept at once, whatever their age
    early: bool = False  # research and extraction begun during the interview (US-34): on in the app (EARLY_RESEARCH)


def load_run_settings(env: Mapping[str, str] | None = None) -> RunSettings:
    """SEARCH_RUN_WORKERS, SEARCH_RUN_KEEP_MINUTES, SEARCH_RUN_PING_SECONDS and SEARCH_RUN_MAX_KEPT, with defaults.

    A blank value means the default. A value that is not a positive number gets the default and a warning; a value
    outside the allowed range is brought inside it, also with a warning. With no `env`, the root .env is read (without
    changing os.environ), and real environment variables win.
    """
    if env is None:
        env = environment()
    workers = number(env, "SEARCH_RUN_WORKERS", DEFAULT_WORKERS, 1, MAX_WORKERS, whole=True)
    keep = keep_minutes(env)
    ping = number(env, "SEARCH_RUN_PING_SECONDS", DEFAULT_PING_SECONDS, MIN_PING_SECONDS, MAX_PING_SECONDS)
    kept = number(env, "SEARCH_RUN_MAX_KEPT", DEFAULT_MAX_KEPT, MIN_MAX_KEPT, MAX_MAX_KEPT, whole=True)
    return RunSettings(workers=int(workers), keep_s=keep * 60, ping_s=ping, max_kept=int(kept), early=early_on(env))


# --- the registry -------------------------------------------------------------------------------


@dataclass
class _Run:
    """One run's state. Every field is read and written under SearchRuns' lock."""

    search_id: str
    status: SearchRunStatus = SearchRunStatus.QUEUED
    started_at: datetime | None = None
    finished_at: datetime | None = None
    ended_at: float | None = None  # the registry's clock when it ended, for the keep-time
    events: list[ProgressEvent] = field(default_factory=list)
    result: SearchResult | None = None
    cancel: CancelFlag = field(default_factory=CancelFlag)
    streams: int = 0  # progress streams reading this run now: the count limit spares it, its keep-time doesn't
    reserved_until: float = 0.0  # set by a stream's start check, so the run is still there for its first read
    # The claims the result's values cite, by ID, for the evidence route (US-21). Never in the run's state, never saved.
    claims: dict[str, Claim] = field(default_factory=dict)
    # Set when the run is dropped while streams still read it: its final state, with no Reddit text in it (D-53).
    parting: SearchRun | None = None
    # The signed-in shopper whose run this is (US-30): only their token reads it. None for a guest's run.
    owner: str | None = None


class _Batch(NamedTuple):
    events: list[ProgressEvent]
    final: SearchRun | None  # set once the run has ended: the stream's last event


class UnknownRun(LookupError):
    """No run has this ID: it never existed, or it ended longer ago than the keep-time."""


class ExpiredRun(UnknownRun):
    """The run with this ID was dropped: it ended longer ago than the keep-time, or newer runs pushed it out."""


class RecentIds:
    """The last `limit` IDs added, oldest out first: a bounded set."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self._order: deque[str] = deque()
        self._ids: set[str] = set()

    def add(self, item: str) -> None:
        if item in self._ids:
            return
        self._order.append(item)
        self._ids.add(item)
        while len(self._order) > self.limit:
            self._ids.discard(self._order.popleft())

    def __contains__(self, item: object) -> bool:
        return item in self._ids


class SearchRuns:
    """Every search run this server knows about, the worker pool that runs them, and their progress streams."""

    def __init__(
        self,
        runner_factory: Callable[[], PipelineRunner],
        settings: RunSettings | None = None,
        clock: Callable[[], float] = monotonic,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.settings = settings or RunSettings()
        self._runner_factory = runner_factory
        self._clock = clock
        self._wall = wall_clock  # the time Reddit data ages by, and the run's own times
        self._free = threading.BoundedSemaphore(self.settings.workers)  # one per worker: no hidden queue
        self._lock = threading.RLock()
        self._runs: dict[str, _Run] = {}
        self._forgotten = RecentIds(MAX_FORGOTTEN_IDS)
        self._open_streams = 0
        self.closed = False
        # Each session's research and extraction begun during the interview, on these same workers (US-34, D-94).
        self.early = EarlySearches(runner_factory, self._free, enabled=self.settings.early)

    @property
    def open_streams(self) -> int:
        """How many progress streams are open right now."""
        with self._lock:
            return self._open_streams

    # Starting, reading and cancelling.

    def start(self, request: SearchRequest) -> str | None:
        """Start a run and return its ID at once, or None when every worker is busy.

        A job begun for this session during the interview, by the same owner on the same brief, is taken over, with its
        worker if it is still going (US-34): its research and extraction are never repeated. Anything else is dropped,
        and the search runs as before, on the dropped job's worker if it still held one. A job taken over when no
        worker is free is kept for the shopper's next try."""
        claimed, usable = self.early.claim(request.session_id, request.user_id, request.rubric)
        job = claimed if usable else None  # a job not taken over is already stopped
        handed = claimed is not None and claimed.hand_slot()
        if not handed and not self._free.acquire(blocking=False):
            if job is not None:
                self.early.give_back(job)
            return None
        run = _Run(search_id=new_search_id(), owner=request.user_id)
        with self._lock:  # close() takes this lock too, so no run starts once the registry is closed
            if self.closed:
                self._free.release()
                _stop(job)
                return None
            self._forget_old()
            self._runs[run.search_id] = run
            # A daemon thread: a forced stop of the server drops the search instead of waiting up to 150 s for it.
            # The worker waits for this lock before it touches the run.
            worker = threading.Thread(target=self._work, args=(run, request, job), name="search-run", daemon=True)
            worker.start()
        return run.search_id

    def get(self, search_id: str, viewer: str | None = None) -> SearchRun:
        """The run's state. `viewer` is the caller's user id (None for a guest): an owned run is only its owner's."""
        with self._lock:
            return self._state(self._find(search_id, viewer))

    def cancel(self, search_id: str, viewer: str | None = None) -> tuple[SearchRun, bool]:
        """Ask a run to stop before its next stage. Returns its state and whether the request took effect.

        It does not when the run has ended, or has started saving its result: then the run ends as it would have.
        """
        with self._lock:
            run = self._find(search_id, viewer)
            stopping = run.status not in _ENDED and run.cancel.request()
            return self._state(run), stopping

    def evidence(
        self, search_id: str, product_id: str, criterion_key: str, viewer: str | None = None
    ) -> EvidenceResponse:
        """The claims behind one value of a finished run, with their sources (US-21).

        Raises UnknownRun (ExpiredRun for one that was dropped), or EvidenceUnavailable: 409 while the run is still
        going, 404 when it has no result (it was cancelled or failed) or the value can't be shown.
        """
        with self._lock:
            run = self._find(search_id, viewer)
            ended, result, claims = run.status in _ENDED, run.result, run.claims
        if not ended:
            raise EvidenceUnavailable(409, NOT_FINISHED)
        if result is None:
            raise EvidenceUnavailable(404, NO_RESULTS)
        if result.cached:  # a repeat answered from the cache (US-34) read no source: its kept citations answer
            return saved_evidence_for(result, product_id, criterion_key)
        return evidence_for(result, claims, product_id, criterion_key)  # `claims` is replaced, never changed

    def drop_expired(self) -> None:
        """Drop every finished run past its keep-time now, with no request (D-53).

        The app's housekeeping (app/main.py) calls this at start-up and every SWEEP_EVERY_S, so a run goes within that
        of its keep-time even on an idle server. The limit on kept runs is applied too, as on any other look.
        """
        with self._lock:
            self._forget_old()
        self.early.drop_expired()

    def resume_from(self, search_id: str, last_event_id: str | None, viewer: str | None = None) -> int | None:
        """The event ID a stream starts after, or None when the run has ended and the client has seen all of it.

        A Last-Event-ID that is not a whole number between 0 and the newest event's ID is ignored, and the stream
        starts from the first event (clients drop what they already have by ID).
        """
        with self._lock:
            run = self._find(search_id, viewer)  # the stream reads only a run this check let through
            ended = run.status in _ENDED
            newest = len(run.events) + (1 if ended else 0)  # the final run event takes the next ID
            raw = (last_event_id or "").strip()
            after = int(raw) if raw.isascii() and raw.isdigit() and len(raw) <= 9 else 0
            if after > newest:
                after = 0
            if ended and after == newest:
                return None
            # Kept until the stream reads it, but never past the run's own keep-time: a client that keeps checking
            # (reconnecting without the last ID) cannot hold a finished run in memory for ever.
            until = self._clock() + STREAM_START_GRACE_S
            if run.ended_at is not None:
                until = min(until, run.ended_at + self.settings.keep_s)
            run.reserved_until = until
            return after

    async def stream(self, search_id: str, after: int) -> AsyncIterator[ServerSentEvent]:
        """Every progress event after `after`, live, then one final "run" event. Silent spells get a keep-alive.

        It holds no thread while it waits: it looks for new events every POLL_SECONDS (or sooner, for a short ping).
        """
        with self._lock:
            self._open_streams += 1
            following = self._runs.get(search_id)
            if following is not None:
                following.streams += 1
        try:
            sent = after
            quiet_since = monotonic()
            poll_s = min(POLL_SECONDS, self.settings.ping_s)
            while True:
                batch = self._take(search_id, sent, following)
                if batch is None:
                    return  # the run was dropped before this stream read it, or the server is shutting down
                if batch.final is not None:
                    yield ServerSentEvent(id=str(len(batch.final.progress) + 1), event="run", data=batch.final)
                    return
                for event in batch.events:
                    sent = event.event_id
                    yield ServerSentEvent(id=str(event.event_id), event="progress", data=event)
                now = monotonic()
                if batch.events:
                    quiet_since = now
                    continue  # look again at once: the run may have ended, or gone, while these were sent
                if now - quiet_since >= self.settings.ping_s:
                    yield ServerSentEvent(comment="ping")
                    quiet_since = now
                await anyio.sleep(poll_s)
        finally:
            with self._lock:
                self._open_streams -= 1
                if following is not None:
                    following.streams -= 1

    def close(self) -> None:
        """At shutdown: refuse new runs, ask every run to stop before its next stage, and end the open streams."""
        with self._lock:
            self.closed = True
            for run in self._runs.values():
                run.cancel.request()
        self.early.close()

    # Inside.

    def _work(self, run: _Run, request: SearchRequest, early: EarlyJob | None = None) -> None:
        outcome: _Outcome = (SearchRunStatus.FAILED, None, {})
        try:
            outcome = self._execute(run, request, early)
        finally:
            _stop(early)  # a joined job still reading stops with its search, whatever ended it
            self._free.release()  # first, so a client that sees the run end can start the next one
            self._end(run, *outcome)

    def _execute(self, run: _Run, request: SearchRequest, early: EarlyJob | None = None) -> _Outcome:
        with self._lock:
            if run.cancel.requested:
                return SearchRunStatus.CANCELLED, None, {}
            run.status = SearchRunStatus.RUNNING
            run.started_at = self._wall()
        try:
            runner, recorder = _recording(self._runner_factory())
            handover = {"early": early} if early is not None else {}  # a runner that never takes one is still run
            result = runner.run(
                request, search_id=run.search_id, progress=lambda e: self._add(run, e), cancel=run.cancel, **handover
            )
        except SearchCancelled:
            return SearchRunStatus.CANCELLED, None, {}
        except Exception as exc:  # the runner never raises by design; if it does, the run still ends (rule 9)
            log.error("search run %s failed: %s", run.search_id, type(exc).__name__)
            # A run the shopper had already cancelled (and was told so) ends cancelled: it never reached its save.
            return (SearchRunStatus.CANCELLED if run.cancel.requested else SearchRunStatus.FAILED), None, {}
        claims = cited_claims(result, recorder.claims()) if recorder is not None else {}
        return (SearchRunStatus.PARTIAL if result.partial else SearchRunStatus.DONE), result, claims

    def _add(self, run: _Run, event: ProgressEvent) -> None:
        with self._lock:
            if len(run.events) < MAX_RUN_EVENTS:  # a run's state may hold no more (P1); the final event still comes
                run.events.append(event)

    def _end(self, run: _Run, status: SearchRunStatus, result: SearchResult | None, claims: dict[str, Claim]) -> None:
        shown = _without_owner(result)  # saved with the owner's id already; never shown with it (US-30)
        if result is not None and shown is None:  # the id can't be left out: show no result rather than the id
            status, claims = SearchRunStatus.FAILED, {}
        with self._lock:
            now = self._wall()
            run.status = status
            run.result = shown
            run.claims = claims
            run.finished_at = max(now, run.started_at) if run.started_at else now
            run.ended_at = self._clock()
            self._forget_old()  # so the limit on kept runs holds as runs end, not only at the next request

    def _take(self, search_id: str, sent: int, following: _Run | None) -> _Batch | None:
        """What a stream sends next, without waiting: the events after `sent`, then the final state once it has ended.

        The final state comes only once every event has been sent, so a stream waiting on a slow client holds no result.
        A run past its keep-time is dropped here as well (D-53), so a stream back from a long wait never sends its
        Reddit text: it gets the parting state its run was given (`following` is the run it started reading).
        """
        with self._lock:
            run = self._runs.get(search_id)
            if run is not None and self._past_keep_time(run, self._clock(), self._wall()):
                self._drop(run)
                run = None
            if run is None:
                parting = following.parting if following is not None else None
                if parting is None:
                    return None
                events = parting.progress[sent:]
                return _Batch(events=events, final=None if events else parting)
            if self.closed and run.status not in _ENDED:
                return None
            events = run.events[sent:]
            return _Batch(events=events, final=self._state(run) if run.status in _ENDED and not events else None)

    def _find(self, search_id: str, viewer: str | None = None) -> _Run:
        """The run, if `viewer` may read it. A signed-in shopper's run is unknown to everyone else (US-30)."""
        self._forget_old()
        run = self._runs.get(search_id)
        if run is None:
            raise ExpiredRun(search_id) if search_id in self._forgotten else UnknownRun(search_id)
        if run.owner is not None and run.owner != viewer:
            raise UnknownRun(search_id)  # the same answer as an ID that never existed: never confirmed
        return run

    def _forget_old(self) -> None:
        """Drop finished runs past the keep-time, and the oldest beyond the limit.

        The keep-time holds for every finished run, even one a progress stream is reading (D-53). The limit on kept
        runs never drops a run that a stream is reading, or that has just passed a stream's start check (so its stream
        gets its events, not an empty answer). A run that is still going is never dropped.
        """
        now, wall = self._clock(), self._wall()
        ended = sorted((r for r in self._runs.values() if r.ended_at is not None), key=lambda r: r.ended_at or 0.0)
        may_go = [r for r in ended if r.streams == 0 and r.reserved_until <= now]
        too_many = {r.search_id for r in may_go[: max(0, len(may_go) - self.settings.max_kept)]}
        for run in ended:
            if run.search_id in too_many or self._past_keep_time(run, now, wall):
                self._drop(run)

    def _past_keep_time(self, run: _Run, now: float, wall: datetime) -> bool:
        """Past its keep-time on the registry's clock, or on the wall clock that Reddit data ages by (D-53)."""
        if run.ended_at is None:
            return False
        if run.ended_at <= now - self.settings.keep_s:
            return True
        return run.finished_at is not None and (wall - run.finished_at).total_seconds() >= self.settings.keep_s

    def _drop(self, run: _Run) -> None:
        """Forget a finished run. Its result and claims go now, even from a stream that still holds the run (D-53)."""
        del self._runs[run.search_id]
        if run.owner is None:  # an owned run leaves no trace: its ID is never confirmed to anyone (US-30)
            self._forgotten.add(run.search_id)
        if run.streams:
            run.parting = _as_saved(self._state(run))
        # Replaced, not cleared: a request already reading the old ones finishes with them.
        run.result = None
        run.claims = {}

    @staticmethod
    def _state(run: _Run) -> SearchRun:
        return SearchRun(
            search_id=run.search_id,
            status=run.status,
            started_at=run.started_at,
            finished_at=run.finished_at,
            progress=list(run.events),
            result=run.result,
        )


_Outcome = tuple[SearchRunStatus, SearchResult | None, dict[str, Claim]]


def _stop(job: EarlyJob | None) -> None:
    if job is not None:
        job.stop.request()


def _without_owner(result: SearchResult | None) -> SearchResult | None:
    """The result as a run keeps and shows it: request.user_id left out (US-30). It was saved with it already."""
    if result is None or result.request.user_id is None:
        return result
    try:
        request = {**result.request.model_dump(), "user_id": None}
        return SearchResult.model_validate({**result.model_dump(), "request": request})
    except Exception as exc:  # never show the id: the run then ends as failed, with no result (build rule 9)
        log.warning("search run result could not be kept without its owner: %s", type(exc).__name__)
        return None


def _as_saved(state: SearchRun) -> SearchRun | None:
    """A run's final state as it may outlive the run: its result as it is saved, with no Reddit text (D-53).

    None when that can't be made (build rule 9): the stream then closes without its final event.
    """
    if state.result is None:
        return state
    try:
        saved = SearchResult.model_validate_json(json.dumps(without_reddit_text(state.result.model_dump(mode="json"))))
        return SearchRun.model_validate({**state.model_dump(), "result": saved})
    except Exception as exc:  # never let a dropped run break the request that dropped it
        log.warning("search run %s: no final state without Reddit text: %s", state.search_id, type(exc).__name__)
        return None


def _recording(runner: PipelineRunner) -> tuple[PipelineRunner, ClaimRecorder | None]:
    """A shallow copy of the runner whose extract stage keeps its claims, and that recorder.

    The factory's runner and its stages are never changed, so a factory may hand out one runner to every run. A runner
    that can't be copied runs as it is: the search still finishes, and its values just have no sources to show.
    """
    try:
        recorder = ClaimRecorder(runner.stages.extract)
        recording = copy.copy(runner)
        recording.stages = dataclasses.replace(runner.stages, extract=recorder)
    except Exception as exc:  # build rule 9: keeping claims must never stop a search
        log.warning("claims not kept for this run: %s", type(exc).__name__)
        return runner, None
    return recording, recorder


def default_runner() -> PipelineRunner:
    """The runner POST /api/search uses: the default stages, saving to the database when one is set (FX-4), and the
    result cache for an exact repeat (US-34, D-80; RESULT_CACHE=off runs every search)."""
    store = DatabaseResultStore(db.default_request_store())
    return PipelineRunner(default_search_stages(), store, cache=default_result_cache())


# --- the routes ---------------------------------------------------------------------------------

router = APIRouter(prefix="/api/search/runs", tags=["search runs"])


def get_search_runs(request: Request) -> SearchRuns:
    runs: SearchRuns = request.app.state.search_runs
    return runs


Runs = Annotated[SearchRuns, Depends(get_search_runs)]
_UNKNOWN: dict[int | str, dict[str, Any]] = {404: {"description": NOT_FOUND}}


@router.post("", status_code=status.HTTP_202_ACCEPTED, responses={503: {"description": BUSY}})
def start_run(request: SearchRequest, runs: Runs, response: Response, user: OptionalUser) -> SearchRunStarted:
    search_id = runs.start(owned_by(request, user))  # a signed-in user's search is saved with their id (US-30)
    if search_id is None:
        raise HTTPException(503, BUSY, headers={"Retry-After": str(RETRY_AFTER_SECONDS)})
    response.headers["Location"] = f"{router.prefix}/{search_id}"
    return SearchRunStarted(search_id=search_id)


def _viewer(user: OptionalUser) -> str | None:
    """The caller's user id, from a checked token. None for a guest, a bad token or no token (US-30)."""
    return str(user.id) if user is not None else None


Viewer = Annotated[str | None, Depends(_viewer)]


@router.get("/{search_id}", responses=_UNKNOWN)
def run_state(search_id: str, runs: Runs, viewer: Viewer) -> SearchRun:
    try:
        return runs.get(search_id, viewer)
    except UnknownRun:
        raise HTTPException(404, NOT_FOUND) from None


def _stream_start(
    search_id: str, runs: Runs, viewer: Viewer, last_event_id: Annotated[str | None, Header()] = None
) -> int:
    """Checked before the stream opens: an unknown ID, or another user's run, is 404; a client that has seen the end
    gets 204."""
    try:
        after = runs.resume_from(search_id, last_event_id, viewer)
    except UnknownRun:
        raise HTTPException(404, NOT_FOUND) from None
    if after is None:
        raise HTTPException(204)  # tells a browser's EventSource to stop reconnecting
    return after


@router.get(
    "/{search_id}/events",
    response_class=EventSourceResponse,
    responses={**_UNKNOWN, 204: {"description": "The run has ended and the client has every event"}},
)
async def run_events(
    search_id: str, runs: Runs, after: Annotated[int, Depends(_stream_start)]
) -> AsyncIterator[ServerSentEvent]:
    # An async generator rather than P-07's plain `def`: a plain one would hold one of the server's shared worker
    # threads for as long as the run lasts, so a few dozen open streams could stall every other request.
    async for message in runs.stream(search_id, after):
        yield message


_EVIDENCE_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"description": " / ".join((NO_SUCH_SEARCH, RUN_EXPIRED, NO_RESULTS, NO_PRODUCT, NO_VALUE, SOURCES_GONE))},
    409: {"description": NOT_FINISHED},
}


@router.get("/{search_id}/evidence", responses=_EVIDENCE_ERRORS)
def run_evidence(
    search_id: str,
    runs: Runs,
    viewer: Viewer,
    product_id: Annotated[str, Query(min_length=1, description="One of the result's products")],
    criterion_key: Annotated[
        str, Query(min_length=1, description="A criterion or hard-rule attribute with a value for it")
    ],
) -> EvidenceResponse:
    """The claims behind one value of a finished run, with their sources (US-21). A missing parameter is a plain 422."""
    try:
        return runs.evidence(search_id, product_id, criterion_key, viewer)
    except ExpiredRun:
        raise HTTPException(404, RUN_EXPIRED) from None
    except UnknownRun:
        raise HTTPException(404, NO_SUCH_SEARCH) from None
    except EvidenceUnavailable as unavailable:
        raise HTTPException(unavailable.status_code, unavailable.detail) from None


@router.delete(
    "/{search_id}",
    responses={**_UNKNOWN, 202: {"description": "Asked to stop before its next stage"}},
)
def cancel_run(search_id: str, runs: Runs, response: Response, viewer: Viewer) -> SearchRun:
    try:
        state, stopping = runs.cancel(search_id, viewer)
    except UnknownRun:
        raise HTTPException(404, NOT_FOUND) from None
    response.status_code = status.HTTP_202_ACCEPTED if stopping else status.HTTP_200_OK
    return state
