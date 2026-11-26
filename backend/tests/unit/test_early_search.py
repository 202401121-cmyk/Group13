"""Research and extraction begun during the interview (US-34, D-94; US-12, US-14): the job, its registry, and the
confirmed search that takes it over. Fake stages only (tests/fakes/early.py); every wait has a deadline."""

from __future__ import annotations

import dataclasses
import logging
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

import app.early_search as early_module
from app import contracts as C
from app.contracts import examples as ex
from app.early_search import (
    EARLY_READ_S,
    EarlyJob,
    EarlySearches,
    early_on,
    early_rubric,
    same_ground,
)
from app.extract import ExtractStage
from app.pipeline import (
    EARLY_UNFINISHED,
    READ_HERE,
    SAMPLE_NOTE,
    STAGE_TIME_LIMITS_S,
    CancelFlag,
    InMemoryResultStore,
    PipelineRunner,
    _Search,
    default_search_stages,
    early_search,
)
from app.research import ResearchStage
from app.search_run_limits import EARLY_KEEP_S
from tests.fakes.early import WAIT_S, CountingResearch, Gate, SpecExtract

SID = ex.SESSION_ID
OWNER = "6f1c2b8e-3d4a-4f5b-9c7d-2e1a0b9c8d7e"
PUBLIC = sorted(s.id for s in ex.sources() if s.data_class is C.DataClass.PUBLIC)
ALL_SOURCES = sorted(s.id for s in ex.sources())


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def runner_factory(research: Any, extract: Any) -> Callable[[], PipelineRunner]:
    store = InMemoryResultStore()

    def build() -> PipelineRunner:
        stages = dataclasses.replace(default_search_stages(), research=research, extract=extract)
        return PipelineRunner(stages, store)

    return build


def registry(
    research: Any | None = None,
    extract: Any | None = None,
    *,
    workers: int = 2,
    clock: Callable[[], float] | None = None,
    enabled: bool = True,
) -> EarlySearches:
    factory = runner_factory(research or CountingResearch(), extract or SpecExtract())
    if clock is None:
        return EarlySearches(factory, threading.BoundedSemaphore(workers), enabled=enabled)
    return EarlySearches(factory, threading.BoundedSemaphore(workers), enabled=enabled, clock=clock)


def start(early: EarlySearches, owner: str | None = None, session_id: str = SID) -> bool:
    return early.start(session_id, owner, ex.QUERY, ex.intent(), ex.criteria())


def job_of(early: EarlySearches, session_id: str = SID) -> EarlyJob:
    return early._jobs[session_id]


def finished(early: EarlySearches, session_id: str = SID) -> EarlyJob:
    job = job_of(early, session_id)
    assert job.done.wait(WAIT_S), "the job never ended"
    return job


def free(workers: threading.BoundedSemaphore, count: int) -> bool:
    """Are `count` workers free? Takes and gives them back."""
    taken = 0
    try:
        while taken < count and workers.acquire(blocking=False):
            taken += 1
        return taken == count
    finally:
        for _ in range(taken):
            workers.release()


def wait_until(check: Callable[[], bool], what: str) -> None:
    deadline = time.monotonic() + WAIT_S
    while not check():
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        time.sleep(0.01)


def final_request(rubric: C.Rubric | None = None, user_id: str | None = None) -> C.SearchRequest:
    return C.SearchRequest(session_id=SID, raw_query=ex.QUERY, rubric=rubric or ex.rubric(), user_id=user_id)


def with_rubric(**changes: Any) -> C.Rubric:
    return C.Rubric.model_validate({**ex.rubric().model_dump(), **changes})


# --- the switch, the rubric and the ground -----------------------------------------------------------------------


def test_early_research_is_on_unless_the_environment_turns_it_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(early_module, "DEFAULT_ON", True)
    assert early_on({}) is True
    assert early_on({"EARLY_RESEARCH": " On "}) is True
    for off in ("off", "0", "false", "No"):
        assert early_on({"EARLY_RESEARCH": off}) is False
    monkeypatch.setenv("EARLY_RESEARCH", "off")
    assert early_on() is False  # the process environment when none is given
    monkeypatch.setattr(early_module, "DEFAULT_ON", False)
    assert early_on({"EARLY_RESEARCH": ""}) is False  # the tests' own default (tests/conftest.py)


