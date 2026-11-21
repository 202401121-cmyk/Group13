"""US-11 on a real Postgres with pgvector. Marker `db`; needs TEST_DATABASE_URL.

- Deleting one preference leaves no row and no vector: the embedding lives in the same row (S5-1).
- Editing a preference's text stores a new embedding; user B can't edit or delete user A's.
- Forgetting everything removes only the shopper's own.
- Deleting the account (DELETE /api/me), with a fake Auth admin API that deletes the user from auth.users as Supabase
  does: the foreign keys remove the user's requests, searches, profile and preferences, the read-back finds none, and
  another user's rows are untouched.
Made-up rows only, under the model-free word embedder.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.api.account import get_account_store, get_auth_admin, get_memory_controls
from app.auth import get_token_verifier
from app.contracts import Exclusion, HardRule
from app.contracts import examples as ex
from app.db import DatabaseSettings, PostgresRequestStore
from app.interview.rubric_builder import build_rubric
from app.main import create_app
from app.memory import MemoryService, MemorySettings, PostgresPreferenceStore
from tests.fakes.auth import TestSigner, bearer, verifier_for
from tests.integration import test_requests_table as requests_table
from tests.integration.test_memory_in_postgres import DIMENSIONS, new_user, state_for
from tests.unit.memory.fakes import WordEmbedder, ready_holder, vector_of
from tests.unit.memory.test_text_and_lasting import answered

pytestmark = pytest.mark.db

database_url = requests_table.database_url  # a fresh database of its own for this file
SIGNER = TestSigner()
RAM = HardRule(attribute="ram_gb", operator=">=", value=16, unit="GB", source_text="at least 16 GB of RAM")
TABLES = {
    "requests": "select count(*) from public.requests where user_id = %s",
    "searches": "select count(*) from public.searches where user_id = %s",
    "profiles": "select count(*) from public.profiles where id = %s",
    "preferences": "select count(*) from public.preferences where user_id = %s",
}


@pytest.fixture
def store(database_url: str) -> Iterator[PostgresRequestStore]:
    built = PostgresRequestStore(DatabaseSettings(url=database_url, timeout_s=2.0))
    yield built
    built.close()


@pytest.fixture
def memory(store: PostgresRequestStore) -> MemoryService:
    return MemoryService(MemorySettings(), ready_holder(WordEmbedder(DIMENSIONS)), PostgresPreferenceStore(store))


def seed(memory: MemoryService, user: uuid.UUID) -> None:
    state = answered(
        state_for(hard_rules=[RAM], exclusions=[Exclusion(kind="brand", value="Acme", always=True)]),
        "battery_life",
        8.0,
    )
    assert memory.remember(user, state, build_rubric(state)) == 3


def counts(database_url: str, user: uuid.UUID) -> dict[str, int]:
    with psycopg.connect(database_url) as conn:
        return {name: conn.execute(query, (user,)).fetchone()[0] for name, query in TABLES.items()}  # type: ignore[index]


def embedding_of(database_url: str, preference_id: uuid.UUID) -> list[float] | None:
    with psycopg.connect(database_url) as conn:
        row = conn.execute("select embedding::text from public.preferences where id = %s", (preference_id,)).fetchone()
    return None if row is None else [float(x) for x in row[0].strip("[]").split(",")]


def test_deleting_one_preference_leaves_no_row_and_no_vector(database_url: str, memory: MemoryService) -> None:
    user = new_user(database_url)
    seed(memory, user)
    [weight] = [r for r in memory.listing(user) if r.kind == "weight"]
    assert embedding_of(database_url, weight.id) is not None
    assert memory.delete(user, weight.id) is True
    assert embedding_of(database_url, weight.id) is None
    with psycopg.connect(database_url) as conn:
        left = conn.execute("select count(*) from public.preferences where id = %s", (weight.id,)).fetchone()
    assert left == (0,)
    assert counts(database_url, user)["preferences"] == 2
    assert memory.delete(user, weight.id) is False


def test_an_edit_to_text_stores_a_new_embedding(database_url: str, memory: MemoryService) -> None:
    from app.contracts import PreferenceEdit

    user = new_user(database_url)
    seed(memory, user)
    [weight] = [r for r in memory.listing(user) if r.kind == "weight"]
    before = embedding_of(database_url, weight.id)
    edited = memory.edit(user, weight.id, PreferenceEdit(text="Battery life: it must last all day"))
    assert edited is not None and not edited.embedding_kept
    after = embedding_of(database_url, weight.id)
    assert after is not None and after != before
    assert after == pytest.approx(vector_of("Battery life: it must last all day", DIMENSIONS), abs=1e-6)


def test_user_b_cant_edit_or_delete_user_as_preferences(database_url: str, memory: MemoryService) -> None:
    from app.contracts import PreferenceEdit

    a, b = new_user(database_url), new_user(database_url)
    seed(memory, a)
    seed(memory, b)
    mine = memory.listing(a)
    for row in mine:
        assert memory.edit(b, row.id, PreferenceEdit(text="taken over")) is None
        assert memory.delete(b, row.id) is False
    assert memory.listing(a) == mine
    assert {r.id for r in memory.listing(b)}.isdisjoint({r.id for r in mine})


def test_forgetting_everything_removes_only_the_shoppers_own(database_url: str, memory: MemoryService) -> None:
    a, b = new_user(database_url), new_user(database_url)
    seed(memory, a)
    seed(memory, b)
    assert memory.delete_all(a) == 3
    assert counts(database_url, a)["preferences"] == 0 and counts(database_url, b)["preferences"] == 3


def test_the_list_is_newest_first(database_url: str, memory: MemoryService) -> None:
    user = new_user(database_url)
    seed(memory, user)
    listed = memory.listing(user)
    assert [r.created_at for r in listed] == sorted((r.created_at for r in listed), reverse=True)


# --- deleting the account ---------------------------------------------------------------------------------------------


class DeletesFromAuthUsers:
    """Supabase's Auth admin API, as far as this test needs it: the user leaves auth.users."""

    def __init__(self, url: str) -> None:
        self.url = url

    def delete_user(self, user_id: uuid.UUID) -> None:
        with psycopg.connect(self.url, autocommit=True) as conn:
            conn.execute("delete from auth.users where id = %s", (user_id,))


