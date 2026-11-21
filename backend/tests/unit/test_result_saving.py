"""Saving each finished search at the end (US-52, F-3): `saved` is true only when the row was committed.

Everything here runs without a database. tests/faults/test_result_save_faults.py forces the failures, and
tests/integration/test_results_in_postgres.py runs the same path on a real Postgres.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.contracts as C
from app.cli import main as cli_main
from app.contracts import examples as ex
from app.db import CircuitBreaker, InMemoryRequestStore, NullRequestStore, RequestStore
from app.main import create_app
from app.pipeline import (
    SAVE_GRACE_S,
    SAVING,
    DatabaseResultStore,
    InMemoryResultStore,
    PipelineRunner,
    _Progress,
    default_search_stages,
)
from app.research import ResearchStage
from app.research.retention import REDDIT_TEXT_NOT_KEPT, without_reddit_text
from tests.fakes.live_run import use_live_shaped_run

QUERY = "best laptop for coding under 70k"
NOT_SAVED_LINE = "This search could not be saved."


def request() -> C.SearchRequest:
    return C.SearchRequest(session_id=ex.SESSION_ID, raw_query=ex.QUERY, rubric=ex.rubric())


def run_search(database: RequestStore) -> C.SearchResult:
    return PipelineRunner(default_search_stages(), DatabaseResultStore(database)).run(request())


def api_search(monkeypatch: pytest.MonkeyPatch, database: RequestStore) -> dict[str, Any]:
    monkeypatch.setattr("app.db.default_request_store", lambda: database)
    response = TestClient(create_app({})).post("/api/search", json=request().model_dump(mode="json"))
    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    return body


# --- the runner ----------------------------------------------------------------------------------


def test_a_committed_row_gives_saved_true_and_holds_the_response_with_no_reddit_text() -> None:
    """The one difference from the response: Reddit data may be kept at most 24 hours, and a row stays (D-53)."""
    database = InMemoryRequestStore()
    result = run_search(database)
    assert result.saved is True
    [row] = database.searches
    assert row.session_id == ex.SESSION_ID
    assert json.loads(row.rubric) == ex.rubric().model_dump(mode="json")
    assert json.loads(row.result) == without_reddit_text(result.model_dump(mode="json"))
    assert any(s.type is C.SourceType.REDDIT for s in result.sources)  # so this really does check the difference


def test_with_the_database_off_saved_is_false_and_the_search_still_finishes(monkeypatch: pytest.MonkeyPatch) -> None:
    use_live_shaped_run(monkeypatch)  # a run whose real decide ranks something (S3-10b)
    result = run_search(NullRequestStore())
    assert result.saved is False
    assert result.decision is not None and result.decision.ranked


def test_a_database_that_refuses_keeps_nothing_and_gives_saved_false() -> None:
    database = InMemoryRequestStore(fail=True)
    assert run_search(database).saved is False
    assert database.searches == []


class _Refuses:
    """Answers "not saved", and keeps what it was offered so a test can compare it with the response."""

    def __init__(self) -> None:
        self.offered: list[C.SearchResult] = []

    def save(self, result: C.SearchResult) -> bool:
        self.offered.append(result)
        return False


def test_a_result_that_was_not_saved_differs_from_the_stored_copy_only_in_saved() -> None:
    store = _Refuses()
    returned = PipelineRunner(default_search_stages(), store).run(request())
    [offered] = store.offered
    assert offered.saved is True  # what the row would have said
    assert returned.saved is False
    assert returned.model_dump(exclude={"saved"}) == offered.model_dump(exclude={"saved"})


def test_every_search_of_a_session_gets_its_own_row() -> None:
    database = InMemoryRequestStore()
    run_search(database)
    run_search(database)
    assert [row.session_id for row in database.searches] == [ex.SESSION_ID, ex.SESSION_ID]


def test_a_guest_search_is_saved_under_the_session_id_alone() -> None:
    """D-36: every finished search is saved, guests included, and a guest has no account to link it to."""
    database = InMemoryRequestStore()
    assert request().user_id is None
    assert run_search(database).saved is True
    [row] = database.searches
    assert json.loads(row.result)["request"]["user_id"] is None


# --- the API -------------------------------------------------------------------------------------


def test_the_api_saves_the_finished_search(monkeypatch: pytest.MonkeyPatch) -> None:
    database = InMemoryRequestStore()
    body = api_search(monkeypatch, database)
    assert body["saved"] is True
    [row] = database.searches
    assert C.SearchResult.model_validate(json.loads(row.result)) == C.SearchResult.model_validate(
        without_reddit_text(body)
    )


def test_the_api_answer_keeps_its_reddit_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the saved row loses Reddit's words: the shopper's answer is never stripped."""
    database = InMemoryRequestStore()
    body = api_search(monkeypatch, database)
    reddit = [s for s in body["sources"] if s["type"] == "reddit"]
    assert reddit
    assert all(s["title"] and s["title"] != REDDIT_TEXT_NOT_KEPT for s in reddit)
    assert all(s["text"] != REDDIT_TEXT_NOT_KEPT for s in reddit)
    assert all(s["title"] not in database.searches[0].result for s in reddit)


