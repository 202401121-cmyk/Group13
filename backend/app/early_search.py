"""Research and extraction begun while the shopper answers the interview (US-34, D-94; US-12, US-14).

An interview takes minutes in which no provider room is used, while extraction gets about a minute of it once the
search starts (S5-6a). So once a session's criteria are final and the shopper has answered the first question (a visit
that bounces spends nothing), one job for that session runs the search's own research and extract stages, at most one
per session, on a worker of the search runs' own pool, under every budget, cap and data-class rule a search has. A reply
that finishes the interview starts none: its confirm follows at once.

It reads only what the interview can't change (D-94 item 1): research reads the category, the use (as a known use
only, D-53), the budget and the region; extraction reads the criteria's keys (their fixed public stand-ins in public
calls) and asks for the price when there is a budget. Hard rules, leave-outs and weights are read at confirm, as before.

At confirm (POST /api/search/runs), the search takes the job over: the same session, the same owner (a signed-in
shopper's own, or a guest's in that guest's session), the same category, use, budget, region and criteria, with
research done. A job still extracting is joined, never repeated, and its worker passes to the search. Anything else
(no job, a job still researching, another owner, a changed brief, a job that failed) runs the normal search, and the
job is dropped; a dropped job still holding a worker gives it to that search.

A job reads for at most EARLY_READ_S, and goes when it is taken over, stopped, or EARLY_KEEP_S after it began, on the
monotonic or the wall clock, whichever is first: the Reddit cache allows for that time (serve_limit(), D-53), so no
Reddit text outlives 24 hours. Code only: nothing here calls a provider itself, and no shopper text reaches a query.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import monotonic
from typing import Literal

from pydantic import ValidationError

from app.contracts import CriteriaSet, Intent, InterviewState, Rubric, SearchRequest, StageReport, StageStatus
from app.interview.rubric_builder import default_rubric
from app.pipeline import CancelFlag, PipelineRunner, _Search, early_search
from app.search_run_limits import EARLY_KEEP_S, environment

log = logging.getLogger(__name__)

# The job's own time for research and extraction (US-34). An interview takes a minute or two; past this a job not
# taken over (an abandoned session, or one whose shopper signed in or out and so has a new one, D-80) stops sending
# and gives its worker back, rather than wait on providers with no room for the whole keep time.
EARLY_READ_S = 300.0
DEFAULT_ON = True  # EARLY_RESEARCH=off turns early jobs off; tests turn them off unless they ask (tests/conftest.py)
JOIN_STEP_S = 0.1  # how often a search waiting for its job checks whether it was cancelled

Slot = Literal["held", "handed", "released"]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def early_on(env: Mapping[str, str] | None = None) -> bool:
    """EARLY_RESEARCH: "off", "0", "false" or "no" turns early jobs off; anything else, or nothing, leaves them on."""
    value = (environment() if env is None else env).get("EARLY_RESEARCH", "").strip().lower()
    if not value:
        return DEFAULT_ON
    return value not in ("off", "0", "false", "no")


def early_rubric(session_id: str, intent: Intent, criteria: CriteriaSet) -> Rubric | None:
    """The rubric early reading may use: the interview's own default weights over the criteria, with the intent's
    category, use, budget and region, and no hard rule or exclusion, as the interview can still change those."""
    try:
        state = InterviewState(session_id=session_id, intent=intent, criteria=criteria)
        rubric = default_rubric(state)
        return Rubric.model_validate({**rubric.model_dump(), "hard_rules": [], "exclusions": []})
    except (ValidationError, ValueError) as exc:  # intent and criteria that don't fit: no early job (build rule 9)
        log.info("early search: no rubric to start from (%s)", type(exc).__name__)
        return None


def same_ground(early: Rubric, final: Rubric) -> bool:
    """Do both rubrics agree on everything early reading used: category, use, budget, region and the criteria's keys?
    The weights themselves may differ: decide reads the final ones."""

    def ground(rubric: Rubric) -> tuple[object, ...]:
        keys = sorted(weight.key for weight in rubric.weights)
        return rubric.category, rubric.use_case, rubric.budget, rubric.region, keys

    return ground(early) == ground(final)


@dataclass(eq=False)
class EarlyJob:
    """One session's early research and extraction. Its fields are set by its own thread, then read once it says so."""

    session_id: str
    owner: str | None
    rubric: Rubric
    search: _Search
    began: float
    began_wall: datetime = field(default_factory=_utc_now)
    stop: CancelFlag = field(default_factory=CancelFlag)
    researched_event: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)
    research_report: StageReport | None = None
    extract_report: StageReport | None = None
    slot: Slot = "held"
    _release: Callable[[], None] = lambda: None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # What the confirmed search takes over (app/pipeline.py's EarlyHandover).

    def researched(self) -> tuple[_Search, StageReport]:
        report = self.research_report
        assert report is not None  # a job is taken over only once research has a report (EarlySearches.claim)
        return self.search, report

    def extracted(self, until: float, wait_s: float, stopped: Callable[[], bool]) -> StageReport | None:
        """Extraction's report once it has ended, waited for at most `wait_s`, and sending nothing new after `until`.
        None when it didn't end by then, failed outside its stage, or `stopped` (the search's cancel) said so: the
        job is then stopped."""
        if not self.done.is_set():
            self.finish_by(until)
            deadline = monotonic() + max(0.0, wait_s)
            while not self.done.is_set() and not stopped():
                left = deadline - monotonic()
                if left <= 0:
                    break
                self.done.wait(timeout=min(JOIN_STEP_S, left))
        if not self.done.is_set() or stopped():
            self.stop.request()  # its reads end on their own; the search goes on without them, or stops
            return None
        return self.extract_report

    def finish_by(self, until: float) -> None:
        """Send nothing new after `until` (the monotonic clock): the confirmed search's own window. A read already
        going ends within its own limit."""
        if self.done.is_set():
            return  # its reading is over: nothing left to close
        ctx = self.search.ctx
        keep = self.search._later_stages_s() + self.search.limits[self.search.stages.extract.name]
        ctx.time_limit_s = min(ctx.time_limit_s, max(0.0, until - ctx.started_at) + keep)
        open_window = getattr(self.search.stages.extract, "open_window", None)
        if open_window is not None:
            try:
                open_window(ctx.search_id, until)
            except Exception as exc:  # the window above still closes; only the stage's own wait stays longer
                log.warning("early search: the extract window was not moved (%s)", type(exc).__name__)

    # The worker the job runs on.

    def hand_slot(self) -> bool:
        """Give the job's worker to the search that takes it over. False when the job has already given it back."""
        with self._lock:
            if self.slot != "held":
                return False
            self.slot = "handed"
            return True

    def release_slot(self) -> None:
        """The job is over: its worker goes back to the pool, unless a search took it over."""
        with self._lock:
            if self.slot != "held":
                return
            self.slot = "released"
        self._release()


