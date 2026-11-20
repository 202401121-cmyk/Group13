"""US-30 on a real Postgres: saving with an owner, the profile at first sign-in, and the user routes' reads (db).

Same rules as test_requests_table.py, which owns the fixture: a throwaway database with every migration applied (the
S4-9 user tables and S4-27's fixed owner included), never Supabase. Users exist in the stub auth.users the migration
makes on a plain Postgres. Tokens are signed locally (tests/fakes/auth.py); no real account is used.
"""

from __future__ import annotations

import secrets
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.types.json import Jsonb

from app.api.account import get_account_store
from app.auth import get_token_verifier
from app.contracts import SavedSearchList, SearchRequest
from app.contracts import examples as ex
from app.db import AccountsUnavailable, DatabaseSettings, PageAfter, PostgresRequestStore, UserGone
from app.db import postgres as pg
from app.main import create_app
from tests.fakes.auth import TestSigner, bearer, verifier_for
from tests.integration import test_requests_table as requests_table

pytestmark = pytest.mark.db

database_url = requests_table.database_url  # a fresh database of its own for this file
SIGNER = TestSigner()


def new_session() -> str:
    return "sess_" + secrets.token_hex(12)


@pytest.fixture
def store(database_url: str) -> Iterator[PostgresRequestStore]:
    built = PostgresRequestStore(DatabaseSettings(url=database_url, timeout_s=2.0))
    yield built
    built.close()


@pytest.fixture
def users(database_url: str) -> tuple[uuid.UUID, uuid.UUID]:
    """Users A and B, as Supabase Auth would hold them, with no profile yet."""
    a, b = uuid.uuid4(), uuid.uuid4()
    with psycopg.connect(database_url, autocommit=True) as conn:
        for user in (a, b):
            conn.execute("insert into auth.users (id) values (%s)", (user,))
    return a, b


def _result(raw_query: str = ex.QUERY, score: float | None = 78.3) -> dict[str, Any]:
    result = ex.search_result().model_dump(mode="json")
    result["request"]["raw_query"] = raw_query
    result["decision"]["ranked"][0]["score"] = score
    return result


def _save(url: str, owner: uuid.UUID | None, created_at: datetime, result: dict[str, Any] | None = None) -> uuid.UUID:
    """A saved search with a chosen time, as the backend's insert writes it."""
    with psycopg.connect(url, autocommit=True) as conn:
        row = conn.execute(
            "insert into public.searches (session_id, rubric, result, user_id, created_at) "
            "values (%s, %s, %s, %s, %s) returning id",
            (
                new_session(),
                Jsonb(ex.rubric().model_dump(mode="json")),
                Jsonb(result or _result()),
                owner,
                created_at,
            ),
        ).fetchone()
    assert row is not None
    found: uuid.UUID = row[0]
    return found


def _owners(url: str, table: str, session: str) -> list[uuid.UUID | None]:
    query = sql.SQL("select user_id from public.{} where session_id = %s order by created_at, id").format(
        sql.Identifier(table)
    )
    with psycopg.connect(url) as conn:
        return [row[0] for row in conn.execute(query, (session,)).fetchall()]


# --- saving with an owner ----------------------------------------------------------------------------------------


def test_a_signed_in_users_rows_are_inserted_with_their_id_and_a_guests_without(
    store: PostgresRequestStore, database_url: str, users: tuple[uuid.UUID, uuid.UUID]
) -> None:
    a, _ = users
    signed, guest = new_session(), new_session()
    assert store.save_request(signed, ex.QUERY, ex.intent(), a) == "saved"
    assert store.save_request(guest, ex.QUERY, ex.intent()) == "saved"
    assert store.save_search(signed, {}, _result(), a) is True
    assert store.save_search(guest, {}, _result()) is True
    assert _owners(database_url, "requests", signed) == [a] and _owners(database_url, "searches", signed) == [a]
    assert _owners(database_url, "requests", guest) == [None] and _owners(database_url, "searches", guest) == [None]


def test_signing_in_later_adds_new_rows_and_never_links_the_guests(
    store: PostgresRequestStore, database_url: str, users: tuple[uuid.UUID, uuid.UUID]
) -> None:
    a, _ = users
    session = new_session()
    store.save_request(session, ex.QUERY, ex.intent())
    store.save_search(session, {}, _result())
    store.save_request(session, ex.QUERY, ex.intent(), a)
    store.save_search(session, {}, _result(), a)
    assert _owners(database_url, "requests", session) == [None, a]
    assert _owners(database_url, "searches", session) == [None, a]


def test_the_store_never_updates_a_row() -> None:
    """user_id is set only by an insert (S4-27 also refuses any change of it in the database)."""
    statements = [value for name, value in vars(pg).items() if name.isupper() and isinstance(value, str)]
    assert statements and not any("update" in text.casefold() for text in statements)


def test_a_deleted_accounts_saves_are_refused_without_opening_the_breaker(
    store: PostgresRequestStore, database_url: str
) -> None:
    """A token stays valid for up to an hour after its account is deleted; its saves must not stop everyone's."""
    gone = uuid.uuid4()  # not in auth.users
    for _ in range(4):
        assert store.save_request(new_session(), ex.QUERY, ex.intent(), gone) == "failed"
        assert store.save_search(new_session(), {}, _result(), gone) is False
    assert store.breaker.state == "closed"
    assert store.save_request(new_session(), ex.QUERY, ex.intent()) == "saved"


# --- the profile at first sign-in --------------------------------------------------------------------------------


