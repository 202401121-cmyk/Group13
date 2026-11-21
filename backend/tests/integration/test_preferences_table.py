"""US-06 (#61, P-40): the preferences table on a real Postgres with pgvector. Marker `db`; needs TEST_DATABASE_URL on
a server that has pgvector, such as CI's `pgvector/pgvector:0.8.2-pg17` (docs/database.md, "Tests").

The migration *_create_preferences.sql installs pgvector in the `extensions` schema and adds public.preferences: one
row per remembered item (a weight, a must-have or a leave-out) of a signed-in user, with a 384-number embedding. Row
level security lets a signed-in user read only their own rows; anon gets nothing, and writing stays with the backend.
A row's owner never changes (D-72), and deleting the user deletes their rows.

Migrations here only move forward, so the matching down SQL lives in supabase/rollback/. These tests apply up, down and
up again on a database of their own. Same fixture rules as test_requests_table.py; made-up rows only.
"""

from __future__ import annotations

import json
import math
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg import errors

from app.contracts import Exclusion, HardRule, PreferenceItem, PreferenceList
from app.contracts import examples as ex
from tests.integration import test_requests_table as requests_table
from tests.integration.test_user_tables import ALL_PRIVILEGES, acting_as

pytestmark = pytest.mark.db

database_url = requests_table.database_url  # a fresh database of its own for this file
MIGRATIONS = requests_table.MIGRATIONS
ROLLBACK = MIGRATIONS.parent / "rollback"
DIMENSIONS = 384
PGVECTOR = "0.8.2"  # the version the Supabase project lists (21 Nov 2026), and CI's image
FUNCTION = "public.keep_user_id_fixed()"
FUNCTION_COMMENT_BEFORE = (
    "Refuses any change of user_id on requests and searches (US-30, D-72). A trigger function: never called directly."
)
FUNCTION_COMMENT = (
    "Refuses any change of user_id on requests, searches and preferences (US-30, US-06, D-72). A trigger function: "
    "never called directly."
)
INSERT = (
    "insert into public.preferences (user_id, kind, category, criterion_key, text, weight, rule, always, embedding) "
    "values (%(user_id)s, %(kind)s, %(category)s, %(criterion_key)s, %(text)s, %(weight)s, %(rule)s::jsonb, "
    "%(always)s, %(embedding)s::extensions.vector) returning id"
)
# the contract's own JSON, as the store will save it
HARD_RULE = HardRule(
    attribute="ram_gb", operator=">=", value=16, unit="GB", source_text="at least 16 GB of RAM"
).model_dump(mode="json")


def up_path() -> Path:
    [path] = MIGRATIONS.glob("*_create_preferences.sql")
    return path


def up_sql() -> str:
    return up_path().read_text(encoding="utf-8")


def down_sql() -> str:
    version = up_path().name.split("_", 1)[0]
    return (ROLLBACK / f"{version}_preferences_down.sql").read_text(encoding="utf-8")


def vector(size: int = DIMENSIONS, seed: int = 1) -> str:
    """A made-up embedding in pgvector's text form, '[0.1,0.2,...]'."""
    return "[" + ",".join(f"{math.sin(seed * (i + 1)):.6f}" for i in range(size)) + "]"


def exclusion(kind: str = "brand", value: str = "Acme", *, always: bool = False) -> dict[str, Any]:
    return Exclusion.model_validate({"kind": kind, "value": value, "always": always}).model_dump(mode="json")


def item(user: uuid.UUID, **changes: Any) -> dict[str, Any]:
    """A weight item for a laptop, with every column; `changes` replaces any of them."""
    row: dict[str, Any] = {
        "user_id": user,
        "kind": "weight",
        "category": "laptop",
        "criterion_key": "battery_life",
        "text": "Battery life matters a lot",
        "weight": 8.0,
        "rule": None,
        "always": False,
        "embedding": vector(),
    }
    row.update(changes)
    if isinstance(row["rule"], dict | list):
        row["rule"] = json.dumps(row["rule"])
    return row


def insert(conn: psycopg.Connection[Any], row: dict[str, Any]) -> uuid.UUID:
    found = conn.execute(INSERT, row).fetchone()
    assert found is not None
    new_id: uuid.UUID = found[0]
    return new_id


def new_user(conn: psycopg.Connection[Any]) -> uuid.UUID:
    user = uuid.uuid4()
    conn.execute("insert into auth.users (id) values (%s)", (user,))
    return user


@dataclass(frozen=True)
class People:
    a: uuid.UUID
    b: uuid.UUID
    a_items: tuple[uuid.UUID, ...]
    b_items: tuple[uuid.UUID, ...]


