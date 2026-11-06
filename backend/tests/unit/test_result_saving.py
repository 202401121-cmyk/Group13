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
from app.db import InMemoryRequestStore, NullRequestStore, RequestStore
from app.main import create_app
from app.pipeline import DatabaseResultStore, InMemoryResultStore, PipelineRunner, default_search_stages
from app.research import ResearchStage

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


def test_a_committed_row_gives_saved_true_and_holds_exactly_the_response() -> None:
    database = InMemoryRequestStore()
    result = run_search(database)
    assert result.saved is True
    [row] = database.searches
    assert row.session_id == ex.SESSION_ID
    assert json.loads(row.rubric) == ex.rubric().model_dump(mode="json")
    assert json.loads(row.result) == result.model_dump(mode="json")


def test_with_the_database_off_saved_is_false_and_the_search_still_finishes() -> None:
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
    assert C.SearchResult.model_validate(json.loads(row.result)) == C.SearchResult.model_validate(body)


def test_the_api_says_not_saved_with_the_database_off(monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_the_cli_says_so_when_the_database_is_off(capsys: pytest.CaptureFixture[str]) -> None:
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


# --- the time the save is given (D-21) -----------------------------------------------------------


class _NotesItsContext(ResearchStage):
    def __init__(self) -> None:
        super().__init__()
        self.time_limit_s: float | None = None

    def run(self, inp: C.ResearchInput, ctx: C.StageContext) -> C.ResearchResult:
        self.time_limit_s = ctx.time_limit_s
        return super().run(inp, ctx)


def test_the_stages_leave_the_store_its_time_out_of_the_search_limit() -> None:
    stages = default_search_stages()
    stages.research = research = _NotesItsContext()
    PipelineRunner(stages, DatabaseResultStore(NullRequestStore(), time_limit_s=2.0)).run(request())
    assert research.time_limit_s == C.SEARCH_TIME_LIMIT_S - 2.0


def test_a_store_that_gives_no_time_limit_takes_nothing_from_the_stages() -> None:
    stages = default_search_stages()
    stages.research = research = _NotesItsContext()
    PipelineRunner(stages, InMemoryResultStore()).run(request())
    assert research.time_limit_s == C.SEARCH_TIME_LIMIT_S


def test_the_time_set_aside_is_the_database_time_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DB_TIMEOUT_SECONDS", raising=False)
    assert DatabaseResultStore(NullRequestStore()).time_limit_s == 2.0
    monkeypatch.setenv("DB_TIMEOUT_SECONDS", "5")
    assert DatabaseResultStore(NullRequestStore()).time_limit_s == 5.0
