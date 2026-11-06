"""The searches table and the search save on a real Postgres (US-52). Marker `db`; needs TEST_DATABASE_URL.

Same rules as test_requests_table.py, which owns the fixture: it skips with a reason without TEST_DATABASE_URL, fails
with REQUIRE_DB_TESTS=1, refuses a Supabase host, and builds a throwaway database with every migration applied in
order. The cases that need no database (database off, closed port) are in tests/unit/db/test_search_saving.py, so
they also run on Windows.
"""

from __future__ import annotations

import json
import logging
import math
import re
import secrets
import time
from collections.abc import Iterator
from datetime import timedelta, timezone

import psycopg
import pytest
from psycopg import errors
from psycopg.conninfo import conninfo_to_dict

from app.contracts import Rubric, SearchRequest, SearchResult
from app.contracts import examples as ex
from app.db import DatabaseSettings, PostgresRequestStore, search_row
from app.pipeline import PipelineRunner, default_search_stages
from tests.integration import test_requests_table as requests_table
from tests.unit.db.test_search_saving import StoreBackedResults

pytestmark = pytest.mark.db

database_url = requests_table.database_url  # the same fixture: a fresh database of its own for this file
MIGRATIONS = requests_table.MIGRATIONS
AWKWARD_TEXT = 'Budget ₹70,000 😀 "quoted" back\\slash \\u0000 line\nbreak'
ROLES = ("anon", "authenticated")
PRIVILEGES = ("select", "insert", "update", "delete")


def new_session() -> str:
    return "sess_" + secrets.token_hex(12)  # the format new_session_id() makes (D-26)


def save(store: PostgresRequestStore, session_id: str, result: SearchResult | None = None) -> bool:
    chosen = result or ex.search_result()
    return store.save_search(session_id, chosen.request.rubric.model_dump(mode="json"), chosen.model_dump(mode="json"))


def count_rows(url: str, session_id: str) -> int:
    with psycopg.connect(url) as conn:
        row = conn.execute("select count(*) from public.searches where session_id = %s", (session_id,)).fetchone()
    assert row is not None
    count: int = row[0]
    return count


def database_lines(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.name.startswith("app.db")]


def awkward_result() -> SearchResult:
    """The values most likely to change on a trip through JSON: an offset, microseconds, emoji, escapes."""
    base = ex.search_result()
    india = timezone(timedelta(hours=5, minutes=30))
    return SearchResult.model_validate(
        {
            **base.model_dump(),
            "started_at": base.started_at.astimezone(india),
            "finished_at": base.finished_at.astimezone(india) + timedelta(microseconds=123456),
            "coverage_note": AWKWARD_TEXT,
        }
    )


@pytest.fixture
def store(database_url: str) -> Iterator[PostgresRequestStore]:
    built = PostgresRequestStore(DatabaseSettings(url=database_url, timeout_s=2.0))
    yield built
    built.close()


# --- the migration -------------------------------------------------------------------------------


def test_every_migration_applies_in_order_on_a_blank_database(database_url: str) -> None:
    names = [path.name for path in sorted(MIGRATIONS.glob("*.sql"))]
    versions = [name.split("_", 1)[0] for name in names]
    assert all(re.fullmatch(r"\d{14}", version) for version in versions)
    assert len(set(versions)) == len(versions)
    requests_at = next(i for i, name in enumerate(names) if name.endswith("_create_requests.sql"))
    searches_at = next(i for i, name in enumerate(names) if name.endswith("_create_searches.sql"))
    assert requests_at < searches_at
    with psycopg.connect(database_url) as conn:  # the fixture stopped at the first migration that failed
        tables = conn.execute("select to_regclass('public.requests'), to_regclass('public.searches')").fetchone()
    assert tables == ("requests", "searches")


