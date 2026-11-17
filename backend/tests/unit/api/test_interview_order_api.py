"""The interview routes on the smart order (US-04, S4-5): start, reply, back and the rubric route.

Every step keeps S4-4's shape with `ordering` set, the session keeps the beliefs the answers give, each smart question
says why it comes now, and a planner that breaks for one turn falls back to the fixed order through the API.
The laptop request gets the fallback criteria here (no real LLM): processor 9, memory 7, battery 7, display 6, ...
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.presearch import get_session_store
from app.api.sessions import InMemorySessionStore
from app.contracts import CriteriaSet, Criterion, InterviewState, InterviewStep, LLMTask, PresearchSession
from app.contracts import examples as ex
from app.interview import AdaptiveInterviewStage
from app.interview.beliefs import BeliefSettings, beliefs_for
from app.interview.parsing import RawReply
from app.main import create_app
from tests.fakes.llm import FakeLLMClient

LAPTOP = "best laptop for coding under 70k"
# D-69's own numbers: one rough answer settles a criterion, so the interview stops once the top 3 are answered.
STOPS_AT_TOP_3 = BeliefSettings(prior_sd=2.5, exact_sd=1.5, settled_sd=1.2)


@pytest.fixture
def store() -> InMemorySessionStore:
    return InMemorySessionStore()


@pytest.fixture
def client(store: InMemorySessionStore) -> Iterator[TestClient]:
    app = create_app(env={})
    app.dependency_overrides[get_session_store] = lambda: store
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def stops_early(monkeypatch: pytest.MonkeyPatch) -> None:
    """The routes' interview, with numbers that stop once the top 3 are settled."""
    monkeypatch.setattr("app.presearch.AdaptiveInterviewStage", lambda: AdaptiveInterviewStage(settings=STOPS_AT_TOP_3))


