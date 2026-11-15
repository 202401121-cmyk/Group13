"""US-30 (#52): user tables and row level security on a real Postgres. Marker `db`; needs TEST_DATABASE_URL.

The migration *_add_user_tables.sql gives requests and searches an optional owner (user_id), adds public.profiles, and
lets a signed-in user read only their own rows through the Data API. Guests' rows (user_id null) are never readable
(D-36), anon gets nothing, and writing stays with the backend.

Each check runs as the Data API runs a request: `set local role authenticated` (or anon) and the user's JWT claims in
`request.jwt.claims`, inside one transaction that is rolled back. On a plain Postgres the migration adds a stub of
Supabase's auth schema (auth.users and auth.uid()); on Supabase, and on any database that already has it, it touches
nothing there. Same fixture rules as test_requests_table.py.
"""

from __future__ import annotations

import json
import secrets
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import psycopg
import pytest
from psycopg import errors, sql

from tests.integration import test_requests_table as requests_table

pytestmark = pytest.mark.db

database_url = requests_table.database_url  # a fresh database of its own for this file
MIGRATIONS = requests_table.MIGRATIONS
OWNED = ("requests", "searches")  # the tables with a user_id
TABLES = (*OWNED, "profiles")
PRIVILEGES = ("select", "insert", "update", "delete")
# everything Supabase's default `grant all` hands out on a table in Postgres 17, truncate (which ignores row level
# security) included
ALL_PRIVILEGES = (*PRIVILEGES, "truncate", "references", "trigger", "maintain")
REQUEST_TEXT = "a made-up laptop request for the row level security tests"


def migration_sql() -> str:
    [path] = MIGRATIONS.glob("*_add_user_tables.sql")
    return path.read_text(encoding="utf-8")


def new_session() -> str:
    return "sess_" + secrets.token_hex(12)  # the format new_session_id() makes (D-26)


@dataclass(frozen=True)
class People:
    a: uuid.UUID
    b: uuid.UUID
    a_session: str
    b_session: str
    guest_session: str


@pytest.fixture
def people(database_url: str) -> People:
    """Users A and B, each with a profile, a request and a search; and a guest's request and search (no user)."""
    found = People(uuid.uuid4(), uuid.uuid4(), new_session(), new_session(), new_session())
    with psycopg.connect(database_url, autocommit=True) as conn:
        for user, name in ((found.a, "Shopper A"), (found.b, "Shopper B")):
            conn.execute("insert into auth.users (id) values (%s)", (user,))
            conn.execute("insert into public.profiles (id, display_name) values (%s, %s)", (user, name))
        for session, owner in ((found.a_session, found.a), (found.b_session, found.b), (found.guest_session, None)):
            conn.execute(
                "insert into public.requests (session_id, request_text, user_id) values (%s, %s, %s)",
                (session, REQUEST_TEXT, owner),
            )
            conn.execute(
                "insert into public.searches (session_id, rubric, result, user_id) values (%s, '{}', '{}', %s)",
                (session, owner),
            )
    return found


@contextmanager
def acting_as(url: str, role: str, user: uuid.UUID | None = None) -> Iterator[psycopg.Connection[Any]]:
    """A connection that acts as the Data API does for one request, rolled back afterwards."""
    with psycopg.connect(url) as conn:
        try:
            conn.execute(sql.SQL("set local role {}").format(sql.Identifier(role)))
            if user is not None:
                claims = json.dumps({"sub": str(user), "role": role})
                conn.execute("select set_config('request.jwt.claims', %s, true)", (claims,))
            yield conn
        finally:
            conn.rollback()


def sessions(conn: psycopg.Connection[Any], table: str) -> list[str]:
    query = sql.SQL("select session_id from public.{} order by session_id").format(sql.Identifier(table))
    return [row[0] for row in conn.execute(query).fetchall()]


