"""Database drills (US-01): a search always finishes when the database is down or stuck. No internet needed.

Drill 1: DATABASE_URL points at a local port with nothing listening. The CLI, the API intake and the search
still finish, and health says "down".
Drill 2: a local server accepts connections and never answers, like a stuck or paused database. Each save gives
up within DB_TIMEOUT_SECONDS + 0.5 s, and after 3 failures the circuit breaker skips the database at once.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

import app.contracts as C
from app.api.presearch import get_session_store
from app.api.sessions import InMemorySessionStore
from app.cli import main as cli_main
from app.contracts import examples as ex
from app.db import DatabaseSettings, PostgresRequestStore
from app.db import default_request_store as real_default_request_store  # tests/conftest.py swaps the attribute
from app.main import create_app
from tests.fakes.live_run import use_live_shaped_run

TIMEOUT_S = 1.0
FAKE_PASSWORD = "drill-password"  # made up


def _url(port: int) -> str:
    return "postgresql://" + "drill:" + FAKE_PASSWORD + f"@127.0.0.1:{port}/shopsense"


def _closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
    return port


class SilentServer:
    """Accepts TCP connections and never sends a byte back."""

    def __init__(self) -> None:
        self._sock = socket.socket()
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(16)
        self.port: int = self._sock.getsockname()[1]
        self._accepted: list[socket.socket] = []
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while True:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            self._accepted.append(conn)

    def close(self) -> None:
        self._sock.close()
        for conn in self._accepted:
            conn.close()


@pytest.fixture
def database_at_closed_port(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The real default store, built from the environment like in production, aimed at nothing."""
    monkeypatch.setenv("DATABASE_URL", _url(_closed_port()))
    monkeypatch.setenv("DB_TIMEOUT_SECONDS", str(TIMEOUT_S))
    real_default_request_store.cache_clear()
    monkeypatch.setattr("app.db.default_request_store", real_default_request_store)
    yield
    real_default_request_store().close()
    real_default_request_store.cache_clear()


@pytest.mark.usefixtures("database_at_closed_port")
@pytest.mark.slow
def test_drill_1_the_cli_finishes_when_the_database_is_down(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    use_live_shaped_run(monkeypatch)  # a run whose real decide ranks something, so every stage runs (S3-10b)
    start = time.monotonic()
    assert cli_main(["best laptop for coding under 70k"]) == 0
    out = capsys.readouterr().out
    assert "Saved: no, the database could not be reached. The search goes on." in out
    assert "1. Nova Book 14" in out  # the shortlist is still printed
    assert time.monotonic() - start < 30  # the save itself is bounded; the rest is the normal search
    assert FAKE_PASSWORD not in out


@pytest.mark.usefixtures("database_at_closed_port")
@pytest.mark.slow
def test_drill_1_the_api_intake_search_and_health_finish_when_the_database_is_down() -> None:
    app = create_app()
    sessions = InMemorySessionStore()
    app.dependency_overrides[get_session_store] = lambda: sessions
    with TestClient(app) as client:
        start = time.monotonic()
        intake = client.post("/api/presearch/intake", json={"text": "best laptop for coding under 70k"})
        assert intake.status_code == 200
        assert time.monotonic() - start < TIMEOUT_S + 0.5
        session = client.get(f"/api/presearch/{intake.json()['session_id']}").json()
        assert session["request_saved"] == "failed"

        request = C.SearchRequest(session_id=ex.SESSION_ID, raw_query=ex.QUERY, rubric=ex.rubric())
        search = client.post("/api/search", json=request.model_dump(mode="json"))
        assert search.status_code == 200
        C.SearchResult.model_validate(search.json())

        health = client.get("/api/health").json()
        assert health["status"] == "ok"
        assert health["database"] == "down"
        assert FAKE_PASSWORD not in str(health)


@pytest.fixture
def stuck_store() -> Iterator[PostgresRequestStore]:
    server = SilentServer()
    store = PostgresRequestStore(DatabaseSettings(url=_url(server.port), timeout_s=TIMEOUT_S))
    yield store
    store.close()
    server.close()


@pytest.mark.slow
def test_drill_2_each_save_gives_up_in_time_and_the_breaker_then_skips_at_once(
    stuck_store: PostgresRequestStore,
) -> None:
    for _ in range(3):
        start = time.monotonic()
        assert stuck_store.save_request(ex.SESSION_ID, ex.QUERY, ex.intent()) == "failed"
        assert time.monotonic() - start < TIMEOUT_S + 0.5
    assert stuck_store.breaker.state == "open"
    start = time.monotonic()
    assert stuck_store.save_request(ex.SESSION_ID, ex.QUERY, ex.intent()) == "failed"
    assert stuck_store.health() == "down"
    assert time.monotonic() - start < 0.1  # skipped, not waited on


@pytest.mark.slow
def test_drill_2_health_gives_up_in_time_too(stuck_store: PostgresRequestStore) -> None:
    start = time.monotonic()
    assert stuck_store.health() == "down"
    assert time.monotonic() - start < TIMEOUT_S + 0.5
