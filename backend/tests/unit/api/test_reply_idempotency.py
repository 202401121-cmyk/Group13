"""A repeated interview reply is safe (US-05, known gap 1): the rules for question_id, and two replies at once.

A network retry or a double click sends the same reply twice. Without question_id the second one answers the next
question. With it: the current question takes the reply (a); a repeat of a settled answer changes nothing (b); a
different answer, or a question the session never asked, gets a 409 (c); leaving it out works as it always did (d).
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app import presearch as flow
from app.api.presearch import FINISHED, NO_INTERVIEW, NOT_CURRENT, get_session_store
from app.api.sessions import InMemorySessionStore
from app.contracts import InterviewStep, InterviewTurn
from app.main import create_app

LAPTOP = "best laptop for coding under 70k"


@pytest.fixture
def store() -> InMemorySessionStore:
    return InMemorySessionStore()


@pytest.fixture
def client(store: InMemorySessionStore) -> Iterator[TestClient]:
    app = create_app(env={})
    app.dependency_overrides[get_session_store] = lambda: store
    with TestClient(app) as test_client:
        yield test_client


def start(client: TestClient) -> tuple[str, dict[str, Any]]:
    session_id = client.post("/api/presearch/intake", json={"text": LAPTOP}).json()["session_id"]
    client.post(f"/api/presearch/{session_id}/criteria")
    step: dict[str, Any] = client.post(f"/api/presearch/{session_id}/interview/start").json()
    return session_id, step


def send(client: TestClient, session_id: str, text: str, question_id: str | None = None) -> httpx.Response:
    body = {"reply_text": text} | ({"question_id": question_id} if question_id else {})
    return client.post(f"/api/presearch/{session_id}/interview/reply", json=body)


def answer(client: TestClient, session_id: str, step: dict[str, Any], text: str) -> dict[str, Any]:
    """Answer the question in `step`, naming it, and return the next step."""
    response = send(client, session_id, text, step["question"]["id"])
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()
    return data


def session_of(client: TestClient, session_id: str) -> dict[str, Any]:
    data: dict[str, Any] = client.get(f"/api/presearch/{session_id}").json()
    return data


def statuses(session: dict[str, Any]) -> list[str]:
    return [entry["status"] for entry in session["interview"]["history"]]


def finish(client: TestClient, session_id: str, step: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Answer every question. Returns the last question's step and the finished step."""
    last = step
    while step["kind"] != "finished":
        last, step = step, answer(client, session_id, step, "Medium")
    return last, step


# --- rule a: question_id is the current question -----------------------------------------------------


def test_a_reply_naming_the_current_question_is_applied(client: TestClient) -> None:
    session_id, first = start(client)
    second = answer(client, session_id, first, "High")
    assert second["kind"] == "question"
    assert second["question"]["id"] != first["question"]["id"]
    history = session_of(client, session_id)["interview"]["history"]
    assert (history[0]["status"], history[0]["reply_text"]) == ("answered", "High")
    assert history[0]["classification"]["weight"] == 8.0


def test_an_unclear_reply_naming_the_current_question_gets_a_rephrase(client: TestClient) -> None:
    session_id, first = start(client)
    step = answer(client, session_id, first, "whatever")
    assert step["kind"] == "rephrase"
    assert step["question"]["id"] == first["question"]["id"]


def test_going_back_makes_that_question_current_again(client: TestClient) -> None:
    session_id, first = start(client)
    answer(client, session_id, first, "High")
    client.post(f"/api/presearch/{session_id}/interview/back", json={"question_id": first["question"]["id"]})
    answer(client, session_id, first, "Low")
    corrected = session_of(client, session_id)["interview"]["history"][0]
    assert (corrected["status"], corrected["reply_text"]) == ("answered", "Low")
    assert corrected["classification"]["reply_type"] == "correction"


# --- rule b: a question already answered, the same answer --------------------------------------------


def test_the_same_reply_twice_answers_one_question(client: TestClient) -> None:
    session_id, first = start(client)
    second = answer(client, session_id, first, "High")
    before = session_of(client, session_id)
    retry = send(client, session_id, "High", first["question"]["id"])
    assert retry.status_code == 200
    assert retry.json() == second  # the step the shopper is on now
    assert session_of(client, session_id) == before
    assert statuses(before) == ["answered", "pending"]