def names(conn: psycopg.Connection[Any]) -> list[str | None]:
    return [row[0] for row in conn.execute("select display_name from public.profiles order by id").fetchall()]


# --- the shape --------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("table", OWNED)
def test_requests_and_searches_have_an_optional_owner_removed_with_the_user(database_url: str, table: str) -> None:
    with psycopg.connect(database_url) as conn:
        column = conn.execute(
            "select data_type, is_nullable, column_default from information_schema.columns "
            "where table_schema = 'public' and table_name = %s and column_name = 'user_id'",
            (table,),
        ).fetchone()
        keys = conn.execute(
            "select conname, confrelid::regclass::text, confdeltype from pg_constraint "
            "where conrelid = %s::regclass and contype = 'f'",
            (f"public.{table}",),
        ).fetchall()
        index = conn.execute(
            "select indexdef from pg_indexes where schemaname = 'public' and tablename = %s and indexname = %s",
            (table, f"{table}_user_id_idx"),
        ).fetchall()
    assert column == ("uuid", "YES", None)  # null for a guest (D-36)
    assert keys == [(f"{table}_user_id_fkey", "auth.users", "c")]  # c: on delete cascade
    [(definition,)] = index
    assert definition.endswith("(user_id)") and "UNIQUE" not in definition


def test_profiles_has_the_agreed_columns_and_belongs_to_a_user(database_url: str) -> None:
    with psycopg.connect(database_url) as conn:
        columns = conn.execute(
            "select column_name, data_type, is_nullable, column_default from information_schema.columns "
            "where table_schema = 'public' and table_name = 'profiles' order by ordinal_position"
        ).fetchall()
        keys = conn.execute(
            "select contype, confrelid::regclass::text, confdeltype, pg_get_constraintdef(oid) from pg_constraint "
            "where conrelid = 'public.profiles'::regclass and contype in ('p', 'f') order by contype"
        ).fetchall()
    assert columns == [
        ("id", "uuid", "NO", None),
        ("display_name", "text", "YES", None),
        ("created_at", "timestamp with time zone", "NO", "now()"),
    ]
    assert [(kind, target, delete) for kind, target, delete, _ in keys] == [("f", "auth.users", "c"), ("p", "-", " ")]
    assert keys[1][3] == "PRIMARY KEY (id)"


@pytest.mark.parametrize("display_name", [None, "A", "x" * 80, "Shopper ₹ 😀", "Two  words"])
def test_a_display_name_is_none_or_1_to_80_characters(database_url: str, display_name: str | None) -> None:
    user = uuid.uuid4()
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute("insert into auth.users (id) values (%s)", (user,))
        conn.execute("insert into public.profiles (id, display_name) values (%s, %s)", (user, display_name))
        row = conn.execute("select display_name from public.profiles where id = %s", (user,)).fetchone()
    assert row == (display_name,)


# Blank, or with a space at either end: MeResponse strips spaces, so "   " would be empty there and fail.
@pytest.mark.parametrize("display_name", ["", "x" * 81, " ", "   ", "\t", "\n", " Shopper", "Shopper ", "A\n"])
def test_an_empty_or_too_long_display_name_is_refused(database_url: str, display_name: str) -> None:
    user = uuid.uuid4()
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute("insert into auth.users (id) values (%s)", (user,))
        with pytest.raises(errors.CheckViolation):
            conn.execute("insert into public.profiles (id, display_name) values (%s, %s)", (user, display_name))


def test_a_profile_or_an_owned_row_for_an_unknown_user_is_refused(database_url: str) -> None:
    stranger = uuid.uuid4()
    with psycopg.connect(database_url, autocommit=True) as conn:
        with pytest.raises(errors.ForeignKeyViolation):
            conn.execute("insert into public.profiles (id) values (%s)", (stranger,))
        with pytest.raises(errors.ForeignKeyViolation):
            conn.execute(
                "insert into public.searches (session_id, rubric, result, user_id) values (%s, '{}', '{}', %s)",
                (new_session(), stranger),
            )


