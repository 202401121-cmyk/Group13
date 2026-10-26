"""The request store interface (US-01): "off" without a database, "saved" or "failed" with one."""

from __future__ import annotations

import pytest

from app.contracts import examples as ex
from app.db import InMemoryRequestStore, NullRequestStore, request_row

SESSION = "sess_abc123def456"


def test_with_no_database_every_save_is_off() -> None:
    store = NullRequestStore()
    assert store.save_request(SESSION, "best laptop for coding under 70k", ex.intent()) == "off"
    assert store.health() == "off"


def test_a_working_store_saves_one_row_per_request() -> None:
    store = InMemoryRequestStore()
    assert store.save_request(SESSION, ex.QUERY, ex.intent()) == "saved"
    assert store.health() == "ok"
    [row] = store.rows
    assert (row.session_id, row.request_text) == (SESSION, ex.QUERY)
    assert row.intent == ex.intent()
    assert row.intent_source == ex.intent().detected_by


def test_a_store_that_is_down_fails_without_raising() -> None:
    store = InMemoryRequestStore(fail=True)
    assert store.save_request(SESSION, ex.QUERY, ex.intent()) == "failed"
    assert store.health() == "down"
    assert store.rows == []


def test_the_intent_is_kept_only_when_it_is_ready_for_research() -> None:
    unclear = ex.unclear_intent()
    assert not unclear.ready_for_research
    row = request_row(SESSION, unclear.raw_query, unclear)
    assert row.intent is None
    assert row.intent_source == unclear.detected_by  # how it was read is still known
    assert request_row(SESSION, "anything", None).intent_source is None


@pytest.mark.parametrize(
    ("session_id", "text"),
    [("not-a-session", "ok"), ("sess_x", "ok"), (SESSION, ""), (SESSION, "x" * 1001)],
)
def test_bad_rows_are_refused_and_never_saved(session_id: str, text: str) -> None:
    with pytest.raises(ValueError):
        request_row(session_id, text, None)
    store = InMemoryRequestStore()
    assert store.save_request(session_id, text, None) == "failed"
    assert store.rows == []


def test_the_longest_allowed_request_is_saved() -> None:
    store = InMemoryRequestStore()
    assert store.save_request(SESSION, "x" * 1000, None) == "saved"
