"""US-06 through the API: a signed-in shopper's interview starts with what is remembered, and its final rubric is saved;
a guest's is neither (D-36). One click forgets a remembered preference. A failing memory never changes the interview."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.presearch import get_memory, get_session_store
from app.api.sessions import InMemorySessionStore
from app.auth.http import optional_user
from app.auth.verify import SignedInUser
from app.contracts import Exclusion, InterviewState, RememberedPreference, Rubric
from app.main import create_app
from app.memory import MemoryService, MemorySettings, RememberedItem
from app.memory.embedder import MemoryUnavailable
from app.memory.store import InMemoryPreferenceStore
from tests.unit.memory.fakes import WordEmbedder, ready_holder

LAPTOP = "best laptop for coding under 70k"
A = SignedInUser(id=uuid.UUID("aaaaaaaa-0000-4000-8000-0000000000a1"))
B = SignedInUser(id=uuid.UUID("bbbbbbbb-0000-4000-8000-0000000000b2"))


class FakeMemory:
    """Records what the API asks of memory, and recalls what it is given."""

    def __init__(self, items: list[RememberedItem] | None = None) -> None:
        self.items = items or []
        self.recalls: list[uuid.UUID | None] = []
        self.saves: list[tuple[uuid.UUID | None, Rubric]] = []
        self.warmed = 0

    def warm(self) -> None:
        self.warmed += 1

    def recall(self, user_id: uuid.UUID | None, state: InterviewState) -> list[RememberedItem]:
        self.recalls.append(user_id)
        return list(self.items) if user_id is not None else []

    def remember(self, user_id: uuid.UUID | None, state: InterviewState, rubric: Rubric) -> int:
        self.saves.append((user_id, rubric))
        return 1


class Signed:
    """Who the next request comes from: a user, or a guest (None)."""

    def __init__(self) -> None:
        self.user: SignedInUser | None = None

    def __call__(self) -> SignedInUser | None:
        return self.user


@pytest.fixture
def signed() -> Signed:
    return Signed()


@pytest.fixture
def memory() -> FakeMemory:
    return FakeMemory()


@pytest.fixture
def client(signed: Signed, memory: FakeMemory) -> Iterator[TestClient]:
    app = create_app(env={})
    store = InMemorySessionStore()
    app.dependency_overrides[get_session_store] = lambda: store
    app.dependency_overrides[optional_user] = signed
    app.dependency_overrides[get_memory] = lambda: memory
    with TestClient(app) as test_client:
        yield test_client


def post(client: TestClient, path: str, body: dict[str, Any] | None = None, status: int = 200) -> dict[str, Any]:
    response = client.post(f"/api/presearch/{path}", json=body)
    assert response.status_code == status, response.text
    data: dict[str, Any] = response.json()
    return data


def started(client: TestClient) -> tuple[str, dict[str, Any]]:
    session_id = post(client, "intake", {"text": LAPTOP})["session_id"]
    post(client, f"{session_id}/criteria")
    return session_id, post(client, f"{session_id}/interview/start")


def finish(client: TestClient, session_id: str, step: dict[str, Any]) -> dict[str, Any]:
    while step["kind"] != "finished":
        body = {"reply_text": "Medium", "question_id": step["question"]["id"]}
        step = post(client, f"{session_id}/interview/reply", body)
    return step


def remembered_weight(key: str = "battery_life", weight: float = 8.0) -> RememberedItem:
    return RememberedItem(
        RememberedPreference(
            preference_id=uuid.uuid4(),
            kind="weight",
            text="Battery life: high importance",
            criterion_key=key,
            weight=weight,
            similarity=0.97,
            age_days=4,
        )
    )


def test_a_guest_saves_and_recalls_nothing(client: TestClient, memory: FakeMemory) -> None:
    session_id, step = started(client)
    finish(client, session_id, step)
    assert memory.saves == [] and memory.warmed == 0
    assert all(user is None for user in memory.recalls)
    assert step["state"]["remembered"] == []


def test_a_signed_in_shopper_starts_with_what_is_remembered(
    client: TestClient, signed: Signed, memory: FakeMemory
) -> None:
    signed.user = A
    memory.items = [remembered_weight()]
    _, step = started(client)
    assert memory.recalls == [A.id] and memory.warmed >= 1
    assert [r["criterion_key"] for r in step["state"]["remembered"]] == ["battery_life"]
    statuses = {e["question"]["criterion_key"]: e["status"] for e in step["state"]["history"]}
    assert statuses["battery_life"] == "remembered"
    assert step["questions_asked"] == sum(1 for status in statuses.values() if status != "remembered")


def test_the_final_rubric_is_saved_once_for_the_shopper_who_started(
    client: TestClient, signed: Signed, memory: FakeMemory
) -> None:
    signed.user = A
    session_id, step = started(client)
    done = finish(client, session_id, step)
    assert [user for user, _ in memory.saves] == [A.id]
    assert memory.saves[0][1] == Rubric.model_validate(done["rubric"])


def test_a_rubric_edit_after_the_finish_saves_again(client: TestClient, signed: Signed, memory: FakeMemory) -> None:
    signed.user = A
    session_id, step = started(client)
    finish(client, session_id, step)
    acme = Exclusion(kind="brand", value="Acme", always=True).model_dump()
    post(client, f"{session_id}/rubric", {"edits": [{"kind": "add_exclusion", "exclusion": acme}]})
    assert len(memory.saves) == 2
    assert memory.saves[1][1].exclusions == [Exclusion(kind="brand", value="Acme", always=True)]


def test_a_rubric_edit_before_the_finish_saves_nothing(client: TestClient, signed: Signed, memory: FakeMemory) -> None:
    signed.user = A
    session_id, _ = started(client)
    post(
        client,
        f"{session_id}/rubric",
        {"edits": [{"kind": "set_weight", "criterion_key": "battery_life", "weight": 3}]},
    )
    assert memory.saves == []


def test_a_guest_who_signs_in_half_way_never_has_the_guests_answers_saved(
    client: TestClient, signed: Signed, memory: FakeMemory
) -> None:
    session_id, step = started(client)  # as a guest
    signed.user = A
    finish(client, session_id, step)
    assert memory.saves == []  # D-36: a guest's data is never linked to an account


def test_another_user_finishing_someone_elses_interview_saves_nothing(
    client: TestClient, signed: Signed, memory: FakeMemory
) -> None:
    signed.user = A
    session_id, step = started(client)
    signed.user = B
    finish(client, session_id, step)
    assert memory.saves == []


def test_forgetting_a_remembered_weight_in_one_click(client: TestClient, signed: Signed, memory: FakeMemory) -> None:
    signed.user = A
    item = remembered_weight()
    memory.items = [item]
    session_id, _ = started(client)
    step = post(client, f"{session_id}/interview/forget", {"preference_id": str(item.preference.preference_id)})
    assert step["state"]["remembered"] == []
    assert all(e["status"] != "remembered" for e in step["state"]["history"])
    stored = client.get(f"/api/presearch/{session_id}").json()["interview"]
    assert stored["remembered"] == []


def test_forgetting_what_this_interview_does_not_hold_is_a_plain_404(client: TestClient, signed: Signed) -> None:
    signed.user = A
    session_id, _ = started(client)
    response = client.post(f"/api/presearch/{session_id}/interview/forget", json={"preference_id": str(uuid.uuid4())})
    assert response.status_code == 404
    assert "remembered" in response.json()["detail"]


def test_forgetting_before_the_interview_starts_is_refused(client: TestClient) -> None:
    session_id = post(client, "intake", {"text": LAPTOP})["session_id"]
    response = client.post(f"/api/presearch/{session_id}/interview/forget", json={"preference_id": str(uuid.uuid4())})
    assert response.status_code == 409


class BrokenStore(InMemoryPreferenceStore):
    def candidates(self, user_id: uuid.UUID, category: str) -> list[Any]:
        raise MemoryUnavailable("the database could not be read")


def test_a_failing_memory_leaves_the_interview_unchanged(signed: Signed) -> None:
    def run(memory: Any, user: SignedInUser | None) -> dict[str, Any]:
        app = create_app(env={})
        store = InMemorySessionStore()
        app.dependency_overrides[get_session_store] = lambda: store
        app.dependency_overrides[optional_user] = lambda: user
        app.dependency_overrides[get_memory] = lambda: memory
        with TestClient(app) as test_client:
            _, step = started(test_client)
        return step

    broken = MemoryService(MemorySettings(), ready_holder(WordEmbedder()), BrokenStore())
    with_failure, as_guest = run(broken, A), run(FakeMemory(), None)
    for step in (with_failure, as_guest):
        step["state"]["session_id"] = "x"
    assert with_failure == as_guest