@pytest.fixture
def people(database_url: str) -> People:
    """Users A and B, each with a weight, a must-have and a leave-out marked Always, saved as the backend saves them."""
    with psycopg.connect(database_url, autocommit=True) as conn:
        a, b = new_user(conn), new_user(conn)
        saved: dict[uuid.UUID, tuple[uuid.UUID, ...]] = {}
        for user, seed in ((a, 1), (b, 2)):
            saved[user] = (
                insert(conn, item(user, embedding=vector(seed=seed))),
                insert(
                    conn,
                    item(user, kind="hard_rule", criterion_key=None, weight=None, rule=HARD_RULE, text="16 GB of RAM"),
                ),
                insert(
                    conn,
                    item(
                        user,
                        kind="exclusion",
                        category=None,
                        criterion_key=None,
                        weight=None,
                        rule=exclusion(always=True),
                        always=True,
                        text="Never Acme",
                    ),
                ),
            )
    return People(a, b, saved[a], saved[b])


def ids(conn: psycopg.Connection[Any]) -> list[uuid.UUID]:
    return [row[0] for row in conn.execute("select id from public.preferences order by id").fetchall()]


# --- the shape --------------------------------------------------------------------------------------------------------


def test_pgvector_is_installed_in_the_extensions_schema(database_url: str) -> None:
    with psycopg.connect(database_url) as conn:
        row = conn.execute(
            "select n.nspname, e.extversion from pg_extension e join pg_namespace n on n.oid = e.extnamespace "
            "where e.extname = 'vector'"
        ).fetchone()
    assert row == ("extensions", PGVECTOR)  # never in public (Supabase's advisor flags that)


def test_the_table_has_the_agreed_columns(database_url: str) -> None:
    with psycopg.connect(database_url) as conn:
        columns = conn.execute(
            "select a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull, pg_get_expr(d.adbin, d.adrelid) "
            "from pg_attribute a left join pg_attrdef d on d.adrelid = a.attrelid and d.adnum = a.attnum "
            "where a.attrelid = 'public.preferences'::regclass and a.attnum > 0 and not a.attisdropped "
            "order by a.attnum"
        ).fetchall()
    assert columns == [
        ("id", "uuid", True, "gen_random_uuid()"),
        ("user_id", "uuid", True, None),
        ("kind", "text", True, None),
        ("category", "text", False, None),
        ("criterion_key", "text", False, None),
        ("text", "text", True, None),
        ("weight", "real", False, None),
        ("rule", "jsonb", False, None),
        ("always", "boolean", True, "false"),
        ("embedding", "extensions.vector(384)", False, None),
        ("created_at", "timestamp with time zone", True, "now()"),
        ("updated_at", "timestamp with time zone", True, "now()"),
    ]


def test_the_owner_is_required_and_deleting_the_user_deletes_the_rows(database_url: str) -> None:
    with psycopg.connect(database_url) as conn:
        keys = conn.execute(
            "select contype, confrelid::regclass::text, confdeltype, pg_get_constraintdef(oid) from pg_constraint "
            "where conrelid = 'public.preferences'::regclass and contype in ('p', 'f') order by contype"
        ).fetchall()
    assert keys == [
        ("f", "auth.users", "c", "FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE"),
        ("p", "-", " ", "PRIMARY KEY (id)"),
    ]


def test_the_indexes_are_the_key_and_user_then_category(database_url: str) -> None:
    """The per-user look-up filters by user and category first; at a few hundred rows a user, pgvector's exact scan
    of those rows needs no vector index (docs/database.md, "Preferences")."""
    with psycopg.connect(database_url) as conn:
        indexes = conn.execute(
            "select indexname, indexdef from pg_indexes where schemaname = 'public' and tablename = 'preferences' "
            "order by indexname"
        ).fetchall()
    assert [name for name, _ in indexes] == ["preferences_pkey", "preferences_user_id_category_idx"]
    definition = dict(indexes)["preferences_user_id_category_idx"]
    assert definition.endswith("USING btree (user_id, category)") and "UNIQUE" not in definition


def test_row_level_security_is_on(database_url: str) -> None:
    with psycopg.connect(database_url) as conn:
        row = conn.execute("select relrowsecurity from pg_class where oid = 'public.preferences'::regclass").fetchone()
    assert row == (True,)


# --- the values the table refuses -------------------------------------------------------------------------------------


@pytest.mark.parametrize("size", [383, 385, 1])
def test_an_embedding_of_any_other_length_than_384_is_refused(database_url: str, size: int) -> None:
    with psycopg.connect(database_url) as conn:
        user = new_user(conn)
        with pytest.raises(errors.DataException, match="expected 384 dimensions"):
            insert(conn, item(user, embedding=vector(size)))
        conn.rollback()


def test_an_embedding_of_384_numbers_or_none_is_kept(database_url: str) -> None:
    with psycopg.connect(database_url) as conn:
        user = new_user(conn)
        kept = insert(conn, item(user))
        empty = insert(conn, item(user, embedding=None))  # made later, after the item is saved
        rows = conn.execute(
            "select id, extensions.vector_dims(embedding) from public.preferences where user_id = %s",
            (user,),
        ).fetchall()
        conn.rollback()
    assert sorted(rows, key=lambda r: r[1] is None) == [(kept, DIMENSIONS), (empty, None)]


