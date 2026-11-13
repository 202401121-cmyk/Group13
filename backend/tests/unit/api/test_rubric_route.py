"""POST /api/presearch/{session_id}/rubric (US-09): the shopper's rubric edits, stored on the session, applied last.

The session starts from the example intent (a "16 GB RAM" hard rule and a "no Zenith" exclusion) and its criteria, as
the keyword intake makes no hard rules. No network, no real LLM.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.presearch import EXPIRED, NO_INTERVIEW, get_session_store
from app.api.sessions import InMemorySessionStore
from app.contracts import PresearchSession, Rubric, RubricEditResponse
from app.contracts import examples as ex
from app.interview.rubric_builder import ALL_ZERO_NOTE
from app.main import create_app

SID = ex.SESSION_ID
RULE = ex.intent().hard_rules[0]


@pytest.fixture
def store() -> InMemorySessionStore:
    return InMemorySessionStore()


@pytest.fixture
def client(store: InMemorySessionStore) -> Iterator[TestClient]:
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


def _edit(client: TestClient, *edits: dict[str, Any], status: int = 200, sid: str = SID) -> Any:
    response = client.post(f"/api/presearch/{sid}/rubric", json={"edits": list(edits)})
    assert response.status_code == status, response.text
    return response


def _rubric(response: Any) -> Rubric:
    return RubricEditResponse.model_validate(response.json()).rubric


def _session(client: TestClient) -> PresearchSession:
    return PresearchSession.model_validate(client.get(f"/api/presearch/{SID}").json())


def _weights(rubric: Rubric) -> dict[str, tuple[float, str]]:
    return {w.key: (w.weight, w.origin) for w in rubric.weights}


def test_the_route_returns_the_rebuilt_rubric_and_stores_the_edits(client: TestClient) -> None:
    _start(client)
    rubric = _rubric(
        _edit(
            client,
            {"kind": "set_weight", "criterion_key": "weight", "weight": 9},
            {"kind": "switch_rule", "rule_id": RULE.id, "active": False},
            {"kind": "add_exclusion", "exclusion": {"kind": "brand", "value": "Nova"}},
        )
    )
    assert _weights(rubric)["weight"] == (9.0, "manual")
    assert [(r.id, r.active, r.source_text) for r in rubric.hard_rules] == [(RULE.id, False, RULE.source_text)]
    assert [e.value for e in rubric.exclusions] == ["Zenith", "Nova"]
    assert abs(sum(w.normalized for w in rubric.weights) - 1.0) <= 1e-9
    stored = _session(client).interview
    assert stored is not None
    assert stored.manual_weights == {"weight": 9.0} and stored.manual_rule_switches == {RULE.id: False}


def test_a_manual_weight_survives_a_new_answer_through_the_routes(client: TestClient) -> None:
    step = _start(client)
    _edit(client, {"kind": "set_weight", "criterion_key": step["question"]["criterion_key"], "weight": 1})
    key = step["question"]["criterion_key"]
    reply = client.post(f"/api/presearch/{SID}/interview/reply", json={"reply_text": "High", "level": "high"})
    assert reply.status_code == 200, reply.text
    assert _weights(_rubric(_edit(client, {"kind": "set_weight", "criterion_key": "weight", "weight": 5})))[key] == (
        1.0,
        "manual",
    )


def test_a_manual_weight_and_a_switch_survive_a_changed_intent(client: TestClient) -> None:
    _start(client)
    _edit(
        client,
        {"kind": "set_weight", "criterion_key": "weight", "weight": 9},
        {"kind": "switch_rule", "rule_id": RULE.id, "active": False},
    )
    changed = client.put(f"/api/presearch/{SID}/intent", json={"changes": {"region": "US"}})
    assert changed.status_code == 200, changed.text
    unstarted = _session(client)
    assert unstarted.interview_step is None  # the interview starts again
    restarted = _start(client)
    assert restarted["state"]["manual_weights"] == {"weight": 9.0}
    rubric = _rubric(_edit(client, {"kind": "add_exclusion", "exclusion": {"kind": "brand", "value": "Nova"}}))
    assert _weights(rubric)["weight"] == (9.0, "manual")
    assert rubric.hard_rules[0].active is False


def test_a_reply_before_the_restart_still_says_the_interview_has_not_started(client: TestClient) -> None:
    _start(client)
    _edit(client, {"kind": "set_weight", "criterion_key": "weight", "weight": 9})
    client.put(f"/api/presearch/{SID}/intent", json={"changes": {"region": "US"}})
    reply = client.post(f"/api/presearch/{SID}/interview/reply", json={"reply_text": "High", "level": "high"})
    assert reply.status_code == 409 and reply.json()["detail"] == NO_INTERVIEW


def test_an_unknown_rule_id_answers_422_with_the_name(client: TestClient) -> None:
    _start(client)
    response = _edit(client, {"kind": "switch_rule", "rule_id": "no_such_rule", "active": False}, status=422)
    assert "no_such_rule" in response.json()["detail"]
    assert _session(client).interview is not None and _session(client).interview.manual_rule_switches == {}  # type: ignore[union-attr]


def test_an_unknown_criterion_key_answers_422_with_the_name(client: TestClient) -> None:
    _start(client)
    response = _edit(client, {"kind": "set_weight", "criterion_key": "not_a_criterion", "weight": 5}, status=422)
    assert "not_a_criterion" in response.json()["detail"]


def test_all_weights_zero_answer_the_defaults_with_a_note(client: TestClient) -> None:
    _start(client)
    zeros = [{"kind": "set_weight", "criterion_key": c.key, "weight": 0} for c in ex.criteria().criteria]
    response = _edit(client, *zeros)
    assert response.headers["X-Rubric-Note"] == ALL_ZERO_NOTE
    assert {origin for _, origin in _weights(_rubric(response)).values()} == {"default"}


def test_the_route_needs_an_interview(client: TestClient) -> None:
    response = _edit(client, {"kind": "set_weight", "criterion_key": "weight", "weight": 9}, status=409)
    assert response.json()["detail"] == NO_INTERVIEW


def test_the_route_answers_404_for_an_expired_session(client: TestClient) -> None:
    response = _edit(
        client, {"kind": "set_weight", "criterion_key": "weight", "weight": 9}, status=404, sid="sess_" + "f" * 24
    )
    assert response.json()["detail"] == EXPIRED


def test_a_finished_interviews_stored_rubric_follows_the_edits(client: TestClient, store: InMemorySessionStore) -> None:
    from app.contracts import InterviewStep

    _start(client)
    session = store.get(SID)
    assert session is not None and session.interview is not None
    finished = InterviewStep(
        questions_asked=0, kind="finished", message="Here is your rubric.", state=session.interview, rubric=ex.rubric()
    )
    store.put(session.changed(interview_step=finished))
    rubric = _rubric(_edit(client, {"kind": "set_weight", "criterion_key": "weight", "weight": 9}))
    step = _session(client).interview_step
    assert step is not None and step.rubric == rubric


# --- from the review -------------------------------------------------------------------------------------------------


def _manual(client: TestClient) -> dict[str, float]:
    interview = _session(client).interview
    return dict(interview.manual_weights) if interview is not None else {}


def test_new_criteria_keep_the_edits(client: TestClient) -> None:
    _start(client)
    _edit(client, {"kind": "set_weight", "criterion_key": "weight", "weight": 9})
    assert client.post(f"/api/presearch/{SID}/criteria").status_code == 200
    assert _manual(client) == {"weight": 9.0}
    restarted = _start(client)
    assert restarted["state"]["manual_weights"] == {"weight": 9.0}


def test_a_new_use_then_another_intent_edit_still_keeps_the_edits(client: TestClient) -> None:
    _start(client)
    _edit(client, {"kind": "set_weight", "criterion_key": "weight", "weight": 9})
    assert client.put(f"/api/presearch/{SID}/intent", json={"changes": {"use_case": "gaming"}}).status_code == 200
    assert _session(client).criteria is None and _manual(client) == {"weight": 9.0}
    assert client.put(f"/api/presearch/{SID}/intent", json={"changes": {"region": "US"}}).status_code == 200
    assert _manual(client) == {"weight": 9.0}  # no criteria yet: the edits still ride on the stored state
    assert client.post(f"/api/presearch/{SID}/criteria").status_code == 200
    assert _start(client)["state"]["manual_weights"] == {"weight": 9.0}


def test_a_bad_edit_after_a_good_one_stores_nothing(client: TestClient) -> None:
    _start(client)
    _edit(
        client,
        {"kind": "set_weight", "criterion_key": "weight", "weight": 9},
        {"kind": "switch_rule", "rule_id": "no_such_rule", "active": False},
        status=422,
    )
    assert _manual(client) == {}


def test_going_back_through_the_route_keeps_the_edits(client: TestClient) -> None:
    first = _start(client)
    question_id = first["question"]["id"]
    reply = client.post(f"/api/presearch/{SID}/interview/reply", json={"reply_text": "High", "level": "high"})
    assert reply.status_code == 200, reply.text
    _edit(client, {"kind": "set_weight", "criterion_key": "weight", "weight": 9})
    back = client.post(f"/api/presearch/{SID}/interview/back", json={"question_id": question_id})
    assert back.status_code == 200, back.text
    assert back.json()["state"]["manual_weights"] == {"weight": 9.0}


def test_an_edit_made_while_the_criteria_are_worked_out_is_kept(
    client: TestClient, store: InMemorySessionStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /criteria waits on the model outside the lock; it saves on top of the session as it is then."""
    from app.api import presearch
    from app.interview.rubric_builder import apply_edits

    _start(client)
    real = presearch.flow.make_criteria

    def slow(*args: Any, **kwargs: Any) -> Any:
        session = store.get(SID)
        assert session is not None and session.interview is not None
        edit = {"kind": "set_weight", "criterion_key": "weight", "weight": 9}
        from app.contracts import RubricEdit

        store.put(session.changed(interview=apply_edits(session.interview, [RubricEdit.model_validate(edit)])))
        return real(*args, **kwargs)

    monkeypatch.setattr(presearch.flow, "make_criteria", slow)
    assert client.post(f"/api/presearch/{SID}/criteria").status_code == 200
    assert _manual(client) == {"weight": 9.0}


def test_the_browser_can_read_the_note() -> None:
    app = create_app(env={})
    with TestClient(app) as test_client:
        response = test_client.get("/api/health", headers={"Origin": "http://localhost:3000"})
    exposed = [h.strip().lower() for h in response.headers.get("access-control-expose-headers", "").split(",")]
    assert "x-rubric-note" in exposed
