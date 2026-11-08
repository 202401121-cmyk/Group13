"""The runner's progress callback, its cancel flag and its search IDs (US-12, P-07).

The CLI and POST /api/search pass none of these, so the last test pins that the runner still behaves as before for them.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import Counter
from typing import Any

import pytest

import app.contracts as C
from app.contracts import examples as ex
from app.contracts.pipeline import MAX_RUN_EVENTS
from app.pipeline import (
    CancelFlag,
    InMemoryResultStore,
    PipelineRunner,
    SearchCancelled,
    default_search_stages,
    new_search_id,
)
from app.research import ResearchStage

RUN_ID = re.compile(r"^srch_[a-f0-9]{24}$")
ORDER = [
    C.StageName.RESEARCH,
    C.StageName.EXTRACT,
    C.StageName.MATCH,
    C.StageName.VERIFY,
    C.StageName.ENRICH,
    C.StageName.DECIDE,
    C.StageName.EXPLAIN,
    C.StageName.COMMERCE,
]
NO_SOURCES = dict.fromkeys(C.SourceType, 0)


def _request() -> C.SearchRequest:
    return C.SearchRequest(session_id=ex.SESSION_ID, raw_query=ex.QUERY, rubric=ex.rubric())


class _Seen:
    """A progress callback that keeps every event it is given."""

    def __init__(self) -> None:
        self.events: list[C.ProgressEvent] = []

    def __call__(self, event: C.ProgressEvent) -> None:
        self.events.append(event)

    def pairs(self) -> list[tuple[C.StageName, str]]:
        return [(e.stage, e.status) for e in self.events]

    def of(self, stage: C.StageName) -> list[C.ProgressEvent]:
        return [e for e in self.events if e.stage is stage]


def _run(stages: Any = None, store: InMemoryResultStore | None = None, **options: Any) -> tuple[C.SearchResult, _Seen]:
    seen = _Seen()
    runner = PipelineRunner(stages or default_search_stages(), store or InMemoryResultStore())
    result = runner.run(_request(), search_id=new_search_id(), progress=seen, **options)
    return result, seen


class _Wrapped:
    """A real stage with something done just before its run() (setting the cancel flag, checking the events so far)."""

    def __init__(self, real: Any, before: Any) -> None:
        self.real = real
        self.name = real.name
        self.is_placeholder = getattr(real, "is_placeholder", False)
        self.before = before

    def run(self, inp: Any, ctx: C.StageContext) -> Any:
        self.before()
        return self.real.run(inp, ctx)

    def fallback(self, inp: Any, ctx: C.StageContext, error: Exception) -> Any:
        return self.real.fallback(inp, ctx, error)


class _BrokenMatch:
    name = C.StageName.MATCH
    is_placeholder = False

    def run(self, inp: C.MatchInput, ctx: C.StageContext) -> C.MatchResult:
        raise RuntimeError("matcher crashed: KeyError('nova')")

    def fallback(self, inp: C.MatchInput, ctx: C.StageContext, error: Exception) -> C.MatchResult:
        raise RuntimeError("fuzzy matcher crashed too")


class _RealResearch(ResearchStage):
    """The sample sources, from a class that says it is real: what P9's research will look like to the runner."""

    is_placeholder = False


class _StuckResearch(ResearchStage):
    """Waits until released (at most 2 s, in the stage's own thread); the test only waits for the stage's limit."""

    def __init__(self) -> None:
        super().__init__()
        self.release = threading.Event()

    def run(self, inp: C.ResearchInput, ctx: C.StageContext) -> C.ResearchResult:
        self.release.wait(2)
        return super().run(inp, ctx)


class _JumpingClock:
    """Monotonic time a fake stage can move forward, so a run of 150 seconds takes none."""

    def __init__(self) -> None:
        self.jumped_s = 0.0

    def __call__(self) -> float:
        return time.monotonic() + self.jumped_s


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _JumpingClock:
    built = _JumpingClock()
    monkeypatch.setattr("app.pipeline.monotonic", built)
    monkeypatch.setattr("app.contracts.pipeline.monotonic", built)
    return built


