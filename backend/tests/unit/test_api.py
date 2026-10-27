"""The HTTP API (US-52), through FastAPI's test client. No server, no network."""

from __future__ import annotations

from fastapi.testclient import TestClient

import app.contracts as C
from app.contracts import examples as ex
from app.main import app

client = TestClient(app)


def test_health() -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    # Tests run with the database off (tests/conftest.py), so health reports it as "off".
    assert response.json() == {"status": "ok", "schema_version": C.SCHEMA_VERSION, "database": "off"}


def test_search_returns_a_valid_search_result() -> None:
    request = C.SearchRequest(session_id=ex.SESSION_ID, raw_query=ex.QUERY, rubric=ex.rubric())
    response = client.post("/api/search", json=request.model_dump(mode="json"))
    assert response.status_code == 200
    result = C.SearchResult.model_validate(response.json())
    assert result.request == request
    assert result.decision is not None and result.decision.ranked


def test_search_rejects_a_bad_request() -> None:
    response = client.post("/api/search", json={"session_id": "nope", "raw_query": "", "rubric": {}})
    assert response.status_code == 422
