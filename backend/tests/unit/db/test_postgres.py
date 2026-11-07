"""The Postgres request store without a database (US-01): it fails fast, never raises, and never leaks the URL."""

from __future__ import annotations

import logging
import socket
import time
from collections.abc import Iterator

import pytest

from app.contracts import examples as ex
from app.db import DatabaseSettings, NullRequestStore, PostgresRequestStore
from app.db import default_request_store as real_default_request_store  # the conftest swaps the module attribute

SESSION = "sess_abc123def456"
FAKE_PASSWORD = "SuperSecret123"  # made up


def closed_port() -> int:
    """A local port with nothing listening on it."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
    return port


def fake_url(port: int) -> str:
    return "postgresql://" + "shopper:" + FAKE_PASSWORD + f"@127.0.0.1:{port}/shopsense"


@pytest.fixture
def store() -> Iterator[PostgresRequestStore]:
    built = PostgresRequestStore(DatabaseSettings(url=fake_url(closed_port()), timeout_s=0.5))
    yield built
    built.close()


def test_nothing_connects_until_the_first_save() -> None:
    built = PostgresRequestStore(DatabaseSettings(url=fake_url(closed_port()), timeout_s=0.5))
    assert built._pool is None  # start-up never waits for the database
    built.close()


def test_a_store_needs_a_url() -> None:
    with pytest.raises(ValueError, match="needs DATABASE_URL"):
        PostgresRequestStore(DatabaseSettings())


def test_an_unreachable_database_fails_within_the_time_limit(store: PostgresRequestStore) -> None:
    start = time.monotonic()
    assert store.save_request(SESSION, ex.QUERY, ex.intent()) == "failed"
    assert time.monotonic() - start < 0.5 + 0.5


@pytest.mark.slow
def test_three_failures_open_the_breaker_and_later_saves_fail_at_once(store: PostgresRequestStore) -> None:
    for _ in range(3):
        assert store.save_request(SESSION, ex.QUERY, ex.intent()) == "failed"
    assert store.breaker.state == "open"
    start = time.monotonic()
    assert store.save_request(SESSION, ex.QUERY, ex.intent()) == "failed"
    assert store.health() == "down"
    assert time.monotonic() - start < 0.1  # skipped, not tried


def test_health_says_down_when_the_database_cannot_be_reached(store: PostgresRequestStore) -> None:
    assert store.health() == "down"


def test_a_row_that_does_not_fit_fails_without_touching_the_database(store: PostgresRequestStore) -> None:
    assert store.save_request("not-a-session", ex.QUERY, None) == "failed"
    assert store.save_request(SESSION, "x" * 1001, None) == "failed"
    assert store.breaker.state == "closed"  # not counted as a database failure
    assert store._pool is None


@pytest.mark.slow
def test_logs_show_the_host_and_error_type_but_never_the_url(
    store: PostgresRequestStore, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        for _ in range(3):
            store.save_request(SESSION, "my private request text", ex.intent())
        store.health()
    assert "host=127.0.0.1" in caplog.text
    assert "skipping the database for 30 s" in caplog.text
    for secret in (FAKE_PASSWORD, "postgresql://", "shopper", "my private request text"):
        assert secret not in caplog.text
    assert FAKE_PASSWORD not in repr(store)


def test_the_default_store_is_off_without_a_url_and_postgres_with_one(monkeypatch: pytest.MonkeyPatch) -> None:
    real_default_request_store.cache_clear()
    monkeypatch.setattr("app.db.load_settings", lambda: DatabaseSettings())
    assert isinstance(real_default_request_store(), NullRequestStore)
    real_default_request_store.cache_clear()
    monkeypatch.setattr("app.db.load_settings", lambda: DatabaseSettings(url=fake_url(closed_port())))
    built = real_default_request_store()
    assert isinstance(built, PostgresRequestStore)
    assert built is real_default_request_store()  # one store per process
    built.close()
    real_default_request_store.cache_clear()


def test_a_closed_store_fails_without_raising(store: PostgresRequestStore) -> None:
    store.close()
    assert store.save_request(SESSION, ex.QUERY, None) == "failed"
    assert store.health() == "down"
