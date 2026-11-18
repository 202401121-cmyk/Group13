"""US-30 criterion 4 (S4-27, D-72, P-29): a row's owner never changes once saved. A guest's request or search is never
linked to an account, and a user's row never moves to another user or back to a guest. Marker `db`; needs
TEST_DATABASE_URL.

The migration *_keep_owner_fixed.sql adds a BEFORE UPDATE OF user_id trigger on public.requests and public.searches.
Inserts and deletes are untouched, so a user's own rows are still saved with their id, and deleting a user still
deletes their rows (the foreign key's cascade). Same fixture rules as test_requests_table.py; made-up rows only.
"""

from __future__ import annotations

import uuid
from typing import Any

import psycopg
import pytest
from psycopg import errors, sql

from tests.integration import test_requests_table as requests_table
from tests.integration import test_user_tables as user_tables
from tests.integration.test_user_tables import OWNED, REQUEST_TEXT, People, new_session

pytestmark = pytest.mark.db

database_url = requests_table.database_url  # a fresh database of its own for this file
people = user_tables.people  # users A and B and a guest, each with a request and a search
MIGRATIONS = requests_table.MIGRATIONS
FUNCTION = "public.keep_user_id_fixed()"
TRIGGERS = {"requests": "requests_user_id_fixed", "searches": "searches_user_id_fixed"}


def migration_sql() -> str:
    [path] = MIGRATIONS.glob("*_keep_owner_fixed.sql")
    return path.read_text(encoding="utf-8")


def _set_owner(conn: psycopg.Connection[Any], table: str, session: str, owner: uuid.UUID | None) -> None:
    query = sql.SQL("update public.{} set user_id = %s where session_id = %s").format(sql.Identifier(table))
    conn.execute(query, (owner, session))


def _owner(conn: psycopg.Connection[Any], table: str, session: str) -> uuid.UUID | None:
    query = sql.SQL("select user_id from public.{} where session_id = %s").format(sql.Identifier(table))
    row = conn.execute(query, (session,)).fetchone()
    assert row is not None
    owner: uuid.UUID | None = row[0]
    return owner


# --- the rule ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("table", OWNED)
def test_a_guests_row_can_never_be_linked_to_an_account(database_url: str, people: People, table: str) -> None:
    with psycopg.connect(database_url) as conn:
        with pytest.raises(errors.CheckViolation, match="never changes"):
            _set_owner(conn, table, people.guest_session, people.a)
        conn.rollback()
        assert _owner(conn, table, people.guest_session) is None


@pytest.mark.parametrize("table", OWNED)
@pytest.mark.parametrize("to", ["another user", "a guest"])
def test_a_users_row_never_moves_to_another_user_or_back_to_a_guest(
    database_url: str, people: People, table: str, to: str
) -> None:
    with psycopg.connect(database_url) as conn:
        with pytest.raises(errors.CheckViolation):
            _set_owner(conn, table, people.a_session, people.b if to == "another user" else None)
        conn.rollback()
        assert _owner(conn, table, people.a_session) == people.a


@pytest.mark.parametrize("table", OWNED)
def test_the_other_columns_still_update(database_url: str, people: People, table: str) -> None:
    column, value = ("request_text", "a changed made-up request") if table == "requests" else ("result", '{"a": 1}')
    query = sql.SQL("update public.{} set {} = %s where session_id = any(%s)").format(
        sql.Identifier(table), sql.Identifier(column)
    )
    with psycopg.connect(database_url) as conn:  # rolled back: other tests read the same rows
        updated = conn.execute(query, (value, [people.a_session, people.guest_session]))
        assert updated.rowcount == 2
        conn.rollback()


@pytest.mark.parametrize("table", OWNED)
def test_setting_the_same_owner_again_is_no_change(database_url: str, people: People, table: str) -> None:
    query = sql.SQL("update public.{} set user_id = %s where session_id = %s").format(sql.Identifier(table))
    with psycopg.connect(database_url) as conn:
        same = conn.execute(query, (people.a, people.a_session))  # "is distinct from": the same id is no change
        assert same.rowcount == 1
        still_guest = conn.execute(query, (None, people.guest_session))
        assert still_guest.rowcount == 1
        conn.rollback()


def test_a_row_is_still_saved_with_its_owner(database_url: str, people: People) -> None:
    session = new_session()
    with psycopg.connect(database_url) as conn:
        conn.execute(
            "insert into public.requests (session_id, request_text, user_id) values (%s, %s, %s)",
            (session, REQUEST_TEXT, people.a),
        )
        conn.execute(
            "insert into public.searches (session_id, rubric, result, user_id) values (%s, '{}', '{}', %s)",
            (session, people.a),
        )
        assert _owner(conn, "requests", session) == _owner(conn, "searches", session) == people.a
        conn.rollback()