def test_row_level_security_is_on_for_every_table_in_public(database_url: str) -> None:
    with psycopg.connect(database_url) as conn:
        tables = conn.execute(
            "select c.relname, c.relrowsecurity from pg_class c join pg_namespace n on n.oid = c.relnamespace "
            "where n.nspname = 'public' and c.relkind in ('r', 'p') order by c.relname"
        ).fetchall()
    assert {name for name, _ in tables} >= {"requests", "searches", "profiles", "product_aliases"}
    assert all(on for _, on in tables), tables


# --- privileges and policies ------------------------------------------------------------------------------------------


def test_anon_has_no_access_to_the_three_tables(database_url: str) -> None:
    with psycopg.connect(database_url) as conn:
        for table in TABLES:
            for privilege in ALL_PRIVILEGES:
                allowed = conn.execute(
                    "select has_table_privilege('anon', %s, %s)", (f"public.{table}", privilege)
                ).fetchone()
                assert allowed == (False,), (table, privilege)
        columns = conn.execute(
            "select table_name, column_name, privilege_type from information_schema.column_privileges "
            "where table_schema = 'public' and grantee = 'anon' and table_name = any(%s)",
            (list(TABLES),),
        ).fetchall()
    assert columns == []


def test_authenticated_may_read_the_three_tables_and_never_insert_or_delete(database_url: str) -> None:
    with psycopg.connect(database_url) as conn:
        for table in TABLES:
            for privilege in ALL_PRIVILEGES:
                allowed = conn.execute(
                    "select has_table_privilege('authenticated', %s, %s)", (f"public.{table}", privilege)
                ).fetchone()
                assert allowed == (privilege == "select",), (table, privilege)
            # a grant on some columns only, which has_table_privilege doesn't see
            for privilege in ("insert", "update", "references"):
                some = conn.execute(
                    "select has_any_column_privilege('authenticated', %s, %s)", (f"public.{table}", privilege)
                ).fetchone()
                assert some == (table == "profiles" and privilege == "update",), (table, privilege)


def test_on_profiles_authenticated_may_change_only_the_display_name(database_url: str) -> None:
    with psycopg.connect(database_url) as conn:
        writes = conn.execute(
            "select table_name, column_name, privilege_type from information_schema.column_privileges "
            "where table_schema = 'public' and grantee = 'authenticated' and privilege_type <> 'SELECT' "
            "order by table_name, column_name, privilege_type"
        ).fetchall()
    # on profiles only: never its id or created_at, and no other column privilege on any table
    assert writes == [("profiles", "display_name", "UPDATE")]


def test_the_policies_show_a_signed_in_user_only_their_own_rows(database_url: str) -> None:
    with psycopg.connect(database_url) as conn:
        policies = conn.execute(
            "select tablename, policyname, permissive, roles::text[], cmd, qual, with_check from pg_policies "
            "where schemaname = 'public' order by tablename, policyname"
        ).fetchall()
    own_id = "(id = ( SELECT auth.uid() AS uid))"
    own_user = "(user_id = ( SELECT auth.uid() AS uid))"
    assert policies == [
        ("profiles", "profiles_select_own", "PERMISSIVE", ["authenticated"], "SELECT", own_id, None),
        ("profiles", "profiles_update_own", "PERMISSIVE", ["authenticated"], "UPDATE", own_id, own_id),
        ("requests", "requests_select_own", "PERMISSIVE", ["authenticated"], "SELECT", own_user, None),
        ("searches", "searches_select_own", "PERMISSIVE", ["authenticated"], "SELECT", own_user, None),
    ]


# --- who can read what ------------------------------------------------------------------------------------------------


