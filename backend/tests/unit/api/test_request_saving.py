"""Saving each request with its session ID (US-01), through the pre-search API and the health check.

tests/conftest.py keeps the LLM fake (intake uses its keyword rules) and the database off; each test here gives
the API its own in-memory request store.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api.presearch import get_request_store, get_session_store
from app.api.sessions import InMemorySessionStore
from app.contracts import SCHEMA_VERSION
from app.db import InMemoryRequestStore
from app.main import create_app

LAPTOP = "best laptop for coding under 70k"
UNCLEAR = "watch under 5k"  # the keyword rules ask which kind of watch


@pytest.fixture
def requests() -> InMemoryRequestStore:
    return InMemoryRequestStore()


@pytest.fixture
def client(requests: InMemoryRequestStore) -> Iterator[TestClient]:
    app = create_app()
    sessions = InMemorySessionStore()
    app.dependency_overrides[get_session_store] = lambda: sessions
    app.dependency_overrides[get_request_store] = lambda: requests
    with TestClient(app) as test_client:
        yield test_client


def test_a_new_request_is_saved_once_with_its_session_id(client: TestClient, requests: InMemoryRequestStore) -> None:
    body = client.post("/api/presearch/intake", json={"text": LAPTOP}).json()
    [row] = requests.rows
    assert row.session_id == body["session_id"]
    assert row.request_text == LAPTOP
    assert row.intent is not None and row.intent.category == body["intent"]["category"]
    assert row.intent_source == "rules"
    assert client.get(f"/api/presearch/{body['session_id']}").json()["request_saved"] == "saved"


def test_a_clarification_reply_is_the_same_request_so_no_new_row(
    client: TestClient, requests: InMemoryRequestStore
) -> None:
    first = client.post("/api/presearch/intake", json={"text": UNCLEAR}).json()
    assert first["intent"]["needs_clarification"]
    [row] = requests.rows
    assert row.intent is None  # not ready for research, so only the text and how it was read
    assert row.intent_source == "rules"
    reply = client.post("/api/presearch/intake", json={"session_id": first["session_id"], "text": "Smartwatch"})
    assert reply.status_code == 200
    assert len(requests.rows) == 1
    assert client.get(f"/api/presearch/{first['session_id']}").json()["request_saved"] == "saved"


def test_a_fresh_request_in_the_same_session_is_a_new_row(client: TestClient, requests: InMemoryRequestStore) -> None:
    first = client.post("/api/presearch/intake", json={"text": LAPTOP}).json()
    client.post("/api/presearch/intake", json={"session_id": first["session_id"], "text": "standing desk under 20k"})
    assert [row.request_text for row in requests.rows] == [LAPTOP, "standing desk under 20k"]
    assert {row.session_id for row in requests.rows} == {first["session_id"]}


def test_a_database_that_is_down_never_stops_intake(client: TestClient, requests: InMemoryRequestStore) -> None:
    requests.fail = True
    response = client.post("/api/presearch/intake", json={"text": LAPTOP})
    assert response.status_code == 200
    assert response.json()["ready_for_research"] is True
    session_id = response.json()["session_id"]
    assert client.get(f"/api/presearch/{session_id}").json()["request_saved"] == "failed"


def test_with_the_database_off_the_session_says_off(monkeypatch: pytest.MonkeyPatch) -> None:
    app = create_app()  # no store override: the conftest's "off" store
    sessions = InMemorySessionStore()
    app.dependency_overrides[get_session_store] = lambda: sessions
    with TestClient(app) as client:
        session_id = client.post("/api/presearch/intake", json={"text": LAPTOP}).json()["session_id"]
        assert client.get(f"/api/presearch/{session_id}").json()["request_saved"] == "off"


@pytest.mark.parametrize(
    ("store", "expected"), [(InMemoryRequestStore(), "ok"), (InMemoryRequestStore(fail=True), "down")]
)
def test_health_reports_the_database_and_nothing_more(
    monkeypatch: pytest.MonkeyPatch, store: InMemoryRequestStore, expected: str
) -> None:
    monkeypatch.setattr("app.db.default_request_store", lambda: store)
    with TestClient(create_app()) as client:
        health = client.get("/api/health").json()
        assert health == {"status": "ok", "schema_version": SCHEMA_VERSION, "database": expected}
