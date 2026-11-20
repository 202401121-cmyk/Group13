"""Sprint 3 leftovers on the rubric route (S4-17, US-09, US-10).

- The all-zero note is in the response body (RubricEditResponse.note). A page reload (GET
  /api/presearch/{session_id}) carries it in PresearchSession.note (1.10.0) while the edits leave every weight at 0,
  and the X-Rubric-Note header carries it on both routes for one more sprint (S5-0).
- "Always" set on a leave-out the request itself named ("no Zenith" in the example intent) is kept across rebuilds:
  a new answer, a step back and a changed intent, as the shopper's own added leave-outs are. It is stored apart from
  the added ones (InterviewState.manual_always, 1.10.0), so it counts once against the 30.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.presearch import RUBRIC_NOTE_HEADER, get_session_store
from app.api.sessions import InMemorySessionStore
from app.contracts import PresearchSession, Rubric, RubricEditResponse
from app.contracts import examples as ex
from app.interview.rubric_builder import ALL_ZERO_NOTE
from app.main import create_app

SID = ex.SESSION_ID
ZENITH = {"kind": "brand", "value": "Zenith"}  # the example request's own leave-out


@pytest.fixture
def client() -> Iterator[TestClient]:
    store = InMemorySessionStore()
    app = create_app(env={})
    app.dependency_overrides[get_session_store] = lambda: store
    store.put(PresearchSession(session_id=SID, request=ex.QUERY, intent=ex.intent(), criteria=ex.criteria()))
    with TestClient(app) as test_client:
        yield test_client


def _start(client: TestClient) -> dict[str, Any]:
    response = client.post(f"/api/presearch/{SID}/interview/start")
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()
    return data


def _edit(client: TestClient, *edits: dict[str, Any]) -> Any:
    response = client.post(f"/api/presearch/{SID}/rubric", json={"edits": list(edits)})
    assert response.status_code == 200, response.text
    return response


def _zeros() -> list[dict[str, Any]]:
    return [{"kind": "set_weight", "criterion_key": c.key, "weight": 0} for c in ex.criteria().criteria]


def _always(rubric: Rubric | dict[str, Any]) -> dict[str, bool]:
    data = rubric.model_dump() if isinstance(rubric, Rubric) else rubric
    return {e["value"]: e["always"] for e in data["exclusions"]}


def _set_always(client: TestClient, always: bool) -> Rubric:
    # What the editor sends (frontend RubricEditor.setAlways): remove the leave-out, then add it with the new flag.
    response = _edit(
        client,
        {"kind": "remove_exclusion", "exclusion": ZENITH},
        {"kind": "add_exclusion", "exclusion": {**ZENITH, "always": always}},
    )
    return RubricEditResponse.model_validate(response.json()).rubric


def _rebuilt(client: TestClient) -> Rubric:
    """The rubric as the next edit rebuilds it: a harmless weight change."""
    response = _edit(client, {"kind": "set_weight", "criterion_key": "weight", "weight": 5})
    return RubricEditResponse.model_validate(response.json()).rubric


# --- the note --------------------------------------------------------------------------------------------------------


def test_the_note_is_in_the_response_body_and_the_header(client: TestClient) -> None:
    _start(client)
    response = _edit(client, *_zeros())
    assert RubricEditResponse.model_validate(response.json()).note == ALL_ZERO_NOTE
    assert response.headers[RUBRIC_NOTE_HEADER] == ALL_ZERO_NOTE  # kept for one sprint (S4-16 may read either)


def test_no_note_while_a_weight_counts(client: TestClient) -> None:
    _start(client)
    response = _edit(client, {"kind": "set_weight", "criterion_key": "weight", "weight": 0})
    assert RubricEditResponse.model_validate(response.json()).note is None
    assert RUBRIC_NOTE_HEADER not in response.headers


def test_a_reload_carries_the_note_while_every_weight_is_0(client: TestClient) -> None:
    _start(client)
    _edit(client, *_zeros())
    reload = client.get(f"/api/presearch/{SID}")
    assert reload.status_code == 200
    assert reload.headers[RUBRIC_NOTE_HEADER] == ALL_ZERO_NOTE


def test_a_reload_drops_the_note_once_a_weight_is_raised(client: TestClient) -> None:
    _start(client)
    _edit(client, *_zeros())
    raised = _edit(client, {"kind": "set_weight", "criterion_key": "weight", "weight": 4})
    assert RubricEditResponse.model_validate(raised.json()).note is None
    assert RUBRIC_NOTE_HEADER not in client.get(f"/api/presearch/{SID}").headers


def test_a_reload_without_an_interview_has_no_note(client: TestClient) -> None:
    assert RUBRIC_NOTE_HEADER not in client.get(f"/api/presearch/{SID}").headers


# --- "Always" on the request's own leave-out --------------------------------------------------------------------------


def test_always_on_a_request_leave_out_is_kept_in_the_answer(client: TestClient) -> None:
    _start(client)
    rubric = _set_always(client, True)
    assert _always(rubric) == {"Zenith": True}
    assert [e.value for e in rubric.exclusions] == ["Zenith"]  # in its place, once


def test_always_on_a_request_leave_out_survives_a_new_answer(client: TestClient) -> None:
    _start(client)
    _set_always(client, True)
    reply = client.post(f"/api/presearch/{SID}/interview/reply", json={"reply_text": "High", "level": "high"})
    assert reply.status_code == 200, reply.text
    assert _always(_rebuilt(client)) == {"Zenith": True}


def test_always_on_a_request_leave_out_survives_a_step_back(client: TestClient) -> None:
    first = _start(client)
    reply = client.post(f"/api/presearch/{SID}/interview/reply", json={"reply_text": "High", "level": "high"})
    assert reply.status_code == 200, reply.text
    _set_always(client, True)
    back = client.post(f"/api/presearch/{SID}/interview/back", json={"question_id": first["question"]["id"]})
    assert back.status_code == 200, back.text
    assert _always(_rebuilt(client)) == {"Zenith": True}


def test_always_on_a_request_leave_out_survives_an_intent_change(client: TestClient) -> None:
    _start(client)
    _set_always(client, True)
    assert client.put(f"/api/presearch/{SID}/intent", json={"changes": {"region": "US"}}).status_code == 200
    _start(client)
    assert _always(_rebuilt(client)) == {"Zenith": True}


def test_always_can_be_cleared_again(client: TestClient) -> None:
    _start(client)
    _set_always(client, True)
    rubric = _set_always(client, False)
    assert _always(rubric) == {"Zenith": False}
    assert _always(_rebuilt(client)) == {"Zenith": False}


def test_removing_a_request_leave_out_with_always_removes_it(client: TestClient) -> None:
    _start(client)
    _set_always(client, True)
    response = _edit(client, {"kind": "remove_exclusion", "exclusion": ZENITH})
    assert RubricEditResponse.model_validate(response.json()).rubric.exclusions == []


# --- the note in the session itself (1.10.0, S5-0) -------------------------------------------------------------------


def test_the_note_survives_a_reload_in_the_session_itself(client: TestClient) -> None:
    _start(client)
    _edit(client, *_zeros())
    reload = client.get(f"/api/presearch/{SID}")
    assert reload.status_code == 200
    assert PresearchSession.model_validate(reload.json()).note == ALL_ZERO_NOTE
    assert reload.headers[RUBRIC_NOTE_HEADER] == ALL_ZERO_NOTE  # kept one more sprint


def test_the_reloaded_session_has_no_note_once_a_weight_counts(client: TestClient) -> None:
    _start(client)
    _edit(client, *_zeros())
    _edit(client, {"kind": "set_weight", "criterion_key": "weight", "weight": 4})
    assert PresearchSession.model_validate(client.get(f"/api/presearch/{SID}").json()).note is None


def test_a_session_with_no_interview_has_no_note(client: TestClient) -> None:
    assert PresearchSession.model_validate(client.get(f"/api/presearch/{SID}").json()).note is None


def test_always_on_a_request_leave_out_reaches_the_session_apart(client: TestClient) -> None:
    _start(client)
    _set_always(client, True)
    state = PresearchSession.model_validate(client.get(f"/api/presearch/{SID}").json()).interview
    assert state is not None
    assert [(e.value, e.always) for e in state.manual_always] == [("Zenith", True)]
    assert state.manual_exclusions_added == []