def test_user_a_reads_only_their_own_requests_searches_and_profile(database_url: str, people: People) -> None:
    with acting_as(database_url, "authenticated", people.a) as conn:
        assert sessions(conn, "searches") == [people.a_session]
        assert sessions(conn, "requests") == [people.a_session]
        assert names(conn) == ["Shopper A"]


def test_user_b_cannot_read_user_as_rows(database_url: str, people: People) -> None:
    with acting_as(database_url, "authenticated", people.b) as conn:
        for table in OWNED:
            rows = conn.execute(
                sql.SQL("select count(*) from public.{} where session_id = %s or user_id = %s").format(
                    sql.Identifier(table)
                ),
                (people.a_session, people.a),
            ).fetchone()
            assert rows == (0,), table
        assert conn.execute("select count(*) from public.profiles where id = %s", (people.a,)).fetchone() == (0,)
        assert sessions(conn, "searches") == [people.b_session]


@pytest.mark.parametrize("table", TABLES)
@pytest.mark.parametrize("claims", [False, True])
def test_anon_reads_nothing(database_url: str, people: People, table: str, claims: bool) -> None:
    """anon is refused outright, even carrying a user's claims."""
    with (
        acting_as(database_url, "anon", people.a if claims else None) as conn,
        pytest.raises(errors.InsufficientPrivilege),
    ):
        conn.execute(sql.SQL("select count(*) from public.{}").format(sql.Identifier(table)))


def test_a_guest_row_is_invisible_to_every_signed_in_user(database_url: str, people: People) -> None:
    for user in (people.a, people.b):
        with acting_as(database_url, "authenticated", user) as conn:
            for table in OWNED:
                assert people.guest_session not in sessions(conn, table), (user, table)
                nulls = conn.execute(
                    sql.SQL("select count(*) from public.{} where user_id is null").format(sql.Identifier(table))
                ).fetchone()
                assert nulls == (0,), (user, table)


def test_a_session_with_no_signed_in_user_reads_nothing(database_url: str, people: People) -> None:
    with acting_as(database_url, "authenticated") as conn:
        for table in OWNED:
            assert sessions(conn, table) == [], table
        assert names(conn) == []


# --- what a signed-in user may change ---------------------------------------------------------------------------------


def test_a_user_renames_only_their_own_profile(database_url: str, people: People) -> None:
    with acting_as(database_url, "authenticated", people.a) as conn:
        own = conn.execute("update public.profiles set display_name = 'A, renamed' where id = %s", (people.a,))
        others = conn.execute("update public.profiles set display_name = 'Taken over' where id = %s", (people.b,))
        assert (own.rowcount, others.rowcount) == (1, 0)
        assert names(conn) == ["A, renamed"]
    with psycopg.connect(database_url) as conn:
        row = conn.execute("select display_name from public.profiles where id = %s", (people.b,)).fetchone()
    assert row == ("Shopper B",)


def test_a_user_cannot_hand_their_profile_to_another_id(database_url: str, people: People) -> None:
    with acting_as(database_url, "authenticated", people.a) as conn, pytest.raises(errors.InsufficientPrivilege):
        conn.execute("update public.profiles set id = %s where id = %s", (people.b, people.a))


@pytest.mark.parametrize(
    "statement",
    [
        "insert into public.searches (session_id, rubric, result, user_id) values ('sess_fromapi01', '{}', '{}', %s)",
        "insert into public.requests (session_id, request_text, user_id) values ('sess_fromapi01', 'made up', %s)",
        "insert into public.profiles (id) values (%s)",
        "delete from public.searches where user_id = %s",
        "delete from public.requests where user_id = %s",
        "delete from public.profiles where id = %s",
        "update public.searches set result = '{}' where user_id = %s",
        "update public.requests set request_text = 'changed' where user_id = %s",
        "update public.searches set user_id = null where user_id = %s",
    ],
)
def test_a_user_can_never_write_their_rows_through_the_api(database_url: str, people: People, statement: str) -> None:
    """Inserts and deletes stay with the backend, through DATABASE_URL."""
    with acting_as(database_url, "authenticated", people.a) as conn, pytest.raises(errors.InsufficientPrivilege):
        conn.execute(statement, (people.a,))


