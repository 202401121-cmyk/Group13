"""The pre-search API (US-05) through FastAPI's TestClient. No server, no network, no real LLM.

tests/conftest.py already makes the LLM a fake that is always unavailable (so intake uses its keyword rules and
criteria use the fallback sets) and gives each test an empty criteria store; each test here also gets its own
session store.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.presearch import EXPIRED, FINISHED, NO_CRITERIA, NO_INTERVIEW, NOT_ANSWERED, NOT_READY, get_session_store
from app.api.sessions import SESSION_ID_PATTERN, InMemorySessionStore
from app.contracts import CriteriaSet, Intent, InterviewStep
from app.main import create_app

LAPTOP = "best laptop for coding under 70k"
FRONTEND = "http://localhost:3000"


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app(env={})  # the default origins, whatever a developer's .env says
    store = InMemorySessionStore()
    app.dependency_overrides[get_session_store] = lambda: store
    with TestClient(app) as test_client:
        yield test_client


def intake(client: TestClient, text: str, session_id: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"text": text}
    if session_id:
        body["session_id"] = session_id
    response = client.post("/api/presearch/intake", json=body)
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()
    Intent.model_validate(data["intent"])
    return data


def post(client: TestClient, path: str, body: dict[str, Any] | None = None, status: int = 200) -> dict[str, Any]:
    response = client.post(f"/api/presearch/{path}", json=body)
    assert response.status_code == status, response.text
    data: dict[str, Any] = response.json()
    return data


def ready_interview(client: TestClient) -> tuple[str, dict[str, Any]]:
    session_id = intake(client, LAPTOP)["session_id"]
    post(client, f"{session_id}/criteria")
    return session_id, post(client, f"{session_id}/interview/start")


def test_health(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_the_full_happy_path(client: TestClient) -> None:
    first = intake(client, LAPTOP)
    assert re.match(SESSION_ID_PATTERN, first["session_id"])
    assert first["ready_for_research"] is True
    assert first["intent"]["category"] == "laptop"
    assert first["intent"]["budget"]["max"]["amount"] == 70_000
    session_id = first["session_id"]

    criteria = CriteriaSet.model_validate(post(client, f"{session_id}/criteria"))
    assert criteria.category == "laptop" and len(criteria.criteria) == 8

    step = InterviewStep.model_validate(post(client, f"{session_id}/interview/start"))
    assert step.kind == "question"
    assert step.message == "Processor speed: how much does it matter to you?"
    assert step.question is not None and step.question.quick_replies == ["Low", "Medium", "High", "Skip"]

    replies = ["High", "Medium", "7", "Skip", "Low", "8/10", "High", "Medium", "Medium", "Medium"]
    turns = 0
    while step.kind != "finished":
        step = InterviewStep.model_validate(
            post(client, f"{session_id}/interview/reply", {"reply_text": replies[turns]})
        )
        turns += 1
    assert turns >= 5
    assert step.rubric is not None
    assert {w.key for w in step.rubric.weights} == {c.key for c in criteria.criteria}
    assert step.rubric.budget is not None and step.rubric.budget.max is not None
    assert step.rubric.budget.max.amount == 70_000

    # A reply after the end is refused, plainly.
    response = client.post(f"/api/presearch/{session_id}/interview/reply", json={"reply_text": "High"})
    assert (response.status_code, response.json()) == (409, {"detail": FINISHED})


def test_no_product_then_unclear_reply_then_examples_then_a_clear_request(client: TestClient) -> None:
    first = intake(client, "something for my mom")
    assert first["ready_for_research"] is False
    assert first["intent"]["clarification_question"] == "What kind of product are you looking for?"
    assert 3 <= len(first["intent"]["clarification_options"]) <= 4
    session_id = first["session_id"]

    unclear = intake(client, "idk, something nice", session_id)
    assert unclear["session_id"] == session_id
    assert unclear["ready_for_research"] is False
    assert unclear["intent"]["show_example_requests"] is True
    assert unclear["intent"]["category"] is None  # never guessed

    clear = intake(client, "a mixer grinder for her kitchen", session_id)
    assert clear["ready_for_research"] is True
    assert clear["intent"]["category"] == "mixer_grinder"
    assert clear["intent"]["raw_query"] == "something for my mom"


def test_a_new_request_without_a_session_starts_over(client: TestClient) -> None:
    first = intake(client, "something for my mom")
    fresh = intake(client, "mixer grinder under 5k")
    assert fresh["session_id"] != first["session_id"]
    assert fresh["ready_for_research"] is True


def test_watch_asks_which_kind_then_smartwatch_is_ready(client: TestClient) -> None:
    first = intake(client, "watch")
    assert first["ready_for_research"] is False
    assert first["intent"]["clarification_options"] == ["Smartwatch", "Fitness band", "Analog watch"]
    second = intake(client, "smartwatch", first["session_id"])
    assert second["ready_for_research"] is True
    assert second["intent"]["category"] == "smartwatch"


def test_edit_the_intent(client: TestClient) -> None:
    session_id, _ = ready_interview(client)
    response = client.put(f"/api/presearch/{session_id}/intent", json={"changes": {"use_case": "video editing"}})
    assert response.status_code == 200
    assert Intent.model_validate(response.json()).use_case == "video editing"
    saved = client.get(f"/api/presearch/{session_id}").json()
    assert saved["intent"]["use_case"] == "video editing"
    assert saved["criteria"] is None and saved["interview"] is None  # a new use needs new criteria


def test_an_invalid_intent_edit_is_refused_plainly(client: TestClient) -> None:
    session_id = intake(client, LAPTOP)["session_id"]
    for changes, field in [({"region": "India"}, "region"), ({"colour": "red"}, "colour")]:
        response = client.put(f"/api/presearch/{session_id}/intent", json={"changes": changes})
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert detail.startswith("That change doesn't work: ") and field in detail
        assert "India" not in detail and "Traceback" not in detail
    assert client.get(f"/api/presearch/{session_id}").json()["intent"]["region"] == "IN"  # unchanged


def test_an_edit_that_keeps_the_product_keeps_the_criteria(client: TestClient) -> None:
    session_id, _ = ready_interview(client)
    response = client.put(f"/api/presearch/{session_id}/intent", json={"changes": {"region": "US"}})
    assert response.status_code == 200
    saved = client.get(f"/api/presearch/{session_id}").json()
    assert saved["criteria"] is not None
    assert saved["interview"] is None  # the interview copies the intent, so it restarts


def test_unknown_session_is_404(client: TestClient) -> None:
    for method, path, body in [
        ("get", "sess_unknown1", None),
        ("post", "sess_unknown1/criteria", None),
        ("post", "sess_unknown1/interview/start", None),
        ("post", "sess_unknown1/interview/reply", {"reply_text": "High"}),
        ("post", "sess_unknown1/interview/back", {"question_id": "q_x"}),
        ("put", "sess_unknown1/intent", {"changes": {}}),
    ]:
        response = client.request(method, f"/api/presearch/{path}", json=body)
        assert (response.status_code, response.json()) == (404, {"detail": EXPIRED}), path
    response = client.post("/api/presearch/intake", json={"session_id": "sess_unknown1", "text": "laptop"})
    assert response.status_code == 404


def test_criteria_before_the_product_is_known_is_409(client: TestClient) -> None:
    session_id = intake(client, "something for my mom")["session_id"]
    assert post(client, f"{session_id}/criteria", status=409) == {"detail": NOT_READY}


def test_interview_before_criteria_is_409(client: TestClient) -> None:
    session_id = intake(client, LAPTOP)["session_id"]
    assert post(client, f"{session_id}/interview/start", status=409) == {"detail": NO_CRITERIA}
    assert post(client, f"{session_id}/interview/reply", {"reply_text": "High"}, status=409) == {"detail": NO_INTERVIEW}
    assert post(client, f"{session_id}/interview/back", {"question_id": "q_x"}, status=409) == {"detail": NO_INTERVIEW}


def test_go_back_and_change_an_answer(client: TestClient) -> None:
    session_id, first = ready_interview(client)
    first_id = first["question"]["id"]
    post(client, f"{session_id}/interview/reply", {"reply_text": "High"})
    post(client, f"{session_id}/interview/reply", {"reply_text": "Medium"})

    back = InterviewStep.model_validate(post(client, f"{session_id}/interview/back", {"question_id": first_id}))
    assert back.kind == "question" and back.question is not None and back.question.id == first_id

    post(client, f"{session_id}/interview/reply", {"reply_text": "Low"})
    history = client.get(f"/api/presearch/{session_id}").json()["interview"]["history"]
    corrected = next(h for h in history if h["question"]["id"] == first_id)
    assert corrected["status"] == "answered"
    assert corrected["classification"]["reply_type"] == "correction"
    assert corrected["classification"]["weight"] == 2.0

    unknown = post(client, f"{session_id}/interview/back", {"question_id": "q_not_asked"}, status=409)
    assert unknown == {"detail": NOT_ANSWERED}


def test_an_unclear_reply_gets_a_rephrase(client: TestClient) -> None:
    session_id, first = ready_interview(client)
    step = post(client, f"{session_id}/interview/reply", {"reply_text": "whatever"})
    assert step["kind"] == "rephrase"
    assert step["message"] == "Please pick one of the buttons, or type a number from 0 to 10."
    assert step["question"]["id"] == first["question"]["id"]


def test_get_session_for_a_page_reload(client: TestClient) -> None:
    session_id, first = ready_interview(client)
    saved = client.get(f"/api/presearch/{session_id}").json()
    assert saved["session_id"] == session_id
    assert saved["request"] == LAPTOP
    assert saved["intent"]["category"] == "laptop"
    assert saved["criteria"]["category"] == "laptop"
    assert saved["interview_step"]["question"]["id"] == first["question"]["id"]
    assert [h["status"] for h in saved["interview"]["history"]] == ["pending"]


def test_bad_bodies_get_a_plain_422_without_echoing_the_input(client: TestClient) -> None:
    session_id, _ = ready_interview(client)
    secret = "my private words " * 40  # over 500 characters
    response = client.post(f"/api/presearch/{session_id}/interview/reply", json={"reply_text": secret})
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "reply_text" in detail and "my private words" not in detail
    response = client.post("/api/presearch/intake", json={"text": secret * 2})  # over 1,000 characters
    assert response.status_code == 422 and "my private words" not in response.json()["detail"]


def test_cors_allows_only_the_frontend_origin(client: TestClient) -> None:
    ok = client.get("/api/health", headers={"Origin": FRONTEND})
    assert ok.headers.get("access-control-allow-origin") == FRONTEND
    other = client.get("/api/health", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in other.headers
    preflight = client.options(
        "/api/presearch/intake",
        headers={
            "Origin": FRONTEND,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers.get("access-control-allow-origin") == FRONTEND


def test_cors_follows_frontend_origins_from_the_environment() -> None:
    with TestClient(create_app(env={"FRONTEND_ORIGINS": "https://shopsense.example.org"})) as custom:
        allowed = custom.get("/api/health", headers={"Origin": "https://shopsense.example.org"})
        assert allowed.headers.get("access-control-allow-origin") == "https://shopsense.example.org"
        local = custom.get("/api/health", headers={"Origin": FRONTEND})
        assert "access-control-allow-origin" not in local.headers


# --- rough answers and exact values (US-08) --------------------------------------------------------


def stored(client: TestClient, session_id: str, question_id: str) -> dict[str, Any]:
    history = client.get(f"/api/presearch/{session_id}").json()["interview"]["history"]
    found: dict[str, Any] = next(h for h in history if h["question"]["id"] == question_id)
    return found


def test_the_route_passes_the_level_on(client: TestClient) -> None:
    session_id, first = ready_interview(client)
    post(client, f"{session_id}/interview/reply", {"reply_text": "High", "level": "low"})  # the text alone would say 8
    assert stored(client, session_id, first["question"]["id"])["classification"]["weight"] == 2.0


def test_the_route_passes_the_exact_weight_on(client: TestClient) -> None:
    session_id, first = ready_interview(client)
    post(client, f"{session_id}/interview/reply", {"reply_text": "High", "exact_weight": 7.5})
    answered = stored(client, session_id, first["question"]["id"])
    assert (answered["status"], answered["classification"]["weight"]) == ("answered", 7.5)


def test_level_and_exact_weight_together_get_the_plain_422_and_change_nothing(client: TestClient) -> None:
    session_id, _ = ready_interview(client)
    before = client.get(f"/api/presearch/{session_id}").json()
    body = {"reply_text": "High", "level": "high", "exact_weight": 8}
    answer = post(client, f"{session_id}/interview/reply", body, status=422)
    assert "never both" in answer["detail"]
    assert client.get(f"/api/presearch/{session_id}").json() == before


@pytest.mark.parametrize("weight", [-0.1, 10.1, 11, "7", True], ids=["below", "just_above", "above", "text", "boolean"])
def test_an_exact_weight_outside_0_to_10_gets_the_plain_422(client: TestClient, weight: object) -> None:
    session_id, _ = ready_interview(client)
    before = client.get(f"/api/presearch/{session_id}").json()
    answer = post(client, f"{session_id}/interview/reply", {"reply_text": "x", "exact_weight": weight}, status=422)
    assert isinstance(answer["detail"], str)
    assert client.get(f"/api/presearch/{session_id}").json() == before


@pytest.mark.parametrize("raw", ["NaN", "Infinity", "-Infinity"])
def test_a_number_json_should_not_have_gets_the_plain_422(client: TestClient, raw: str) -> None:
    """Python's JSON reader accepts these, so a hand-written client could send one."""
    session_id, _ = ready_interview(client)
    before = client.get(f"/api/presearch/{session_id}").json()
    answer = client.post(
        f"/api/presearch/{session_id}/interview/reply",
        content=f'{{"reply_text": "x", "exact_weight": {raw}}}'.encode(),
        headers={"content-type": "application/json"},
    )
    assert answer.status_code == 422
    assert isinstance(answer.json()["detail"], str)
    assert client.get(f"/api/presearch/{session_id}").json() == before