def test_the_profile_is_made_at_first_sign_in_and_nothing_if_it_exists(
    store: PostgresRequestStore, database_url: str, users: tuple[uuid.UUID, uuid.UUID]
) -> None:
    a, _ = users
    assert store.ensure_profile(a) is None
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute("update public.profiles set display_name = 'Asha' where id = %s", (a,))
    assert store.ensure_profile(a) == "Asha"  # kept as it was
    with psycopg.connect(database_url) as conn:
        count = conn.execute("select count(*) from public.profiles where id = %s", (a,)).fetchone()
    assert count == (1,)


def test_a_user_no_longer_in_auth_users_is_gone_and_the_breaker_does_not_count_it(store: PostgresRequestStore) -> None:
    for _ in range(4):
        with pytest.raises(UserGone):
            store.ensure_profile(uuid.uuid4())
    assert store.breaker.state == "closed"


# --- the reads, scoped to the user -------------------------------------------------------------------------------


def test_user_a_reads_only_their_own_searches_newest_first(
    store: PostgresRequestStore, database_url: str, users: tuple[uuid.UUID, uuid.UUID]
) -> None:
    a, b = users
    start = datetime(2026, 11, 20, 8, 0, tzinfo=UTC)
    mine = [_save(database_url, a, start + timedelta(minutes=i)) for i in range(5)]
    theirs = _save(database_url, b, start + timedelta(hours=1))
    guests = _save(database_url, None, start + timedelta(hours=2))
    first = store.saved_searches(a, None, 3)
    assert [row.id for row in first] == mine[::-1][:3]
    rest = store.saved_searches(a, PageAfter(first[-1].created_at, first[-1].id), 3)
    assert [row.id for row in rest] == mine[::-1][3:]
    assert store.saved_search(a, theirs) is None and store.saved_search(a, guests) is None
    assert store.saved_search(b, theirs) is not None
    assert store.saved_search(a, uuid.uuid4()) is None


def test_searches_saved_at_the_same_moment_page_by_id(
    store: PostgresRequestStore, database_url: str, users: tuple[uuid.UUID, uuid.UUID]
) -> None:
    a, _ = users
    moment = datetime(2026, 11, 19, 8, 0, tzinfo=UTC)
    made = {_save(database_url, a, moment) for _ in range(4)}
    first = store.saved_searches(a, None, 2)
    second = store.saved_searches(a, PageAfter(first[-1].created_at, first[-1].id), 10)
    seen = [row.id for row in first + second]
    assert set(seen) == made and len(seen) == 4


def test_a_summary_reads_the_request_category_and_the_scored_top_product(
    store: PostgresRequestStore, database_url: str, users: tuple[uuid.UUID, uuid.UUID]
) -> None:
    a, _ = users
    now = datetime.now(UTC)
    _save(database_url, a, now, _result(score=None))
    _save(database_url, a, now + timedelta(seconds=1), _result("x" * 1000))
    _save(database_url, a, now + timedelta(seconds=2), {"broken": True})  # an old or odd row never breaks the list
    odd, long_one, unscored = store.saved_searches(a, None, 10)
    assert (odd.request_text, odd.category, odd.top_product) == (None, "laptop", None)
    assert long_one.request_text == "x" * 1000 and long_one.top_product == "Nova Book 14"
    assert unscored.top_product is None


def test_a_database_that_is_down_makes_the_reads_unavailable() -> None:
    down = PostgresRequestStore(DatabaseSettings(url="postgresql://nobody@127.0.0.1:1/none", timeout_s=0.5))
    try:
        for read in (
            lambda: down.ensure_profile(uuid.uuid4()),
            lambda: down.saved_searches(uuid.uuid4(), None, 20),
            lambda: down.saved_search(uuid.uuid4(), uuid.uuid4()),
        ):
            with pytest.raises(AccountsUnavailable):
                read()
    finally:
        down.close()


# --- through the API -------------------------------------------------------------------------------------------


def test_through_the_api_a_cannot_read_bs_search(
    store: PostgresRequestStore, monkeypatch: pytest.MonkeyPatch, users: tuple[uuid.UUID, uuid.UUID]
) -> None:
    a, b = users
    monkeypatch.setattr("app.db.default_request_store", lambda: store)
    app = create_app(env={})
    verifier, _ = verifier_for(SIGNER)
    app.dependency_overrides[get_token_verifier] = lambda: verifier
    app.dependency_overrides[get_account_store] = lambda: store
    body = SearchRequest(session_id=new_session(), raw_query=ex.QUERY, rubric=ex.rubric()).model_dump(mode="json")
    with TestClient(app) as client:
        assert client.get("/api/me", headers=bearer(SIGNER.token(b))).json()["signed_in"] is True
        searched = client.post("/api/search", json=body, headers=bearer(SIGNER.token(b)))
        assert searched.status_code == 200 and searched.json()["saved"] is True
        listed = SavedSearchList.model_validate(
            client.get("/api/me/searches", headers=bearer(SIGNER.token(b))).json()
        ).items
        assert len(listed) == 1
        own = client.get(f"/api/me/searches/{listed[0].id}", headers=bearer(SIGNER.token(b)))
        assert own.status_code == 200 and own.json()["saved"] is True
        stolen = client.get(f"/api/me/searches/{listed[0].id}", headers=bearer(SIGNER.token(a)))
        assert stolen.status_code == 404
        assert client.get("/api/me/searches", headers=bearer(SIGNER.token(a))).json()["items"] == []