def test_the_table_has_the_agreed_columns_and_a_plain_index_on_session_id(database_url: str) -> None:
    with psycopg.connect(database_url) as conn:
        columns = conn.execute(
            "select column_name, data_type, is_nullable, column_default from information_schema.columns "
            "where table_schema = 'public' and table_name = 'searches' order by ordinal_position"
        ).fetchall()
        index = conn.execute(
            "select indexdef from pg_indexes where schemaname = 'public' and tablename = 'searches' "
            "and indexdef like '%(session_id)%'"
        ).fetchall()
        foreign_keys = conn.execute(
            "select count(*) from pg_constraint where conrelid = 'public.searches'::regclass and contype = 'f'"
        ).fetchone()
    assert columns == [
        ("id", "uuid", "NO", "gen_random_uuid()"),
        ("session_id", "text", "NO", None),
        ("rubric", "jsonb", "NO", None),
        ("result", "jsonb", "NO", None),
        ("created_at", "timestamp with time zone", "NO", "now()"),
    ]
    [(definition,)] = index
    assert "UNIQUE" not in definition  # one session can run more than one search
    assert foreign_keys == (0,)  # a request can fail to save while its search saves later


def test_row_level_security_is_on_with_no_policies(database_url: str) -> None:
    with psycopg.connect(database_url) as conn:
        rls = conn.execute("select relrowsecurity from pg_class where oid = 'public.searches'::regclass").fetchone()
        policies = conn.execute(
            "select count(*) from pg_policies where schemaname = 'public' and tablename = 'searches'"
        ).fetchone()
    assert rls == (True,)
    assert policies == (0,)


def test_the_data_api_roles_have_no_access(database_url: str) -> None:
    with psycopg.connect(database_url) as conn:
        for role in ROLES:
            for privilege in PRIVILEGES:
                allowed = conn.execute(
                    "select has_table_privilege(%s, 'public.searches', %s)", (role, privilege)
                ).fetchone()
                assert allowed == (False,), (role, privilege)


def test_the_revoke_beats_the_default_grants_supabase_gives_new_tables(database_url: str) -> None:
    """A plain Postgres grants these roles nothing, so only this copy of Supabase's set-up can catch a lost revoke."""
    [migration] = MIGRATIONS.glob("*_create_searches.sql")
    with psycopg.connect(database_url) as conn:  # one transaction, rolled back below: DDL undoes cleanly in Postgres
        conn.execute("drop table public.searches")
        conn.execute("alter default privileges in schema public grant all on tables to anon, authenticated")
        conn.execute("create table public.default_grant_control (id int)")
        control = conn.execute("select has_table_privilege('anon', 'public.default_grant_control', 'select')")
        assert control.fetchone() == (True,)  # the default grant is really in force

        conn.execute(migration.read_text(encoding="utf-8").encode("utf-8"))
        for role in ROLES:
            for privilege in PRIVILEGES:
                allowed = conn.execute(
                    "select has_table_privilege(%s, 'public.searches', %s)", (role, privilege)
                ).fetchone()
                assert allowed == (False,), (role, privilege)
        conn.rollback()


@pytest.mark.parametrize(
    "session_id",
    ["not-a-session", "sess_x", "sess_abcdef\n", "sess_has-dash1", "sess_" + "a" * 41],
)
def test_a_bad_session_id_is_rejected_by_the_check(database_url: str, session_id: str) -> None:
    with psycopg.connect(database_url, autocommit=True) as conn, pytest.raises(errors.CheckViolation):
        conn.execute("insert into public.searches (session_id, rubric, result) values (%s, '{}', '{}')", (session_id,))


def test_the_table_accepts_the_same_session_ids_as_public_requests(database_url: str) -> None:
    insert = "insert into public.searches (session_id, rubric, result) values (%s, '{}', '{}')"
    with psycopg.connect(database_url, autocommit=True) as conn:
        for session_id in (new_session(), "sess_abcdef", "sess_" + "a" * 40, ex.SESSION_ID):
            conn.execute(insert, (session_id,))