def test_the_api_says_not_saved_with_the_database_off(monkeypatch: pytest.MonkeyPatch) -> None:
    use_live_shaped_run(monkeypatch)  # a run whose real decide ranks something (S3-10b)
    body = api_search(monkeypatch, NullRequestStore())
    assert body["saved"] is False
    assert body["decision"]["ranked"]


# --- the CLI -------------------------------------------------------------------------------------


def test_the_cli_saves_the_search_under_the_session_of_its_request(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    database = InMemoryRequestStore()
    monkeypatch.setattr("app.db.default_request_store", lambda: database)
    assert cli_main([QUERY]) == 0
    out = capsys.readouterr().out
    [request_row] = database.rows
    [search_row] = database.searches
    assert search_row.session_id == request_row.session_id
    assert json.loads(search_row.result)["saved"] is True
    assert NOT_SAVED_LINE not in out


def test_the_cli_says_so_when_the_database_is_off(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    use_live_shaped_run(monkeypatch)  # a run whose real decide ranks something (S3-10b)
    assert cli_main([QUERY]) == 0  # tests/conftest.py keeps the database off
    out = capsys.readouterr().out
    assert "1. Nova Book 14" in out
    assert NOT_SAVED_LINE in out


def test_no_db_saves_no_search(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    database = InMemoryRequestStore()
    monkeypatch.setattr("app.db.default_request_store", lambda: database)
    assert cli_main([QUERY, "--no-db"]) == 0
    assert database.searches == []
    assert NOT_SAVED_LINE in capsys.readouterr().out


# --- the time the save is given (P-38, replacing D-21) -------------------------------------------


class _NotesItsContext(ResearchStage):
    def __init__(self) -> None:
        super().__init__()
        self.time_limit_s: float | None = None

    def run(self, inp: C.ResearchInput, ctx: C.StageContext) -> C.ResearchResult:
        self.time_limit_s = ctx.time_limit_s
        return super().run(inp, ctx)


def test_the_stages_have_the_whole_search_limit_and_the_save_comes_after_it() -> None:
    stages = default_search_stages()
    stages.research = research = _NotesItsContext()
    runner = PipelineRunner(stages, DatabaseResultStore(NullRequestStore(), time_limit_s=15.0))
    runner.run(request())
    assert research.time_limit_s == C.SEARCH_TIME_LIMIT_S
    assert runner.save_wait_s == 15.0 + SAVE_GRACE_S


def test_a_store_that_gives_no_time_limit_takes_nothing_from_the_stages() -> None:
    stages = default_search_stages()
    stages.research = research = _NotesItsContext()
    PipelineRunner(stages, InMemoryResultStore()).run(request())
    assert research.time_limit_s == C.SEARCH_TIME_LIMIT_S


def test_the_save_is_given_db_save_timeout_seconds_and_never_db_timeout_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DB_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("DB_SAVE_TIMEOUT_SECONDS", raising=False)
    assert DatabaseResultStore(NullRequestStore()).time_limit_s == 15.0
    monkeypatch.setenv("DB_TIMEOUT_SECONDS", "5")
    assert DatabaseResultStore(NullRequestStore()).time_limit_s == 15.0
    monkeypatch.setenv("DB_SAVE_TIMEOUT_SECONDS", "12")
    assert DatabaseResultStore(NullRequestStore()).time_limit_s == 12.0


def test_a_store_with_no_limit_of_its_own_is_waited_for_the_default_save_limit() -> None:
    assert PipelineRunner(default_search_stages(), InMemoryResultStore()).save_wait_s == 15.0 + SAVE_GRACE_S


# --- while it saves, the progress stream says so once (P-38) -------------------------------------


RUN_ID = "srch_" + "a" * 24


def _run(store: object) -> tuple[list[C.ProgressEvent], C.SearchResult]:
    heard: list[C.ProgressEvent] = []
    runner = PipelineRunner(default_search_stages(), store)  # type: ignore[arg-type]
    return heard, runner.run(request(), search_id=RUN_ID, progress=heard.append)


def _events(store: object) -> list[C.ProgressEvent]:
    return _run(store)[0]


def test_while_it_saves_the_progress_stream_says_so_once_on_the_last_line_that_ran_ok() -> None:
    heard, result = _run(DatabaseResultStore(InMemoryRequestStore(), time_limit_s=15.0))
    saying = [e for e in heard if e.message == SAVING]
    assert len(saying) == 1
    assert heard[-1] is saying[0]  # last: after every stage has finished
    ran_ok = [r.stage for r in result.stage_reports if r.status is C.StageStatus.OK]
    assert (saying[0].stage, saying[0].status) == (ran_ok[-1], "done")
    assert [e.event_id for e in heard] == list(range(1, len(heard) + 1))
    assert result.saved is True


def _report(stage: C.StageName, status: C.StageStatus) -> C.StageReport:
    reason = None if status is C.StageStatus.OK else "a made-up reason"
    return C.StageReport(stage=stage, status=status, duration_ms=1, fallback_reason=reason)


def _saying(reports: list[C.StageReport]) -> list[C.ProgressEvent]:
    heard: list[C.ProgressEvent] = []
    _Progress(RUN_ID, heard.append).saving(reports, {})
    return heard


def test_a_stage_that_fell_back_failed_or_was_skipped_keeps_its_own_words() -> None:
    reports = [
        _report(C.StageName.RESEARCH, C.StageStatus.OK),
        _report(C.StageName.EXTRACT, C.StageStatus.OK),
        _report(C.StageName.MATCH, C.StageStatus.FALLBACK),
        _report(C.StageName.VERIFY, C.StageStatus.TIMEOUT),
        _report(C.StageName.ENRICH, C.StageStatus.SKIPPED),
        _report(C.StageName.DECIDE, C.StageStatus.FAILED),
    ]
    (said,) = _saying(reports)
    assert (said.stage, said.status, said.message) == (C.StageName.EXTRACT, "done", SAVING)


def test_with_no_stage_that_ran_ok_no_save_is_announced() -> None:
    failed = [_report(C.StageName.RESEARCH, C.StageStatus.FAILED), _report(C.StageName.EXTRACT, C.StageStatus.SKIPPED)]
    assert _saying(failed) == []
    assert _saying([]) == []


class _Tripped(InMemoryRequestStore):
    """A store whose breaker is open: its save answers False at once."""

    def __init__(self) -> None:
        super().__init__()
        self.breaker = CircuitBreaker(max_failures=1)
        self.breaker.record_failure()


def test_while_the_breaker_skips_the_database_no_save_is_announced() -> None:
    store = DatabaseResultStore(_Tripped(), time_limit_s=15.0)
    assert store.saves is False
    assert all(e.message != SAVING for e in _events(store))
    assert DatabaseResultStore(InMemoryRequestStore(), time_limit_s=15.0).saves is True


class _NotesItsTime(InMemoryRequestStore):
    def __init__(self) -> None:
        super().__init__()
        self.given: list[float | None] = []

    def save_search(self, *args: Any, within_s: float | None = None, **kwargs: Any) -> bool:
        self.given.append(within_s)
        return super().save_search(*args, within_s=within_s, **kwargs)


def test_the_save_hands_the_store_what_is_left_of_its_limit_after_building_the_row() -> None:
    requests = _NotesItsTime()
    store = DatabaseResultStore(requests, time_limit_s=15.0)
    assert store.save(ex.search_result()) is True
    (left,) = requests.given
    assert left is not None and 14.0 < left <= 15.0


def test_a_save_whose_row_took_all_its_time_to_build_is_not_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = _NotesItsTime()
    store = DatabaseResultStore(requests, time_limit_s=0.0)
    assert store.save(ex.search_result()) is False
    assert requests.given == []


@pytest.mark.parametrize("store", [DatabaseResultStore(NullRequestStore()), InMemoryResultStore()])
def test_with_nothing_to_write_no_save_is_announced(store: object) -> None:
    assert all(e.message != SAVING for e in _events(store))