@pytest.mark.parametrize("same", ["high", " HIGH ", "High.", "high!"])
def test_the_same_answer_typed_a_little_differently_is_still_a_repeat(client: TestClient, same: str) -> None:
    session_id, first = start(client)
    second = answer(client, session_id, first, "High")
    retry = send(client, session_id, same, first["question"]["id"])
    assert (retry.status_code, retry.json()) == (200, second)


def test_a_repeated_skip_changes_nothing(client: TestClient) -> None:
    session_id, first = start(client)
    second = answer(client, session_id, first, "Skip")
    retry = send(client, session_id, "Skip", first["question"]["id"])
    assert (retry.status_code, retry.json()) == (200, second)
    assert statuses(session_of(client, session_id)) == ["skipped", "pending"]


def test_a_retry_of_the_last_reply_returns_the_finished_step(client: TestClient) -> None:
    session_id, first = start(client)
    last, finished = finish(client, session_id, first)
    assert finished["kind"] == "finished"
    retry = send(client, session_id, "Medium", last["question"]["id"])
    assert (retry.status_code, retry.json()) == (200, finished)


def test_a_late_retry_of_an_earlier_answer_changes_nothing(client: TestClient) -> None:
    session_id, first = start(client)
    third = answer(client, session_id, answer(client, session_id, first, "High"), "Low")
    retry = send(client, session_id, "High", first["question"]["id"])
    assert (retry.status_code, retry.json()) == (200, third)


def test_a_retry_while_an_earlier_question_is_reopened_changes_nothing(client: TestClient) -> None:
    session_id, first = start(client)
    second = answer(client, session_id, first, "High")
    answer(client, session_id, second, "Low")
    back = client.post(f"/api/presearch/{session_id}/interview/back", json={"question_id": first["question"]["id"]})
    retry = send(client, session_id, "Low", second["question"]["id"])
    assert (retry.status_code, retry.json()) == (200, back.json())


def test_after_a_correction_the_new_answer_is_the_one_that_repeats(client: TestClient) -> None:
    session_id, first = start(client)
    question_id = first["question"]["id"]
    answer(client, session_id, first, "High")
    client.post(f"/api/presearch/{session_id}/interview/back", json={"question_id": question_id})
    after = answer(client, session_id, first, "Low")
    assert send(client, session_id, "Low", question_id).json() == after
    assert send(client, session_id, "High", question_id).status_code == 409


# --- rule c: a different answer, or a question this session never asked ------------------------------


def assert_refused(response: httpx.Response) -> None:
    assert (response.status_code, response.json()) == (409, {"detail": NOT_CURRENT})


def test_a_different_answer_to_a_settled_question_is_refused(client: TestClient) -> None:
    session_id, first = start(client)
    answer(client, session_id, first, "High")
    before = session_of(client, session_id)
    assert_refused(send(client, session_id, "Low", first["question"]["id"]))
    assert session_of(client, session_id) == before


@pytest.mark.parametrize("question_id", ["q_not_in_this_session", "not an id", "   ", "x" * 64])
def test_a_question_this_session_does_not_have_is_refused(client: TestClient, question_id: str) -> None:
    session_id, _ = start(client)
    before = session_of(client, session_id)
    assert_refused(send(client, session_id, "High", question_id))
    assert session_of(client, session_id) == before


def test_a_question_not_asked_yet_is_refused(client: TestClient) -> None:
    session_id, first = start(client)
    criteria = session_of(client, session_id)["criteria"]["criteria"]
    later = next(c["key"] for c in criteria if c["key"] != first["question"]["criterion_key"])
    assert_refused(send(client, session_id, "High", f"q_{later}"))
    assert statuses(session_of(client, session_id)) == ["pending"]


def test_a_different_answer_after_the_end_is_told_to_go_back(client: TestClient) -> None:
    session_id, first = start(client)
    last, _ = finish(client, session_id, first)
    assert_refused(send(client, session_id, "High", last["question"]["id"]))