@pytest.mark.parametrize("text", ["x", "x" * 200, "₹" * 200, "😀" * 200, "Battery life matters a lot", "Two  words"])
def test_text_of_1_to_200_characters_is_kept(database_url: str, text: str) -> None:
    with psycopg.connect(database_url) as conn:
        user = new_user(conn)
        insert(conn, item(user, text=text))
        row = conn.execute("select text from public.preferences where user_id = %s", (user,)).fetchone()
        conn.rollback()
    assert row == (text,)


# Blank, or with a space at either end: the contract strips spaces, so "   " would be empty there and fail.
@pytest.mark.parametrize("text", ["", "x" * 201, "₹" * 201, " ", "   ", "\n", " Battery", "Battery ", "A\n"])
def test_empty_or_blank_ended_text_or_text_over_200_characters_is_refused(database_url: str, text: str) -> None:
    with psycopg.connect(database_url) as conn:
        user = new_user(conn)
        with pytest.raises(errors.CheckViolation):
            insert(conn, item(user, text=text))
        conn.rollback()


def test_text_is_required(database_url: str) -> None:
    with psycopg.connect(database_url) as conn:
        user = new_user(conn)
        with pytest.raises(errors.NotNullViolation):
            insert(conn, item(user, text=None))
        conn.rollback()


@pytest.mark.parametrize("weight", [0.0, 10.0, 7.5])
def test_a_weight_of_0_to_10_is_kept(database_url: str, weight: float) -> None:
    with psycopg.connect(database_url) as conn:
        user = new_user(conn)
        insert(conn, item(user, weight=weight))
        row = conn.execute("select weight from public.preferences where user_id = %s", (user,)).fetchone()
        conn.rollback()
    assert row == (weight,)


@pytest.mark.parametrize("weight", [11.0, 10.01, -0.5, float("nan"), float("inf")])
def test_a_weight_outside_0_to_10_is_refused(database_url: str, weight: float) -> None:
    with psycopg.connect(database_url) as conn:
        user = new_user(conn)
        with pytest.raises(errors.CheckViolation):
            insert(conn, item(user, weight=weight))
        conn.rollback()


@pytest.mark.parametrize("kind", ["colour", "", "Weight", None])
def test_an_unknown_kind_is_refused(database_url: str, kind: str | None) -> None:
    with psycopg.connect(database_url) as conn:
        user = new_user(conn)
        with pytest.raises((errors.CheckViolation, errors.NotNullViolation)):
            insert(conn, item(user, kind=kind))
        conn.rollback()


@pytest.mark.parametrize("column", ["category", "criterion_key"])
@pytest.mark.parametrize("value", ["laptop", "gaming-laptop", "battery_life", "a" * 64, "usb4"])
def test_a_category_or_criterion_that_is_a_slug_is_kept(database_url: str, column: str, value: str) -> None:
    with psycopg.connect(database_url) as conn:
        user = new_user(conn)
        insert(conn, item(user, **{column: value}))
        conn.rollback()


# The contract's Slug: lower-case letters and digits, joined by single "_" or "-", at most 64 characters.
@pytest.mark.parametrize("column", ["category", "criterion_key"])
@pytest.mark.parametrize("value", ["", "Laptop", "a" * 65, "laptop ", "battery life", "_laptop", "laptop__pro", "₹"])
def test_a_category_or_criterion_that_is_not_a_slug_is_refused(database_url: str, column: str, value: str) -> None:
    with psycopg.connect(database_url) as conn:
        user = new_user(conn)
        with pytest.raises(errors.CheckViolation):
            insert(conn, item(user, **{column: value}))
        conn.rollback()