@pytest.mark.parametrize("column", ["rubric", "result"])
def test_a_search_needs_both_its_rubric_and_its_result(database_url: str, column: str) -> None:
    values = {"rubric": "'{}'", "result": "'{}'", column: "null"}
    statement = (
        f"insert into public.searches (session_id, rubric, result) values (%s, {values['rubric']}, {values['result']})"
    )
    with psycopg.connect(database_url, autocommit=True) as conn, pytest.raises(errors.NotNullViolation):
        conn.execute(statement.encode("utf-8"), (new_session(),))


# --- the save ------------------------------------------------------------------------------------


def test_a_saved_search_reads_back_with_the_same_rubric_and_result(
    store: PostgresRequestStore, database_url: str
) -> None:
    result = ex.search_result()
    session = new_session()
    assert save(store, session, result) is True
    with psycopg.connect(database_url) as conn:  # a separate connection sees only what was committed
        row = conn.execute(
            "select id, session_id, rubric, result, created_at from public.searches where session_id = %s", (session,)
        ).fetchone()
    assert row is not None
    row_id, session_id, rubric, stored, created_at = row
    assert row_id is not None and session_id == session
    assert created_at.utcoffset() is not None
    assert rubric == result.request.rubric.model_dump(mode="json")
    assert stored == result.model_dump(mode="json")
    assert Rubric.model_validate(rubric) == result.request.rubric
    assert SearchResult.model_validate(stored) == result


def test_offsets_microseconds_urls_and_awkward_text_survive_exactly(
    store: PostgresRequestStore, database_url: str
) -> None:
    result = awkward_result()
    session = new_session()
    assert save(store, session, result) is True
    with psycopg.connect(database_url) as conn:
        row = conn.execute("select result from public.searches where session_id = %s", (session,)).fetchone()
    assert row is not None
    [stored] = row
    assert stored == result.model_dump(mode="json")
    back = SearchResult.model_validate(stored)
    assert back == result
    assert back.started_at.utcoffset() == timedelta(hours=5, minutes=30)  # == alone compares only the instant
    assert back.finished_at.microsecond == result.finished_at.microsecond
    assert back.coverage_note == AWKWARD_TEXT
    assert [str(source.url) for source in back.sources] == [str(source.url) for source in result.sources]
    assert back.commerce is not None and result.commerce is not None
    assert back.commerce.no_offer == result.commerce.no_offer


def test_a_real_pipeline_result_saves_and_reads_back_equal(store: PostgresRequestStore, database_url: str) -> None:
    session = new_session()
    request = SearchRequest(session_id=session, raw_query=ex.QUERY, rubric=ex.rubric())
    result = PipelineRunner(default_search_stages(), StoreBackedResults(store)).run(request)
    assert result.saved is True
    with psycopg.connect(database_url) as conn:
        row = conn.execute("select result from public.searches where session_id = %s", (session,)).fetchone()
    assert row is not None
    assert SearchResult.model_validate(row[0]) == result


def test_one_session_can_save_more_than_one_search(store: PostgresRequestStore, database_url: str) -> None:
    session = new_session()
    assert save(store, session) is True
    assert save(store, session) is True
    assert count_rows(database_url, session) == 2


def test_a_search_saves_with_no_request_row_for_its_session(store: PostgresRequestStore, database_url: str) -> None:
    session = new_session()
    assert save(store, session) is True
    with psycopg.connect(database_url) as conn:
        requests = conn.execute("select count(*) from public.requests where session_id = %s", (session,)).fetchone()
    assert requests == (0,)


# --- when the save cannot happen -----------------------------------------------------------------


