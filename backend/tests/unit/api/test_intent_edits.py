"""PUT /api/presearch/{id}/intent (US-02, failure F-2) through FastAPI's TestClient. No server, no real LLM.

Every refusal must keep the error shape, {"detail": "<one plain sentence>"}, and leave the whole session as it
was: the Intent, the criteria and the interview. tests/conftest.py makes the LLM a fake that is always unavailable.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from app.api.presearch import get_session_store
from app.api.sessions import InMemorySessionStore
from app.main import create_app

LAPTOP = "laptop for coding under 70k"
PRODUCT = "To change the product, start a new request."
RULES = "Must-haves and leave-outs can't be changed here yet. Start a new request with the change instead."
RULE = {"attribute": "ram_gb", "operator": ">=", "value": 16, "unit": "GB", "source_text": "16 GB RAM"}
EXCLUSION = {"kind": "brand", "value": "Acme", "always": False}


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app(env={})
    store = InMemorySessionStore()
    app.dependency_overrides[get_session_store] = lambda: store
    with TestClient(app) as test_client:
        yield test_client


def new_session(client: TestClient, text: str = LAPTOP) -> dict[str, Any]:
    response = client.post("/api/presearch/intake", json={"text": text})
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()
    return data


def session_with_interview(client: TestClient) -> str:
    session_id: str = new_session(client)["session_id"]
    assert client.post(f"/api/presearch/{session_id}/criteria").status_code == 200
    assert client.post(f"/api/presearch/{session_id}/interview/start").status_code == 200
    return session_id


def put_intent(client: TestClient, session_id: str, changes: dict[str, Any]) -> Response:
    return client.put(f"/api/presearch/{session_id}/intent", json={"changes": changes})


def saved(client: TestClient, session_id: str) -> dict[str, Any]:
    data: dict[str, Any] = client.get(f"/api/presearch/{session_id}").json()
    return data


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"category": "smartphone"}, PRODUCT),
        ({"category_label": "Smartphone"}, PRODUCT),
        ({"category": "smartphone", "category_label": "Smartphone"}, PRODUCT),
        ({"category": None}, PRODUCT),
        ({"use_case": "gaming", "category": "smartphone"}, PRODUCT),
        ({"hard_rules": [RULE]}, RULES),
        ({"hard_rules": ["16 GB RAM"]}, RULES),
        ({"exclusions": [EXCLUSION]}, RULES),
        ({"exclusions": ["no refurbished"]}, RULES),
        ({"region": "US", "exclusions": [EXCLUSION]}, RULES),
    ],
)
def test_a_refused_edit_keeps_the_error_shape_and_the_session(
    client: TestClient, changes: dict[str, Any], message: str
) -> None:
    session_id = session_with_interview(client)
    before = saved(client, session_id)
    response = put_intent(client, session_id, changes)
    assert response.status_code == 422
    assert response.json() == {"detail": message}
    assert saved(client, session_id) == before


def test_the_f2_steps_no_longer_leave_a_mismatched_label(client: TestClient) -> None:
    session_id = new_session(client)["session_id"]
    assert put_intent(client, session_id, {"category": "smartphone"}).status_code == 422
    intent = saved(client, session_id)["intent"]
    assert (intent["category"], intent["category_label"]) == ("laptop", "Laptop")


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"raw_query": "a phone"}, "raw_query"),
        ({"detected_by": "llm"}, "detected_by"),
        ({"needs_clarification": True, "clarification_question": "Which one?"}, "needs_clarification"),
        ({"show_example_requests": True}, "show_example_requests"),
    ],
)
def test_a_field_only_code_sets_is_refused_in_the_usual_shape(
    client: TestClient, changes: dict[str, Any], field: str
) -> None:
    session_id = session_with_interview(client)
    before = saved(client, session_id)
    response = put_intent(client, session_id, changes)
    assert response.status_code == 422
    assert list(response.json()) == ["detail"]
    assert response.json()["detail"].startswith(f"That change doesn't work: {field} - ")
    assert saved(client, session_id) == before


def test_a_session_waiting_for_a_reply_gets_its_product_from_the_reply(client: TestClient) -> None:
    first = new_session(client, "watch")
    session_id = first["session_id"]
    changes = {"category": "smartwatch", "category_label": "Smartwatch"}
    assert put_intent(client, session_id, changes).json() == {"detail": PRODUCT}
    assert saved(client, session_id)["intent"] == first["intent"]
    reply = client.post("/api/presearch/intake", json={"session_id": session_id, "text": "smartwatch"})
    assert reply.json()["intent"]["category"] == "smartwatch"


@pytest.mark.parametrize(
    "changes",
    [
        {"category": "laptop"},
        {"category_label": "Laptop"},
        {"category": "laptop", "category_label": "Laptop"},
        {"hard_rules": [], "exclusions": []},
    ],
)
def test_repeating_the_current_values_is_accepted(client: TestClient, changes: dict[str, Any]) -> None:
    session_id = new_session(client)["session_id"]
    before = saved(client, session_id)["intent"]
    response = put_intent(client, session_id, changes)
    assert response.status_code == 200
    assert response.json() == before
    assert saved(client, session_id)["intent"] == before


def test_the_whole_intent_sent_back_with_one_change_is_accepted(client: TestClient) -> None:
    first = new_session(client)
    response = put_intent(client, first["session_id"], {**first["intent"], "use_case": "gaming"})
    assert response.status_code == 200
    assert response.json() == {**first["intent"], "use_case": "gaming"}


def test_a_repeated_product_with_a_new_region_keeps_the_criteria(client: TestClient) -> None:
    session_id = session_with_interview(client)
    response = put_intent(client, session_id, {"category": "laptop", "region": "US"})
    assert response.status_code == 200
    after = saved(client, session_id)
    assert after["intent"]["region"] == "US"
    assert after["criteria"] is not None
    assert after["interview"] is None  # the interview copies the intent, so it restarts


def test_use_budget_and_region_edits_are_saved(client: TestClient) -> None:
    session_id = new_session(client)["session_id"]
    budget = {"min": None, "max": {"amount": 60_000, "currency": "INR"}}
    response = put_intent(client, session_id, {"use_case": "gaming", "budget": budget, "region": "US"})
    assert response.status_code == 200
    intent = saved(client, session_id)["intent"]
    assert (intent["use_case"], intent["budget"], intent["region"]) == ("gaming", budget, "US")
    assert (intent["category"], intent["category_label"]) == ("laptop", "Laptop")


def test_a_negative_budget_is_still_refused_in_the_usual_shape(client: TestClient) -> None:
    session_id = new_session(client)["session_id"]
    before = saved(client, session_id)
    response = put_intent(client, session_id, {"budget": {"max": {"amount": -5, "currency": "INR"}}})
    assert response.status_code == 422
    assert list(response.json()) == ["detail"]
    detail = response.json()["detail"]
    assert detail.startswith("That change doesn't work: budget.max.amount - ")
    assert "-5" not in detail and "Traceback" not in detail
    assert saved(client, session_id) == before