# Each kind carries its own fields and none of the others', as PreferenceItem does (backend/app/contracts/memory.py).
KEPT_SHAPES: dict[str, dict[str, Any]] = {
    "a weight": {},
    "a must-have": {"kind": "hard_rule", "criterion_key": None, "weight": None, "rule": HARD_RULE},
    "a must-have on a criterion": {"kind": "hard_rule", "criterion_key": "ram_gb", "weight": None, "rule": HARD_RULE},
    "a leave-out": {"kind": "exclusion", "criterion_key": None, "weight": None, "rule": exclusion()},
    "a model left out with Always": {
        "kind": "exclusion",
        "criterion_key": None,
        "weight": None,
        "rule": exclusion("model", "Acme Book 14", always=True),
        "always": True,
    },
    "a brand left out with Always, for every category": {
        "kind": "exclusion",
        "category": None,
        "criterion_key": None,
        "weight": None,
        "rule": exclusion(always=True),
        "always": True,
    },
}
REFUSED_SHAPES: dict[str, dict[str, Any]] = {
    "a weight with no criterion": {"criterion_key": None},
    "a weight with no weight": {"weight": None},
    "a weight with a rule": {"rule": HARD_RULE},
    "a must-have with no rule": {"kind": "hard_rule", "weight": None},
    "a must-have with a weight": {"kind": "hard_rule", "rule": HARD_RULE},
    "a leave-out with no rule": {"kind": "exclusion", "criterion_key": None, "weight": None},
    "a leave-out with a weight": {"kind": "exclusion", "criterion_key": None, "rule": exclusion()},
    "a leave-out with a criterion": {"kind": "exclusion", "weight": None, "rule": exclusion()},
    "a rule that is not an object": {"kind": "hard_rule", "weight": None, "rule": ["ram_gb"]},
    # a model, with a category, so only the Always rule can refuse these two
    "a leave-out marked Always in its rule but not on the item": {
        "kind": "exclusion",
        "criterion_key": None,
        "weight": None,
        "rule": exclusion("model", "Acme Book 14", always=True),
        "always": False,
    },
    "a leave-out marked Always on the item but not in its rule": {
        "kind": "exclusion",
        "criterion_key": None,
        "weight": None,
        "rule": exclusion("model", "Acme Book 14", always=False),
        "always": True,
    },
    "a must-have whose rule is a leave-out": {"kind": "hard_rule", "weight": None, "rule": exclusion()},
    "a must-have with an empty rule": {"kind": "hard_rule", "weight": None, "rule": {}},
    "a leave-out whose rule is a must-have": {
        "kind": "exclusion",
        "criterion_key": None,
        "weight": None,
        "rule": {**HARD_RULE, "always": False},
    },
    "a leave-out of an unknown kind": {
        "kind": "exclusion",
        "criterion_key": None,
        "weight": None,
        "rule": {"kind": "colour", "value": "red", "always": False},
    },
    "a leave-out with no value": {
        "kind": "exclusion",
        "criterion_key": None,
        "weight": None,
        "rule": {"kind": "brand", "always": False},
    },
    "a leave-out that does not say Always or not": {
        "kind": "exclusion",
        "criterion_key": None,
        "weight": None,
        "rule": {"kind": "brand", "value": "Acme"},
    },
    "no category for a weight": {"category": None},
    "no category for a brand left out without Always": {
        "kind": "exclusion",
        "category": None,
        "criterion_key": None,
        "weight": None,
        "rule": exclusion(),
    },
    "no category for a model left out with Always": {
        "kind": "exclusion",
        "category": None,
        "criterion_key": None,
        "weight": None,
        "rule": exclusion("model", "Acme Book 14", always=True),
        "always": True,
    },
    "a category for a brand left out with Always": {
        "kind": "exclusion",
        "criterion_key": None,
        "weight": None,
        "rule": exclusion(always=True),
        "always": True,
    },
}


@pytest.mark.parametrize("shape", KEPT_SHAPES, ids=list(KEPT_SHAPES))
def test_each_kind_with_its_own_fields_is_kept(database_url: str, shape: str) -> None:
    with psycopg.connect(database_url) as conn:
        user = new_user(conn)
        insert(conn, item(user, **KEPT_SHAPES[shape]))
        conn.rollback()


@pytest.mark.parametrize("shape", REFUSED_SHAPES, ids=list(REFUSED_SHAPES))
def test_a_kind_with_the_wrong_fields_is_refused(database_url: str, shape: str) -> None:
    with psycopg.connect(database_url) as conn:
        user = new_user(conn)
        with pytest.raises(errors.CheckViolation):
            insert(conn, item(user, **REFUSED_SHAPES[shape]))
        conn.rollback()


SAVE = (
    "insert into public.preferences (id, user_id, kind, category, criterion_key, text, weight, rule, always, "
    "created_at, updated_at) values (%(id)s, %(user_id)s, %(kind)s, %(category)s, %(criterion_key)s, %(text)s, "
    "%(weight)s, %(rule)s::jsonb, %(always)s, %(created_at)s, %(updated_at)s)"
)


def _row(found: PreferenceItem, user: uuid.UUID) -> dict[str, Any]:
    """A contract item as the columns hold it: a must-have's rule or a leave-out's exclusion goes in `rule`."""
    shape = found.rule if found.kind == "hard_rule" else found.exclusion
    return {
        **found.model_dump(include={"id", "kind", "category", "criterion_key", "text", "weight", "always"}),
        "created_at": found.created_at,
        "updated_at": found.updated_at,
        "user_id": user,
        "rule": None if shape is None else json.dumps(shape.model_dump(mode="json")),
    }


