"""The alias store (US-15, DR-06, D-28): aliases are kept in memory always, and in Postgres when the database is up.

The cases that need no database run here, so they also run on Windows. The table itself is tested on a real Postgres
in tests/integration/test_product_aliases_table.py.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator

import pytest

from app.db import (
    CircuitBreaker,
    DatabaseSettings,
    InMemoryAliasStore,
    InMemoryRequestStore,
    NullRequestStore,
    PostgresAliasStore,
    PostgresRequestStore,
    alias_store_for,
    shared_memory_aliases,
)
from tests.unit.db.test_search_saving import closed_port

FAKE_PASSWORD = "not-a-real-password"  # a made-up login for a port nothing listens on
TIMEOUT_S = 0.5


@pytest.fixture
def down() -> Iterator[PostgresRequestStore]:
    url = "postgresql://" + "shopper:" + FAKE_PASSWORD + f"@127.0.0.1:{closed_port()}/shopsense"
    built = PostgresRequestStore(DatabaseSettings(url=url, timeout_s=TIMEOUT_S))
    yield built
    built.close()


def test_memory_store_saves_and_loads_by_category() -> None:
    store = InMemoryAliasStore()
    assert store.save("headphones", {"xm5": "prd_sony_wh1000xm5"}) is True
    assert store.load("headphones") == {"xm5": "prd_sony_wh1000xm5"}
    assert store.load("laptop") == {}
    loaded = store.load("headphones")
    loaded["xm4"] = "prd_other"
    assert "xm4" not in store.load("headphones")  # a copy: a caller can't change what is kept


def test_the_first_product_id_for_an_alias_is_kept() -> None:
    store = InMemoryAliasStore()
    store.save("headphones", {"xm5": "prd_sony_wh1000xm5"})
    store.save("headphones", {"xm5": "prd_sony_wf1000xm5", "1000xm5": "prd_sony_wh1000xm5"})
    assert store.load("headphones") == {"xm5": "prd_sony_wh1000xm5", "1000xm5": "prd_sony_wh1000xm5"}


def test_aliases_that_do_not_fit_are_not_kept() -> None:
    store = InMemoryAliasStore()
    store.save("headphones", {"x" * 81: "prd_long_one", "": "prd_empty", "ok": "not-an-id", "xm5": "prd_sony_xm5"})
    store.save("Not A Category!", {"xm5": "prd_sony_xm5"})
    assert store.load("headphones") == {"xm5": "prd_sony_xm5"}
    assert store.load("Not A Category!") == {}


def test_with_the_database_down_aliases_come_from_memory_and_nothing_raises(
    down: PostgresRequestStore, caplog: pytest.LogCaptureFixture
) -> None:
    store = PostgresAliasStore(down, InMemoryAliasStore())
    started = time.monotonic()
    store.prefetch("headphones")
    assert store.save("headphones", {"xm5": "prd_sony_wh1000xm5"}) is True  # kept in memory at once ...
    assert store.load("headphones") == {"xm5": "prd_sony_wh1000xm5"}
    assert time.monotonic() - started < TIMEOUT_S / 2  # ... and nothing waited for the database
    assert store.settle(10 * TIMEOUT_S)  # the background read and write gave up in time, and raised nothing
    assert store.load("headphones") == {"xm5": "prd_sony_wh1000xm5"}
    lines = [r.getMessage() for r in caplog.records if r.name.startswith("app.db")]
    assert lines and all(FAKE_PASSWORD not in line for line in lines)


def test_a_search_never_waits_for_a_database_that_does_not_answer(down: PostgresRequestStore) -> None:
    store = PostgresAliasStore(down, InMemoryAliasStore())
    store.prefetch("headphones")
    started = time.monotonic()
    for _ in range(20):
        store.load("headphones")
        store.save("headphones", {"xm5": "prd_sony_wh1000xm5"})
    assert time.monotonic() - started < TIMEOUT_S / 2
    assert store.settle(30 * TIMEOUT_S)  # and the background work gave up in time, too


def test_an_open_breaker_skips_the_database() -> None:
    breaker = CircuitBreaker(max_failures=1, cooldown_s=60)
    breaker.record_failure()
    url = "postgresql://" + "shopper:" + FAKE_PASSWORD + f"@127.0.0.1:{closed_port()}/shopsense"
    requests = PostgresRequestStore(DatabaseSettings(url=url, timeout_s=TIMEOUT_S), breaker=breaker)
    try:
        store = PostgresAliasStore(requests, InMemoryAliasStore())
        started = time.monotonic()
        store.prefetch("headphones")
        assert store.save("headphones", {"xm5": "prd_sony_xm5"}) is True
        assert store.settle(TIMEOUT_S)
        assert time.monotonic() - started < TIMEOUT_S  # no connection was even tried
        assert requests._pool is None
        assert store.load("headphones") == {"xm5": "prd_sony_xm5"}
    finally:
        requests.close()


def test_alias_store_for_picks_postgres_only_with_a_postgres_request_store(down: PostgresRequestStore) -> None:
    assert isinstance(alias_store_for(down), PostgresAliasStore)
    assert alias_store_for(NullRequestStore()) is shared_memory_aliases()
    assert alias_store_for(InMemoryRequestStore()) is shared_memory_aliases()
    assert alias_store_for(None) is shared_memory_aliases()


def test_a_missing_table_is_not_a_database_failure(caplog: pytest.LogCaptureFixture) -> None:
    """Before the migration is applied, the table is missing: aliases stay in memory, and the breaker stays shut, so
    request and search saves (which share it) are never skipped because of aliases."""
    from psycopg import errors

    class NoTable(PostgresAliasStore):
        def _select(self, category: str) -> dict[str, str]:
            raise errors.UndefinedTable("relation public.product_aliases does not exist")

    breaker = CircuitBreaker(max_failures=1, cooldown_s=60)
    requests = PostgresRequestStore(DatabaseSettings(url="postgresql://localhost/x", timeout_s=TIMEOUT_S), breaker)
    try:
        store = NoTable(requests, InMemoryAliasStore())
        with caplog.at_level(logging.WARNING, logger="app.db"):
            store.prefetch("headphones")
            assert store.settle(5.0)
            store.prefetch("headphones")  # the table is known to be missing: nothing more is tried
            store.save("headphones", {"xm5": "prd_sony_xm5"})
            assert store.settle(5.0)
        assert store.load("headphones") == {"xm5": "prd_sony_xm5"}
        assert breaker.state == "closed"
        assert [r.getMessage() for r in caplog.records].count(
            "product_aliases table is missing: aliases are kept in memory until the migration is applied"
        ) == 1
    finally:
        requests.close()


def test_one_postgres_alias_store_serves_every_search_of_a_request_store(down: PostgresRequestStore) -> None:
    """So a missing table is found once, not once per search, and alias work never takes both database workers."""
    assert alias_store_for(down) is alias_store_for(down)
