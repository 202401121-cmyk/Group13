"""Search runs with live progress (US-12, P-07): start a search, follow it stage by stage, cancel it, fetch it by ID.

    POST   /api/search/runs                     202 {"search_id"}; 503 with Retry-After when every worker is busy
    GET    /api/search/runs/{search_id}         the run's state (SearchRun), with its result once it has finished
    GET    /api/search/runs/{search_id}/events  Server-Sent Events: each ProgressEvent, then one final "run" event
    DELETE /api/search/runs/{search_id}         cancel; 202 while it stops, 200 (and no change) when it is too late

Runs execute on a small, bounded set of worker threads, so a request thread is never held for a whole search. They
are daemon threads, so a forced stop of the server never waits for a whole search. A progress stream holds no thread
either: it is an async generator that looks for new events every POLL_SECONDS. Each run's state lives in memory
(build rule 10) and is dropped a while after it ends; the result itself is saved at the end by the runner, as
POST /api/search does. The stream sends its own keep-alive comment, so tests can make it short.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import monotonic
from typing import Annotated, Any, NamedTuple

import anyio
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from fastapi.sse import EventSourceResponse, ServerSentEvent

from app import db
from app.contracts import ProgressEvent, SearchRequest, SearchResult, SearchRun, SearchRunStarted, SearchRunStatus
from app.contracts.pipeline import MAX_RUN_EVENTS
from app.pipeline import (
    CancelFlag,
    DatabaseResultStore,
    PipelineRunner,
    SearchCancelled,
    default_search_stages,
    new_search_id,
)
from app.search_run_limits import DEFAULT_KEEP_MINUTES, environment, keep_minutes, number

log = logging.getLogger(__name__)

RETRY_AFTER_SECONDS = 30
BUSY = "ShopSense is busy with other searches right now. Please try again in a moment."
NOT_FOUND = "We couldn't find that search. Finished searches are only kept for a while."

DEFAULT_WORKERS = 4
MAX_WORKERS = 16
DEFAULT_PING_SECONDS = 15.0
# FastAPI sends its own keep-alive after 15 s of silence, so a longer setting would have no effect.
MIN_PING_SECONDS, MAX_PING_SECONDS = 0.01, 15.0
DEFAULT_MAX_KEPT = 500  # finished runs kept in memory at once; the oldest go first, unless a stream is reading one
MIN_MAX_KEPT, MAX_MAX_KEPT = 1, 10_000
POLL_SECONDS = 0.1  # how often an open stream looks for new events
STREAM_START_GRACE_S = 10.0  # a run that passed a stream's start check is kept this long, until the stream reads it

_ENDED = {SearchRunStatus.DONE, SearchRunStatus.PARTIAL, SearchRunStatus.FAILED, SearchRunStatus.CANCELLED}


# --- settings -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class RunSettings:
    workers: int = DEFAULT_WORKERS  # searches that may run at once; one more gets 503
    keep_s: float = DEFAULT_KEEP_MINUTES * 60  # how long a finished run's state stays in memory
    ping_s: float = DEFAULT_PING_SECONDS  # the longest a stream stays silent; then it sends a keep-alive
    max_kept: int = DEFAULT_MAX_KEPT  # finished runs kept at once, whatever their age


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
    return RunSettings(workers=int(workers), keep_s=keep * 60, ping_s=ping, max_kept=int(kept))


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
    streams: int = 0  # progress streams reading this run now; a run being read is never dropped
    reserved_until: float = 0.0  # set by a stream's start check, so the run is still there for its first read


class _Batch(NamedTuple):
    events: list[ProgressEvent]
    final: SearchRun | None  # set once the run has ended: the stream's last event


class UnknownRun(LookupError):
    """No run has this ID: it never existed, or it ended longer ago than the keep-time."""


class SearchRuns:
    """Every search run this server knows about, the worker pool that runs them, and their progress streams."""

    def __init__(
        self,
        runner_factory: Callable[[], PipelineRunner],
        settings: RunSettings | None = None,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self.settings = settings or RunSettings()
        self._runner_factory = runner_factory
        self._clock = clock
        self._free = threading.BoundedSemaphore(self.settings.workers)  # one per worker: no hidden queue
        self._lock = threading.RLock()
        self._runs: dict[str, _Run] = {}
        self._open_streams = 0
        self.closed = False

    @property
    def open_streams(self) -> int:
        """How many progress streams are open right now."""
        with self._lock:
            return self._open_streams

    # Starting, reading and cancelling.

    def start(self, request: SearchRequest) -> str | None:
        """Start a run and return its ID at once, or None when every worker is busy."""
        if not self._free.acquire(blocking=False):
            return None
        run = _Run(search_id=new_search_id())
        with self._lock:  # close() takes this lock too, so no run starts once the registry is closed
            if self.closed:
                self._free.release()
                return None
            self._forget_old()
            self._runs[run.search_id] = run
            # A daemon thread: a forced stop of the server drops the search instead of waiting up to 150 s for it.
            # The worker waits for this lock before it touches the run.
            worker = threading.Thread(target=self._work, args=(run, request), name="search-run", daemon=True)
            worker.start()
        return run.search_id

    def get(self, search_id: str) -> SearchRun:
        with self._lock:
            return self._state(self._find(search_id))

    def cancel(self, search_id: str) -> tuple[SearchRun, bool]:
        """Ask a run to stop before its next stage. Returns its state and whether the request took effect.

        It does not when the run has ended, or has started saving its result: then the run ends as it would have.
        """
        with self._lock:
            run = self._find(search_id)
            stopping = run.status not in _ENDED and run.cancel.request()
            return self._state(run), stopping

    def resume_from(self, search_id: str, last_event_id: str | None) -> int | None:
        """The event ID a stream starts after, or None when the run has ended and the client has seen all of it.

        A Last-Event-ID that is not a whole number between 0 and the newest event's ID is ignored, and the stream
        starts from the first event (clients drop what they already have by ID).
        """
        with self._lock:
            run = self._find(search_id)
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
                batch = self._take(search_id, sent)
                if batch is None:
                    return  # the run was dropped, or the server is shutting down
                for event in batch.events:
                    sent = event.event_id
                    yield ServerSentEvent(id=str(event.event_id), event="progress", data=event)
                if batch.final is not None:
                    yield ServerSentEvent(id=str(len(batch.final.progress) + 1), event="run", data=batch.final)
                    return
                now = monotonic()
                if batch.events:
                    quiet_since = now
                elif now - quiet_since >= self.settings.ping_s:
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

    # Inside.

    def _work(self, run: _Run, request: SearchRequest) -> None:
        outcome: tuple[SearchRunStatus, SearchResult | None] = (SearchRunStatus.FAILED, None)
        try:
            outcome = self._execute(run, request)
        finally:
            self._free.release()  # first, so a client that sees the run end can start the next one
            self._end(run, *outcome)

    def _execute(self, run: _Run, request: SearchRequest) -> tuple[SearchRunStatus, SearchResult | None]:
        with self._lock:
            if run.cancel.requested:
                return SearchRunStatus.CANCELLED, None
            run.status = SearchRunStatus.RUNNING
            run.started_at = datetime.now(UTC)
        try:
            runner = self._runner_factory()
            result = runner.run(
                request, search_id=run.search_id, progress=lambda e: self._add(run, e), cancel=run.cancel
            )
        except SearchCancelled:
            return SearchRunStatus.CANCELLED, None
        except Exception as exc:  # the runner never raises by design; if it does, the run still ends (rule 9)
            log.error("search run %s failed: %s", run.search_id, type(exc).__name__)
            # A run the shopper had already cancelled (and was told so) ends cancelled: it never reached its save.
            return (SearchRunStatus.CANCELLED if run.cancel.requested else SearchRunStatus.FAILED), None
        return (SearchRunStatus.PARTIAL if result.partial else SearchRunStatus.DONE), result

    def _add(self, run: _Run, event: ProgressEvent) -> None:
        with self._lock:
            if len(run.events) < MAX_RUN_EVENTS:  # a run's state may hold no more (P1); the final event still comes
                run.events.append(event)

    def _end(self, run: _Run, status: SearchRunStatus, result: SearchResult | None) -> None:
        with self._lock:
            now = datetime.now(UTC)
            run.status = status
            run.result = result
            run.finished_at = max(now, run.started_at) if run.started_at else now
            run.ended_at = self._clock()
            self._forget_old()  # so the limit on kept runs holds as runs end, not only at the next request

    def _take(self, search_id: str, sent: int) -> _Batch | None:
        """What a stream sends next, without waiting: the events after `sent`, and the final state once it has ended."""
        with self._lock:
            run = self._runs.get(search_id)
            if run is None or (self.closed and run.status not in _ENDED):
                return None
            final = self._state(run) if run.status in _ENDED else None
            return _Batch(events=run.events[sent:], final=final)

    def _find(self, search_id: str) -> _Run:
        self._forget_old()
        run = self._runs.get(search_id)
        if run is None:
            raise UnknownRun(search_id)
        return run

    def _forget_old(self) -> None:
        """Drop finished runs past the keep-time, and the oldest beyond the limit.

        A run that is still going, that a progress stream is reading, or that has just passed a stream's start check
        (so its stream gets its events, not an empty answer) is never dropped.
        """
        now = self._clock()
        cutoff = now - self.settings.keep_s
        ended = sorted(
            (r for r in self._runs.values() if r.ended_at is not None and r.streams == 0 and r.reserved_until <= now),
            key=lambda r: r.ended_at or 0.0,
        )
        too_many = max(0, len(ended) - self.settings.max_kept)
        for index, run in enumerate(ended):
            if index < too_many or (run.ended_at is not None and run.ended_at <= cutoff):
                del self._runs[run.search_id]

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


def default_runner() -> PipelineRunner:
    """The runner POST /api/search uses: the default stages, saving to the database when one is set (FX-4)."""
    return PipelineRunner(default_search_stages(), DatabaseResultStore(db.default_request_store()))


# --- the routes ---------------------------------------------------------------------------------

router = APIRouter(prefix="/api/search/runs", tags=["search runs"])


def get_search_runs(request: Request) -> SearchRuns:
    runs: SearchRuns = request.app.state.search_runs
    return runs


Runs = Annotated[SearchRuns, Depends(get_search_runs)]
_UNKNOWN: dict[int | str, dict[str, Any]] = {404: {"description": NOT_FOUND}}


@router.post("", status_code=status.HTTP_202_ACCEPTED, responses={503: {"description": BUSY}})
def start_run(request: SearchRequest, runs: Runs, response: Response) -> SearchRunStarted:
    search_id = runs.start(request)
    if search_id is None:
        raise HTTPException(503, BUSY, headers={"Retry-After": str(RETRY_AFTER_SECONDS)})
    response.headers["Location"] = f"{router.prefix}/{search_id}"
    return SearchRunStarted(search_id=search_id)


@router.get("/{search_id}", responses=_UNKNOWN)
def run_state(search_id: str, runs: Runs) -> SearchRun:
    try:
        return runs.get(search_id)
    except UnknownRun:
        raise HTTPException(404, NOT_FOUND) from None


def _stream_start(search_id: str, runs: Runs, last_event_id: Annotated[str | None, Header()] = None) -> int:
    """Checked before the stream opens: an unknown ID is 404, and a client that has seen the end gets 204."""
    try:
        after = runs.resume_from(search_id, last_event_id)
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


@router.delete(
    "/{search_id}",
    responses={**_UNKNOWN, 202: {"description": "Asked to stop before its next stage"}},
)
def cancel_run(search_id: str, runs: Runs, response: Response) -> SearchRun:
    try:
        state, stopping = runs.cancel(search_id)
    except UnknownRun:
        raise HTTPException(404, NOT_FOUND) from None
    response.status_code = status.HTTP_202_ACCEPTED if stopping else status.HTTP_200_OK
    return state
