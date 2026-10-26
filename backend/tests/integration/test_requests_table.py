"""The requests table on a real Postgres (US-01). Marker `db`; needs TEST_DATABASE_URL.

Run against a throwaway local Postgres with the same major version as the Supabase project (17), for example:
    docker run -d --name shopsense-pg17 -e POSTGRES_PASSWORD=<local password> -p 127.0.0.1:55432:5432 postgres:17
    TEST_DATABASE_URL=postgresql://postgres:<local password>@127.0.0.1:55432/postgres pytest -m db -rs

Without TEST_DATABASE_URL these tests skip (pytest -rs prints why). With REQUIRE_DB_TESTS=1 a missing or unreachable
database makes them FAIL instead, so CI can never skip them by accident. They never touch a Supabase host: each run
creates its own database on the server, applies every migration in supabase/migrations/ in order, and drops it.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from psycopg import errors, sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app.contracts import examples as ex
from app.db import DatabaseSettings, PostgresRequestStore

pytestmark = pytest.mark.db

MIGRATIONS = Path(__file__).resolve().parents[3] / "supabase" / "migrations"
SESSION = "sess_integration01"


def _unavailable(reason: str) -> None:
    if os.environ.get("REQUIRE_DB_TESTS") == "1":
        pytest.fail(f"REQUIRE_DB_TESTS=1 but {reason}")
    pytest.skip(reason)


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    """A fresh database with every migration applied; dropped afterwards."""
    server_url = os.environ.get("TEST_DATABASE_URL", "").strip()
    if not server_url:
        _unavailable("TEST_DATABASE_URL is not set (see docs/database.md, 'Tests')")
    host = str(conninfo_to_dict(server_url).get("host", ""))
    if "supabase" in host:
        pytest.fail("TEST_DATABASE_URL points at Supabase; use a throwaway local Postgres instead")
    try:
        admin = psycopg.connect(server_url, autocommit=True, connect_timeout=5)
    except psycopg.OperationalError as exc:
        _unavailable(f"TEST_DATABASE_URL cannot be reached ({type(exc).__name__})")
    name = f"shopsense_test_{secrets.token_hex(4)}"
    with admin:
        admin.execute(sql.SQL("create database {}").format(sql.Identifier(name)))
        url = make_conninfo(server_url, dbname=name)
        try:
            with psycopg.connect(url, autocommit=True) as conn:
                for role in ("anon", "authenticated"):  # Supabase has these roles; a plain Postgres does not
                    conn.execute(
                        sql.SQL(
                            "do $$ begin create role {} nologin; exception when duplicate_object then null; end $$"
                        ).format(sql.Identifier(role))
                    )
                for migration in sorted(MIGRATIONS.glob("*.sql")):
                    conn.execute(migration.read_text(encoding="utf-8").encode("utf-8"))
            yield url
        finally:
            admin.execute(sql.SQL("drop database {} with (force)").format(sql.Identifier(name)))


@pytest.fixture
def store(database_url: str) -> Iterator[PostgresRequestStore]:
    built = PostgresRequestStore(DatabaseSettings(url=database_url, timeout_s=2.0))
    yield built
    built.close()


def test_a_saved_request_can_be_read_back(store: PostgresRequestStore, database_url: str) -> None:
    assert store.health() == "ok"
    assert store.save_request(SESSION, ex.QUERY, ex.intent()) == "saved"
    with psycopg.connect(database_url) as conn:
        row = conn.execute(
            "select id, session_id, request_text, intent, intent_source, created_at from public.requests "
            "where session_id = %s",
            (SESSION,),
        ).fetchone()
    assert row is not None
    row_id, session_id, text, intent, source, created_at = row
    assert row_id is not None and created_at is not None
    assert (session_id, text, source) == (SESSION, ex.QUERY, ex.intent().detected_by)
    assert intent == ex.intent().model_dump(mode="json")


def test_an_unclear_request_is_saved_without_an_intent(store: PostgresRequestStore, database_url: str) -> None:
    unclear = ex.unclear_intent()
    assert store.save_request("sess_integration02", unclear.raw_query, unclear) == "saved"
    with psycopg.connect(database_url) as conn:
        row = conn.execute(
            "select intent, intent_source from public.requests where session_id = 'sess_integration02'"
        ).fetchone()
    assert row == (None, unclear.detected_by)


def test_row_level_security_is_on_and_the_data_api_roles_have_no_access(database_url: str) -> None:
    with psycopg.connect(database_url) as conn:
        rls = conn.execute("select relrowsecurity from pg_class where oid = 'public.requests'::regclass").fetchone()
        assert rls == (True,)
        for role in ("anon", "authenticated"):
            for privilege in ("select", "insert", "update", "delete"):
                allowed = conn.execute(
                    "select has_table_privilege(%s, 'public.requests', %s)", (role, privilege)
                ).fetchone()
                assert allowed == (False,), (role, privilege)


def test_the_table_enforces_the_length_limit_and_the_session_id_format(database_url: str) -> None:
    insert = "insert into public.requests (session_id, request_text) values (%s, %s)"
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute(insert, ("sess_integration03", "x" * 1000))  # the longest allowed request
        with pytest.raises(errors.CheckViolation):
            conn.execute(insert, ("sess_integration03", "x" * 1001))
        with pytest.raises(errors.CheckViolation):
            conn.execute(insert, ("not-a-session", "ok"))
        with pytest.raises(errors.CheckViolation):
            conn.execute(
                "insert into public.requests (session_id, request_text, intent_source) values (%s, %s, %s)",
                ("sess_integration03", "ok", "keywords"),
            )


def test_each_pooled_connection_has_the_statement_timeout(store: PostgresRequestStore) -> None:
    assert store.health() == "ok"
    with store._get_pool().connection() as conn:
        assert conn.execute("show statement_timeout").fetchone() == ("2s",)
