"""US-06 through the API, after a restart or an edit, and under every kind of failure: a restart keeps what memory added
marked as remembered; a remembered rule the shopper switched off stays off; what they forgot or removed never comes
back; and memory failing in any way leaves the interview exactly as a guest's."""

from __future__ import annotations

import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.presearch import get_memory, get_session_store
from app.api.sessions import InMemorySessionStore
from app.auth.http import optional_user
from app.auth.verify import SignedInUser
from app.contracts import Exclusion, HardRule, RememberedPreference
from app.main import create_app
from app.memory import (
    EmbedderHolder,
    InMemoryPreferenceStore,
    MemoryService,
    MemorySettings,
    RememberedItem,
    StoredPreference,
    load_memory_settings,
)
from app.memory.embedder import MemoryUnavailable, load_embedder
from tests.unit.api.test_memory_api import A, FakeMemory, Signed, post, remembered_weight, started
from tests.unit.memory.fakes import WordEmbedder, ready_holder

RAM = HardRule(attribute="ram_gb", operator=">=", value=16, unit="GB", source_text="at least 16 GB of RAM")


@pytest.fixture
def signed() -> Signed:
    return Signed()


@pytest.fixture
def memory() -> FakeMemory:
    return FakeMemory()


@pytest.fixture
def client(signed: Signed, memory: FakeMemory) -> Any:
    app = create_app(env={})
    store = InMemorySessionStore()
    app.dependency_overrides[get_session_store] = lambda: store
    app.dependency_overrides[optional_user] = signed
    app.dependency_overrides[get_memory] = lambda: memory
    with TestClient(app) as test_client:
        yield test_client


def remembered_leave_out(value: str = "Acme") -> RememberedItem:
    return RememberedItem(
        RememberedPreference(
            preference_id=uuid.uuid4(), kind="exclusion", text=f"Never {value}", similarity=1.0, age_days=2
        ),
        exclusion=Exclusion(kind="brand", value=value, always=True),
    )


def remembered_rule() -> RememberedItem:
    return RememberedItem(
        RememberedPreference(
            preference_id=uuid.uuid4(),
            kind="hard_rule",
            text=f"Must have: {RAM.source_text}",
            similarity=0.8,
            age_days=1,
        ),
        rule=RAM,
    )


def test_a_restart_keeps_the_remembered_marks(client: TestClient, signed: Signed, memory: FakeMemory) -> None:
    signed.user = A
    item = remembered_leave_out()
    memory.items = [item]
    session_id, _ = started(client)
    again = post(client, f"{session_id}/interview/start")
    assert [r["preference_id"] for r in again["state"]["remembered"]] == [str(item.preference.preference_id)]
    assert [e["value"] for e in again["state"]["manual_exclusions_added"]] == ["Acme"]  # once, not twice
    post(client, f"{session_id}/interview/forget", {"preference_id": str(item.preference.preference_id)})


def test_a_remembered_rule_switched_off_stays_off_after_a_restart(
    client: TestClient, signed: Signed, memory: FakeMemory
) -> None:
    signed.user = A
    memory.items = [remembered_rule()]
    session_id, _ = started(client)
    post(client, f"{session_id}/rubric", {"edits": [{"kind": "switch_rule", "rule_id": RAM.id, "active": False}]})
    again = post(client, f"{session_id}/interview/start")
    assert again["state"]["manual_rule_switches"] == {RAM.id: False}
    assert [r["kind"] for r in again["state"]["remembered"]] == ["hard_rule"]


def test_what_the_shopper_forgot_or_removed_never_comes_back_on_a_restart(
    client: TestClient, signed: Signed, memory: FakeMemory
) -> None:
    signed.user = A
    weight_item, leave_item = remembered_weight(), remembered_leave_out()
    memory.items = [weight_item, leave_item]
    session_id, _ = started(client)
    post(client, f"{session_id}/interview/forget", {"preference_id": str(weight_item.preference.preference_id)})
    acme = Exclusion(kind="brand", value="Acme").model_dump()
    post(client, f"{session_id}/rubric", {"edits": [{"kind": "remove_exclusion", "exclusion": acme}]})
    again = post(client, f"{session_id}/interview/start")
    assert again["state"]["remembered"] == []
    assert again["state"]["manual_exclusions_added"] == []


# --- every failure leaves the interview as a guest's ------------------------------------------------------------------


class BrokenStore(InMemoryPreferenceStore):
    def candidates(self, user_id: uuid.UUID, category: str) -> list[StoredPreference]:
        raise MemoryUnavailable("the database could not be read")


class OneRowStore(InMemoryPreferenceStore):
    """One row for anyone, so recall goes on to the model."""

    def candidates(self, user_id: uuid.UUID, category: str) -> list[StoredPreference]:
        now = datetime.now(UTC)
        text = "Battery life: high importance"
        return [
            StoredPreference(
                uuid.uuid4(), "weight", category, "battery_life", text, 8.0, None, False, now, now, (1.0, 0.0)
            )
        ]


class BrokenModel:
    def start(self) -> None:
        return None

    def get(self, timeout: float) -> Any:
        raise MemoryUnavailable("the model could not be loaded (RuntimeError)")


def wrong_sha_holder() -> EmbedderHolder:
    folder = Path(tempfile.mkdtemp())
    model = folder / "model.onnx"
    model.write_bytes(b"not the pinned model")
    return EmbedderHolder(
        load_memory_settings(env={}), lambda s: load_embedder(s, folder, download=lambda **a: str(model))
    )


def first_step(memory: Any, user: SignedInUser | None) -> dict[str, Any]:
    app = create_app(env={})
    store = InMemorySessionStore()
    app.dependency_overrides[get_session_store] = lambda: store
    app.dependency_overrides[optional_user] = lambda: user
    app.dependency_overrides[get_memory] = lambda: memory
    with TestClient(app) as test_client:
        _, step = started(test_client)
    step["state"]["session_id"] = "x"
    return step


@pytest.mark.parametrize("failure", ["the database down", "the model failing", "the sha256 wrong"])
def test_every_failure_leaves_the_interview_unchanged(failure: str) -> None:
    holder: Any = ready_holder(WordEmbedder())
    store: InMemoryPreferenceStore = BrokenStore()
    if failure == "the model failing":
        holder, store = BrokenModel(), OneRowStore()
    elif failure == "the sha256 wrong":
        holder, store = wrong_sha_holder(), OneRowStore()
    assert first_step(MemoryService(MemorySettings(), holder, store), A) == first_step(FakeMemory(), None)
