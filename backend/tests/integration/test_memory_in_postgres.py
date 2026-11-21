"""US-06 on a real Postgres with pgvector (S5-1's public.preferences). Marker `db`; needs TEST_DATABASE_URL.

Saving and recall through the Postgres store: the same preference saved again updates its row; user B never sees user
A's rows (zero cross-user leaks, DECIDED 6); a brand left out with Always comes back in another category; the three
limits from S5-1's hand-over (NUL in text, a weight too small for real, a weight read back exactly); and a database
that is down gives an empty list, never an error. Made-up rows only, under the model-free word embedder.
"""

from __future__ import annotations

import socket
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import psycopg
import pytest

from app.contracts import CriteriaInput, Exclusion, HardRule, Intent, InterviewState
from app.criteria import fallback_set
from app.db import DatabaseSettings, PostgresRequestStore
from app.interview.rubric_builder import build_rubric
from app.memory import MemoryService, MemorySettings
from app.memory.embedder import MemoryUnavailable
from app.memory.lasting import Lasting
from app.memory.store import PostgresPreferenceStore
from tests.integration import test_requests_table as requests_table
from tests.unit.memory.fakes import WordEmbedder, ready_holder
from tests.unit.memory.test_text_and_lasting import answered

pytestmark = pytest.mark.db

database_url = requests_table.database_url  # a fresh database of its own for this file
DIMENSIONS = 384
CATEGORIES = ("laptop", "headphones", "earbuds", "smartphone", "air_fryer", "smartwatch")


@pytest.fixture
def store(database_url: str) -> Iterator[PostgresRequestStore]:
    built = PostgresRequestStore(DatabaseSettings(url=database_url, timeout_s=2.0))
    yield built
    built.close()


@pytest.fixture
def prefs(store: PostgresRequestStore) -> PostgresPreferenceStore:
    return PostgresPreferenceStore(store)


def new_user(database_url: str) -> uuid.UUID:
    user = uuid.uuid4()
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute("insert into auth.users (id) values (%s)", (user,))
    return user


def state_for(category: str = "laptop", **intent: Any) -> InterviewState:
    criteria = fallback_set(CriteriaInput(category=category, category_label=category.replace("_", " ").capitalize()))
    made = Intent(
        raw_query=f"a made-up {category} request",
        category=category,
        category_label=category.capitalize(),
        detected_by="rules",
        **intent,
    )
    return InterviewState(session_id="sess_memorydbtest1", intent=made, criteria=criteria)


def memory_on(prefs: Any) -> MemoryService:
    return MemoryService(MemorySettings(), ready_holder(WordEmbedder(DIMENSIONS)), prefs)


def rows_of(database_url: str, user: uuid.UUID) -> list[tuple[Any, ...]]:
    with psycopg.connect(database_url) as conn:
        return conn.execute(
            "select kind, category, criterion_key, text, weight, created_at, updated_at, "
            "extensions.vector_dims(embedding) "
            "from public.preferences where user_id = %s order by kind, criterion_key",
            (user,),
        ).fetchall()


def test_a_saved_weight_is_recalled_in_a_new_session(database_url: str, prefs: PostgresPreferenceStore) -> None:
    user = new_user(database_url)
    memory = memory_on(prefs)
    assert (
        memory.remember(
            user, answered(state_for(), "battery_life", 8.0), build_rubric(answered(state_for(), "battery_life", 8.0))
        )
        == 1
    )
    [(kind, category, key, text, weight, _, _, dims)] = rows_of(database_url, user)
    assert (kind, category, key, weight, dims) == ("weight", "laptop", "battery_life", 8.0, DIMENSIONS)
    assert text.endswith(": high importance")
    [item] = memory.recall(user, state_for())
    assert (item.preference.criterion_key, item.preference.weight) == ("battery_life", 8.0)


def test_saving_the_same_preference_again_updates_its_row_and_its_time(
    database_url: str, prefs: PostgresPreferenceStore
) -> None:
    user = new_user(database_url)
    memory = memory_on(prefs)
    first = answered(state_for(), "battery_life", 8.0)
    memory.remember(user, first, build_rubric(first))
    [before] = rows_of(database_url, user)
    again = answered(state_for(), "battery_life", 2.0)
    memory.remember(user, again, build_rubric(again))
    [after] = rows_of(database_url, user)
    assert after[4] == 2.0 and after[3].endswith("low importance")
    assert after[5] == before[5] and after[6] >= before[6]  # created_at kept, updated_at moved on