def test_an_unknown_level_gets_the_plain_422(client: TestClient) -> None:
    session_id, _ = ready_interview(client)
    answer = post(client, f"{session_id}/interview/reply", {"reply_text": "x", "level": "very_high"}, status=422)
    assert isinstance(answer["detail"], str)


def test_switching_through_the_api_shows_in_get_session(client: TestClient) -> None:
    session_id, first = ready_interview(client)
    question_id = first["question"]["id"]
    post(client, f"{session_id}/interview/reply", {"reply_text": "Low", "level": "low", "question_id": question_id})
    assert stored(client, session_id, question_id)["classification"]["weight"] == 2.0

    post(client, f"{session_id}/interview/back", {"question_id": question_id})
    post(
        client, f"{session_id}/interview/reply", {"reply_text": "7.5", "exact_weight": 7.5, "question_id": question_id}
    )
    assert stored(client, session_id, question_id)["classification"]["weight"] == 7.5

    post(client, f"{session_id}/interview/back", {"question_id": question_id})
    post(client, f"{session_id}/interview/reply", {"reply_text": "High", "level": "high", "question_id": question_id})
    answered = stored(client, session_id, question_id)
    assert (answered["reply_text"], answered["classification"]["weight"]) == ("High", 8.0)


def test_an_answer_can_switch_to_an_exact_value_after_the_interview_finished(client: TestClient) -> None:
    session_id, first = ready_interview(client)
    question_id = first["question"]["id"]
    step = first
    while step["kind"] != "finished":
        step = post(client, f"{session_id}/interview/reply", {"reply_text": "Medium", "level": "medium"})

    post(client, f"{session_id}/interview/back", {"question_id": question_id})
    done = post(client, f"{session_id}/interview/reply", {"reply_text": "7.5", "exact_weight": 7.5})

    assert done["kind"] == "finished"
    weight = next(w for w in done["rubric"]["weights"] if w["key"] == first["question"]["criterion_key"])
    assert weight["weight"] == 7.5
    assert abs(sum(w["normalized"] for w in done["rubric"]["weights"]) - 1) < 1e-9