def test_each_example_item_is_saved_and_reads_back_as_the_same_contract(database_url: str) -> None:
    """The contract's own items fit the table, and its columns give the same PreferenceList back."""
    with psycopg.connect(database_url) as conn:
        user = new_user(conn)
        for found in ex.preference_list().items:
            conn.execute(SAVE, _row(found, user))
        rows = conn.execute(
            "select id, kind, category, criterion_key, text, weight, rule, always, created_at, updated_at "
            "from public.preferences where user_id = %s order by created_at desc",
            (user,),
        ).fetchall()
        conn.rollback()
    names = ("id", "kind", "category", "criterion_key", "text", "weight", "rule", "always", "created_at", "updated_at")
    read = []
    for row in rows:
        fields = dict(zip(names, row, strict=True))
        shape = fields.pop("rule")
        read.append(
            PreferenceItem.model_validate(
                {
                    **fields,
                    "rule": shape if fields["kind"] == "hard_rule" else None,
                    "exclusion": shape if fields["kind"] == "exclusion" else None,
                }
            )
        )
    assert PreferenceList(items=read) == ex.preference_list()
    assert {found.kind for found in read} == {"weight", "hard_rule", "exclusion"}


def test_updated_before_created_is_refused(database_url: str) -> None:
    with psycopg.connect(database_url) as conn:
        user = new_user(conn)
        saved = insert(conn, item(user))
        with pytest.raises(errors.CheckViolation):
            conn.execute(
                "update public.preferences set updated_at = created_at - interval '1 second' where id = %s", (saved,)
            )
        conn.rollback()


def test_a_row_for_an_unknown_user_or_with_no_user_is_refused(database_url: str) -> None:
    with psycopg.connect(database_url, autocommit=True) as conn:
        with pytest.raises(errors.ForeignKeyViolation):
            insert(conn, item(uuid.uuid4()))
        with pytest.raises(errors.NotNullViolation):
            insert(conn, item(None))  # type: ignore[arg-type]  # a guest has no memory (D-36)


# --- privileges and the policy ----------------------------------------------------------------------------------------


def test_anon_has_no_access(database_url: str) -> None:
    with psycopg.connect(database_url) as conn:
        for privilege in ALL_PRIVILEGES:
            allowed = conn.execute(
                "select has_table_privilege('anon', 'public.preferences', %s)", (privilege,)
            ).fetchone()
            assert allowed == (False,), privilege
        columns = conn.execute(
            "select column_name, privilege_type from information_schema.column_privileges "
            "where table_schema = 'public' and table_name = 'preferences' and grantee = 'anon'"
        ).fetchall()
    assert columns == []


def test_authenticated_may_only_read(database_url: str) -> None:
    with psycopg.connect(database_url) as conn:
        for privilege in ALL_PRIVILEGES:
            allowed = conn.execute(
                "select has_table_privilege('authenticated', 'public.preferences', %s)", (privilege,)
            ).fetchone()
            assert allowed == (privilege == "select",), privilege
        for privilege in ("insert", "update", "references"):  # a grant on some columns only
            some = conn.execute(
                "select has_any_column_privilege('authenticated', 'public.preferences', %s)", (privilege,)
            ).fetchone()
            assert some == (False,), privilege


def test_the_one_policy_shows_a_signed_in_user_only_their_own_rows(database_url: str) -> None:
    with psycopg.connect(database_url) as conn:
        policies = conn.execute(
            "select policyname, permissive, roles::text[], cmd, qual, with_check from pg_policies "
            "where schemaname = 'public' and tablename = 'preferences'"
        ).fetchall()
    assert policies == [
        (
            "preferences_select_own",
            "PERMISSIVE",
            ["authenticated"],
            "SELECT",
            "(user_id = ( SELECT auth.uid() AS uid))",
            None,
        )
    ]


# --- who can read what ------------------------------------------------------------------------------------------------


def test_user_a_reads_only_their_own_preferences_with_their_embeddings(database_url: str, people: People) -> None:
    with acting_as(database_url, "authenticated", people.a) as conn:
        assert ids(conn) == sorted(people.a_items)
        # the column itself, with no call into the extensions schema (Supabase gives the role usage of it; the plain
        # Postgres here doesn't)
        embeddings = [row[0] for row in conn.execute("select embedding from public.preferences").fetchall()]
        assert [len(str(e).strip("[]").split(",")) for e in embeddings] == [DIMENSIONS] * 3


def test_user_a_cannot_select_user_bs_preferences(database_url: str, people: People) -> None:
    with acting_as(database_url, "authenticated", people.a) as conn:
        found = conn.execute(
            "select count(*) from public.preferences where user_id = %s or id = any(%s)",
            (people.b, list(people.b_items)),
        ).fetchone()
        assert found == (0,)
    with acting_as(database_url, "authenticated", people.b) as conn:
        assert ids(conn) == sorted(people.b_items)


@pytest.mark.parametrize("claims", [False, True])
def test_anon_reads_nothing(database_url: str, people: People, claims: bool) -> None:
    """anon is refused outright, even carrying a user's claims."""
    with (
        acting_as(database_url, "anon", people.a if claims else None) as conn,
        pytest.raises(errors.InsufficientPrivilege),
    ):
        conn.execute("select count(*) from public.preferences")


def test_a_session_with_no_signed_in_user_reads_nothing(database_url: str, people: People) -> None:
    with acting_as(database_url, "authenticated") as conn:
        assert ids(conn) == []