def everything_of(store: PostgresRequestStore, memory: MemoryService, user: uuid.UUID) -> None:
    """A request, a search, a profile and three preferences, saved the way the app saves them."""
    assert store.save_request(ex.SESSION_ID, ex.QUERY, ex.intent(), user) == "saved"
    rubric, result = ex.rubric().model_dump(mode="json"), ex.search_result().model_dump(mode="json")
    assert store.save_search(ex.SESSION_ID, rubric, result, user) is True
    store.ensure_profile(user)
    seed(memory, user)


def client_for(database_url: str, store: PostgresRequestStore, memory: MemoryService) -> TestClient:
    verifier, _ = verifier_for(SIGNER)
    app = create_app(env={})
    app.dependency_overrides[get_token_verifier] = lambda: verifier
    app.dependency_overrides[get_account_store] = lambda: store
    app.dependency_overrides[get_auth_admin] = lambda: DeletesFromAuthUsers(database_url)
    app.dependency_overrides[get_memory_controls] = lambda: memory
    return TestClient(app)


def test_deleting_the_account_removes_all_four_tables_rows_and_only_the_users(
    database_url: str, store: PostgresRequestStore, memory: MemoryService
) -> None:
    a, b = new_user(database_url), new_user(database_url)
    everything_of(store, memory, a)
    everything_of(store, memory, b)
    assert counts(database_url, a) == {"requests": 1, "searches": 1, "profiles": 1, "preferences": 3}
    response = client_for(database_url, store, memory).request(
        "DELETE", "/api/me", headers=bearer(SIGNER.token(a)), json={"confirm": "delete"}
    )
    assert response.status_code == 204, response.text
    assert counts(database_url, a) == {"requests": 0, "searches": 0, "profiles": 0, "preferences": 0}
    assert counts(database_url, b) == {"requests": 1, "searches": 1, "profiles": 1, "preferences": 3}


def test_the_account_delete_is_refused_without_the_word_and_nothing_goes(
    database_url: str, store: PostgresRequestStore, memory: MemoryService
) -> None:
    a = new_user(database_url)
    everything_of(store, memory, a)
    response = client_for(database_url, store, memory).request(
        "DELETE", "/api/me", headers=bearer(SIGNER.token(a)), json={"confirm": "no"}
    )
    assert response.status_code == 422
    assert counts(database_url, a) == {"requests": 1, "searches": 1, "profiles": 1, "preferences": 3}


def test_the_read_back_names_the_user(database_url: str, store: PostgresRequestStore, memory: MemoryService) -> None:
    a, b = new_user(database_url), new_user(database_url)
    everything_of(store, memory, a)
    everything_of(store, memory, b)
    assert (
        store.account_rows(a)
        == counts(database_url, a)
        == {"requests": 1, "searches": 1, "profiles": 1, "preferences": 3}
    )


