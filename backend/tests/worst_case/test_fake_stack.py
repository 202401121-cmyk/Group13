"""US-50: the fake stack the merge browser test starts (tests/worst_case/fake_stack.py). Its intake answers by rules, a
search ends with a result, nothing is saved, and its launcher turns everything that could leave the machine off."""

from __future__ import annotations

import os
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.contracts as C
from tests.fakes.llm import FakeLLMClient
from tests.unit.api.test_evidence_route import finished, start
from tests.worst_case import fake_stack


def test_the_fake_stack_answers_by_rules_and_a_search_ends_with_a_result_never_saved() -> None:
    with TestClient(fake_stack.build_app("http://localhost:3200")) as client:
        intake = client.post("/api/presearch/intake", json={"text": "Laptop for coding under 80k"})
        assert intake.status_code == 200
        assert intake.json()["intent"]["detected_by"] == "rules"  # the fake providers never answer
        run = finished(client, start(client))
        assert run.status is C.SearchRunStatus.DONE and run.result is not None
        assert run.result.saved is False  # the database is off
        assert client.get("/api/health").json()["database"] == "off"
        preflight = client.options(
            "/api/presearch/intake",
            headers={"Origin": "http://localhost:3200", "Access-Control-Request-Method": "POST"},
        )
        assert preflight.headers.get("access-control-allow-origin") == "http://localhost:3200"


def test_the_launcher_turns_off_what_could_leave_the_machine_and_serves_the_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.main as app_main
    import app.presearch as presearch_flow

    monkeypatch.setattr(presearch_flow, "default_llm", presearch_flow.default_llm)  # put back after the test
    monkeypatch.setattr(app_main, "default_search_stages", app_main.default_search_stages)
    served: list[dict[str, Any]] = []
    for name in fake_stack.OFFLINE:
        monkeypatch.setenv(name, "on")  # put back after the test, whatever the launcher sets
    monkeypatch.setattr("uvicorn.run", lambda app, **kw: served.append({"app": app, **kw}))
    assert fake_stack.main(["--port", "8123", "--frontend-origin", "http://localhost:3123"]) == 0
    assert all(os.environ[name] == value for name, value in fake_stack.OFFLINE.items())
    (call,) = served
    assert (call["host"], call["port"]) == ("127.0.0.1", 8123)
    assert isinstance(presearch_flow.default_llm(), FakeLLMClient)  # a typed interview reply never reaches a provider
    assert app_main.default_search_stages is fake_stack.stub_stages  # nor does the plain search route