@pytest.mark.parametrize(
    "statement",
    [
        "insert into public.preferences (user_id, kind, category, criterion_key, text, weight) "
        "values (%s, 'weight', 'laptop', 'battery_life', 'made up', 5)",
        "update public.preferences set text = 'changed' where user_id = %s",
        "update public.preferences set always = true where user_id = %s",
        "update public.preferences set user_id = user_id where user_id = %s",
        "delete from public.preferences where user_id = %s",
    ],
)
def test_a_user_can_never_write_their_preferences_through_the_api(
    database_url: str, people: People, statement: str
) -> None:
    """Inserts, changes and deletes stay with the backend, through DATABASE_URL (US-11's routes)."""
    with acting_as(database_url, "authenticated", people.a) as conn, pytest.raises(errors.InsufficientPrivilege):
        conn.execute(statement, (people.a,))


# --- the owner never changes (D-72) -----------------------------------------------------------------------------------


@pytest.mark.parametrize("to", ["another user", "a new user"])
def test_changing_user_id_is_refused(database_url: str, people: People, to: str) -> None:
    with psycopg.connect(database_url) as conn:
        target = people.b if to == "another user" else new_user(conn)
        with pytest.raises(errors.CheckViolation, match="never changes"):
            conn.execute("update public.preferences set user_id = %s where id = %s", (target, people.a_items[0]))
        conn.rollback()
        row = conn.execute("select user_id from public.preferences where id = %s", (people.a_items[0],)).fetchone()
    assert row == (people.a,)


def test_the_trigger_fires_before_an_update_of_user_id(database_url: str) -> None:
    with psycopg.connect(database_url) as conn:
        row = conn.execute(
            "select pg_get_triggerdef(oid) from pg_trigger where tgrelid = 'public.preferences'::regclass "
            "and tgname = 'preferences_user_id_fixed'"
        ).fetchone()
        comment = conn.execute("select obj_description(%s::regprocedure, 'pg_proc')", (FUNCTION,)).fetchone()
    assert row is not None
    assert "BEFORE UPDATE OF user_id ON public.preferences FOR EACH ROW EXECUTE FUNCTION keep_user_id_fixed()" in row[0]
    assert comment == (FUNCTION_COMMENT,)


def test_the_backend_still_changes_the_other_columns(database_url: str, people: People) -> None:
    with psycopg.connect(database_url) as conn:  # rolled back: other tests read the same rows
        changed = conn.execute(
            "update public.preferences set text = 'Battery life matters most', weight = 9, "
            "embedding = %s::extensions.vector, updated_at = now() where id = %s",
            (vector(seed=7), people.a_items[0]),
        )
        same_owner = conn.execute(
            "update public.preferences set user_id = %s where id = %s", (people.a, people.a_items[1])
        )
        assert (changed.rowcount, same_owner.rowcount) == (1, 1)
        conn.rollback()


def test_deleting_a_user_deletes_their_preferences_and_only_theirs(database_url: str, people: People) -> None:
    with psycopg.connect(database_url) as conn:  # rolled back
        conn.execute("delete from auth.users where id = %s", (people.a,))
        left = conn.execute(
            "select user_id, count(*) from public.preferences where user_id = any(%s) group by user_id",
            ([people.a, people.b],),
        ).fetchall()
        conn.rollback()
    assert left == [(people.b, 3)]


# --- safe to apply ----------------------------------------------------------------------------------------------------


def _catalogue(conn: psycopg.Connection[Any]) -> dict[str, list[tuple[Any, ...]]]:
    """Everything the migration sets, read from the catalogue."""
    queries = {
        "extension": "select e.extname, n.nspname, e.extversion from pg_extension e "
        "join pg_namespace n on n.oid = e.extnamespace where e.extname = 'vector'",
        "columns": "select attname, format_type(atttypid, atttypmod), attnotnull from pg_attribute "
        "where attrelid = to_regclass('public.preferences') and attnum > 0 and not attisdropped order by attnum",
        "constraints": "select conname, pg_get_constraintdef(oid) from pg_constraint "
        "where conrelid = to_regclass('public.preferences') order by 1",
        "indexes": "select indexname, indexdef from pg_indexes where schemaname = 'public' "
        "and tablename = 'preferences' order by 1",
        "rls": "select relrowsecurity, relforcerowsecurity from pg_class where oid = to_regclass('public.preferences')",
        "policies": "select policyname, permissive, roles::text[], cmd, qual, with_check from pg_policies "
        "where schemaname = 'public' order by tablename, policyname",
        "grants": "select table_name, grantee, privilege_type from information_schema.role_table_grants "
        "where table_schema = 'public' and grantee in ('anon', 'authenticated') order by 1, 2, 3",
        "triggers": "select tgrelid::regclass::text, tgname, pg_get_triggerdef(oid) from pg_trigger "
        "where not tgisinternal order by 1, 2",
        "comments": "select objsubid, description from pg_description where objoid = to_regclass('public.preferences') "
        "order by 1",
        "function": "select obj_description(%(fn)s::regprocedure, 'pg_proc'), proacl::text from pg_proc "
        "where oid = %(fn)s::regprocedure",
    }
    return {name: conn.execute(query, {"fn": FUNCTION}).fetchall() for name, query in queries.items()}