def test_the_message_points_to_the_change_an_answer_step_and_leaks_nothing(client: TestClient) -> None:
    assert "go back" in NOT_CURRENT.lower()
    session_id, _ = start(client)
    body = send(client, session_id, "High", "q_private_words").json()
    assert list(body) == ["detail"]
    assert "q_private_words" not in body["detail"]


def test_going_back_still_changes_an_answer_after_a_refusal(client: TestClient) -> None:
    session_id, first = start(client)
    question_id = first["question"]["id"]
    answer(client, session_id, first, "High")
    assert_refused(send(client, session_id, "Low", question_id))
    client.post(f"/api/presearch/{session_id}/interview/back", json={"question_id": question_id})
    answer(client, session_id, first, "Low")
    assert session_of(client, session_id)["interview"]["history"][0]["reply_text"] == "Low"


# --- rule d: no question_id --------------------------------------------------------------------------


def test_with_no_question_id_a_second_reply_answers_the_next_question(client: TestClient) -> None:
    session_id, _ = start(client)
    assert send(client, session_id, "High").status_code == 200
    assert send(client, session_id, "High").status_code == 200
    assert statuses(session_of(client, session_id)) == ["answered", "answered", "pending"]


def test_a_null_question_id_is_the_same_as_none(client: TestClient) -> None:
    session_id, _ = start(client)
    path = f"/api/presearch/{session_id}/interview/reply"
    assert client.post(path, json={"reply_text": "High", "question_id": None}).status_code == 200
    assert statuses(session_of(client, session_id)) == ["answered", "pending"]


def test_with_no_question_id_a_reply_after_the_end_is_refused_as_before(client: TestClient) -> None:
    session_id, first = start(client)
    finish(client, session_id, first)
    response = send(client, session_id, "High")
    assert (response.status_code, response.json()) == (409, {"detail": FINISHED})


def test_a_reply_before_the_interview_starts_is_refused_with_or_without_an_id(client: TestClient) -> None:
    session_id = client.post("/api/presearch/intake", json={"text": LAPTOP}).json()["session_id"]
    for question_id in (None, "q_battery_life"):
        response = send(client, session_id, "High", question_id)
        assert (response.status_code, response.json()) == (409, {"detail": NO_INTERVIEW})


# --- the body itself ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"reply_text": "High", "question_id": ""}, "question_id"),
        ({"reply_text": "High", "question_id": "q" * 65}, "question_id"),
        ({"reply_text": "High", "question_id": 7}, "question_id"),
        ({"reply_text": ""}, "reply_text"),
        ({"reply_text": "x" * 501}, "reply_text"),
        ({"reply_text": "High", "extra": 1}, "extra"),
    ],
)
def test_a_bad_body_gets_a_plain_422(client: TestClient, body: dict[str, Any], field: str) -> None:
    session_id, _ = start(client)
    response = client.post(f"/api/presearch/{session_id}/interview/reply", json=body)
    assert response.status_code == 422
    assert list(response.json()) == ["detail"]
    assert field in response.json()["detail"]


# --- two replies at the same moment ------------------------------------------------------------------


class OverlappingTurns:
    """Holds each turn until a second one arrives, or a moment has passed, so unlocked requests must overlap."""

    def __init__(self) -> None:
        self.barrier = threading.Barrier(2)
        self.started = threading.Event()
        self.turns = 0
        self.real = flow.interview_turn

    def __call__(self, turn: InterviewTurn) -> InterviewStep:
        self.turns += 1
        self.started.set()
        with contextlib.suppress(threading.BrokenBarrierError):  # alone in the turn: the lock keeps the other out
            self.barrier.wait(timeout=0.3)
        return self.real(turn)


def send_both(client: TestClient, session_id: str, replies: list[str], question_id: str) -> list[httpx.Response]:
    with ThreadPoolExecutor(max_workers=2) as pool:
        return list(pool.map(lambda text: send(client, session_id, text, question_id), replies))


