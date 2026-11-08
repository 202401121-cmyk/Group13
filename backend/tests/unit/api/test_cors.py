"""Which browser origins may call the API (D-35): FRONTEND_ORIGINS, comma-separated, never "*".

Every test passes its own environment to create_app, so a developer's .env never changes the result.
"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from app.main import DEFAULT_FRONTEND_ORIGINS, allowed_origins, create_app

LOCAL = "http://localhost:3000"
STAGING = "https://staging.shopsense.example.org"
EVIL = "http://evil.example"


RUN = "srch_" + "0" * 24


def preflight(
    client: TestClient,
    origin: str,
    method: str = "POST",
    headers: str = "content-type",
    path: str = "/api/presearch/intake",
) -> dict[str, str]:
    response = client.options(
        path,
        headers={"Origin": origin, "Access-Control-Request-Method": method, "Access-Control-Request-Headers": headers},
    )
    return {"status": str(response.status_code), **{k.lower(): v for k, v in response.headers.items()}}


def test_the_default_is_the_local_frontend_only() -> None:
    assert allowed_origins({}) == [LOCAL]
    assert list(DEFAULT_FRONTEND_ORIGINS) == [LOCAL]
    assert allowed_origins({"FRONTEND_ORIGINS": "  "}) == [LOCAL]


def test_a_comma_separated_list_is_trimmed_and_loses_trailing_slashes() -> None:
    assert allowed_origins({"FRONTEND_ORIGINS": f" {LOCAL}/ , {STAGING} ,, "}) == [LOCAL, STAGING]


def test_a_wildcard_is_never_allowed(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="app.main"):
        assert allowed_origins({"FRONTEND_ORIGINS": f"*, {STAGING}"}) == [STAGING]
        assert allowed_origins({"FRONTEND_ORIGINS": "*"}) == [LOCAL]
    assert "FRONTEND_ORIGINS" in caplog.text


def test_the_older_single_origin_name_still_works_but_the_list_wins() -> None:
    assert allowed_origins({"FRONTEND_ORIGIN": STAGING}) == [STAGING]
    assert allowed_origins({"FRONTEND_ORIGIN": STAGING, "FRONTEND_ORIGINS": LOCAL}) == [LOCAL]


def test_every_listed_origin_passes_the_preflight_and_a_normal_request() -> None:
    with TestClient(create_app(env={"FRONTEND_ORIGINS": f"{LOCAL},{STAGING}"})) as client:
        for origin in (LOCAL, STAGING):
            answer = preflight(client, origin)
            assert answer["status"] == "200"
            assert answer["access-control-allow-origin"] == origin
            normal = client.get("/api/health", headers={"Origin": origin})
            assert normal.headers.get("access-control-allow-origin") == origin


def test_an_unlisted_origin_is_refused() -> None:
    with TestClient(create_app(env={"FRONTEND_ORIGINS": STAGING})) as client:
        for origin in (EVIL, LOCAL):
            answer = preflight(client, origin)
            assert answer["status"] == "400"
            assert "access-control-allow-origin" not in answer
            normal = client.get("/api/health", headers={"Origin": origin})
            assert "access-control-allow-origin" not in normal.headers


def test_only_the_methods_and_headers_the_frontend_uses_are_allowed() -> None:
    with TestClient(create_app(env={})) as client:
        for method in ("GET", "POST", "PUT", "DELETE"):  # DELETE cancels a search run (US-12)
            assert preflight(client, LOCAL, method=method)["status"] == "200"
        assert preflight(client, LOCAL, method="PATCH")["status"] == "400"
        assert preflight(client, LOCAL, headers="last-event-id")["status"] == "200"  # resuming a progress stream
        assert preflight(client, LOCAL, headers="x-api-key")["status"] == "400"


@pytest.mark.parametrize(
    ("method", "path", "headers"),
    [
        ("POST", "/api/search/runs", "content-type"),
        ("GET", f"/api/search/runs/{RUN}", "content-type"),
        ("GET", f"/api/search/runs/{RUN}/events", "last-event-id"),
        ("DELETE", f"/api/search/runs/{RUN}", "content-type"),
    ],
)
def test_the_run_routes_allow_the_frontend_origin_and_never_a_wildcard(method: str, path: str, headers: str) -> None:
    with TestClient(create_app(env={"FRONTEND_ORIGINS": f"*, {STAGING}"})) as client:
        allowed = preflight(client, STAGING, method=method, headers=headers, path=path)
        assert allowed["status"] == "200"
        assert allowed["access-control-allow-origin"] == STAGING
        refused = preflight(client, EVIL, method=method, headers=headers, path=path)
        assert refused["status"] == "400"
        assert "access-control-allow-origin" not in refused
        normal = client.request(method, path, headers={"Origin": EVIL})
        assert "access-control-allow-origin" not in normal.headers
        normal = client.request(method, path, headers={"Origin": STAGING})
        assert normal.headers.get("access-control-allow-origin") == STAGING  # never "*", even on a 404


def test_retry_after_is_readable_by_the_frontend() -> None:
    """A busy answer (503) says when to try again; the browser shows the frontend that header only if it is exposed."""
    with TestClient(create_app(env={})) as client:
        response = client.get("/api/health", headers={"Origin": LOCAL})
    exposed = [h.strip().lower() for h in response.headers.get("access-control-expose-headers", "").split(",")]
    assert "retry-after" in exposed