def test_running_the_migration_again_changes_nothing(database_url: str) -> None:
    with psycopg.connect(database_url) as conn:  # one transaction, rolled back below
        before = _catalogue(conn)
        conn.execute(up_sql().encode("utf-8"))
        conn.execute(up_sql().encode("utf-8"))
        after = _catalogue(conn)
        conn.rollback()
    assert after == before
    assert len(before["policies"]) >= 5 and before["columns"]  # the table and every earlier policy are there


def test_the_down_sql_carries_the_migrations_version() -> None:
    version = up_path().name.split("_", 1)[0]
    assert [p.name for p in ROLLBACK.glob(f"{version}_*.sql")] == [f"{version}_preferences_down.sql"]
    assert not list(MIGRATIONS.glob("*_down.sql"))  # never run by db push or by the tests' set-up


# Everything in the database's own schemas, to compare a database after down with one that never had the migration.
SCHEMAS = ["public", "auth", "extensions"]
EVERYTHING = {
    "schemas": "select nspname, pg_get_userbyid(nspowner), nspacl::text, obj_description(oid, 'pg_namespace') "
    "from pg_namespace where nspname !~ '^pg_' and nspname <> 'information_schema' order by 1",
    "extensions": "select extname, extversion, extnamespace::regnamespace::text from pg_extension order by 1",
    "relations": "select c.oid::regclass::text, c.relkind, c.relrowsecurity, c.relforcerowsecurity, c.relacl::text "
    "from pg_class c join pg_namespace n on n.oid = c.relnamespace where n.nspname = any(%(s)s) order by 1",
    "columns": "select table_schema, table_name, column_name, data_type, udt_name, is_nullable, column_default "
    "from information_schema.columns where table_schema = any(%(s)s) order by 1, 2, 3",
    "constraints": "select conrelid::regclass::text, conname, pg_get_constraintdef(oid) from pg_constraint "
    "where connamespace = any(array(select oid from pg_namespace where nspname = any(%(s)s))) order by 1, 2",
    "indexes": "select schemaname, tablename, indexname, indexdef from pg_indexes where schemaname = any(%(s)s) "
    "order by 1, 2, 3",
    "policies": "select schemaname, tablename, policyname, permissive, roles::text[], cmd, qual, with_check "
    "from pg_policies order by 1, 2, 3",
    "triggers": "select tgrelid::regclass::text, tgname, tgenabled, pg_get_triggerdef(oid) from pg_trigger "
    "where not tgisinternal order by 1, 2",
    "functions": "select p.oid::regprocedure::text, pg_get_functiondef(p.oid), p.proacl::text, "
    "obj_description(p.oid, 'pg_proc') from pg_proc p join pg_namespace n on n.oid = p.pronamespace "
    "where n.nspname in ('public', 'auth') order by 1",
    "comments": "select c.oid::regclass::text, d.objsubid, d.description from pg_description d "
    "join pg_class c on c.oid = d.objoid join pg_namespace n on n.oid = c.relnamespace "
    "where n.nspname = any(%(s)s) order by 1, 2",
    "column_grants": "select table_schema, table_name, column_name, grantee, privilege_type "
    "from information_schema.column_privileges where table_schema = any(%(s)s) "
    "and grantee in ('anon', 'authenticated', 'PUBLIC') order by 1, 2, 3, 4, 5",
}


def _everything(conn: psycopg.Connection[Any]) -> dict[str, list[tuple[Any, ...]]]:
    return {name: conn.execute(query, {"s": SCHEMAS}).fetchall() for name, query in EVERYTHING.items()}


def test_down_takes_out_exactly_what_up_added_and_up_puts_it_back() -> None:
    """Each step committed. After down, the database matches one that never had this migration (with the extensions
    schema there first, as on Supabase); after up again, it matches the first up."""
    with (
        requests_table.migrated_database(before=_supabase_extensions, leave_out=up_path().name) as base,
        psycopg.connect(base) as conn,
    ):
        never = _everything(conn)
        assert conn.execute("select to_regclass('public.preferences')").fetchone() == (None,)
    with (
        requests_table.migrated_database(before=_supabase_extensions) as url,
        psycopg.connect(url, autocommit=True) as conn,
    ):
        up = _everything(conn)
        assert up != never and _catalogue(conn)["columns"]
        user = new_user(conn)
        insert(conn, item(user))

        conn.execute(down_sql().encode("utf-8"))
        assert conn.execute("select count(*) from pg_extension where extname = 'vector'").fetchone() == (0,)
        assert conn.execute("select count(*) from auth.users where id = %s", (user,)).fetchone() == (1,)
        assert _everything(conn) == never
        comment = conn.execute("select obj_description(%s::regprocedure, 'pg_proc')", (FUNCTION,)).fetchone()
        assert comment == (FUNCTION_COMMENT_BEFORE,)

        conn.execute(down_sql().encode("utf-8"))  # a second run finds nothing to undo
        assert _everything(conn) == never

        conn.execute(up_sql().encode("utf-8"))
        assert _everything(conn) == up
        insert(conn, item(user))