def test_the_early_rubric_keeps_what_the_interview_cannot_change_and_nothing_it_can() -> None:
    rubric = early_rubric(SID, ex.intent(), ex.criteria())
    assert rubric is not None
    assert (rubric.hard_rules, rubric.exclusions) == ([], [])  # ex.intent() has one of each: read at confirm
    assert {w.key for w in rubric.weights} == {c.key for c in ex.criteria().criteria}
    assert same_ground(rubric, ex.rubric())


def test_an_intent_and_criteria_that_do_not_fit_start_nothing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def broken(state: C.InterviewState) -> C.Rubric:
        raise ValueError("the criteria don't fit")

    monkeypatch.setattr(early_module, "default_rubric", broken)
    caplog.set_level(logging.INFO, logger="app.early_search")
    assert early_rubric(SID, ex.intent(), ex.criteria()) is None
    assert "the criteria don't fit" not in caplog.text and "ValueError" in caplog.text
    early = registry()
    assert start(early) is False
    assert not early.running(SID) and free(early._workers, 2)


@pytest.mark.parametrize(
    "change",
    [
        {"category": "phone"},
        {"use_case": "gaming"},
        {"budget": None},
        {"region": "US"},
    ],
)
def test_the_ground_is_category_use_budget_and_region(change: dict[str, Any]) -> None:
    assert not same_ground(ex.rubric(), with_rubric(**change))


def test_weights_rules_and_leave_outs_are_not_ground() -> None:
    changed = with_rubric(hard_rules=[], exclusions=[])
    assert same_ground(ex.rubric(), changed)


# --- starting a job ------------------------------------------------------------------------------------------------


def test_a_job_reads_every_source_once_and_gives_its_worker_back() -> None:
    research, extract = CountingResearch(), SpecExtract()
    early = registry(research, extract)
    assert start(early) is True
    job = finished(early)
    assert len(research.runs) == 1
    assert sorted(extract.read_by(job.search.ctx.search_id)) == ALL_SOURCES
    assert job.researched_event.is_set()
    assert job.research_report is not None and job.research_report.status is C.StageStatus.OK
    assert job.extract_report is not None and job.extract_report.status is C.StageStatus.OK
    assert job.slot == "released" and free(early._workers, 2)
    # What it asked for: the price, as there is a budget, and no rule key or feature, as the interview can change those.
    assert extract.asked == [(job.search.ctx.search_id, [], True, [])]
    assert job.search.ctx.time_limit_s == EARLY_READ_S


def test_a_session_gets_one_job_at_most() -> None:
    research = CountingResearch()
    early = registry(research)
    assert start(early) is True
    finished(early)
    assert start(early) is False
    assert len(research.runs) == 1


def test_another_owners_turn_drops_the_job() -> None:
    early = registry()
    assert start(early, owner=OWNER) is True
    job = finished(early)
    assert early.owner_of(SID) == OWNER
    assert start(early, owner=None) is False  # D-80: a sign-out starts a new session
    assert job.stop.requested and not early.running(SID) and early.owner_of(SID) is None


@pytest.mark.parametrize("why", ["off", "blank request", "closed"])
def test_no_job_starts_when_early_jobs_are_off_the_request_is_blank_or_the_registry_closed(why: str) -> None:
    research = CountingResearch()
    early = registry(research, enabled=why != "off")
    if why == "closed":
        early.close()
    request = "  " if why == "blank request" else ex.QUERY
    assert early.start(SID, None, request, ex.intent(), ex.criteria()) is False
    assert not early.running(SID) and research.runs == [] and free(early._workers, 2)


def test_a_full_pool_starts_no_job() -> None:
    research = CountingResearch()
    early = registry(research, workers=1)
    assert early._workers.acquire(blocking=False)  # a search holds the only worker
    assert start(early) is False
    assert not early.running(SID) and research.runs == []
    early._workers.release()


