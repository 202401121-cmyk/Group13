"""POST /api/presearch/exclusions/parse (US-10): exclusions from the shopper's words, with no session and no LLM."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.contracts import ExclusionParseResponse
from app.main import create_app

ROUTE = "/api/presearch/exclusions/parse"


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app(env={})) as test_client:
        yield test_client


def test_the_route_returns_exclusions_and_words_not_understood(client: TestClient) -> None:
    response = client.post(ROUTE, json={"text": "no Xiaomi or Zorblax, not touchscreen"})
    assert response.status_code == 200, response.text
    body = ExclusionParseResponse.model_validate(response.json())
    assert [(e.kind, e.value, e.always) for e in body.exclusions] == [
        ("brand", "Xiaomi", False),
        ("feature", "touchscreen", False),
    ]
    assert body.not_understood == ["Zorblax"]


def test_text_over_200_characters_answers_422(client: TestClient) -> None:
    response = client.post(ROUTE, json={"text": "no Xiaomi " * 25})
    assert response.status_code == 422
    assert "text" in response.json()["detail"]


def test_blank_text_answers_422(client: TestClient) -> None:
    assert client.post(ROUTE, json={"text": ""}).status_code == 422


def test_it_needs_no_session(client: TestClient) -> None:
    """A path that looks like a session's must not be taken for one: this route is stateless."""
    response = client.post(ROUTE, json={"text": "no notch"})
    assert response.status_code == 200
    assert response.json()["exclusions"] == [{"kind": "feature", "value": "notch", "always": False}]
