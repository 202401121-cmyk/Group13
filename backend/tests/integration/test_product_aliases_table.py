"""The product_aliases table and the Postgres alias store on a real Postgres (US-15, DR-06). Marker `db`.

Same rules as test_requests_table.py, which owns the fixture: it skips with a reason without TEST_DATABASE_URL, fails
with REQUIRE_DB_TESTS=1, refuses a Supabase host, and builds a throwaway database with every migration applied in
order. The cases that need no database (database down, breaker open, table missing) are in
tests/unit/db/test_aliases.py.
"""

from __future__ import annotations

from collections.abc import Iterator

import psycopg
import pytest
from psycopg import errors

from app.db import DatabaseSettings, InMemoryAliasStore, PostgresAliasStore, PostgresRequestStore
from tests.integration import test_requests_table as requests_table

pytestmark = pytest.mark.db

database_url = requests_table.database_url  # the same fixture: a fresh database of its own for this file
MIGRATIONS = requests_table.MIGRATIONS
ROLES = ("anon", "authenticated")
PRIVILEGES = ("select", "insert", "update", "delete")


@pytest.fixture
def aliases(database_url: str) -> Iterator[PostgresAliasStore]:
    requests = PostgresRequestStore(DatabaseSettings(url=database_url, timeout_s=5.0))
    yield PostgresAliasStore(requests, InMemoryAliasStore())
    requests.close()


def test_the_migration_applies_and_sorts_after_create_searches(database_url: str) -> None:
    names = [path.name for path in sorted(MIGRATIONS.glob("*.sql"))]
    searches_at = next(i for i, name in enumerate(names) if name.endswith("_create_searches.sql"))
    aliases_at = next(i for i, name in enumerate(names) if name.endswith("_create_product_aliases.sql"))
    assert searches_at < aliases_at
    with psycopg.connect(database_url) as conn:
        columns = conn.execute(
            "select column_name, data_type, is_nullable, column_default from information_schema.columns "
            "where table_schema = 'public' and table_name = 'product_aliases' order by ordinal_position"
        ).fetchall()
        key = conn.execute(
            "select pg_get_constraintdef(oid) from pg_constraint "
            "where conrelid = 'public.product_aliases'::regclass and contype = 'p'"
        ).fetchone()
    assert columns == [
        ("alias", "text", "NO", None),
        ("product_id", "text", "NO", None),
        ("category", "text", "NO", None),
        ("first_seen", "timestamp with time zone", "NO", "now()"),
        ("last_seen", "timestamp with time zone", "NO", "now()"),
    ]
    assert key == ("PRIMARY KEY (category, alias)",)


def test_row_level_security_is_on_with_no_policies_and_no_access_for_anon(database_url: str) -> None:
    with psycopg.connect(database_url) as conn:
        rls = conn.execute(
            "select relrowsecurity from pg_class where oid = 'public.product_aliases'::regclass"
        ).fetchone()
        policies = conn.execute(
            "select count(*) from pg_policies where schemaname = 'public' and tablename = 'product_aliases'"
        ).fetchone()
        allowed = [
            (role, privilege)
            for role in ROLES
            for privilege in PRIVILEGES
            if conn.execute(
                "select has_table_privilege(%s, 'public.product_aliases', %s)", (role, privilege)
            ).fetchone()
            == (True,)
        ]
    assert rls == (True,)
    assert policies == (0,)
    assert allowed == []


@pytest.mark.parametrize(
    ("alias", "product_id", "category"),
    [
        ("", "prd_sony_xm5", "headphones"),
        ("x" * 81, "prd_sony_xm5", "headphones"),
        (" xm5", "prd_sony_xm5", "headphones"),
        ("xm5", "sony_xm5", "headphones"),
        ("xm5", "prd_sony_xm5", "Head Phones"),
    ],
)
def test_the_table_refuses_what_does_not_fit(database_url: str, alias: str, product_id: str, category: str) -> None:
    with psycopg.connect(database_url) as conn, pytest.raises(errors.CheckViolation):
        conn.execute(
            "insert into public.product_aliases (alias, product_id, category) values (%s, %s, %s)",
            (alias, product_id, category),
        )


def test_postgres_alias_store_round_trip_and_upsert_keeps_the_first_id(
    aliases: PostgresAliasStore, database_url: str
) -> None:
    assert aliases.save("headphones", {"xm5": "prd_sony_wh1000xm5", "sony wh1000xm5": "prd_sony_wh1000xm5"}) is True
    assert aliases.settle(10.0)
    with psycopg.connect(database_url) as conn:
        first = conn.execute("select last_seen from public.product_aliases where alias = 'xm5'").fetchone()
    assert aliases.save("headphones", {"xm5": "prd_sony_wf1000xm5"}) is True
    assert aliases.settle(10.0)
    fresh = PostgresAliasStore(aliases.requests, InMemoryAliasStore())  # nothing in memory: the table alone
    assert fresh.load("headphones") == {}  # nothing is read until a search asks for the category
    fresh.prefetch("headphones")
    fresh.prefetch("laptop")
    assert fresh.settle(10.0)
    assert fresh.load("headphones") == {"xm5": "prd_sony_wh1000xm5", "sony wh1000xm5": "prd_sony_wh1000xm5"}
    assert fresh.load("laptop") == {}
    with psycopg.connect(database_url) as conn:
        later = conn.execute("select last_seen from public.product_aliases where alias = 'xm5'").fetchone()
    assert first is not None and later is not None and later[0] > first[0]  # the second save moved last_seen on


def test_the_migration_runs_on_a_plain_postgres_without_supabase_roles(database_url: str) -> None:
    """The revoke is guarded, as the database rules ask: with no anon or authenticated role it still applies."""
    sql = next(MIGRATIONS.glob("*_create_product_aliases.sql")).read_text(encoding="utf-8")
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute("create schema plain_check")
        try:
            conn.execute(
                sql.replace("public.product_aliases", "plain_check.product_aliases")
                .replace("rolname = 'anon'", "rolname = 'no_such_role_a'")
                .replace("rolname = 'authenticated'", "rolname = 'no_such_role_b'")
            )
            found = conn.execute("select to_regclass('plain_check.product_aliases')").fetchone()
        finally:
            conn.execute("drop schema plain_check cascade")
    assert found == ("plain_check.product_aliases",)
