"""US-30: the account reads share the request store's time limit and circuit breaker. No database needed.

The Postgres store runs each read on a worker thread and waits at most DB_TIMEOUT_SECONDS, like a save (build rule 10).
Reads and saves share one breaker: 3 failures of either kind skip the database for both. A deleted account's row is
not a database failure. tests/integration/test_accounts_in_postgres.py runs the reads on a real Postgres.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator

import pytest

from app.contracts import examples as ex
from app.db import AccountsUnavailable, DatabaseSettings, PostgresRequestStore, UserGone

NOWHERE = "postgresql://nobody@127.0.0.1:1/none"  # nothing listens on port 1


@pytest.fixture
def store() -> Iterator[PostgresRequestStore]:
    built = PostgresRequestStore(DatabaseSettings(url=NOWHERE, timeout_s=0.5))
    yield built
    built.close()


def _fails() -> None:
    raise ConnectionError("down")


def test_a_slow_read_answers_unavailable_within_the_time_limit(store: PostgresRequestStore) -> None:
    started = time.monotonic()
    with pytest.raises(AccountsUnavailable):
        store._read("saved searches", lambda: time.sleep(3))
    assert time.monotonic() - started < 1.5


def test_three_failed_reads_skip_the_database_for_saves_too(store: PostgresRequestStore) -> None:
    for _ in range(3):
        with pytest.raises(AccountsUnavailable):
            store._read("profile", _fails)
    assert store.breaker.state == "open"
    started = time.monotonic()
    assert store.save_request(ex.SESSION_ID, ex.QUERY, ex.intent()) == "failed"
    assert store.health() == "down"
    assert time.monotonic() - started < 0.2  # skipped at once, not tried


def test_three_failed_saves_make_the_reads_unavailable_at_once(store: PostgresRequestStore) -> None:
    for _ in range(3):
        assert store._guarded("search save", _fails) is False
    started = time.monotonic()
    with pytest.raises(AccountsUnavailable):
        store.ensure_profile(uuid.uuid4())
    assert time.monotonic() - started < 0.2


def test_a_deleted_account_is_not_a_database_failure(store: PostgresRequestStore) -> None:
    def gone() -> None:
        raise UserGone("no such user")

    for _ in range(5):
        assert store._guarded("search save", gone) is False
        with pytest.raises(UserGone):
            store._read("profile", gone)
    assert store.breaker.state == "closed"


def test_a_closed_store_makes_the_reads_unavailable(store: PostgresRequestStore) -> None:
    store.close()
    with pytest.raises(AccountsUnavailable):
        store.saved_search(uuid.uuid4(), uuid.uuid4())
