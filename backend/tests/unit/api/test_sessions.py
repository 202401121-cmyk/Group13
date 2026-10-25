"""In-memory pre-search sessions (US-05): ids, expiry after 2 hours, a 1,000-session cap, thread safety."""

from __future__ import annotations

import re
import threading

import pytest
from pydantic import ValidationError

from app.api.sessions import (
    MAX_SESSIONS,
    SESSION_ID_PATTERN,
    SESSION_TTL_S,
    InMemorySessionStore,
    PresearchSession,
    new_session_id,
)
from app.contracts import examples as ex


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def session(session_id: str | None = None) -> PresearchSession:
    return PresearchSession(session_id=session_id or new_session_id(), request="laptop", intent=ex.intent())


def test_ids_match_the_session_id_pattern_and_differ() -> None:
    ids = {new_session_id() for _ in range(200)}
    assert len(ids) == 200
    assert all(re.match(SESSION_ID_PATTERN, i) for i in ids)


def test_a_bad_id_is_refused() -> None:
    with pytest.raises(ValidationError):
        PresearchSession(session_id="session-1", request="laptop")


def test_put_get_delete() -> None:
    store = InMemorySessionStore()
    saved = session()
    store.put(saved)
    assert store.get(saved.session_id) == saved
    store.delete(saved.session_id)
    assert store.get(saved.session_id) is None
    store.delete(saved.session_id)  # deleting twice is fine


def test_sessions_expire_two_hours_after_last_use() -> None:
    clock = Clock()
    store = InMemorySessionStore(clock=clock)
    saved = session()
    store.put(saved)
    clock.now += SESSION_TTL_S - 1
    assert store.get(saved.session_id) == saved  # used again: the two hours start over
    clock.now += SESSION_TTL_S - 1
    assert store.get(saved.session_id) == saved
    clock.now += SESSION_TTL_S + 1
    assert store.get(saved.session_id) is None
    assert len(store) == 0


def test_expired_sessions_are_cleared_when_new_ones_arrive() -> None:
    clock = Clock()
    store = InMemorySessionStore(clock=clock)
    store.put(session())
    clock.now += SESSION_TTL_S + 1
    store.put(session())
    assert len(store) == 1


def test_at_most_max_sessions_and_the_longest_unused_goes_first() -> None:
    assert MAX_SESSIONS == 1_000
    clock = Clock()
    store = InMemorySessionStore(max_sessions=3, clock=clock)
    first, second, third, fourth = (session() for _ in range(4))
    for s in (first, second, third):
        clock.now += 1
        store.put(s)
    clock.now += 1
    store.get(first.session_id)  # first is used again, so second is now the longest unused
    store.put(fourth)
    assert len(store) == 3
    assert store.get(second.session_id) is None
    assert [store.get(s.session_id) for s in (first, third, fourth)] == [first, third, fourth]


def test_changed_returns_a_validated_copy() -> None:
    saved = session()
    updated = saved.changed(unclear_attempts=2)
    assert (saved.unclear_attempts, updated.unclear_attempts) == (0, 2)
    with pytest.raises(ValidationError):
        saved.changed(unclear_attempts=3)


def test_the_store_is_thread_safe() -> None:
    store = InMemorySessionStore(max_sessions=500)
    sessions = [session() for _ in range(800)]

    def work(chunk: list[PresearchSession]) -> None:
        for s in chunk:
            store.put(s)
            store.get(s.session_id)

    threads = [threading.Thread(target=work, args=(sessions[i::8],)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(store) == 500