# --- the search ID ------------------------------------------------------------------------------


def test_a_new_search_id_is_srch_and_24_hex() -> None:
    ids = {new_search_id() for _ in range(50)}
    assert len(ids) == 50
    assert all(RUN_ID.fullmatch(i) for i in ids)
    result = PipelineRunner(default_search_stages(), InMemoryResultStore()).run(_request())
    assert RUN_ID.fullmatch(result.search_id)  # the CLI's and POST /api/search's searches too
    assert C.SearchRunStarted(search_id=result.search_id).search_id == result.search_id  # the run contract takes it


def test_the_result_keeps_the_search_id_it_was_given() -> None:
    search_id = new_search_id()
    seen = _Seen()
    result = PipelineRunner(default_search_stages(), InMemoryResultStore()).run(
        _request(), search_id=search_id, progress=seen
    )
    assert result.search_id == search_id
    assert {e.search_id for e in seen.events} == {search_id}


# --- the events ---------------------------------------------------------------------------------


def test_each_stage_reports_started_then_its_outcome_in_order() -> None:
    _, seen = _run()
    assert seen.pairs() == [(stage, status) for stage in ORDER for status in ("started", "done")]


def test_event_ids_count_up_from_1_without_gaps() -> None:
    result, seen = _run()
    assert [e.event_id for e in seen.events] == list(range(1, 17))
    times = [e.at for e in seen.events]
    assert times == sorted(times)
    assert all(result.started_at <= t <= result.finished_at for t in times)
    run = C.SearchRun(
        search_id=result.search_id,
        status=C.SearchRunStatus.DONE,
        started_at=result.started_at,
        finished_at=result.finished_at,
        progress=seen.events,
        result=result,
    )
    assert len(run.progress) <= MAX_RUN_EVENTS


def test_events_arrive_while_the_search_runs() -> None:
    seen = _Seen()
    when_extract_ran: list[list[tuple[C.StageName, str]]] = []
    stages = default_search_stages()
    stages.extract = _Wrapped(stages.extract, lambda: when_extract_ran.append(seen.pairs()))
    PipelineRunner(stages, InMemoryResultStore()).run(_request(), search_id=new_search_id(), progress=seen)
    assert when_extract_ran[0][-3:] == [
        (C.StageName.RESEARCH, "started"),
        (C.StageName.RESEARCH, "done"),
        (C.StageName.EXTRACT, "started"),
    ]


def test_a_stage_that_falls_back_emits_a_fallback_event_and_the_run_continues() -> None:
    stages = default_search_stages()
    stages.research = ResearchStage(fail=True)
    result, seen = _run(stages)
    assert seen.pairs()[:2] == [(C.StageName.RESEARCH, "started"), (C.StageName.RESEARCH, "fallback")]
    assert [e.stage for e in seen.events if e.status == "started"] == ORDER  # every later stage still had its turn
    assert not result.partial


def test_a_timed_out_stage_emits_a_fallback_event() -> None:
    stuck = _StuckResearch()
    stages = default_search_stages()
    stages.research = stuck
    seen = _Seen()
    runner = PipelineRunner(stages, InMemoryResultStore(), time_limits={C.StageName.RESEARCH: 0.05})
    try:
        runner.run(_request(), search_id=new_search_id(), progress=seen)
    finally:
        stuck.release.set()
    research = seen.of(C.StageName.RESEARCH)
    assert [e.status for e in research] == ["started", "fallback"]
    assert "too long" in research[1].message


def test_a_stage_whose_fallback_fails_emits_failed_and_the_result_is_partial() -> None:
    stages = default_search_stages()
    stages.match = _BrokenMatch()
    result, seen = _run(stages)
    assert seen.pairs()[-2:] == [(C.StageName.MATCH, "started"), (C.StageName.MATCH, "failed")]
    assert {e.stage for e in seen.events} == {C.StageName.RESEARCH, C.StageName.EXTRACT, C.StageName.MATCH}
    assert result.partial


