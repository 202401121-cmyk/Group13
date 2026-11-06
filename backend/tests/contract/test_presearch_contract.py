"""Contract tests for the pre-search API's own bodies (US-01, US-05).

Every rule has a test, and real answers from the routes are checked against the exported schemas, because those are
the files the frontend validates live responses with.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any, get_args

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import app.contracts as C
from app import db
from app.api.presearch import get_session_store
from app.api.sessions import InMemorySessionStore, new_session_id
from app.contracts import examples as ex
from app.contracts.presearch import RequestSaved
from app.main import create_app
from tests.contract.schema_check import schema_errors

SCHEMAS = Path(__file__).resolve().parents[3] / "shared" / "contracts" / "schema"
LAPTOP = "best laptop for coding under 70k"


def exported(name: str) -> dict[str, Any]:
    schema: dict[str, Any] = json.loads((SCHEMAS / f"{name}.json").read_text(encoding="utf-8"))
    return schema


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app(env={})
    sessions = InMemorySessionStore()
    app.dependency_overrides[get_session_store] = lambda: sessions
    with TestClient(app) as test_client:
        yield test_client


def intake(client: TestClient, text: str, session_id: str | None = None) -> dict[str, Any]:
    body = {"text": text} | ({"session_id": session_id} if session_id else {})
    response = client.post("/api/presearch/intake", json=body)
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()
    return data


def session_of(client: TestClient, session_id: str) -> dict[str, Any]:
    response = client.get(f"/api/presearch/{session_id}")
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()
    return data


def assert_valid_intake_response(data: dict[str, Any]) -> None:
    assert schema_errors(data, exported("IntakeResponse")) == []
    assert C.IntakeResponse.model_validate(data).model_dump(mode="json") == data


def assert_valid_session(data: dict[str, Any]) -> None:
    assert schema_errors(data, exported("PresearchSession")) == []
    assert C.PresearchSession.model_validate(data).model_dump(mode="json") == data


# --- ReplyRequest ------------------------------------------------------------------------------------


def test_a_reply_needs_only_its_text() -> None:
    assert C.ReplyRequest(reply_text="High").question_id is None
    assert C.ReplyRequest(reply_text="High", question_id="q_battery_life").question_id == "q_battery_life"


def test_the_exported_schema_says_question_id_is_optional() -> None:
    schema = exported("ReplyRequest")
    assert schema["required"] == ["reply_text"]
    assert schema["additionalProperties"] is False


@pytest.mark.parametrize(
    "body",
    [
        {"reply_text": ""},
        {"reply_text": "x" * 501},
        {"question_id": "q_x"},
        {"reply_text": "High", "question_id": ""},
        {"reply_text": "High", "question_id": "q" * 65},
        {"reply_text": "High", "extra": True},
    ],
)
def test_a_bad_reply_is_refused(body: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        C.ReplyRequest.model_validate(body)


def test_a_reply_of_only_spaces_passes_so_the_interview_can_ask_again() -> None:
    assert C.ReplyRequest(reply_text="   ").reply_text == "   "


def test_a_reply_is_frozen() -> None:
    with pytest.raises(ValidationError):
        C.ReplyRequest(reply_text="High").reply_text = "Low"  # type: ignore[misc]


# --- real answers from the routes, against the exported schemas --------------------------------------


@pytest.mark.parametrize(
    ("text", "ready"),
    [(LAPTOP, True), ("watch under 5k", False), ("something for my mom", False), ("   ", False)],
    ids=["clear", "which-kind", "no-product", "blank"],
)
def test_a_real_intake_answer_matches_its_schema(client: TestClient, text: str, ready: bool) -> None:
    data = intake(client, text)
    assert data["ready_for_research"] is ready
    assert_valid_intake_response(data)


def test_a_real_answer_showing_example_requests_matches_its_schema(client: TestClient) -> None:
    first = intake(client, "something for my mom")
    data = intake(client, "idk, something nice", first["session_id"])
    assert data["intent"]["show_example_requests"] is True
    assert_valid_intake_response(data)


def test_a_real_session_matches_its_schema_at_every_step(client: TestClient) -> None:
    session_id = intake(client, LAPTOP)["session_id"]
    assert_valid_session(session_of(client, session_id))

    assert client.post(f"/api/presearch/{session_id}/criteria").status_code == 200
    assert_valid_session(session_of(client, session_id))

    step = client.post(f"/api/presearch/{session_id}/interview/start").json()
    assert_valid_session(session_of(client, session_id))

    while step["kind"] != "finished":
        body = {"reply_text": "High", "question_id": step["question"]["id"]}
        assert schema_errors(body, exported("ReplyRequest")) == []
        step = client.post(f"/api/presearch/{session_id}/interview/reply", json=body).json()
        assert_valid_session(session_of(client, session_id))
    assert session_of(client, session_id)["interview_step"]["rubric"] is not None


def test_a_real_session_after_blank_text_matches_its_schema(client: TestClient) -> None:
    session = session_of(client, intake(client, "   ")["session_id"])
    assert (session["request"], session["intent"]) == ("", None)
    assert_valid_session(session)


def test_a_real_session_waiting_for_a_clarification_matches_its_schema(client: TestClient) -> None:
    session = session_of(client, intake(client, "watch under 5k")["session_id"])
    assert session["intent"]["needs_clarification"] is True
    assert_valid_session(session)


# --- the checker really does refuse what the schemas refuse ------------------------------------------


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        ({"session_id": "session-1"}, "session_id"),
        ({"ready_for_research": "yes"}, "ready_for_research"),
        ({"surprise": 1}, "surprise"),
        ({"intent": {**ex.intent().model_dump(mode="json"), "region": "India"}}, "region"),
    ],
)
def test_a_wrong_intake_answer_fails_the_schema(change: dict[str, Any], problem: str) -> None:
    good = C.IntakeResponse(session_id=ex.SESSION_ID, intent=ex.intent(), ready_for_research=True)
    assert schema_errors(good.model_dump(mode="json"), exported("IntakeResponse")) == []
    errors = schema_errors({**good.model_dump(mode="json"), **change}, exported("IntakeResponse"))
    assert any(problem in error for error in errors), errors


@pytest.mark.parametrize("missing", ["session_id", "intent", "ready_for_research"])
def test_an_intake_answer_with_a_field_missing_fails_the_schema(missing: str) -> None:
    data = C.IntakeResponse(session_id=ex.SESSION_ID, intent=ex.intent(), ready_for_research=True)
    errors = schema_errors(data.model_dump(mode="json", exclude={missing}), exported("IntakeResponse"))
    assert errors == [f"$: {missing} is missing"]


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        ({"unclear_attempts": 3}, "unclear_attempts"),
        ({"request": "x" * 1001}, "request"),
        ({"request_saved": "maybe"}, "request_saved"),
    ],
)
def test_a_wrong_session_fails_the_schema(change: dict[str, Any], problem: str) -> None:
    good = C.PresearchSession(session_id=ex.SESSION_ID, request="laptop").model_dump(mode="json")
    assert schema_errors(good, exported("PresearchSession")) == []
    errors = schema_errors({**good, **change}, exported("PresearchSession"))
    assert any(problem in error for error in errors), errors


def test_the_checker_never_skips_a_rule_it_does_not_know() -> None:
    with pytest.raises(ValueError, match="not"):
        schema_errors({}, {"not": {"type": "object"}})


# --- IntakeResponse ----------------------------------------------------------------------------------


def test_every_field_of_an_intake_answer_is_required_in_its_schema() -> None:
    schema = exported("IntakeResponse")
    assert set(schema["required"]) == {"session_id", "intent", "ready_for_research"}
    assert schema["additionalProperties"] is False


@pytest.mark.parametrize(
    "change",
    [{"session_id": "session-1"}, {"session_id": "sess_abc"}, {"surprise": 1}, {"ready_for_research": "maybe"}],
)
def test_a_bad_intake_answer_is_refused(change: dict[str, object]) -> None:
    good = {"session_id": ex.SESSION_ID, "intent": ex.intent(), "ready_for_research": True}
    with pytest.raises(ValidationError):
        C.IntakeResponse.model_validate({**good, **change})


def test_an_intake_answer_takes_a_backend_session_id_and_is_frozen() -> None:
    answer = C.IntakeResponse(session_id=new_session_id(), intent=ex.intent(), ready_for_research=True)
    with pytest.raises(ValidationError):
        answer.ready_for_research = False  # type: ignore[misc]


# --- PresearchSession --------------------------------------------------------------------------------


def test_every_field_of_a_session_is_required_in_its_schema() -> None:
    schema = exported("PresearchSession")
    assert set(schema["required"]) == set(C.PresearchSession.model_fields)
    assert schema["additionalProperties"] is False


def test_a_session_keeps_a_trimmed_request_and_may_have_none_yet() -> None:
    assert C.PresearchSession(session_id=ex.SESSION_ID, request="  laptop \n").request == "laptop"
    assert C.PresearchSession(session_id=ex.SESSION_ID, request="").request == ""


@pytest.mark.parametrize(
    "change",
    [
        {"session_id": "session-1"},
        {"request": "x" * 1001},
        {"unclear_attempts": -1},
        {"unclear_attempts": C.MAX_UNCLEAR_ATTEMPTS + 1},
        {"request_saved": "maybe"},
        {"surprise": 1},
    ],
)
def test_a_bad_session_is_refused(change: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        C.PresearchSession.model_validate({"session_id": ex.SESSION_ID, "request": "laptop", **change})


def test_a_session_is_frozen() -> None:
    with pytest.raises(ValidationError):
        C.PresearchSession(session_id=ex.SESSION_ID, request="laptop").request = "phone"  # type: ignore[misc]


def test_changed_validates_and_keeps_everything_else() -> None:
    state = C.InterviewState(session_id=ex.SESSION_ID, intent=ex.intent(), criteria=ex.criteria())
    session = C.PresearchSession(
        session_id=ex.SESSION_ID, request=ex.QUERY, intent=ex.intent(), criteria=ex.criteria(), interview=state
    )
    updated = session.changed(unclear_attempts=1)
    assert updated == C.PresearchSession.model_validate({**session.model_dump(), "unclear_attempts": 1})
    assert (updated.intent, updated.criteria, updated.interview) == (ex.intent(), ex.criteria(), state)
    with pytest.raises(ValidationError):
        session.changed(unclear_attempts=C.MAX_UNCLEAR_ATTEMPTS + 1)
    with pytest.raises(ValidationError):
        session.changed(surprise=True)


def test_the_limits_agree_with_the_rest_of_the_code() -> None:
    assert get_args(RequestSaved) == get_args(db.SaveOutcome)
    limit = C.IntakeInput.model_json_schema()["properties"]["unclear_attempts"]["maximum"]
    assert limit == C.MAX_UNCLEAR_ATTEMPTS