def test_the_migration_runs_up_down_and_up_again_where_it_made_the_extensions_schema() -> None:
    """On a plain Postgres the up makes the extensions schema; down leaves it, and up runs again over it."""
    with requests_table.migrated_database() as url, psycopg.connect(url, autocommit=True) as conn:
        up = _everything(conn)
        conn.execute(down_sql().encode("utf-8"))
        assert conn.execute("select to_regclass('public.preferences')").fetchone() == (None,)
        assert conn.execute("select count(*) from pg_namespace where nspname = 'extensions'").fetchone() == (1,)
        conn.execute(up_sql().encode("utf-8"))
        assert _everything(conn) == up


def test_the_down_sql_keeps_pgvector_while_another_table_uses_it() -> None:
    with requests_table.migrated_database() as url, psycopg.connect(url, autocommit=True) as conn:
        conn.execute("create table public.other_vectors (v extensions.vector(3))")
        conn.execute(down_sql().encode("utf-8"))
        assert conn.execute("select to_regclass('public.preferences')").fetchone() == (None,)
        assert conn.execute("select count(*) from pg_extension where extname = 'vector'").fetchone() == (1,)


def test_supabases_default_grants_on_a_new_table_are_undone(database_url: str) -> None:
    """Supabase grants every new table in public to anon and authenticated. A plain Postgres doesn't, so this copies
    that set-up: without the migration's revokes, this fails."""
    with psycopg.connect(database_url) as conn:  # one transaction, rolled back below
        conn.execute("drop table public.preferences")
        conn.execute("alter default privileges in schema public grant all on tables to anon, authenticated")
        conn.execute("create table public.default_grant_control (id int)")
        control = conn.execute("select has_table_privilege('anon', 'public.default_grant_control', 'select')")
        assert control.fetchone() == (True,)  # the default grant is really in force

        conn.execute(up_sql().encode("utf-8"))
        for privilege in ALL_PRIVILEGES:
            anon = conn.execute("select has_table_privilege('anon', 'public.preferences', %s)", (privilege,))
            assert anon.fetchone() == (False,), privilege
            signed_in = conn.execute(
                "select has_table_privilege('authenticated', 'public.preferences', %s)", (privilege,)
            )
            assert signed_in.fetchone() == (privilege == "select",), privilege
        conn.rollback()


def test_the_role_blocks_are_guarded_for_a_plain_postgres(database_url: str) -> None:
    """With no anon or authenticated role, the migration still applies (the database rules ask for the guards)."""
    plain = (
        up_sql()
        .replace("rolname = 'anon'", "rolname = 'no_such_role_a'")
        .replace("rolname = 'authenticated'", "rolname = 'no_such_role_b'")
    )
    assert plain != up_sql()
    with psycopg.connect(database_url) as conn:
        conn.execute("drop table public.preferences")
        conn.execute(plain.encode("utf-8"))
        assert conn.execute("select to_regclass('public.preferences')").fetchone() == ("preferences",)
        conn.rollback()


def _supabase_extensions(conn: psycopg.Connection[Any]) -> None:
    """Supabase's own extensions schema, made before any migration, with the usage it gives the Data API roles."""
    conn.execute("create schema extensions")
    conn.execute("grant usage on schema extensions to anon, authenticated")
    conn.execute("comment on schema extensions is 'Supabase''s own: a migration never replaces it'")


def test_a_database_that_already_has_the_extensions_schema_keeps_it() -> None:
    with requests_table.migrated_database(before=_supabase_extensions) as url, psycopg.connect(url) as conn:
        schema = conn.execute(
            "select obj_description(oid, 'pg_namespace'), has_schema_privilege('anon', oid, 'usage') "
            "from pg_namespace where nspname = 'extensions'"
        ).fetchone()
        assert schema == ("Supabase's own: a migration never replaces it", True)
        assert conn.execute("select to_regclass('public.preferences')").fetchone() == ("preferences",)


def test_the_extensions_schema_made_on_a_plain_postgres_gives_no_role_anything(database_url: str) -> None:
    """On a plain Postgres the migration makes the schema only so pgvector has its home; the Data API roles still read
    their embeddings through the table's own select (above)."""
    with psycopg.connect(database_url) as conn:
        for role in ("anon", "authenticated"):
            row = conn.execute("select has_schema_privilege(%s, 'extensions', 'create')", (role,)).fetchone()
            assert row == (False,), role
