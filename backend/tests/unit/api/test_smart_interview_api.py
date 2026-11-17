"""The live interview is the smart one (US-04): every step says information_gain, and the stored beliefs are always the
ones the answers give, through a rubric edit (US-09) and a correction (D-69 #6)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.presearch import get_session_store
from app.api.sessions import InMemorySessionStore
from app.contracts import InterviewState
from app.interview.beliefs import beliefs_for
from app.main import create_app

LAPTOP = "best laptop for coding under 70k"


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app(env={})
    store = InMemorySessionStore()
    app.dependency_overrides[get_session_store] = lambda: store
    with TestClient(app) as test_client:
        yield test_client


def post(client: TestClient, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    response = client.post(f"/api/presearch/{path}", json=body)
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()
    return data


def stored(client: TestClient, session_id: str) -> InterviewState:
    return InterviewState.model_validate(client.get(f"/api/presearch/{session_id}").json()["interview"])


def test_the_live_interview_is_smart_and_its_beliefs_survive_an_edit_and_a_correction(client: TestClient) -> None:
    response = client.post("/api/presearch/intake", json={"text": LAPTOP})
    session_id = response.json()["session_id"]
    post(client, f"{session_id}/criteria")
    first = post(client, f"{session_id}/interview/start")
    assert first["ordering"] == "information_gain"
    first_id, first_key = first["question"]["id"], first["question"]["criterion_key"]

    step = post(client, f"{session_id}/interview/reply", {"reply_text": "High", "question_id": first_id})
    assert step["ordering"] == "information_gain"
    state = stored(client, session_id)
    assert state.beliefs == beliefs_for(state)
    assert (
        state.beliefs[first_key].sd
        < beliefs_for(InterviewState.model_validate({**state.model_dump(), "history": []}))[first_key].sd
    )

    post(client, f"{session_id}/rubric", {"edits": [{"kind": "set_weight", "criterion_key": first_key, "weight": 1}]})
    edited = stored(client, session_id)
    assert edited.manual_weights == {first_key: 1.0}
    assert edited.beliefs == state.beliefs  # an edit is not an answer

    post(client, f"{session_id}/interview/back", {"question_id": first_id})
    corrected = post(client, f"{session_id}/interview/reply", {"reply_text": "Low"})
    assert corrected["ordering"] == "information_gain"
    after = stored(client, session_id)
    assert after.beliefs == beliefs_for(after)
    assert after.beliefs[first_key].mean < state.beliefs[first_key].mean
