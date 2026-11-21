"""US-11: the shopper sees, edits, exports and deletes what the app remembers (P-43). The owner comes from the token
only; every query names the user; another user's id answers 404, never 403. No database and no network: the
preferences are in the in-memory store, and tokens are signed locally (tests/fakes/auth.py)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.account import (
    ACCOUNT_UNAVAILABLE,
    EMBEDDING_KEPT,
    MEMORY_NOTE_HEADER,
    PREFERENCE_NOT_FOUND,
    SIGN_IN_FOR_MEMORY,
    get_account_store,
    get_memory_controls,
)
from app.auth import get_token_verifier
from app.contracts import Exclusion, HardRule, PreferenceExport, PreferenceItem, PreferenceList
from app.db import InMemoryRequestStore
from app.interview.rubric_builder import build_rubric
from app.main import create_app
from app.memory import InMemoryPreferenceStore, MemoryService, MemorySettings, StoredPreference
from app.memory.embedder import MemoryUnavailable
from tests.fakes.auth import TestSigner, bearer, verifier_for
from tests.unit.memory.fakes import WordEmbedder, ready_holder, vector_of
from tests.unit.memory.test_service import state_for
from tests.unit.memory.test_text_and_lasting import answered

A = uuid.UUID("a1000000-0000-4000-8000-00000000000a")
B = uuid.UUID("b1000000-0000-4000-8000-00000000000b")
SIGNER = TestSigner()
NOW = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
RAM = HardRule(attribute="ram_gb", operator=">=", value=16, unit="GB", source_text="at least 16 GB of RAM")


class BrokenModel:
    def start(self) -> None:
        return None

    def get(self, timeout: float) -> Any:
        raise MemoryUnavailable("the model could not be loaded (RuntimeError)")


class DownStore(InMemoryPreferenceStore):
    """The database is down for every preference read and write."""

    def listing(self, user_id: uuid.UUID) -> list[StoredPreference]:
        raise MemoryUnavailable("the database could not be read")

    def one(self, user_id: uuid.UUID, preference_id: uuid.UUID) -> StoredPreference | None:
        raise MemoryUnavailable("the database could not be read")

    def delete(self, user_id: uuid.UUID, preference_id: uuid.UUID) -> bool:
        raise MemoryUnavailable("the database could not be written")

    def delete_all(self, user_id: uuid.UUID) -> int:
        raise MemoryUnavailable("the database could not be written")


class World:
    def __init__(self, holder: Any = None, store: InMemoryPreferenceStore | None = None) -> None:
        self.clock = {"now": NOW - timedelta(days=3)}
        self.store = store or InMemoryPreferenceStore(clock=lambda: self.clock["now"])
        self.memory = MemoryService(
            MemorySettings(), holder or ready_holder(WordEmbedder()), self.store, clock=lambda: self.clock["now"]
        )
        verifier, _ = verifier_for(SIGNER)
        self.app = create_app(env={})
        self.app.dependency_overrides[get_token_verifier] = lambda: verifier
        self.accounts = InMemoryRequestStore()
        self.app.dependency_overrides[get_account_store] = lambda: self.accounts
        self.app.dependency_overrides[get_memory_controls] = lambda: self.memory
        self.client = TestClient(self.app)

    def seed(self, user: uuid.UUID) -> None:
        """A weight, a must-have and a brand left out with Always, saved as the interview saves them."""
        state = answered(
            state_for(hard_rules=[RAM], exclusions=[Exclusion(kind="brand", value="Acme", always=True)]),
            "battery_life",
            8.0,
        )
        seeding = MemoryService(
            MemorySettings(), ready_holder(WordEmbedder()), self.store, clock=lambda: self.clock["now"]
        )
        assert seeding.remember(user, state, build_rubric(state)) == 3  # a working model, whatever this world has
        self.clock["now"] = NOW

    def call(self, method: str, path: str, user: uuid.UUID | None = None, **kwargs: Any) -> Any:
        headers = bearer(SIGNER.token(user)) if user else {}
        return self.client.request(method, f"/api/me{path}", headers=headers, **kwargs)

    def items(self, user: uuid.UUID) -> list[dict[str, Any]]:
        response = self.call("GET", "/preferences", user)
        assert response.status_code == 200, response.text
        found: list[dict[str, Any]] = response.json()["items"]
        return found

    def row(self, user: uuid.UUID, kind: str) -> StoredPreference:
        return next(r for r in self.store.rows(user) if r.kind == kind)


@pytest.fixture
def world() -> World:
    built = World()
    built.seed(A)
    built.seed(B)
    return built


# --- see --------------------------------------------------------------------------------------------------------------


def test_the_shopper_sees_every_stored_preference_and_only_their_own(world: World) -> None:
    items = world.items(A)
    assert PreferenceList.model_validate({"items": items})
    assert sorted(i["kind"] for i in items) == ["exclusion", "hard_rule", "weight"]
    assert {i["id"] for i in items} == {str(r.id) for r in world.store.rows(A)}
    assert not {i["id"] for i in items} & {str(r.id) for r in world.store.rows(B)}


def test_a_guest_or_a_bad_token_sees_nothing(world: World) -> None:
    assert world.call("GET", "/preferences").status_code == 401
    assert world.call("GET", "/preferences").json()["detail"] == SIGN_IN_FOR_MEMORY
    bad = world.client.get("/api/me/preferences", headers={"Authorization": "Bearer not-a-token"})
    assert bad.status_code == 401


# --- edit -------------------------------------------------------------------------------------------------------------


def test_an_edit_to_text_updates_the_embedding(world: World) -> None:
    weight = world.row(A, "weight")
    response = world.call("PATCH", f"/preferences/{weight.id}", A, json={"text": "Battery life: it must last all day"})
    assert response.status_code == 200, response.text
    item = PreferenceItem.model_validate(response.json())
    assert item.text == "Battery life: it must last all day" and MEMORY_NOTE_HEADER not in response.headers
    after = world.row(A, "weight")
    assert after.embedding == tuple(vector_of("Battery life: it must last all day"))
    assert after.embedding != weight.embedding and after.updated_at > weight.updated_at


def test_when_the_embedding_fails_the_row_keeps_its_old_one_and_says_so() -> None:
    world = World(holder=BrokenModel())
    world.seed(A)
    weight = world.row(A, "weight")
    response = world.call("PATCH", f"/preferences/{weight.id}", A, json={"text": "Battery: all day"})
    assert response.status_code == 200
    assert response.headers[MEMORY_NOTE_HEADER] == EMBEDDING_KEPT
    after = world.row(A, "weight")
    assert after.text == "Battery: all day" and after.embedding == weight.embedding


def test_a_weight_edit_changes_the_weight_and_its_plain_words_but_not_the_embedding(world: World) -> None:
    weight = world.row(A, "weight")
    response = world.call("PATCH", f"/preferences/{weight.id}", A, json={"weight": 2})
    assert response.status_code == 200
    item = PreferenceItem.model_validate(response.json())
    assert (item.weight, item.text.endswith(": low importance")) == (2.0, True)
    assert world.row(A, "weight").embedding == weight.embedding


def test_a_must_have_can_be_switched_off(world: World) -> None:
    rule = world.row(A, "hard_rule")
    response = world.call("PATCH", f"/preferences/{rule.id}", A, json={"active": False})
    assert response.status_code == 200
    assert PreferenceItem.model_validate(response.json()).rule is not None
    assert world.row(A, "hard_rule").rule["active"] is False  # type: ignore[index]


@pytest.mark.parametrize(
    ("kind", "body", "words"),
    [
        ("exclusion", {"weight": 5}, "weight"),
        ("weight", {"active": False}, "must-have"),
        ("weight", {"always": True}, "leave-out"),
        ("exclusion", {"always": False}, "delete"),
        ("weight", {"text": "   "}, "text"),  # blank words: the contract refuses them, naming the field
    ],
)
def test_an_edit_that_doesnt_fit_the_kind_is_a_plain_422(
    world: World, kind: str, body: dict[str, Any], words: str
) -> None:
    row = world.row(A, kind)
    response = world.call("PATCH", f"/preferences/{row.id}", A, json=body)
    assert response.status_code == 422
    assert words in response.json()["detail"]
    assert world.row(A, kind) == row  # nothing changed


def test_an_edit_that_changes_nothing_is_refused(world: World) -> None:
    response = world.call("PATCH", f"/preferences/{world.row(A, 'weight').id}", A, json={})
    assert response.status_code == 422


# --- user B can't touch user A's --------------------------------------------------------------------------------------


@pytest.mark.parametrize("method", ["PATCH", "DELETE"])
def test_user_b_cant_edit_or_delete_user_as_preference(world: World, method: str) -> None:
    mine = world.row(A, "weight")
    body = {"json": {"text": "taken over"}} if method == "PATCH" else {}
    response = world.call(method, f"/preferences/{mine.id}", B, **body)
    assert response.status_code == 404 and response.json()["detail"] == PREFERENCE_NOT_FOUND
    assert world.row(A, "weight") == mine


def test_user_b_cant_read_or_export_user_as_preferences(world: World) -> None:
    exported = world.call("GET", "/preferences/export", B).json()
    assert {i["id"] for i in exported["items"]} == {str(r.id) for r in world.store.rows(B)}
    assert {i["id"] for i in world.items(B)}.isdisjoint({str(r.id) for r in world.store.rows(A)})


@pytest.mark.parametrize("wanted", ["not-a-uuid", str(uuid.uuid4())])
def test_an_unknown_id_is_404(world: World, wanted: str) -> None:
    assert world.call("DELETE", f"/preferences/{wanted}", A).status_code == 404
    assert world.call("PATCH", f"/preferences/{wanted}", A, json={"text": "x"}).status_code == 404


# --- delete -----------------------------------------------------------------------------------------------------------


def test_deleting_one_preference_takes_its_row_and_its_embedding(world: World) -> None:
    weight = world.row(A, "weight")
    response = world.call("DELETE", f"/preferences/{weight.id}", A)
    assert response.status_code == 204 and response.content == b""
    assert weight.id not in {r.id for r in world.store.rows(A)}
    assert all(r.embedding != weight.embedding for r in world.store.rows(A))
    assert world.call("DELETE", f"/preferences/{weight.id}", A).status_code == 404


def test_forgetting_everything_leaves_nothing_of_the_shoppers_and_all_of_others(world: World) -> None:
    others = world.store.rows(B)
    assert world.call("DELETE", "/preferences", A).status_code == 204
    assert world.store.rows(A) == [] and world.items(A) == []
    assert world.store.rows(B) == others


# --- export -----------------------------------------------------------------------------------------------------------


def _keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in _keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in _keys(v)}
    return set()


def test_the_export_is_a_json_download_with_no_embedding_user_id_or_email(world: World) -> None:
    response = world.call("GET", "/preferences/export", A)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["content-disposition"].startswith("attachment;")
    exported = PreferenceExport.model_validate(response.json())
    assert len(exported.items) == 3
    assert {"embedding", "user_id", "email"}.isdisjoint(_keys(response.json()))
    assert str(A) not in response.text


# --- the database down ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/preferences", None),
        ("GET", "/preferences/export", None),
        ("PATCH", f"/preferences/{uuid.uuid4()}", {"text": "x"}),
        ("DELETE", f"/preferences/{uuid.uuid4()}", None),
        ("DELETE", "/preferences", None),
    ],
)
def test_the_database_down_answers_503(method: str, path: str, body: dict[str, Any] | None) -> None:
    world = World(store=DownStore())
    response = world.call(method, path, A, **({"json": body} if body else {}))
    assert response.status_code == 503 and response.json()["detail"] == ACCOUNT_UNAVAILABLE