def test_two_identical_replies_at_the_same_moment_apply_once(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id, first = start(client)
    turns = OverlappingTurns()
    monkeypatch.setattr(flow, "interview_turn", turns)
    responses = send_both(client, session_id, ["High", "High"], first["question"]["id"])
    assert [r.status_code for r in responses] == [200, 200]
    assert responses[0].json() == responses[1].json()
    assert turns.turns == 1  # the reply was applied once; the other found it already done
    session = session_of(client, session_id)
    assert statuses(session) == ["answered", "pending"]
    assert session["interview"]["history"][0]["reply_text"] == "High"


def test_two_different_replies_at_the_same_moment_one_wins_and_one_is_refused(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id, first = start(client)
    turns = OverlappingTurns()
    monkeypatch.setattr(flow, "interview_turn", turns)
    responses = send_both(client, session_id, ["High", "Low"], first["question"]["id"])
    assert sorted(r.status_code for r in responses) == [200, 409]
    assert turns.turns == 1
    winner = "High" if responses[0].status_code == 200 else "Low"
    session = session_of(client, session_id)
    assert statuses(session) == ["answered", "pending"]
    assert session["interview"]["history"][0]["reply_text"] == winner


def test_two_replies_with_no_id_at_the_same_moment_lose_neither(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id, _ = start(client)
    monkeypatch.setattr(flow, "interview_turn", OverlappingTurns())
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda text: send(client, session_id, text), ["High", "Low"]))
    assert [r.status_code for r in responses] == [200, 200]
    assert statuses(session_of(client, session_id)) == ["answered", "answered", "pending"]


def test_a_reply_and_a_go_back_at_the_same_moment_do_not_overwrite_each_other(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id, first = start(client)
    second = answer(client, session_id, first, "High")
    turns = OverlappingTurns()
    monkeypatch.setattr(flow, "interview_turn", turns)
    question_id = first["question"]["id"]
    with ThreadPoolExecutor(max_workers=1) as pool:
        reply = pool.submit(send, client, session_id, "Low", second["question"]["id"])
        assert turns.started.wait(timeout=5)  # the reply is in the middle of its turn
        back = client.post(f"/api/presearch/{session_id}/interview/back", json={"question_id": question_id})
        assert (reply.result().status_code, back.status_code) == (200, 200)
    history = session_of(client, session_id)["interview"]["history"]
    assert [e["question"]["id"] for e in history if e["status"] == "pending"] == [question_id]  # the back was kept
    assert history[1]["reply_text"] == "Low"  # and so was the reply


# --- the lock ----------------------------------------------------------------------------------------


def test_a_session_lock_lets_one_request_in_at_a_time(store: InMemorySessionStore) -> None:
    order: list[str] = []
    second_is_waiting = threading.Event()

    def second() -> None:
        second_is_waiting.set()
        with store.lock("sess_abcdef"):
            order.append("second")

    with store.lock("sess_abcdef"):
        thread = threading.Thread(target=second)
        thread.start()
        assert second_is_waiting.wait(timeout=2)
        thread.join(timeout=0.05)
        assert thread.is_alive()  # still waiting for the first
        order.append("first")
    thread.join(timeout=2)
    assert order == ["first", "second"]


def test_different_sessions_never_wait_for_each_other(store: InMemorySessionStore) -> None:
    entered = threading.Event()

    def other() -> None:
        with store.lock("sess_other1"):
            entered.set()

    with store.lock("sess_abcdef"):
        thread = threading.Thread(target=other)
        thread.start()
        assert entered.wait(timeout=2)
    thread.join(timeout=2)


def test_a_lock_is_released_after_an_error_and_never_left_behind(store: InMemorySessionStore) -> None:
    with pytest.raises(ValueError, match="boom"), store.lock("sess_abcdef"):
        raise ValueError("boom")
    with store.lock("sess_abcdef"):
        pass
    assert store.locks_in_use() == 0


def test_locks_do_not_pile_up_for_ids_sent_by_clients(client: TestClient, store: InMemorySessionStore) -> None:
    for number in range(20):
        response = send(client, f"sess_unknown{number}", "High")
        assert response.status_code == 404
    assert store.locks_in_use() == 0