def test_a_job_that_cannot_be_built_gives_its_worker_back(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def broken(*args: object) -> _Search:
        raise RuntimeError("no runner today")

    monkeypatch.setattr(early_module, "early_search", broken)
    early = registry()
    assert start(early) is False
    assert not early.running(SID) and free(early._workers, 2)
    assert "RuntimeError" in caplog.text and "no runner today" not in caplog.text


# --- how a job ends ----------------------------------------------------------------------------------------------


def test_a_job_whose_research_fails_ends_with_nothing_to_take_over() -> None:
    extract = SpecExtract()
    early = registry(CountingResearch(fail=True), extract)
    assert start(early) is True
    job = finished(early)
    assert job.research_report is not None and job.research_report.status is C.StageStatus.FAILED
    assert not job.researched_event.is_set() and extract.reads == []
    assert early.claim(SID, None, ex.rubric()) == (job, False)
    assert job.slot == "released" and free(early._workers, 2)


def test_a_job_that_raises_stops_and_gives_its_worker_back(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    gate = Gate()
    early = registry(CountingResearch(gate=gate))
    assert start(early) is True
    job = job_of(early)
    assert gate.entered.wait(WAIT_S)

    def crash() -> C.StageReport:
        raise RuntimeError("extract crashed outside its stage")

    monkeypatch.setattr(job.search, "extract", crash)
    gate.opened.set()
    finished(early)
    assert job.stop.requested and job.extract_report is None
    assert "RuntimeError" in caplog.text and "outside its stage" not in caplog.text
    assert early.claim(SID, None, ex.rubric()) == (job, False)
    assert free(early._workers, 2)


def test_a_job_cancelled_during_research_never_extracts() -> None:
    gate, extract = Gate(), SpecExtract()
    early = registry(CountingResearch(gate=gate), extract)
    assert start(early) is True
    job = job_of(early)
    assert gate.entered.wait(WAIT_S)
    early.cancel(SID)
    assert not early.running(SID) and job.stop.requested
    gate.opened.set()
    assert job.done.wait(WAIT_S)
    assert extract.reads == [] and job.extract_report is None
    early.cancel("sess_nosuchone")  # nothing to cancel: no error


def test_a_job_is_dropped_its_keep_time_after_it_began() -> None:
    clock = Clock()
    early = registry(clock=clock)
    assert start(early, session_id="sess_firstone1") is True
    clock.now += EARLY_KEEP_S - 1
    assert start(early, session_id="sess_second222") is True
    first, second = finished(early, "sess_firstone1"), finished(early, "sess_second222")
    early.drop_expired()
    assert early.running("sess_firstone1") and early.running("sess_second222")
    clock.now += 1
    early.drop_expired()
    assert not early.running("sess_firstone1") and first.stop.requested
    assert early.running("sess_second222") and not second.stop.requested


def test_closing_drops_every_job_and_starts_none() -> None:
    gate = Gate()
    early = registry(CountingResearch(gate=gate))
    assert start(early) is True
    job = job_of(early)
    early.close()
    assert job.stop.requested and not early.running(SID)
    assert start(early, session_id="sess_another11") is False
    gate.opened.set()
    assert job.done.wait(WAIT_S)
    assert free(early._workers, 2)


# --- taking a job over ---------------------------------------------------------------------------------------------


def test_the_same_owner_on_the_same_ground_takes_the_job_over_once() -> None:
    early = registry()
    assert start(early, owner=OWNER) is True
    job = finished(early)
    assert early.claim(SID, OWNER, ex.rubric()) == (job, True)
    assert not early.running(SID) and not job.stop.requested
    assert early.claim(SID, OWNER, ex.rubric()) == (None, False)  # taken: never twice


@pytest.mark.parametrize(
    ("owner", "rubric"),
    [
        (None, ex.rubric()),  # a guest's confirm of a signed-in shopper's session
        ("00000000-0000-4000-8000-000000000000", ex.rubric()),  # another user's
        (OWNER, with_rubric(category="phone")),
        (OWNER, with_rubric(region="US")),
    ],
)
def test_another_owner_or_another_ground_never_gets_the_job(owner: str | None, rubric: C.Rubric) -> None:
    early = registry()
    assert start(early, owner=OWNER) is True
    job = finished(early)
    assert early.claim(SID, owner, rubric) == (job, False)
    assert job.stop.requested and not early.running(SID)


def test_a_guests_job_is_never_given_to_a_signed_in_confirm() -> None:
    early = registry()
    assert start(early, owner=None) is True
    job = finished(early)
    assert early.claim(SID, OWNER, ex.rubric()) == (job, False)
    assert job.stop.requested


def test_a_job_still_researching_is_dropped_at_confirm() -> None:
    gate = Gate()
    early = registry(CountingResearch(gate=gate))
    assert start(early) is True
    job = job_of(early)
    assert gate.entered.wait(WAIT_S)
    assert early.claim(SID, None, ex.rubric()) == (job, False)
    assert job.stop.requested
    gate.opened.set()
    assert job.done.wait(WAIT_S)


def test_a_stopped_or_old_job_is_never_taken_over() -> None:
    clock = Clock()
    early = registry(clock=clock)
    assert start(early) is True
    job = finished(early)
    job.stop.request()
    assert early.claim(SID, None, ex.rubric()) == (job, False)
    assert start(early) is True
    old = finished(early)
    clock.now += EARLY_KEEP_S
    assert early.claim(SID, None, ex.rubric()) == (old, False)


def test_no_job_means_none() -> None:
    assert registry().claim(SID, None, ex.rubric()) == (None, False)


# --- the job's worker and window -------------------------------------------------------------------------------


def test_a_worker_is_handed_over_once_and_never_given_back_twice() -> None:
    gate = Gate()
    early = registry(CountingResearch(gate=gate), workers=1)
    assert start(early) is True
    job = job_of(early)
    assert job.hand_slot() is True and job.slot == "handed"
    assert job.hand_slot() is False
    gate.opened.set()
    assert job.done.wait(WAIT_S)
    assert not free(early._workers, 1)  # the search that took the worker gives it back, not the job
    early._workers.release()
    job.release_slot()
    assert free(early._workers, 1)


def test_a_job_that_gave_its_worker_back_cannot_hand_it_over() -> None:
    early = registry(workers=1)
    assert start(early) is True
    job = finished(early)
    assert job.slot == "released"
    assert job.hand_slot() is False
    job.release_slot()  # already given back: never twice
    assert free(early._workers, 1)
    with pytest.raises(ValueError):
        early._workers.release()  # a BoundedSemaphore refuses a release past its size


def test_a_finished_job_hands_over_its_reports() -> None:
    early = registry()
    assert start(early) is True
    job = finished(early)
    search, report = job.researched()
    assert search is job.search and report is job.research_report
    assert job.extracted(time.monotonic(), 0.0, lambda: False) is job.extract_report


def test_a_job_still_reading_closes_its_window_then_is_given_up() -> None:
    gate = Gate()
    extract = SpecExtract(gate=gate)
    early = registry(CountingResearch(), extract)
    assert start(early) is True
    job = job_of(early)
    assert gate.entered.wait(WAIT_S)
    before = job.search.ctx.time_limit_s
    until = time.monotonic() + 1.0
    assert job.extracted(until, 0.05, lambda: False) is None
    assert job.stop.requested
    assert job.search.ctx.time_limit_s < before
    assert (job.search.ctx.search_id, until) in extract.windows
    # Nothing new is sent after `until`; every later stage and one read keep their own time.
    later = job.search._later_stages_s() + job.search.limits[C.StageName.EXTRACT]
    assert job.search.ctx.time_limit_s == pytest.approx(until - job.search.ctx.started_at + later)
    gate.opened.set()
    assert job.done.wait(WAIT_S)


def test_a_job_still_reading_is_joined_when_it_ends_in_time() -> None:
    gate = Gate()
    extract = SpecExtract(gate=gate)
    early = registry(CountingResearch(), extract)
    assert start(early) is True
    job = job_of(early)
    assert gate.entered.wait(WAIT_S)
    threading.Timer(0.05, gate.opened.set).start()
    report = job.extracted(time.monotonic() + 10.0, WAIT_S, lambda: False)
    assert report is not None and report is job.extract_report and not job.stop.requested


def test_a_window_the_stage_cannot_move_still_closes_by_the_time_limit(caplog: pytest.LogCaptureFixture) -> None:
    class NoWindow(SpecExtract):
        def open_window(self, search_id: str, until: float) -> None:
            raise RuntimeError("window store down")

    gate = Gate()
    early = registry(CountingResearch(gate=gate), NoWindow())
    assert start(early) is True
    job = job_of(early)
    assert gate.entered.wait(WAIT_S)
    job.finish_by(time.monotonic())
    assert job.search.ctx.time_limit_s < EARLY_READ_S
    assert "RuntimeError" in caplog.text and "window store down" not in caplog.text
    gate.opened.set()
    assert job.done.wait(WAIT_S)


def test_a_stage_with_no_window_still_closes_by_the_time_limit() -> None:
    gate = Gate()
    early = registry(CountingResearch(gate=gate), ExtractStage())
    assert start(early) is True
    job = job_of(early)
    assert gate.entered.wait(WAIT_S)
    job.finish_by(time.monotonic())
    assert job.search.ctx.time_limit_s < EARLY_READ_S
    gate.opened.set()
    assert job.done.wait(WAIT_S)


def test_the_early_search_has_its_own_id_limit_and_the_searchs_stages() -> None:
    research, extract = CountingResearch(), SpecExtract()
    runner = runner_factory(research, extract)()
    search = early_search(runner, final_request(), CancelFlag(), 42.0)
    assert search.ctx.time_limit_s == 42.0
    assert search.stages.research is research and search.stages.extract is extract
    assert search.early is None


# --- the confirmed search ------------------------------------------------------------------------------------------


def taken_over(research: Any, extract: Any, owner: str | None = None) -> tuple[EarlyJob, Callable[[], PipelineRunner]]:
    factory = runner_factory(research, extract)
    early = EarlySearches(factory, threading.BoundedSemaphore(2), enabled=True)
    assert start(early, owner=owner) is True
    job = finished(early)
    assert early.claim(SID, owner, ex.rubric()) == (job, True)
    return job, factory


def test_a_confirmed_search_takes_the_job_over_and_asks_only_for_the_rule_key_the_interview_added() -> None:
    research, extract = CountingResearch(), SpecExtract(without=("ram_gb",))
    job, factory = taken_over(research, extract)
    events: list[C.ProgressEvent] = []
    result = factory().run(final_request(), progress=events.append, early=job)
    assert len(research.runs) == 1  # the job's: research never runs again
    assert sorted(extract.read_by(job.search.ctx.search_id)) == ALL_SOURCES
    # Asked again: ram_gb, of the public pages with claims only (never the Reddit thread), under the search's own ID.
    assert sorted(extract.read_by(result.search_id)) == PUBLIC
    assert (result.search_id, ["ram_gb"], False, []) in extract.asked
    reports = {r.stage: r for r in result.stage_reports}
    assert reports[C.StageName.RESEARCH] == job.research_report
    assert reports[C.StageName.EXTRACT].status is C.StageStatus.OK
    assert "asked 2 sources again for keys added in the interview" in reports[C.StageName.EXTRACT].notes
    # Only the claims on the key asked for are added: the criteria were read already.
    ram = [c.id for c in ex.claims() if c.criterion_key == "ram_gb"]
    assert sorted(set(extract.kept) & set(ram)) == sorted(ram)
    # The stream shows research and extraction as stages done, as for any search.
    seen = [(e.stage, e.status) for e in events if e.stage in (C.StageName.RESEARCH, C.StageName.EXTRACT)]
    assert seen == [
        (C.StageName.RESEARCH, "started"),
        (C.StageName.RESEARCH, "done"),
        (C.StageName.EXTRACT, "started"),
        (C.StageName.EXTRACT, "done"),
    ]


def search_over(job: EarlyJob, factory: Callable[[], PipelineRunner], rubric: C.Rubric | None = None) -> _Search:
    """The confirmed search's research and extraction, run by hand: its claims can then be read."""
    runner = factory()
    ctx = C.StageContext(search_id="srch_" + "a" * 24)
    search = _Search(runner.stages, runner.time_limits, final_request(rubric), ctx, early=job)
    assert search.research() == job.research_report
    return search


def test_the_search_keeps_every_claim_the_job_found_and_the_ones_asked_again() -> None:
    extract = SpecExtract(without=("ram_gb",))
    job, factory = taken_over(CountingResearch(), extract)
    search = search_over(job, factory)
    report = search.extract()
    found = list(job.search.claims)
    asked = [c for c in search.claims if c not in found]
    assert all(c in search.claims for c in found)
    assert asked and {c.criterion_key for c in asked} == {"ram_gb"}
    assert search.mentions == job.search.mentions
    assert search.extract_note == "3 sources read 2 sources read"
    assert report.status is C.StageStatus.OK


def test_a_ruled_out_feature_added_in_the_interview_is_asked_for_by_its_own_name() -> None:
    extract = SpecExtract(without=())
    job, factory = taken_over(CountingResearch(), extract)
    leave_out = {**ex.rubric().exclusions[0].model_dump(), "kind": "feature", "value": "Touch screen"}
    rubric = with_rubric(exclusions=[leave_out])
    search = search_over(job, factory, rubric)
    search.extract()
    assert ("srch_" + "a" * 24, [], False, ["touchscreen"]) in extract.asked
    added = [c for c in search.claims if c.criterion_key == "has_touchscreen"]
    assert sorted(c.source_id for c in added) == PUBLIC


def test_nothing_is_asked_again_when_every_key_has_claims() -> None:
    extract = SpecExtract(without=())
    job, factory = taken_over(CountingResearch(), extract)
    search = search_over(job, factory)
    assert search.extract() is job.extract_report
    assert extract.read_by(search.ctx.search_id) == []
    assert search.claims == job.search.claims


def test_a_stage_that_cannot_ask_for_keys_reads_nothing_again() -> None:
    job, factory = taken_over(CountingResearch(), ExtractStage())
    search = search_over(job, factory)
    assert search.extract() is job.extract_report


def test_with_no_public_page_to_ask_the_key_stays_unknown() -> None:
    class RedditOnly(CountingResearch):
        def run(self, inp: C.ResearchInput, ctx: C.StageContext) -> C.ResearchResult:
            out = super().run(inp, ctx)
            reddit = [s for s in out.sources if s.data_class is C.DataClass.REDDIT]
            return C.ResearchResult.model_validate({**out.model_dump(), "sources": [s.model_dump() for s in reddit]})

    extract = SpecExtract(without=("ram_gb",))
    job, factory = taken_over(RedditOnly(), extract)
    search = search_over(job, factory)
    assert search.extract() is job.extract_report
    assert extract.read_by(search.ctx.search_id) == []
    assert all(c.criterion_key != "ram_gb" for c in search.claims)  # never guessed (DR-09)


def test_a_search_out_of_time_asks_nothing_again() -> None:
    extract = SpecExtract(without=("ram_gb",))
    job, factory = taken_over(CountingResearch(), extract)
    search = search_over(job, factory)
    search.out_of_time = True
    search.extract()
    assert extract.read_by(search.ctx.search_id) == []


def test_a_cancelled_search_asks_nothing_again() -> None:
    class CancelsOnAsk(SpecExtract):
        cancel: CancelFlag | None = None

        def ask_for_specs(self, search_id: str, keys: Any, price: bool, features: Any = ()) -> None:
            super().ask_for_specs(search_id, keys, price, features)
            if self.cancel is not None:
                self.cancel.request()

    extract = CancelsOnAsk(without=("ram_gb",))
    job, factory = taken_over(CountingResearch(), extract)
    search = search_over(job, factory)
    search.cancel = extract.cancel = CancelFlag()
    search.extract()
    assert extract.read_by(search.ctx.search_id) == []


def test_a_failed_read_again_reports_its_fallback_with_the_jobs_report() -> None:
    class FailsForTheSearch(SpecExtract):
        def run(self, inp: C.ExtractInput, ctx: C.StageContext) -> C.ExtractionResult:
            if ctx.search_id.endswith("a" * 24):
                raise RuntimeError("provider down")
            return super().run(inp, ctx)

    extract = FailsForTheSearch(without=("ram_gb",))
    job, factory = taken_over(CountingResearch(), extract)
    search = search_over(job, factory)
    report = search.extract()
    assert report.status is C.StageStatus.FALLBACK
    assert report.fallback_reason
    assert "asked 2 sources again for keys added in the interview" in report.notes
    assert all(c.criterion_key != "ram_gb" for c in search.claims)


def test_the_note_from_asking_again_stands_alone_when_the_job_kept_none() -> None:
    extract = SpecExtract(without=("ram_gb",))
    quiet: Callable[[str], str | None] = lambda search_id: None  # noqa: E731
    extract.coverage_note = quiet  # type: ignore[method-assign]
    job, factory = taken_over(CountingResearch(), extract)
    assert job.search.extract_note is None
    del extract.coverage_note  # the class's own again, for the search
    search = search_over(job, factory)
    search.extract()
    assert search.extract_note == "2 sources read"


class Unfinished:
    """A job whose extraction never ended in the search's window."""

    def __init__(self, job: EarlyJob) -> None:
        self.job = job
        self.waits: list[tuple[float, float]] = []

    def researched(self) -> tuple[_Search, C.StageReport]:
        return self.job.researched()

    def extracted(self, until: float, wait_s: float, stopped: Callable[[], bool]) -> C.StageReport | None:
        self.waits.append((until, wait_s))
        return None


def test_a_job_that_left_no_report_with_the_window_still_open_is_read_by_the_search_itself() -> None:
    """Build rule 9: the job failed outside its stage after the confirm took it over, so the search reads its sources
    in the time it has, as a normal search would."""
    extract = SpecExtract()
    job, factory = taken_over(CountingResearch(), extract)
    handover = Unfinished(job)
    result = factory().run(final_request(), early=handover)
    report = {r.stage: r for r in result.stage_reports}[C.StageName.EXTRACT]
    assert report.status is C.StageStatus.OK and READ_HERE in report.notes
    assert sorted(extract.read_by(result.search_id)) == ALL_SOURCES
    assert len(result.stage_reports) == 8  # every later stage still ran
    [(until, wait_s)] = handover.waits
    # It waited for the search's own send window, plus one read's limit and the grace, and no longer.
    assert wait_s == pytest.approx(until - time.monotonic() + STAGE_TIME_LIMITS_S[C.StageName.EXTRACT] + 1.0, abs=0.5)


def test_a_job_that_did_not_end_in_the_window_leaves_extract_skipped() -> None:
    extract = SpecExtract()
    job, factory = taken_over(CountingResearch(), extract)
    runner = factory()
    ctx = C.StageContext(search_id="srch_" + "b" * 24)
    search = _Search(runner.stages, runner.time_limits, final_request(), ctx, early=Unfinished(job))
    search.research()
    search.ctx.time_limit_s = 1.0  # the window has closed by the time the wait ends
    report = search.extract()
    assert (report.status, report.fallback_reason) == (C.StageStatus.SKIPPED, EARLY_UNFINISHED)
    assert extract.read_by(search.ctx.search_id) == []


def test_a_search_cancelled_while_it_waits_for_its_job_stops_the_job_and_reads_nothing() -> None:
    gate = Gate()
    extract = SpecExtract(gate=gate)
    early = registry(CountingResearch(), extract)
    assert start(early) is True
    job = job_of(early)
    assert gate.entered.wait(WAIT_S)
    assert early.claim(SID, None, ex.rubric()) == (job, True)
    runner = runner_factory(CountingResearch(), extract)()
    cancel = CancelFlag()
    ctx = C.StageContext(search_id="srch_" + "c" * 24)
    search = _Search(runner.stages, runner.time_limits, final_request(), ctx, cancel, early=job)
    search.research()
    threading.Timer(0.1, cancel.request).start()
    began = time.monotonic()
    report = search.extract()
    assert time.monotonic() - began < 2.0  # not the ~90 s the join would allow
    assert (report.status, report.fallback_reason) == (C.StageStatus.SKIPPED, EARLY_UNFINISHED)
    assert job.stop.requested
    assert extract.read_by(search.ctx.search_id) == []
    gate.opened.set()
    assert job.done.wait(WAIT_S)


def test_a_stopped_wait_gives_the_job_up_even_if_it_ended() -> None:
    early = registry()
    assert start(early) is True
    job = finished(early)
    assert job.extracted(time.monotonic(), 1.0, lambda: True) is None  # a search cancelled at that moment
    assert job.stop.requested


def test_a_finished_job_moves_no_window() -> None:
    extract = SpecExtract()
    early = registry(CountingResearch(), extract)
    assert start(early) is True
    job = finished(early)
    before = (job.search.ctx.time_limit_s, list(extract.windows))
    job.finish_by(time.monotonic())
    assert (job.search.ctx.time_limit_s, extract.windows) == before  # no window left behind in the stage


def test_a_job_whose_extraction_crashes_after_the_confirm_is_read_by_the_search() -> None:
    gate, research_gate = Gate(), Gate()
    extract = SpecExtract()
    early = registry(CountingResearch(gate=research_gate), extract)
    assert start(early) is True
    job = job_of(early)
    assert research_gate.entered.wait(WAIT_S)

    def crash() -> C.StageReport:
        gate.hold()
        raise RuntimeError("extract crashed outside its stage")

    job.search.extract = crash  # type: ignore[method-assign]
    research_gate.opened.set()
    assert job.researched_event.wait(WAIT_S)
    assert early.claim(SID, None, ex.rubric()) == (job, True)
    threading.Timer(0.1, gate.opened.set).start()
    result = runner_factory(CountingResearch(), extract)().run(final_request(), early=job)
    report = {r.stage: r for r in result.stage_reports}[C.StageName.EXTRACT]
    assert READ_HERE in report.notes
    assert sorted(extract.read_by(result.search_id)) == ALL_SOURCES


def test_a_stub_job_keeps_the_sample_note_on_the_result() -> None:
    """The research stub's example sources reach the result through the job: the sample note must still show."""
    job, factory = taken_over(ResearchStage(), ExtractStage())
    result = factory().run(final_request(), early=job)
    assert result.coverage_note is not None and SAMPLE_NOTE in result.coverage_note


def test_a_stub_extraction_taken_over_keeps_the_sample_flag() -> None:
    job, factory = taken_over(CountingResearch(), ExtractStage())
    search = search_over(job, factory)
    search.extract()
    assert search.used_placeholder  # the job's extraction was the stub's example claims


def test_a_rule_key_a_public_call_cannot_ask_for_reads_nothing_again() -> None:
    extract = SpecExtract(without=())
    job, factory = taken_over(CountingResearch(), extract)
    known = {k: v for k, v in ex.rubric().hard_rules[0].model_dump().items() if k != "id"}
    rule = {**known, "attribute": "warranty_years"}  # not a known spec key
    search = search_over(job, factory, with_rubric(hard_rules=[rule]))
    assert search.extract() is job.extract_report
    assert extract.read_by(search.ctx.search_id) == []


def test_a_stage_that_takes_no_features_is_asked_for_the_rule_keys_only() -> None:
    class RulesOnly(SpecExtract):
        def ask_for_specs(self, search_id: str, keys: Any, price: bool) -> None:  # type: ignore[override]
            super().ask_for_specs(search_id, keys, price)

    extract = RulesOnly(without=("ram_gb",))
    job, factory = taken_over(CountingResearch(), extract)
    leave_out = {**ex.rubric().exclusions[0].model_dump(), "kind": "feature", "value": "Touch screen"}
    search = search_over(job, factory, with_rubric(exclusions=[leave_out]))
    search.extract()
    assert (search.ctx.search_id, ["ram_gb"], False, []) in extract.asked
    assert all(c.criterion_key != "has_touchscreen" for c in search.claims)


def test_other_criteria_are_another_ground() -> None:
    fewer = ex.rubric().weights[:-1]
    total = sum(w.weight for w in fewer)
    weights = [{**w.model_dump(), "normalized": w.weight / total} for w in fewer]
    assert not same_ground(ex.rubric(), with_rubric(weights=weights))


def test_a_job_past_its_keep_time_on_the_wall_clock_is_dropped_though_the_machine_slept() -> None:
    wall = [datetime(2026, 11, 27, 0, 30, tzinfo=UTC)]
    factory = runner_factory(CountingResearch(), SpecExtract())
    early = EarlySearches(factory, threading.BoundedSemaphore(2), wall_clock=lambda: wall[0])
    assert start(early) is True
    job = finished(early)
    wall[0] += timedelta(seconds=EARLY_KEEP_S)  # the monotonic clock stood still while it slept
    assert early.claim(SID, None, ex.rubric()) == (job, False)
    assert start(early) is True
    later = finished(early)
    wall[0] += timedelta(seconds=EARLY_KEEP_S)
    early.drop_expired()
    assert not early.running(SID) and later.stop.requested


def test_a_job_given_back_waits_for_the_next_try() -> None:
    early = registry()
    assert start(early) is True
    job = finished(early)
    assert early.claim(SID, None, ex.rubric()) == (job, True)
    early.give_back(job)
    assert early.running(SID) and not job.stop.requested
    assert early.claim(SID, None, ex.rubric()) == (job, True)


@pytest.mark.parametrize("why", ["closed", "stopped", "expired", "replaced"])
def test_a_job_that_cannot_wait_is_stopped_instead_of_given_back(why: str) -> None:
    clock = Clock()
    early = registry(clock=clock)
    assert start(early) is True
    job = finished(early)
    assert early.claim(SID, None, ex.rubric()) == (job, True)
    if why == "closed":
        early.close()
    elif why == "stopped":
        job.stop.request()
    elif why == "expired":
        clock.now += EARLY_KEEP_S
    else:
        assert start(early) is True  # a newer job for the session
        finished(early)
    early.give_back(job)
    assert job.stop.requested
    assert early._jobs.get(SID) is not job


def test_a_thread_that_cannot_start_leaves_no_job_and_its_worker_free(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    class NoThread:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def start(self) -> None:
            raise RuntimeError("cannot start a new thread")

    monkeypatch.setattr(early_module.threading, "Thread", NoThread)
    early = registry()
    assert start(early) is False
    assert not early.running(SID) and free(early._workers, 2)
    assert "RuntimeError" in caplog.text
