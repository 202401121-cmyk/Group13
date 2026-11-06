"""Blank text at the intake (US-01, F-1): one clear follow-up question, never a server error.

Text of only spaces, tabs or newlines passes the route's length check, so it must be handled at that boundary: the
inner IntakeInput refuses it. The same goes for every other value a route accepts and then cleans up again, which
is why replies, intent edits and the CLI are here too. tests/conftest.py keeps the LLM fake and the database off.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.presearch import NOT_ANSWERED, NOT_READY, get_llm, get_request_store, get_session_store
from app.api.sessions import SESSION_ID_PATTERN, InMemorySessionStore
from app.cli import run
from app.contracts import Intent
from app.db import InMemoryRequestStore
from app.intake.rules import NO_CATEGORY_QUESTION
from app.main import create_app
from app.presearch import start_request
from tests.fakes.llm import FakeLLMClient

BLANK = ["   ", "\t\t", "\n\n", " \t\r\n ", " " * 1000]
LAPTOP = "best laptop for coding under 70k"
REPHRASE = "Please pick one of the buttons, or type a number from 0 to 10."


@pytest.fixture
def llm() -> FakeLLMClient:
    return FakeLLMClient()


@pytest.fixture
def requests() -> InMemoryRequestStore:
    return InMemoryRequestStore()


@pytest.fixture
def client(llm: FakeLLMClient, requests: InMemoryRequestStore) -> Iterator[TestClient]:
    app = create_app(env={})
    sessions = InMemorySessionStore()
    app.dependency_overrides[get_session_store] = lambda: sessions
    app.dependency_overrides[get_request_store] = lambda: requests
    app.dependency_overrides[get_llm] = lambda: llm
    with TestClient(app) as test_client:
        yield test_client


def intake(client: TestClient, text: str, session_id: str | None = None) -> dict[str, Any]:
    body = {"text": text} | ({"session_id": session_id} if session_id else {})
    response = client.post("/api/presearch/intake", json=body)
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()
    Intent.model_validate(data["intent"])
    return data


def session_of(client: TestClient, session_id: str) -> dict[str, Any]:
    data: dict[str, Any] = client.get(f"/api/presearch/{session_id}").json()
    return data


def start_interview(client: TestClient) -> str:
    session_id: str = intake(client, LAPTOP)["session_id"]
    client.post(f"/api/presearch/{session_id}/criteria")
    client.post(f"/api/presearch/{session_id}/interview/start")
    return session_id


# --- blank text as a new request ---------------------------------------------------------------------


@pytest.mark.parametrize("text", BLANK, ids=["spaces", "tabs", "newlines", "mixed", "1000-spaces"])
def test_blank_text_gets_the_follow_up_question_not_an_error(client: TestClient, text: str) -> None:
    body = intake(client, text)
    assert body["ready_for_research"] is False
    assert re.match(SESSION_ID_PATTERN, body["session_id"])
    intent = body["intent"]
    assert intent["needs_clarification"] is True
    assert intent["clarification_question"] == NO_CATEGORY_QUESTION
    assert 3 <= len(intent["clarification_options"]) <= 4
    assert intent["category"] is None and intent["show_example_requests"] is False


def test_blank_text_gets_the_same_question_as_text_with_nothing_to_read(client: TestClient) -> None:
    blank, unreadable = intake(client, "   ")["intent"], intake(client, "???")["intent"]
    assert blank["clarification_question"] == unreadable["clarification_question"]
    assert blank["clarification_options"] == unreadable["clarification_options"]


def test_blank_text_spends_no_llm_call_and_is_not_saved_as_a_request(
    client: TestClient, llm: FakeLLMClient, requests: InMemoryRequestStore
) -> None:
    session_id = intake(client, "   ")["session_id"]
    assert llm.calls == []
    assert requests.rows == []
    session = session_of(client, session_id)
    assert (session["request"], session["intent"], session["request_saved"]) == ("", None, None)
    assert session["unclear_attempts"] == 1


def test_research_cannot_start_after_blank_text(client: TestClient) -> None:
    session_id = intake(client, "   ")["session_id"]
    response = client.post(f"/api/presearch/{session_id}/criteria")
    assert (response.status_code, response.json()) == (409, {"detail": NOT_READY})


def test_text_with_no_characters_gets_the_same_follow_up_as_blank_text(
    client: TestClient, llm: FakeLLMClient, requests: InMemoryRequestStore
) -> None:
    empty, blank = intake(client, ""), intake(client, "   ")
    assert empty["ready_for_research"] is False
    assert empty["intent"] == blank["intent"]
    assert empty["intent"]["clarification_question"] == NO_CATEGORY_QUESTION
    assert 3 <= len(empty["intent"]["clarification_options"]) <= 4
    assert llm.calls == [] and requests.rows == []
    session = session_of(client, empty["session_id"])
    assert (session["request"], session["intent"], session["unclear_attempts"]) == ("", None, 1)


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"session_id": "sess_abcdef"},
        {"text": None},
        {"text": 7},
        {"text": ["laptop"]},
        {"text": "x" * 1001},
        {"text": "x" * 1000 + " "},
        {"text": "", "surprise": 1},
    ],
    ids=["no-body-fields", "no-text", "null", "number", "list", "too-long", "too-long-as-sent", "extra-field"],
)
def test_a_body_that_is_still_invalid_gets_a_plain_422(client: TestClient, body: dict[str, Any]) -> None:
    response = client.post("/api/presearch/intake", json=body)
    assert response.status_code == 422
    assert list(response.json()) == ["detail"]
    assert response.json()["detail"].startswith("Please check: ")


def test_a_full_length_request_still_fits(client: TestClient) -> None:
    assert client.post("/api/presearch/intake", json={"text": "x" * 1000}).status_code == 200


def test_blank_text_over_the_limit_is_too_long(client: TestClient) -> None:
    response = client.post("/api/presearch/intake", json={"text": " " * 1001})
    assert response.status_code == 422
    assert "at most 1000 characters" in response.json()["detail"]


# --- what comes after blank text ---------------------------------------------------------------------


def test_the_text_after_blank_text_is_the_request(client: TestClient, requests: InMemoryRequestStore) -> None:
    session_id = intake(client, "   ")["session_id"]
    body = intake(client, LAPTOP, session_id)
    assert body["session_id"] == session_id
    assert body["ready_for_research"] is True
    assert body["intent"]["raw_query"] == LAPTOP
    assert body["intent"]["category"] == "laptop"
    assert [row.request_text for row in requests.rows] == [LAPTOP]
    session = session_of(client, session_id)
    assert (session["request"], session["request_saved"]) == (LAPTOP, "saved")


def test_an_unclear_reply_after_blank_text_shows_example_requests(client: TestClient) -> None:
    session_id = intake(client, "   ")["session_id"]
    body = intake(client, "something nice", session_id)
    assert body["ready_for_research"] is False
    assert body["intent"]["show_example_requests"] is True
    assert body["intent"]["category"] is None  # never guessed


def test_blank_text_twice_shows_example_requests(client: TestClient) -> None:
    session_id = intake(client, "   ")["session_id"]
    body = intake(client, "\n", session_id)
    assert body["session_id"] == session_id
    assert body["intent"]["show_example_requests"] is True
    assert body["intent"]["clarification_question"] is None
    assert session_of(client, session_id)["unclear_attempts"] == 2


def test_blank_text_never_clears_a_request_already_in_the_session(
    client: TestClient, requests: InMemoryRequestStore
) -> None:
    session_id = start_interview(client)
    before = session_of(client, session_id)
    body = intake(client, "   ", session_id)
    assert body["session_id"] == session_id
    assert body["intent"]["clarification_question"] == NO_CATEGORY_QUESTION
    assert session_of(client, session_id) == before
    assert len(requests.rows) == 1


def test_a_blank_reply_to_our_question_shows_example_requests(client: TestClient) -> None:
    first = intake(client, "something for my mom")
    body = intake(client, "   ", first["session_id"])
    assert body["intent"]["show_example_requests"] is True
    assert body["intent"]["raw_query"] == "something for my mom"
    assert session_of(client, first["session_id"])["request"] == "something for my mom"


def test_the_text_after_empty_text_is_the_request(client: TestClient, requests: InMemoryRequestStore) -> None:
    session_id = intake(client, "")["session_id"]
    body = intake(client, LAPTOP, session_id)
    assert (body["session_id"], body["ready_for_research"]) == (session_id, True)
    assert [row.request_text for row in requests.rows] == [LAPTOP]


def test_empty_text_twice_shows_example_requests(client: TestClient) -> None:
    session_id = intake(client, "")["session_id"]
    assert intake(client, "", session_id)["intent"]["show_example_requests"] is True


def test_empty_text_never_clears_a_request_already_in_the_session(client: TestClient) -> None:
    session_id = start_interview(client)
    before = session_of(client, session_id)
    assert intake(client, "", session_id)["intent"]["clarification_question"] == NO_CATEGORY_QUESTION
    assert session_of(client, session_id) == before


def test_an_empty_reply_to_our_question_shows_example_requests(client: TestClient) -> None:
    first = intake(client, "something for my mom")
    body = intake(client, "", first["session_id"])
    assert body["intent"]["show_example_requests"] is True
    assert body["intent"]["category"] is None  # never guessed


# --- text is cleaned once, at the boundary -----------------------------------------------------------


def test_spaces_around_a_request_are_trimmed_once_everywhere(
    client: TestClient, requests: InMemoryRequestStore
) -> None:
    body = intake(client, f"  \n{LAPTOP}\t ")
    assert body["intent"]["raw_query"] == LAPTOP
    assert session_of(client, body["session_id"])["request"] == LAPTOP
    assert [row.request_text for row in requests.rows] == [LAPTOP]


def test_start_request_answers_blank_text_without_the_stage_or_the_llm() -> None:
    llm = FakeLLMClient()
    intent = start_request(" \n ", llm=llm)
    assert intent.needs_clarification and intent.clarification_question == NO_CATEGORY_QUESTION
    assert llm.calls == []
    assert start_request("", unclear_attempts=1, llm=llm).show_example_requests is True


# --- the same class of bug in the other routes -------------------------------------------------------


@pytest.mark.parametrize("reply", ["   ", "\t", "\n \n"])
def test_a_reply_of_only_spaces_gets_a_rephrase(client: TestClient, reply: str) -> None:
    session_id = start_interview(client)
    response = client.post(f"/api/presearch/{session_id}/interview/reply", json={"reply_text": reply})
    assert response.status_code == 200
    assert (response.json()["kind"], response.json()["message"]) == ("rephrase", REPHRASE)


def test_two_replies_of_only_spaces_move_on_to_the_next_question(client: TestClient) -> None:
    session_id = start_interview(client)
    path = f"/api/presearch/{session_id}/interview/reply"
    first = client.post(path, json={"reply_text": "   "}).json()
    second = client.post(path, json={"reply_text": "   "})
    assert second.status_code == 200
    assert second.json()["question"]["id"] != first["question"]["id"]


def test_an_intent_edit_of_only_spaces_is_refused_plainly(client: TestClient) -> None:
    session_id = intake(client, LAPTOP)["session_id"]
    response = client.put(f"/api/presearch/{session_id}/intent", json={"changes": {"raw_query": "   "}})
    assert response.status_code == 422
    assert response.json()["detail"].startswith("That change doesn't work: raw_query - ")
    assert session_of(client, session_id)["intent"]["raw_query"] == LAPTOP


def test_going_back_to_a_blank_question_id_is_refused_plainly(client: TestClient) -> None:
    session_id = start_interview(client)
    response = client.post(f"/api/presearch/{session_id}/interview/back", json={"question_id": "   "})
    assert (response.status_code, response.json()) == (409, {"detail": NOT_ANSWERED})


# --- the command line runs the same path -------------------------------------------------------------


@pytest.mark.parametrize("text", ["", "   ", "\n\t"], ids=["empty", "spaces", "newline-tab"])
def test_the_cli_asks_and_saves_nothing_for_blank_text(text: str, capsys: pytest.CaptureFixture[str]) -> None:
    store = InMemoryRequestStore()
    assert run(text, False, store) == 1
    out = capsys.readouterr().out
    assert NO_CATEGORY_QUESTION in out
    assert "Results for" not in out and "Saved:" not in out
    assert store.rows == []
