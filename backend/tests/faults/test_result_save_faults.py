"""Fault tests (US-52): every way the save can fail ends in saved=False, on time, and never in an error.

The save is the last thing a search does, so it must not cost the shopper the result (build rule 10) or the
150 seconds (D-21). No database is needed: the failures come from a closed port, a server that never answers, the
circuit breaker, and fake databases.
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

import app.contracts as C
from app.contracts import examples as ex
from app.db import DatabaseSettings, InMemoryRequestStore, JsonObject, NullRequestStore, PostgresRequestStore
from app.main import create_app
from app.pipeline import DatabaseResultStore, PipelineRunner, default_search_stages
from app.research import ResearchStage
from tests.faults.test_database_drills import SilentServer
from tests.faults.test_pipeline_fallback import SEARCH_STAGES, _AlwaysFails, _BrokenMatch

FAKE_PASSWORD = "drill-password"  # made up
SECRET_IN_ERROR = "could not reach " + "postgresql://" + "drill:" + FAKE_PASSWORD + "@db.example/shopsense"
DB_TIMEOUT_S = 2.0
SLACK_S = 1.0  # room for a slow machine; every limit below is still far under what a hang would take


def request() -> C.SearchRequest:
    return C.SearchRequest(session_id=ex.SESSION_ID, raw_query=ex.QUERY, rubric=ex.rubric())


def database_url(port: int) -> str:
    return "postgresql://" + "drill:" + FAKE_PASSWORD + f"@127.0.0.1:{port}/shopsense"


def closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
    return port


@pytest.fixture
def down_database() -> Iterator[PostgresRequestStore]:
    built = PostgresRequestStore(DatabaseSettings(url=database_url(closed_port()), timeout_s=DB_TIMEOUT_S))
    yield built
    built.close()


# --- the database is down, stuck or skipped ------------------------------------------------------


@pytest.mark.slow
def test_a_closed_port_gives_saved_false_within_the_database_time_limit(down_database: PostgresRequestStore) -> None:
    start = time.monotonic()
    result = PipelineRunner(default_search_stages(), DatabaseResultStore(down_database)).run(request())
    assert time.monotonic() - start < DB_TIMEOUT_S + SLACK_S  # the stubs take milliseconds
    assert result.saved is False
    assert result.decision is not None and result.decision.ranked


def test_the_breaker_open_gives_saved_false_at_once(down_database: PostgresRequestStore) -> None:
    for _ in range(3):
        down_database.breaker.record_failure()
    start = time.monotonic()
    result = PipelineRunner(default_search_stages(), DatabaseResultStore(down_database)).run(request())
    assert time.monotonic() - start < DB_TIMEOUT_S / 2  # skipped, not tried
    assert result.saved is False
    assert down_database._pool is None  # nothing was even opened


@pytest.mark.slow
def test_a_database_that_never_answers_gives_saved_false_in_time() -> None:
    server = SilentServer()
    timeout_s = 1.0
    built = PostgresRequestStore(DatabaseSettings(url=database_url(server.port), timeout_s=timeout_s))
    try:
        start = time.monotonic()
        result = PipelineRunner(default_search_stages(), DatabaseResultStore(built)).run(request())
        assert time.monotonic() - start < timeout_s + SLACK_S
    finally:
        built.close()
        server.close()
    assert result.saved is False


# --- the save itself fails -----------------------------------------------------------------------


class _BrokenDatabase(NullRequestStore):
    """Breaks the promise that save_search never raises, with the worst error text there could be."""

    def save_search(self, session_id: str, rubric: JsonObject, result: JsonObject) -> bool:
        raise ConnectionError(SECRET_IN_ERROR)


def test_a_save_that_raises_only_marks_the_result_unsaved(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        result = PipelineRunner(default_search_stages(), DatabaseResultStore(_BrokenDatabase())).run(request())
    assert result.saved is False
    assert result.decision is not None
    assert "could not save search" in caplog.text
    assert "ConnectionError" in caplog.text
    assert FAKE_PASSWORD not in caplog.text  # only the error's type reaches the log


def test_a_save_that_fails_shows_no_error_in_the_api_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.db.default_request_store", _BrokenDatabase)
    response = TestClient(create_app({})).post("/api/search", json=request().model_dump(mode="json"))
    assert response.status_code == 200
    body = response.json()
    assert body["saved"] is False
    assert "detail" not in body
    C.SearchResult.model_validate(body)
    assert FAKE_PASSWORD not in response.text
    assert "ConnectionError" not in response.text


@pytest.mark.parametrize("field", SEARCH_STAGES)
def test_any_failing_stage_with_the_database_off_gives_a_valid_unsaved_result(field: str) -> None:
    """With the database off the saved=False copy is built on every search, so it must never fail validation."""
    stages = default_search_stages()
    setattr(stages, field, _AlwaysFails(getattr(stages, field)))
    result = PipelineRunner(stages, DatabaseResultStore(NullRequestStore())).run(request())
    assert result.saved is False
    assert C.SearchResult.model_validate_json(result.model_dump_json()) == result


def test_a_partial_result_with_the_database_off_gives_a_valid_unsaved_result() -> None:
    stages = default_search_stages()
    stages.match = _BrokenMatch()
    result = PipelineRunner(stages, DatabaseResultStore(NullRequestStore())).run(request())
    assert result.partial
    assert result.saved is False
    assert C.SearchResult.model_validate_json(result.model_dump_json()) == result


# --- the 150-second limit holds with a slow save (D-21) ------------------------------------------


class _StuckDatabase(NullRequestStore):
    """A save that does not come back, whatever time limit it was given. Commits only if a test lets it go."""

    def __init__(self) -> None:
        self.release = threading.Event()

    def save_search(self, session_id: str, rubric: JsonObject, result: JsonObject) -> bool:
        self.release.wait(timeout=30)
        return True


@pytest.fixture
def stuck_database() -> Iterator[_StuckDatabase]:
    database = _StuckDatabase()
    yield database
    database.release.set()  # let the background thread finish


@pytest.mark.slow
def test_a_save_that_hangs_cannot_hold_the_search_past_its_limit(
    stuck_database: _StuckDatabase, caplog: pytest.LogCaptureFixture
) -> None:
    limit_s = 1.0
    runner = PipelineRunner(
        default_search_stages(), DatabaseResultStore(stuck_database, time_limit_s=0.2), search_time_limit_s=limit_s
    )
    start = time.monotonic()
    with caplog.at_level(logging.WARNING):
        result = runner.run(request())
    assert time.monotonic() - start < limit_s + SLACK_S / 2  # the fake would hold it for 30 s
    assert result.saved is False
    assert result.decision is not None
    assert "ran past the time limit" in caplog.text


class _SlowResearch(ResearchStage):
    def run(self, inp: C.ResearchInput, ctx: C.StageContext) -> C.ResearchResult:
        time.sleep(1.2)  # in a background thread: the test only waits for the small limits below
        return super().run(inp, ctx)


def test_a_search_that_runs_out_of_time_still_gets_its_save() -> None:
    database = InMemoryRequestStore()
    stages = default_search_stages()
    stages.research = _SlowResearch()
    limit_s = 1.0
    runner = PipelineRunner(stages, DatabaseResultStore(database, time_limit_s=0.7), search_time_limit_s=limit_s)
    start = time.monotonic()
    result = runner.run(request())
    assert time.monotonic() - start < limit_s + SLACK_S / 2
    assert result.partial
    assert result.saved is True
    assert len(database.searches) == 1


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


class _TakesTime(ResearchStage):
    def __init__(self, clock: _JumpingClock, seconds: float) -> None:
        super().__init__()
        self.clock = clock
        self.seconds = seconds

    def run(self, inp: C.ResearchInput, ctx: C.StageContext) -> C.ResearchResult:
        self.clock.jumped_s += self.seconds
        return super().run(inp, ctx)


def test_at_full_length_the_stages_stop_and_the_save_runs_inside_the_150_seconds(clock: _JumpingClock) -> None:
    database = InMemoryRequestStore()
    stages = default_search_stages()
    stages.research = _TakesTime(clock, seconds=C.SEARCH_TIME_LIMIT_S - 1)  # past the stages' 148, inside the 150
    result = PipelineRunner(stages, DatabaseResultStore(database, time_limit_s=2.0)).run(request())
    assert result.partial
    assert {r.status for r in result.stage_reports[1:]} == {C.StageStatus.SKIPPED}
    assert result.saved is True
    assert len(database.searches) == 1


def test_a_run_already_past_the_limit_is_not_saved_and_the_store_is_not_called(clock: _JumpingClock) -> None:
    database = InMemoryRequestStore()
    stages = default_search_stages()
    stages.research = _TakesTime(clock, seconds=C.SEARCH_TIME_LIMIT_S + 1)
    result = PipelineRunner(stages, DatabaseResultStore(database, time_limit_s=2.0)).run(request())
    assert result.partial
    assert result.saved is False
    assert database.searches == []