class EarlySearches:
    """Every session's early job, on the search runs' own workers (app/api/search_runs.py)."""

    def __init__(
        self,
        runner_factory: Callable[[], PipelineRunner],
        workers: threading.BoundedSemaphore,
        *,
        enabled: bool = True,
        keep_s: float = EARLY_KEEP_S,
        read_s: float = EARLY_READ_S,
        clock: Callable[[], float] = monotonic,
        wall_clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._runner_factory = runner_factory
        self._workers = workers
        self.enabled = enabled
        self.keep_s = keep_s
        self.read_s = read_s
        self._clock = clock
        self._wall = wall_clock  # the time Reddit data ages by: a machine that sleeps stops the monotonic clock
        self._lock = threading.Lock()
        self._jobs: dict[str, EarlyJob] = {}
        self.closed = False

    def start(self, session_id: str, owner: str | None, request: str, intent: Intent, criteria: CriteriaSet) -> bool:
        """Begin the session's job, unless one is there already, the pool has no free worker, or early jobs are off.
        Never raises: no job is no harm, as the normal search runs at confirm (build rule 9).

        A job begun for another owner is dropped instead (D-80: signing in or out starts a new session)."""
        if not self.enabled or not request.strip():
            return False
        with self._lock:
            held = self._jobs.get(session_id)
            if held is not None:
                if held.owner != owner:
                    self._drop(session_id)
                return False
            if self.closed:
                return False
            rubric = early_rubric(session_id, intent, criteria)
            if rubric is None or not self._workers.acquire(blocking=False):
                return False  # the pool is full: the normal search runs at confirm
            try:
                job = self._job(session_id, owner, request, rubric)
                threading.Thread(target=self._work, args=(job,), name="early-search", daemon=True).start()
            except Exception as exc:  # no job, and the normal search at confirm
                self._workers.release()
                log.warning("early search not started: %s", type(exc).__name__)
                return False
            self._jobs[session_id] = job
        return True

    def cancel(self, session_id: str) -> None:
        """Stop the session's job and drop what it found: its brief changed, or a new one began."""
        with self._lock:
            self._drop(session_id)

    def claim(self, session_id: str, owner: str | None, rubric: Rubric) -> tuple[EarlyJob | None, bool]:
        """The session's job, taken out of the registry, and whether the confirmed search may take it over. One it may
        not is stopped, and the normal search runs; its worker may still pass to that search (EarlyJob.hand_slot)."""
        with self._lock:
            job = self._jobs.pop(session_id, None)
        if job is None:
            return None, False
        usable = (
            job.owner == owner
            and same_ground(job.rubric, rubric)
            and job.researched_event.is_set()
            and not job.stop.requested
            and not self._expired(job)
        )
        if not usable:
            job.stop.request()
        return job, usable

    def give_back(self, job: EarlyJob) -> None:
        """A job claimed by a confirm that got no worker: kept for the shopper's next try, unless it can't be."""
        with self._lock:
            if self.closed or job.stop.requested or self._expired(job) or job.session_id in self._jobs:
                job.stop.request()
                return
            self._jobs[job.session_id] = job

    def owner_of(self, session_id: str) -> str | None:
        with self._lock:
            job = self._jobs.get(session_id)
            return job.owner if job is not None else None

    def running(self, session_id: str) -> bool:
        with self._lock:
            return session_id in self._jobs

    def drop_expired(self) -> None:
        """Drop every job EARLY_KEEP_S after it began, taken over or not (D-53: serve_limit() allows for this time)."""
        with self._lock:
            for session_id in [s for s, job in self._jobs.items() if self._expired(job)]:
                self._drop(session_id)

    def close(self) -> None:
        with self._lock:
            self.closed = True
            for session_id in list(self._jobs):
                self._drop(session_id)

    def _expired(self, job: EarlyJob) -> bool:
        """Past its keep time on the monotonic clock or on the wall clock that Reddit data ages by (D-53)."""
        late = self._clock() - job.began >= self.keep_s
        return late or (self._wall() - job.began_wall).total_seconds() >= self.keep_s

    def _drop(self, session_id: str) -> None:
        """Call with the lock held."""
        job = self._jobs.pop(session_id, None)
        if job is not None:
            job.stop.request()

    def _job(self, session_id: str, owner: str | None, request: str, rubric: Rubric) -> EarlyJob:
        stop = CancelFlag()
        search_request = SearchRequest(session_id=session_id, raw_query=request, rubric=rubric, user_id=owner)
        search = early_search(self._runner_factory(), search_request, stop, self.read_s)
        job = EarlyJob(session_id, owner, rubric, search, self._clock(), self._wall(), stop=stop)
        job._release = self._workers.release
        return job

    def _work(self, job: EarlyJob) -> None:
        try:
            job.research_report = job.search.research()
            if job.research_report.status is not StageStatus.FAILED and job.search.research_out is not None:
                job.researched_event.set()
                if not job.stop.requested:
                    job.extract_report = job.search.extract()
        except Exception as exc:  # build rule 9: the job ends, and its search runs as normal at confirm
            log.warning("early search failed: %s", type(exc).__name__)
            job.stop.request()
        finally:
            job.done.set()
            job.release_slot()
