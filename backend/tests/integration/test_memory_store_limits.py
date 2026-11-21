"""US-06's Postgres store at its limits. Marker `db`; needs TEST_DATABASE_URL.

A save past its time limit is rolled back, so "not saved" is true; a deleted account's save is refused without counting
as a database failure; and memory's failures open its own circuit breaker, never the one request and search saves
share."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest

from app.db import DatabaseSettings, PostgresRequestStore
from app.interview.rubric_builder import build_rubric
from app.memory.embedder import MemoryUnavailable
from app.memory.lasting import Lasting
from app.memory.store import PostgresPreferenceStore
from tests.integration import test_requests_table as requests_table
from tests.integration.test_memory_in_postgres import DIMENSIONS, _closed_port, memory_on, new_user, rows_of, state_for
from tests.unit.memory.fakes import WordEmbedder
from tests.unit.memory.test_text_and_lasting import answered

pytestmark = pytest.mark.db

database_url = requests_table.database_url  # a fresh database of its own for this file


@pytest.fixture
def store(database_url: str) -> Iterator[PostgresRequestStore]:
    built = PostgresRequestStore(DatabaseSettings(url=database_url, timeout_s=2.0))
    yield built
    built.close()


def test_a_save_past_its_time_limit_is_rolled_back(database_url: str, store: PostgresRequestStore) -> None:
    user = new_user(database_url)
    late = PostgresPreferenceStore(store, save_timeout_s=0.0)
    state = answered(state_for(), "battery_life", 8.0)
    assert memory_on(late).remember(user, state, build_rubric(state)) == 0
    assert rows_of(database_url, user) == []  # reported as not saved, and really not saved


def test_a_deleted_accounts_save_is_refused_without_counting_as_a_database_failure(store: PostgresRequestStore) -> None:
    prefs = PostgresPreferenceStore(store)
    gone = uuid.uuid4()  # never in auth.users
    item = Lasting("weight", "laptop", "battery_life", "Battery life: high importance", 8.0, None, False, "x")
    for _ in range(4):
        with pytest.raises(MemoryUnavailable, match="no longer exists"):
            prefs.save(gone, [(item, WordEmbedder(DIMENSIONS).embed(["x"])[0])])
    assert prefs.breaker.state == "closed"


def test_memorys_failures_never_trip_the_breaker_request_saves_use() -> None:
    url = f"postgresql://nobody:x@127.0.0.1:{_closed_port()}/none"
    down = PostgresRequestStore(DatabaseSettings(url=url, timeout_s=1.0))
    try:
        prefs = PostgresPreferenceStore(down)
        for _ in range(3):
            with pytest.raises(MemoryUnavailable):
                prefs.candidates(uuid.uuid4(), "laptop")
        assert prefs.breaker.state == "open"
        assert down.breaker.state == "closed"
    finally:
        down.close()