# --- deleting a user --------------------------------------------------------------------------------------------------


def test_deleting_a_user_deletes_their_rows_and_only_theirs(database_url: str, people: People) -> None:
    def counts(conn: psycopg.Connection[Any], session: str) -> tuple[int, ...]:
        found = []
        for table in OWNED:
            query = sql.SQL("select count(*) from public.{} where session_id = %s").format(sql.Identifier(table))
            row = conn.execute(query, (session,)).fetchone()
            assert row is not None
            found.append(row[0])
        return tuple(found)

    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute("delete from auth.users where id = %s", (people.a,))
        profiles = conn.execute("select id from public.profiles where id = any(%s)", ([people.a, people.b],))
        assert [row[0] for row in profiles.fetchall()] == [people.b]
        assert counts(conn, people.a_session) == (0, 0)
        assert counts(conn, people.b_session) == (1, 1)
        assert counts(conn, people.guest_session) == (1, 1)


# --- safe to apply ----------------------------------------------------------------------------------------------------


def _catalogue(conn: psycopg.Connection[Any]) -> dict[str, list[tuple[Any, ...]]]:
    """Everything the migration sets, read from the catalogue."""
    tables = ["requests", "searches", "profiles"]
    queries = {
        "columns": "select table_name, column_name, data_type, is_nullable, column_default "
        "from information_schema.columns where table_schema = 'public' and table_name = any(%(t)s) "
        "order by table_name, ordinal_position",
        "constraints": "select conrelid::regclass::text, conname, pg_get_constraintdef(oid) from pg_constraint "
        "where conrelid = any(array(select ('public.' || t)::regclass from unnest(%(t)s::text[]) t)) "
        "order by 1, 2",
        "indexes": "select tablename, indexname, indexdef from pg_indexes "
        "where schemaname = 'public' and tablename = any(%(t)s) order by 1, 2",
        "policies": "select tablename, policyname, permissive, roles::text[], cmd, qual, with_check from pg_policies "
        "where schemaname = 'public' order by 1, 2",
        "grants": "select table_name, grantee, privilege_type from information_schema.role_table_grants "
        "where table_schema = 'public' and grantee in ('anon', 'authenticated') order by 1, 2, 3",
        "column_grants": "select table_name, column_name, grantee, privilege_type "
        "from information_schema.column_privileges where table_schema = 'public' "
        "and grantee in ('anon', 'authenticated') order by 1, 2, 3, 4",
        "auth": "select column_name, data_type from information_schema.columns "
        "where table_schema = 'auth' and table_name = 'users' order by ordinal_position",
        "uid": "select pg_get_functiondef('auth.uid()'::regprocedure)",
        "comments": "select objoid::regclass::text, objsubid, description from pg_description "
        "where objoid = any(array(select ('public.' || t)::regclass from unnest(%(t)s::text[]) t)) order by 1, 2",
    }
    return {name: conn.execute(query, {"t": tables}).fetchall() for name, query in queries.items()}


def test_running_the_migration_again_changes_nothing(database_url: str) -> None:
    with psycopg.connect(database_url) as conn:  # one transaction, rolled back below
        before = _catalogue(conn)
        conn.execute(migration_sql().encode("utf-8"))
        after = _catalogue(conn)
        conn.rollback()
    assert after == before


