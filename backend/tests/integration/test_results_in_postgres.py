"""A finished search reaches a real Postgres through the runner, the API and the CLI (US-52). Marker `db`.

Same rules as test_requests_table.py, which owns the fixture: it skips with a reason without TEST_DATABASE_URL, fails
with REQUIRE_DB_TESTS=1, refuses a Supabase host, and builds a throwaway database with every migration applied in
order. The cases that need no database are in tests/unit/test_result_saving.py and
tests/faults/test_result_save_faults.py, so they also run on Windows.
"""

from __future__ import annotations

import re
import secrets
import time
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.cli import main as cli_main
from app.contracts import SearchRequest, SearchResult
from app.contracts import examples as ex
from app.db import DatabaseSettings, PostgresRequestStore
from app.main import create_app
from app.pipeline import DatabaseResultStore, PipelineRunner, default_search_stages
from tests.integration import test_requests_table as requests_table

pytestmark = pytest.mark.db

database_url = requests_table.database_url  # the same fixture: a fresh database of its own for this file

QUERY = "best laptop for coding under 70k"


def new_session() -> str:
    return "sess_" + secrets.token_hex(12)  # the format new_session_id() makes (D-26)


def request(session: str) -> SearchRequest:
    return SearchRequest(session_id=session, raw_query=ex.QUERY, rubric=ex.rubric())


def run_search(store: PostgresRequestStore, session: str) -> SearchResult:
    return PipelineRunner(default_search_stages(), DatabaseResultStore(store)).run(request(session))


def stored_results(url: str, session: str) -> list[dict[str, Any]]:
    with psycopg.connect(url) as conn:  # a separate connection sees only what was committed
        rows = conn.execute("select result from public.searches where session_id = %s", (session,)).fetchall()
    return [row[0] for row in rows]


@pytest.fixture
def store(database_url: str) -> Iterator[PostgresRequestStore]:
    built = PostgresRequestStore(DatabaseSettings(url=database_url, timeout_s=2.0))
    yield built
    built.close()


def test_database_on_gives_saved_true_and_one_row_equal_to_the_response(
    store: PostgresRequestStore, database_url: str
) -> None:
    session = new_session()
    result = run_search(store, session)
    assert result.saved is True
    [stored] = stored_results(database_url, session)
    assert stored == result.model_dump(mode="json")
    assert SearchResult.model_validate(stored) == result
    assert stored["saved"] is True


def test_the_api_stores_exactly_what_it_answers(
    store: PostgresRequestStore, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.db.default_request_store", lambda: store)
    session = new_session()
    response = TestClient(create_app({})).post("/api/search", json=request(session).model_dump(mode="json"))
    assert response.status_code == 200
    body = response.json()
    assert body["saved"] is True
    [stored] = stored_results(database_url, session)
    assert SearchResult.model_validate(stored) == SearchResult.model_validate(body)


def test_the_cli_stores_the_request_and_the_search_under_one_session(
    store: PostgresRequestStore, database_url: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("app.db.default_request_store", lambda: store)
    assert cli_main([QUERY]) == 0
    out = capsys.readouterr().out
    found = re.search(r"Saved: yes \(session (sess_\w+)\)", out)
    assert found is not None
    session = found.group(1)
    [stored] = stored_results(database_url, session)
    assert stored["request"]["session_id"] == session
    assert stored["saved"] is True
    assert "This search could not be saved." not in out
    with psycopg.connect(database_url) as conn:
        requests = conn.execute("select count(*) from public.requests where session_id = %s", (session,)).fetchone()
    assert requests == (1,)


def test_a_failed_insert_gives_saved_false_and_no_row(store: PostgresRequestStore, database_url: str) -> None:
    session = new_session()
    with psycopg.connect(database_url, autocommit=True) as admin:
        admin.execute("alter table public.searches rename to searches_hidden")
        try:
            result = run_search(store, session)
        finally:
            admin.execute("alter table public.searches_hidden rename to searches")
    assert result.saved is False
    assert result.decision is not None
    assert stored_results(database_url, session) == []


def test_a_locked_table_gives_saved_false_in_time_and_no_row_lands_later(database_url: str) -> None:
    timeout_s = 0.5
    built = PostgresRequestStore(DatabaseSettings(url=database_url, timeout_s=timeout_s))
    session = new_session()
    try:
        assert built.health() == "ok"  # warm the pool, so the time below is the save alone
        with psycopg.connect(database_url) as blocker:
            blocker.execute("lock table public.searches in access exclusive mode")
            start = time.monotonic()
            result = PipelineRunner(default_search_stages(), DatabaseResultStore(built)).run(request(session))
            assert time.monotonic() - start < timeout_s + 1.0
            blocker.rollback()  # the insert may now go through: it must still not commit
        built._workers.shutdown(wait=True)  # let the worker finish, so the rows below are final
    finally:
        built.close()
    assert result.saved is False
    assert stored_results(database_url, session) == []


def test_with_the_breaker_open_the_result_is_unsaved_and_nothing_reaches_the_table(
    store: PostgresRequestStore, database_url: str
) -> None:
    session = new_session()
    for _ in range(3):
        store.breaker.record_failure()
    assert run_search(store, session).saved is False
    assert store._pool is None
    assert stored_results(database_url, session) == []