def test_a_skipped_stage_reports_done_with_the_reason() -> None:
    stages = default_search_stages()
    stages.research = ResearchStage(fail=True)  # the fallback finds no sources, so extract has nothing to read
    _, seen = _run(stages)
    extract = seen.of(C.StageName.EXTRACT)
    assert [e.status for e in extract] == ["started", "done"]
    assert "there were no sources to read" in extract[1].message


def test_running_out_of_the_search_limit_ends_partial_with_no_events_for_stages_that_never_ran(
    clock: _JumpingClock,
) -> None:
    stages = default_search_stages()
    stages.research = _Wrapped(stages.research, lambda: setattr(clock, "jumped_s", C.SEARCH_TIME_LIMIT_S + 1))
    result, seen = _run(stages)
    assert result.partial
    assert {e.stage for e in seen.events} == {C.StageName.RESEARCH}


# --- source counts ------------------------------------------------------------------------------


def test_source_counts_are_zero_while_research_is_a_placeholder() -> None:
    _, seen = _run()
    assert all(e.sources_found == NO_SOURCES for e in seen.events)


def test_source_counts_follow_a_real_research_stage() -> None:
    stages = default_search_stages()
    stages.research = _RealResearch()
    _, seen = _run(stages)
    found = {**NO_SOURCES, **Counter(s.type for s in ex.sources())}
    assert seen.events[0].sources_found == NO_SOURCES  # nothing is found before research has run
    assert all(e.sources_found == found for e in seen.events[1:])
    assert seen.events[1].message == f"Found {len(ex.sources())} sources."


# --- the messages -------------------------------------------------------------------------------


def test_every_message_is_plain_and_fits_the_contract() -> None:
    falls_back = default_search_stages()
    falls_back.research = ResearchStage(fail=True)
    fails = default_search_stages()
    fails.match = _BrokenMatch()
    messages = [e.message for stages in (None, falls_back, fails) for e in _run(stages)[1].events]
    for message in messages:
        assert 1 <= len(message) <= 200
        assert message[0].isupper() and message.endswith(".")
        assert "_" not in message  # no code names such as web_search
        assert not any(word in message.lower() for word in ("error", "exception", "crashed", "simulated", "keyerror"))
        sentences = [s for s in re.split(r"[.!?]\s*", message) if s]
        assert all(len(s.split()) <= 16 for s in sentences), message  # short sentences, about Grade 8


# --- cancel -------------------------------------------------------------------------------------


def test_cancel_before_a_stage_stops_the_run_and_saves_nothing() -> None:
    cancel = CancelFlag()
    store = InMemoryResultStore()
    stages = default_search_stages()
    stages.research = _Wrapped(stages.research, cancel.request)  # DELETE arrives while research runs
    seen = _Seen()
    with pytest.raises(SearchCancelled):
        PipelineRunner(stages, store).run(_request(), search_id=new_search_id(), progress=seen, cancel=cancel)
    assert seen.pairs() == [(C.StageName.RESEARCH, "started"), (C.StageName.RESEARCH, "done")]  # it finished its stage
    assert store.results == []


def test_a_run_cancelled_before_it_starts_runs_no_stage() -> None:
    cancel = CancelFlag()
    assert cancel.request() is True
    store = InMemoryResultStore()
    seen = _Seen()
    with pytest.raises(SearchCancelled):
        PipelineRunner(default_search_stages(), store).run(_request(), progress=seen, cancel=cancel)
    assert seen.events == []
    assert store.results == []


def test_cancel_during_the_last_stage_still_saves_nothing() -> None:
    cancel = CancelFlag()
    store = InMemoryResultStore()
    stages = default_search_stages()
    stages.commerce = _Wrapped(stages.commerce, cancel.request)
    seen = _Seen()
    with pytest.raises(SearchCancelled):
        PipelineRunner(stages, store).run(_request(), search_id=new_search_id(), progress=seen, cancel=cancel)
    assert seen.pairs()[-1] == (C.StageName.COMMERCE, "done")
    assert store.results == []


class _SavesWhileCancelled(InMemoryResultStore):
    """A store that tries to cancel the search while it is saving it, as a late DELETE would."""

    def __init__(self, cancel: CancelFlag) -> None:
        super().__init__()
        self.cancel = cancel
        self.refused: list[bool] = []

    def save(self, result: C.SearchResult) -> bool:
        self.refused.append(not self.cancel.request())
        return super().save(result)