def test_rows_left_after_the_delete_are_never_answered_204(
    database_url: str, store: PostgresRequestStore, memory: MemoryService
) -> None:
    """An admin API that answers but deletes nothing: the read-back finds the rows, so the route says 503."""

    class DeletesNothing:
        def delete_user(self, user_id: uuid.UUID) -> None:
            return None

    a = new_user(database_url)
    everything_of(store, memory, a)
    client = client_for(database_url, store, memory)
    client.app.dependency_overrides[get_auth_admin] = DeletesNothing  # type: ignore[attr-defined]
    response = client.request("DELETE", "/api/me", headers=bearer(SIGNER.token(a)), json={"confirm": "delete"})
    assert response.status_code == 503
    assert counts(database_url, a)["preferences"] == 3  # nothing deleted behind the shopper's back either


def test_the_routes_on_real_rows(database_url: str, store: PostgresRequestStore, memory: MemoryService) -> None:
    """The list, the export and an edit through the API on Postgres rows: jsonb rules, real weights, timestamptz."""
    from app.contracts import PreferenceExport, PreferenceItem, PreferenceList

    a = new_user(database_url)
    seed(memory, a)
    client = client_for(database_url, store, memory)
    me = bearer(SIGNER.token(a))
    listed = PreferenceList.model_validate(client.get("/api/me/preferences", headers=me).json())
    assert sorted(i.kind for i in listed.items) == ["exclusion", "hard_rule", "weight"]
    exported = client.get("/api/me/preferences/export", headers=me)
    assert exported.headers["content-disposition"].startswith("attachment;")
    assert len(PreferenceExport.model_validate(exported.json()).items) == 3
    weight = next(i for i in listed.items if i.kind == "weight")
    edited = client.patch(f"/api/me/preferences/{weight.id}", headers=me, json={"weight": 7.3})
    assert edited.status_code == 200, edited.text
    assert PreferenceItem.model_validate(edited.json()).weight == 7.3  # read back exactly, in text mode
    rule = next(i for i in listed.items if i.kind == "hard_rule")
    switched = client.patch(f"/api/me/preferences/{rule.id}", headers=me, json={"active": False})
    item = PreferenceItem.model_validate(switched.json())
    assert item.rule is not None and item.rule.active is False


def test_the_list_is_newest_first_across_different_times(database_url: str, memory: MemoryService) -> None:
    user = new_user(database_url)
    seed(memory, user)
    later = answered(state_for("headphones"), "sound_quality", 8.0)
    memory.remember(user, later, build_rubric(later))  # another transaction: a later created_at
    listed = memory.listing(user)
    assert listed[0].category == "headphones" and len({r.created_at for r in listed}) == 2
    assert [r.created_at for r in listed] == sorted((r.created_at for r in listed), reverse=True)


def test_an_edit_from_a_stale_read_never_lands(database_url: str, memory: MemoryService) -> None:
    from app.memory.store import Change

    user = new_user(database_url)
    seed(memory, user)
    weight = next(r for r in memory.listing(user) if r.kind == "weight")
    prefs = memory._store
    assert prefs.update(user, weight.id, Change("Battery: newer", 9.0, None, read_at=weight.updated_at)) is not None
    stale = prefs.update(user, weight.id, Change("Battery: stale", 1.0, None, read_at=weight.updated_at))
    assert stale is None
    assert next(r for r in memory.listing(user) if r.kind == "weight").weight == 9.0


def test_a_deleted_preference_is_never_made_again_by_an_open_interview(
    database_url: str, memory: MemoryService
) -> None:
    from app.interview.remembered import with_remembered

    user = new_user(database_url)
    seed(memory, user)
    state = state_for()
    state = with_remembered(state, memory.recall(user, state))
    assert state.remembered
    assert memory.delete_all(user) == 3
    memory.remember(user, state, build_rubric(state))
    assert counts(database_url, user)["preferences"] == 0


def test_the_account_tables_named_are_the_ones_with_a_user(database_url: str) -> None:
    """Every table in public with a key to auth.users is one the read-back counts: a new one must be added here."""
    with psycopg.connect(database_url) as conn:
        found = conn.execute(
            "select c.conrelid::regclass::text from pg_constraint c where c.contype = 'f' "
            "and c.confrelid = 'auth.users'::regclass order by 1"
        ).fetchall()
    owned: Any = {name.removeprefix("public.") for (name,) in found}
    assert owned == set(TABLES)