def test_a_failed_insert_answers_false_with_one_clean_line(
    store: PostgresRequestStore, database_url: str, caplog: pytest.LogCaptureFixture
) -> None:
    session = new_session()
    with psycopg.connect(database_url, autocommit=True) as admin:
        admin.execute("alter table public.searches rename to searches_hidden")
        try:
            with caplog.at_level(logging.DEBUG):
                assert save(store, session) is False
        finally:
            admin.execute("alter table public.searches_hidden rename to searches")
    [line] = database_lines(caplog)
    assert re.fullmatch(r"database search save failed \(host=\S+\): UndefinedTable", line.getMessage())
    assert count_rows(database_url, session) == 0
    password = str(conninfo_to_dict(database_url).get("password"))
    # The messages, not caplog.text: that adds each logger's name, and CI's password is the word in "app.db.postgres".
    logged = " ".join(record.getMessage() for record in caplog.records)
    for secret in (password, "postgresql://", session):
        assert secret not in logged


def test_with_the_breaker_open_nothing_reaches_the_database(
    store: PostgresRequestStore, database_url: str, caplog: pytest.LogCaptureFixture
) -> None:
    session = new_session()
    for _ in range(3):
        store.breaker.record_failure()
    assert store.breaker.state == "open"
    start = time.monotonic()
    with caplog.at_level(logging.DEBUG):
        assert save(store, session) is False
    assert time.monotonic() - start < 0.1  # skipped, not tried
    assert store._pool is None
    [line] = database_lines(caplog)
    assert "circuit breaker is open" in line.getMessage()
    assert count_rows(database_url, session) == 0


def test_a_save_works_again_once_the_breaker_lets_a_trial_through(
    database_url: str,
) -> None:
    built = PostgresRequestStore(DatabaseSettings(url=database_url, timeout_s=2.0))
    built.breaker.cooldown_s = 0.0
    session = new_session()
    try:
        for _ in range(3):
            built.breaker.record_failure()
        assert built.breaker.state == "half_open"
        assert save(built, session) is True
        assert built.breaker.state == "closed"
    finally:
        built.close()
    assert count_rows(database_url, session) == 1


def test_a_locked_table_answers_false_in_time_and_no_row_lands_later(
    database_url: str, caplog: pytest.LogCaptureFixture
) -> None:
    timeout_s = 0.5
    built = PostgresRequestStore(DatabaseSettings(url=database_url, timeout_s=timeout_s))
    session = new_session()
    try:
        assert built.health() == "ok"  # warm the pool, so the time below is the save alone
        with psycopg.connect(database_url) as blocker:
            blocker.execute("lock table public.searches in access exclusive mode")
            start = time.monotonic()
            with caplog.at_level(logging.DEBUG):
                assert save(built, session) is False
            assert time.monotonic() - start < timeout_s + 0.5
            blocker.rollback()  # the insert may now go through: it must still not commit
        built._workers.shutdown(wait=True)  # let the worker finish, so the count below is final
    finally:
        built.close()
    assert count_rows(database_url, session) == 0
    [line] = database_lines(caplog)
    assert "search save failed" in line.getMessage()


def test_an_insert_that_finishes_after_the_caller_gave_up_is_rolled_back(
    store: PostgresRequestStore, database_url: str
) -> None:
    result = ex.search_result()
    session = new_session()
    row = search_row(session, result.request.rubric.model_dump(mode="json"), result.model_dump(mode="json"))
    with pytest.raises(TimeoutError):
        store._insert_search(row, deadline=time.monotonic() - 1)
    assert count_rows(database_url, session) == 0


def test_jsonb_refuses_what_the_row_check_refuses_so_it_must_be_checked_first(database_url: str) -> None:
    with psycopg.connect(database_url, autocommit=True) as conn:
        with pytest.raises(errors.UntranslatableCharacter):
            conn.execute("select %s::jsonb", (json.dumps({"quote": "a\x00b"}),))
        with pytest.raises(errors.InvalidTextRepresentation):
            conn.execute("select %s::jsonb", (json.dumps({"value": math.nan}),))
        kept = conn.execute("select %s::jsonb", (json.dumps({"quote": "\\u0000"}),)).fetchone()
    assert kept == ({"quote": "\\u0000"},)  # plain text that only looks like the escape is fine