def test_deleting_a_user_still_deletes_their_rows(database_url: str, people: People) -> None:
    """The cascade deletes rows, it never sets user_id, so the trigger is never in its way."""

    def count(conn: psycopg.Connection[Any], table: str) -> int:
        query = sql.SQL("select count(*) from public.{} where session_id = %s").format(sql.Identifier(table))
        row = conn.execute(query, (people.b_session,)).fetchone()
        assert row is not None
        found: int = row[0]
        return found

    with psycopg.connect(database_url) as conn:  # rolled back
        assert [count(conn, table) for table in OWNED] == [1, 1]  # B's rows are there first
        conn.execute("delete from auth.users where id = %s", (people.b,))
        assert [count(conn, table) for table in OWNED] == [0, 0]
        conn.rollback()


# --- the trigger and its function -------------------------------------------------------------------------------------


def test_each_table_has_the_trigger_before_an_update_of_user_id(database_url: str) -> None:
    with psycopg.connect(database_url) as conn:
        for table, name in TRIGGERS.items():
            row = conn.execute(
                "select pg_get_triggerdef(oid) from pg_trigger where tgrelid = %s::regclass and tgname = %s",
                (f"public.{table}", name),
            ).fetchone()
            assert row is not None, table
            assert (
                f"BEFORE UPDATE OF user_id ON public.{table} FOR EACH ROW EXECUTE FUNCTION keep_user_id_fixed()"
                in (row[0])
            )


def test_the_function_has_a_fixed_search_path(database_url: str) -> None:
    with psycopg.connect(database_url) as conn:
        row = conn.execute("select proconfig from pg_proc where oid = %s::regprocedure", (FUNCTION,)).fetchone()
        assert row is not None and row[0] == ['search_path=""']


@pytest.mark.parametrize("role", ["anon", "authenticated", "public"])
def test_anon_and_authenticated_cannot_execute_the_function(database_url: str, role: str) -> None:
    with psycopg.connect(database_url) as conn:
        row = conn.execute("select has_function_privilege(%s, %s, 'execute')", (role, FUNCTION)).fetchone()
        assert row == (False,)


def test_supabases_default_grants_on_a_new_function_are_undone(database_url: str) -> None:
    """Supabase grants execute on every new function in public to anon and authenticated directly, not only through
    PUBLIC. A plain Postgres doesn't, so this copies that set-up: without the migration's own revoke for those roles,
    this fails."""
    with psycopg.connect(database_url) as conn:  # one transaction, rolled back below
        conn.execute("drop function public.keep_user_id_fixed() cascade")  # its triggers go too, and come back
        conn.execute("alter default privileges in schema public grant execute on functions to anon, authenticated")
        conn.execute("create function public.default_grant_control() returns int language sql as 'select 1'")
        control = conn.execute("select has_function_privilege('anon', 'public.default_grant_control()', 'execute')")
        assert control.fetchone() == (True,)  # the default grant is really in force
        conn.execute("revoke execute on function public.default_grant_control() from public")
        direct = conn.execute("select has_function_privilege('anon', 'public.default_grant_control()', 'execute')")
        assert direct.fetchone() == (True,)  # granted to anon itself: revoking from public alone leaves it

        conn.execute(migration_sql().encode("utf-8"))
        for role in ("anon", "authenticated"):
            row = conn.execute("select has_function_privilege(%s, %s, 'execute')", (role, FUNCTION)).fetchone()
            assert row == (False,), role
        triggers = conn.execute("select count(*) from pg_trigger where tgname = any(%s)", (list(TRIGGERS.values()),))
        assert triggers.fetchone() == (2,)
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


def test_running_the_migration_again_changes_nothing(database_url: str) -> None:
    catalogue = (
        "select tgrelid::regclass::text, tgname, pg_get_triggerdef(oid) from pg_trigger "
        "where tgname = any(%(names)s) order by 1",
        "select pg_get_functiondef(%(fn)s::regprocedure), proconfig, proacl::text from pg_proc "
        "where oid = %(fn)s::regprocedure",
    )
    params = {"names": list(TRIGGERS.values()), "fn": FUNCTION}
    with psycopg.connect(database_url) as conn:  # one transaction, rolled back below
        before = [conn.execute(q, params).fetchall() for q in catalogue]
        conn.execute(migration_sql().encode("utf-8"))
        after = [conn.execute(q, params).fetchall() for q in catalogue]
        conn.rollback()
    assert after == before
    assert len(before[0]) == 2  # one trigger a table, not two after a second run