def post(client: TestClient, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    response = client.post(f"/api/presearch/{path}", json=body)
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()
    return data


def started(client: TestClient) -> tuple[str, dict[str, Any]]:
    session_id = post(client, "intake", {"text": LAPTOP})["session_id"]
    post(client, f"{session_id}/criteria")
    return session_id, post(client, f"{session_id}/interview/start")


def session(client: TestClient, session_id: str) -> PresearchSession:
    return PresearchSession.model_validate(client.get(f"/api/presearch/{session_id}").json())


def stored_state(client: TestClient, session_id: str) -> InterviewState:
    state = session(client, session_id).interview
    assert state is not None
    return state


def key_of(step: dict[str, Any]) -> str:
    key: str = step["question"]["criterion_key"]
    return key


def answer(client: TestClient, session_id: str, text: str) -> dict[str, Any]:
    return post(client, f"{session_id}/interview/reply", {"reply_text": text})


# --- every route keeps the shape, says the ordering and stores the beliefs ------------------------------------------


def test_every_route_returns_an_interview_step_with_the_smart_ordering_and_stores_its_beliefs(
    client: TestClient,
) -> None:
    session_id, first = started(client)
    steps = [first, answer(client, session_id, "High"), answer(client, session_id, "Medium")]
    steps.append(post(client, f"{session_id}/interview/back", {"question_id": first["question"]["id"]}))
    for step in steps:
        InterviewStep.model_validate(step)  # S4-4's shape, unchanged
        assert step["ordering"] == "information_gain"
    state = stored_state(client, session_id)
    assert state.beliefs == beliefs_for(state)
    assert session(client, session_id).interview_step == InterviewStep.model_validate(steps[-1])


def test_each_smart_question_says_in_plain_words_why_it_comes_now(client: TestClient) -> None:
    session_id, first = started(client)
    assert first["question"]["why_asking"].startswith("Processor speed: it usually matters a lot")
    nxt = answer(client, session_id, "High")
    why = nxt["question"]["why_asking"]
    assert why.startswith("RAM and storage: it usually matters a lot and we don't know your view yet")
    assert len(why) <= 200


# --- an early stop, and going back after it -------------------------------------------------------------------------


def test_an_interview_can_stop_before_10_questions(client: TestClient, stops_early: None) -> None:
    session_id, first = started(client)
    assert key_of(first) == "processor_speed"
    second = answer(client, session_id, "High")
    third = answer(client, session_id, "High")
    assert [key_of(second), key_of(third)] == ["memory_and_storage", "battery_life"]
    done = answer(client, session_id, "High")
    assert done["kind"] == "finished" and done["rubric"] is not None
    assert done["questions_asked"] == 3 and done["ordering"] == "information_gain"
    stored = session(client, session_id)
    assert stored.interview_step is not None and stored.interview_step.kind == "finished"
    assert stored.interview is not None and stored.interview.beliefs == beliefs_for(stored.interview, STOPS_AT_TOP_3)


def test_the_shipped_numbers_stop_early_when_the_top_3_are_settled(
    client: TestClient, store: InMemorySessionStore
) -> None:
    """With the tuned numbers (D-75) only a "good enough" answer settles a criterion, so the routes as shipped stop
    early when the three highest are all such answers: here every other criterion usually matters less."""
    weights = [("hinge_quality", 3.6), ("fan_noise", 3.5), ("port_choice", 3.4), ("speaker_volume", 1.0)]
    criteria = [
        Criterion(key=k, label=k.replace("_", " ").capitalize(), definition="How good it is for you.", default_weight=w)
        for k, w in weights
    ]
    criteria.append(
        Criterion(key="webcam_quality", label="Webcam quality", definition="How clear the camera is.", default_weight=1)
    )
    criteria.append(
        Criterion(
            key="value_for_money",
            label="Value for money",
            definition="How much you get for the price.",
            default_weight=1,
            is_value_for_money=True,
        )
    )
    sid = ex.SESSION_ID
    criteria_set = CriteriaSet(category="laptop", criteria=criteria, source="fallback")
    store.put(PresearchSession(session_id=sid, request=ex.QUERY, intent=ex.intent(), criteria=criteria_set))
    step = post(client, f"{sid}/interview/start")
    for _ in range(3):
        step = answer(client, sid, "Good enough")
    assert step["kind"] == "finished" and step["questions_asked"] == 3 and step["ordering"] == "information_gain"
    asked = [e.question.criterion_key for e in stored_state(client, sid).history]
    assert asked == ["hinge_quality", "fan_noise", "port_choice"]


def test_going_back_after_an_early_stop_re_plans_the_rest(client: TestClient, stops_early: None) -> None:
    session_id, first = started(client)
    for _ in range(3):
        answer(client, session_id, "High")
    back = post(client, f"{session_id}/interview/back", {"question_id": first["question"]["id"]})
    assert back["kind"] == "question" and key_of(back) == "processor_speed"
    reopened = stored_state(client, session_id)
    assert reopened.beliefs["processor_speed"].sd == STOPS_AT_TOP_3.prior_sd  # its answer is open again

    nxt = answer(client, session_id, "Low")  # processor speed is now low: display quality enters the top 3
    assert nxt["kind"] == "question" and key_of(nxt) == "display_quality"
    done = answer(client, session_id, "High")
    assert done["kind"] == "finished" and done["questions_asked"] == 4
    weights = {w["key"]: w["weight"] for w in done["rubric"]["weights"]}
    assert weights["processor_speed"] == 2.0
    state = stored_state(client, session_id)
    assert state.beliefs == beliefs_for(state, STOPS_AT_TOP_3)
    assert state.beliefs["processor_speed"].mean < 4


def test_a_correction_of_an_earlier_answer_re_plans_the_rest(
    client: TestClient, stops_early: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id, _ = started(client)
    answer(client, session_id, "High")
    third = answer(client, session_id, "High")  # battery life pending
    correction = RawReply(reply_type="correction", corrects="processor_speed", level="low")
    monkeypatch.setattr("app.presearch.default_llm", lambda: FakeLLMClient({LLMTask.CLASSIFY_REPLY: correction}))
    corrected = answer(client, session_id, "actually make processor speed low")
    assert corrected["kind"] == "question" and key_of(corrected) == key_of(third)  # the current question again
    assert corrected["message"].startswith("Done: I changed Processor speed to 2 out of 10.")
    assert corrected["ordering"] == "information_gain"
    state = stored_state(client, session_id)
    assert state.beliefs == beliefs_for(state, STOPS_AT_TOP_3)
    # Without the correction, a third "High" would finish here; now processor speed is low, so one more is asked.
    nxt = answer(client, session_id, "High")
    assert nxt["kind"] == "question" and key_of(nxt) == "display_quality"


# --- the shopper's rubric edits -------------------------------------------------------------------------------------


def edit_weight(client: TestClient, session_id: str, key: str, weight: float) -> dict[str, Any]:
    body = {"edits": [{"kind": "set_weight", "criterion_key": key, "weight": weight}]}
    return post(client, f"{session_id}/rubric", body)


def test_an_edit_mid_interview_never_moves_a_belief_and_survives_the_later_answers(client: TestClient) -> None:
    session_id, _ = started(client)
    answer(client, session_id, "High")
    before = stored_state(client, session_id).beliefs
    edit_weight(client, session_id, "weight", 9)
    after = stored_state(client, session_id)
    assert after.beliefs == before
    assert session(client, session_id).interview_step is not None
    assert session(client, session_id).interview_step.ordering == "information_gain"  # type: ignore[union-attr]
    step: dict[str, Any] = {}
    for _ in range(12):
        step = answer(client, session_id, "Medium")
        if step["kind"] == "finished":
            break
    assert step["kind"] == "finished"
    weights = {w["key"]: (w["weight"], w["origin"]) for w in step["rubric"]["weights"]}
    assert weights["weight"] == (9.0, "manual")
    final = stored_state(client, session_id)
    assert final.beliefs == beliefs_for(final)  # the edit is not an answer


def test_an_edit_after_an_early_stop_updates_the_stored_rubric_and_keeps_the_order(
    client: TestClient, stops_early: None
) -> None:
    session_id, _ = started(client)
    for _ in range(3):
        answer(client, session_id, "High")
    edited = edit_weight(client, session_id, "processor_speed", 1)
    weights = {w["key"]: w["weight"] for w in edited["rubric"]["weights"]}
    assert weights["processor_speed"] == 1.0
    stored = session(client, session_id)
    assert stored.interview_step is not None and stored.interview_step.rubric is not None
    assert stored.interview_step.ordering == "information_gain"
    assert {w.key: w.weight for w in stored.interview_step.rubric.weights}["processor_speed"] == 1.0
    assert stored.interview is not None and stored.interview.beliefs == beliefs_for(stored.interview, STOPS_AT_TOP_3)


def test_starting_again_keeps_the_edits_with_a_fresh_interview_and_its_beliefs(client: TestClient) -> None:
    session_id, _ = started(client)
    answer(client, session_id, "High")
    edit_weight(client, session_id, "weight", 9)
    again = post(client, f"{session_id}/interview/start")
    assert again["ordering"] == "information_gain" and again["questions_asked"] == 1
    state = stored_state(client, session_id)
    assert state.manual_weights == {"weight": 9.0}
    assert state.beliefs == beliefs_for(state)
    assert all(b.sd == BeliefSettings().prior_sd for b in state.beliefs.values())  # no answers yet


# --- the fixed order as the fallback, through the API (criterion 5) -------------------------------------------------


def test_a_planner_that_breaks_for_one_reply_falls_back_to_the_fixed_order_through_the_api(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id, _ = started(client)

    def explode(*_: object, **__: object) -> object:
        raise RuntimeError("broken")

    with monkeypatch.context() as patch:
        patch.setattr("app.interview.adaptive.next_key", explode)
        fell_back = answer(client, session_id, "High")
    assert fell_back["kind"] == "question" and key_of(fell_back) == "memory_and_storage"  # the fixed order's next
    assert (fell_back["ordering"], fell_back["state"]["beliefs"]) == ("fixed_order", {})
    assert stored_state(client, session_id).beliefs == {}
    smart = answer(client, session_id, "Medium")
    assert smart["ordering"] == "information_gain"
    state = stored_state(client, session_id)
    assert state.beliefs == beliefs_for(state)


def test_a_turn_that_fails_outright_gets_the_runners_fixed_fallback(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id, _ = started(client)

    def explode(*_: object, **__: object) -> object:
        raise RuntimeError("plan broken")

    monkeypatch.setattr("app.interview.stage.plan_questions", explode)
    monkeypatch.setattr("app.interview.adaptive.plan_questions", explode)
    step = InterviewStep.model_validate(answer(client, session_id, "High"))
    assert step.kind == "question" and step.ordering == "fixed_order" and step.state.beliefs == {}
    assert step.question is not None and step.question.criterion_key == "memory_and_storage"
    criteria = session(client, session_id).criteria
    memory = criteria.get("memory_and_storage") if criteria is not None else None
    assert memory is not None
    assert step.question.why_asking == memory.definition[:200].rstrip()  # the fixed order keeps the definition


def test_a_repeated_reply_returns_the_stored_step_with_its_ordering(client: TestClient) -> None:
    session_id, first = started(client)
    body = {"reply_text": "High", "question_id": first["question"]["id"]}
    once = post(client, f"{session_id}/interview/reply", body)
    twice = post(client, f"{session_id}/interview/reply", body)
    assert twice == once and twice["ordering"] == "information_gain"