def test_supabases_default_grants_on_a_new_table_are_undone(database_url: str) -> None:
    """Supabase grants every new table in public to anon and authenticated. A plain Postgres doesn't, so this copies
    that set-up."""
    with psycopg.connect(database_url) as conn:  # one transaction, rolled back below: DDL undoes cleanly in Postgres
        conn.execute("drop table public.profiles")
        conn.execute("alter default privileges in schema public grant all on tables to anon, authenticated")
        conn.execute("create table public.default_grant_control (id int)")
        control = conn.execute("select has_table_privilege('anon', 'public.default_grant_control', 'select')")
        assert control.fetchone() == (True,)  # the default grant is really in force

        conn.execute(migration_sql().encode("utf-8"))
        for privilege in ALL_PRIVILEGES:
            anon = conn.execute("select has_table_privilege('anon', 'public.profiles', %s)", (privilege,))
            assert anon.fetchone() == (False,), privilege
            signed_in = conn.execute("select has_table_privilege('authenticated', 'public.profiles', %s)", (privilege,))
            assert signed_in.fetchone() == (privilege == "select",), privilege
        for column in ("id", "display_name", "created_at"):
            update = conn.execute(
                "select has_column_privilege('authenticated', 'public.profiles', %s, 'update')", (column,)
            )
            assert update.fetchone() == (column == "display_name",), column
        conn.rollback()


def test_the_role_blocks_are_guarded_for_a_plain_postgres(database_url: str) -> None:
    """With no anon or authenticated role, the migration still applies (the database rules ask for the guards)."""
    plain = (
        migration_sql()
        .replace("rolname = 'anon'", "rolname = 'no_such_role_a'")
        .replace("rolname = 'authenticated'", "rolname = 'no_such_role_b'")
    )
    assert plain != migration_sql()
    with psycopg.connect(database_url) as conn:
        conn.execute(plain.encode("utf-8"))
        conn.rollback()


# The auth schema as Supabase has it: more columns on auth.users, and its own auth.uid().
SUPABASE_UID_BODY = """
  select coalesce(
    nullif(current_setting('request.jwt.claim.sub', true), ''),
    (nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub')
  )::uuid
"""
SUPABASE_AUTH = f"""
create schema auth;
grant usage on schema auth to anon, authenticated;
create table auth.users (id uuid primary key, email text, raw_user_meta_data jsonb, created_at timestamptz);
create function auth.uid() returns uuid language sql stable as $$ {SUPABASE_UID_BODY} $$;
comment on function auth.uid() is 'Supabase''s own: a migration never replaces it';
"""


def _supabase_auth(conn: psycopg.Connection[Any]) -> None:
    conn.execute(SUPABASE_AUTH.encode("utf-8"))


def test_a_database_that_already_has_the_auth_schema_keeps_it_unchanged() -> None:
    with requests_table.migrated_database(before=_supabase_auth) as url:
        with psycopg.connect(url) as conn:
            columns = conn.execute(
                "select column_name from information_schema.columns where table_schema = 'auth' "
                "and table_name = 'users' order by ordinal_position"
            ).fetchall()
            uid = conn.execute(
                "select prosrc, obj_description(oid, 'pg_proc') from pg_proc where oid = 'auth.uid()'::regprocedure"
            ).fetchone()
            functions = conn.execute(
                "select count(*) from pg_proc p join pg_namespace n on n.oid = p.pronamespace where n.nspname = 'auth'"
            ).fetchone()
        assert columns == [("id",), ("email",), ("raw_user_meta_data",), ("created_at",)]
        assert uid == (f" {SUPABASE_UID_BODY} ", "Supabase's own: a migration never replaces it")
        assert functions == (1,)

        a, b = uuid.uuid4(), uuid.uuid4()
        session = new_session()
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute(
                "insert into auth.users (id, email) values (%s, 'a@example.com'), (%s, 'b@example.com')", (a, b)
            )
            conn.execute(
                "insert into public.searches (session_id, rubric, result, user_id) values (%s, '{}', '{}', %s)",
                (session, a),
            )
        with acting_as(url, "authenticated", a) as conn:
            assert sessions(conn, "searches") == [session]
        with acting_as(url, "authenticated", b) as conn:
            assert sessions(conn, "searches") == []