def test_user_b_never_sees_user_as_rows(database_url: str, prefs: PostgresPreferenceStore) -> None:
    """Zero cross-user leaks: whatever A saved, in every category, B's recall finds none of it, and the store's read for
    B returns no row."""
    a, b = new_user(database_url), new_user(database_url)
    memory = memory_on(prefs)
    for category in CATEGORIES:
        state = state_for(category, exclusions=[Exclusion(kind="brand", value=f"Acme {category}", always=True)])
        key = state.criteria.criteria[0].key
        filled = answered(state, key, 8.0)
        assert memory.remember(a, filled, build_rubric(filled)) >= 2
    leaks = 0
    for category in CATEGORIES:
        leaks += len(prefs.candidates(b, category)) + len(memory.recall(b, state_for(category)))
        assert len(memory.recall(a, state_for(category))) >= 1  # A's own are there
    assert leaks == 0


def test_a_brand_left_out_with_always_fills_another_category(database_url: str, prefs: PostgresPreferenceStore) -> None:
    user = new_user(database_url)
    memory = memory_on(prefs)
    laptop = state_for("laptop", exclusions=[Exclusion(kind="brand", value="Acme", always=True)])
    memory.remember(user, laptop, build_rubric(laptop))
    [item] = memory.recall(user, state_for("headphones"))
    assert item.exclusion == Exclusion(kind="brand", value="Acme", always=True)
    assert rows_of(database_url, user)[0][1] is None  # no category: it holds for every one


def test_a_laptop_weight_never_fills_a_headphone_criterion(database_url: str, prefs: PostgresPreferenceStore) -> None:
    user = new_user(database_url)
    memory = memory_on(prefs)
    laptop = answered(state_for("laptop"), "battery_life", 8.0)  # headphones have a battery_life criterion too
    memory.remember(user, laptop, build_rubric(laptop))
    assert memory.recall(user, state_for("headphones")) == []


def test_nul_in_text_is_saved_without_it(database_url: str, prefs: PostgresPreferenceStore) -> None:
    user = new_user(database_url)
    rule = HardRule(attribute="ram_gb", operator=">=", value=16, unit="GB", source_text="at least\x00 16 GB of RAM")
    state = state_for(hard_rules=[rule], exclusions=[Exclusion(kind="brand", value="Ac\x00me", always=True)])
    assert memory_on(prefs).remember(user, state, build_rubric(state)) == 2
    texts = sorted(row[3] for row in rows_of(database_url, user))
    assert texts == ["Must have: at least 16 GB of RAM", "Never Acme"]


def test_a_weight_too_small_for_real_is_saved_as_0(database_url: str, prefs: PostgresPreferenceStore) -> None:
    user = new_user(database_url)
    state = state_for()
    item = Lasting("weight", "laptop", "battery_life", "Battery life: 0 out of 10", 1e-46, None, False, "Battery life")
    from app.memory.text import round_weight

    tiny = Lasting(**{**item.__dict__, "weight": round_weight(1e-46)})
    assert prefs.save(user, [(tiny, WordEmbedder(DIMENSIONS).embed(["Battery life"])[0])]) == 1
    assert rows_of(database_url, user)[0][4] == 0.0
    with pytest.raises(MemoryUnavailable):  # the unrounded value is refused by the database, never saved half-way
        prefs.save(user, [(item, WordEmbedder(DIMENSIONS).embed(["Battery life"])[0])])
    assert state.criteria.category == "laptop"


def test_a_weight_reads_back_exactly_as_saved(database_url: str, prefs: PostgresPreferenceStore) -> None:
    """Text mode: a stored real 7.3 reads back as 7.3, not 7.300000190734863."""
    user = new_user(database_url)
    memory = memory_on(prefs)
    state = answered(state_for(), "battery_life", 7.3)
    memory.remember(user, state, build_rubric(state))
    [read] = prefs.candidates(user, "laptop")
    assert read.weight == 7.3
    [item] = memory.recall(user, state_for())
    assert item.preference.weight == 7.3


def _closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_the_database_down_gives_an_empty_list_and_a_failed_save_never_raises() -> None:
    down = PostgresRequestStore(
        DatabaseSettings(url=f"postgresql://nobody:x@127.0.0.1:{_closed_port()}/none", timeout_s=1.0)
    )
    try:
        memory = memory_on(PostgresPreferenceStore(down))
        user = uuid.uuid4()
        state = answered(state_for(), "battery_life", 8.0)
        started = datetime.now(UTC)
        assert memory.recall(user, state_for()) == []
        assert memory.remember(user, state, build_rubric(state)) == 0
        assert (datetime.now(UTC) - started).total_seconds() < 5
    finally:
        down.close()


def test_every_statement_names_the_user() -> None:
    """The store never reads or writes a row without `user_id = %s`, as well as row level security."""
    from app.memory import store

    statements = [*store.FIND.values(), store.UPDATE, store.CANDIDATES]
    assert all("user_id = %s" in s for s in statements)
    assert "user_id" in store.INSERT