def test_a_cancel_once_the_save_has_started_is_refused_and_the_search_is_saved() -> None:
    cancel = CancelFlag()
    store = _SavesWhileCancelled(cancel)
    result = PipelineRunner(default_search_stages(), store).run(_request(), search_id=new_search_id(), cancel=cancel)
    assert store.refused == [True]  # too late: the search is saved, so it must not also be reported as cancelled
    assert result.saved is True
    assert store.results == [result]
    assert cancel.requested is False


class _CountingExtract:
    """Extract that counts the sources it reads, and cancels the search during the first one."""

    def __init__(self, real: Any, cancel: CancelFlag) -> None:
        self.real = real
        self.name = real.name
        self.is_placeholder = real.is_placeholder
        self.cancel = cancel
        self.read = 0

    def run(self, inp: C.ExtractInput, ctx: C.StageContext) -> C.ExtractionResult:
        self.read += 1
        self.cancel.request()
        result: C.ExtractionResult = self.real.run(inp, ctx)
        return result

    def fallback(self, inp: C.ExtractInput, ctx: C.StageContext, error: Exception) -> C.ExtractionResult:
        result: C.ExtractionResult = self.real.fallback(inp, ctx, error)
        return result


def test_a_cancel_during_extract_stops_before_the_next_source() -> None:
    """Read one at a time, the next source never starts. test_pipeline_extract.py has it with several workers."""
    assert len(ex.sources()) > 1  # there would be more to read
    cancel = CancelFlag()
    stages = default_search_stages()
    extract = _CountingExtract(stages.extract, cancel)
    stages.extract = extract
    seen = _Seen()
    with pytest.raises(SearchCancelled):
        PipelineRunner(stages, InMemoryResultStore(), extract_workers=1).run(
            _request(), search_id=new_search_id(), progress=seen, cancel=cancel
        )
    assert extract.read == 1
    assert seen.pairs()[-1] == (C.StageName.EXTRACT, "done")


# --- the callback never breaks a search, and no callback means no change -------------------------


def test_a_callback_that_raises_does_not_stop_the_search(caplog: pytest.LogCaptureFixture) -> None:
    def broken(event: C.ProgressEvent) -> None:
        raise ZeroDivisionError(event.message)

    store = InMemoryResultStore()
    with caplog.at_level(logging.WARNING):
        result = PipelineRunner(default_search_stages(), store).run(
            _request(), search_id=new_search_id(), progress=broken
        )
    assert not result.partial
    assert result.saved is True
    assert store.results == [result]
    assert "ZeroDivisionError" in caplog.text
    assert "Looking for" not in caplog.text  # the error's type only, never its text


def test_with_no_callback_the_runner_behaves_as_before() -> None:
    plain_store = InMemoryResultStore()
    plain = PipelineRunner(default_search_stages(), plain_store).run(_request())
    followed, _ = _run()

    assert [(r.stage, r.status) for r in plain.stage_reports] == [(r.stage, r.status) for r in followed.stage_reports]
    assert all(r.status is C.StageStatus.OK for r in plain.stage_reports)
    assert plain.saved is True
    assert plain_store.results == [plain]
    same = {"products", "decision", "explanations", "commerce", "sources", "coverage_note", "partial", "request"}
    assert plain.model_dump(include=same) == followed.model_dump(include=same)


def test_an_id_that_is_not_a_runs_sends_no_events_and_the_search_finishes(caplog: pytest.LogCaptureFixture) -> None:
    seen = _Seen()
    with caplog.at_level(logging.WARNING):
        result = PipelineRunner(default_search_stages(), InMemoryResultStore()).run(
            _request(), search_id="srch_demo0001", progress=seen
        )
    assert result.search_id == "srch_demo0001"  # an older-style ID still works for the search itself
    assert seen.events == []  # but a progress event needs a run's ID, so none is sent
    assert result.saved is True
    assert "progress not sent: ValidationError" in caplog.text
