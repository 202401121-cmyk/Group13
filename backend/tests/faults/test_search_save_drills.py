"""Database drill for the search save (US-52): a database that never answers cannot hold a finished search back.

A local server accepts connections and never answers, like a stuck or paused database. Each save gives up within
DB_TIMEOUT_SECONDS + 0.5 s and answers False, and after 3 failures the circuit breaker skips the database at once.
"""

from __future__ import annotations

import time
from collections.abc import Iterator

import pytest

from app.contracts import examples as ex
from app.db import DatabaseSettings, PostgresRequestStore
from tests.faults.test_database_drills import SilentServer

TIMEOUT_S = 1.0
FAKE_PASSWORD = "drill-password"  # made up


@pytest.fixture
def stuck_store() -> Iterator[PostgresRequestStore]:
    server = SilentServer()
    url = "postgresql://" + "drill:" + FAKE_PASSWORD + f"@127.0.0.1:{server.port}/shopsense"
    store = PostgresRequestStore(DatabaseSettings(url=url, timeout_s=TIMEOUT_S))
    yield store
    store.close()
    server.close()


@pytest.mark.slow
def test_each_search_save_gives_up_in_time_and_the_breaker_then_skips_at_once(
    stuck_store: PostgresRequestStore,
) -> None:
    result = ex.search_result()
    rubric, dumped = result.request.rubric.model_dump(mode="json"), result.model_dump(mode="json")
    for _ in range(3):
        start = time.monotonic()
        assert stuck_store.save_search(ex.SESSION_ID, rubric, dumped) is False
        assert time.monotonic() - start < TIMEOUT_S + 0.5
    assert stuck_store.breaker.state == "open"
    start = time.monotonic()
    assert stuck_store.save_search(ex.SESSION_ID, rubric, dumped) is False
    assert time.monotonic() - start < 0.1  # skipped, not waited on
