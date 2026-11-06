"""Saving a finished search without a database (US-52): False, never an exception, and one clean log line."""

from __future__ import annotations

import json
import logging
import math
import socket
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest

from app.contracts import Rubric, SearchRequest, SearchResult
from app.contracts import examples as ex
from app.db import (
    DatabaseSettings,
    InMemoryRequestStore,
    JsonObject,
    NullRequestStore,
    PostgresRequestStore,
    RequestStore,
    search_row,
)
from app.pipeline import PipelineRunner, default_search_stages

SESSION = "sess_abc123def456"
FAKE_PASSWORD = "SuperSecret123"  # made up
PRIVATE_TEXT = "my private shopping request"
TIMEOUT_S = 0.5


def rubric_json() -> JsonObject:
    return ex.rubric().model_dump(mode="json")


def result_json() -> JsonObject:
    return ex.search_result().model_dump(mode="json")


class StoreBackedResults:
    """The shape the pipeline's ResultStore needs: everything save_search takes is already on the SearchResult."""

    def __init__(self, store: RequestStore) -> None:
        self.store = store

    def save(self, result: SearchResult) -> bool:
        request = result.request
        return self.store.save_search(
            request.session_id, request.rubric.model_dump(mode="json"), result.model_dump(mode="json")
        )


def closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
    return port


@pytest.fixture
def store() -> Iterator[PostgresRequestStore]:
    url = "postgresql://" + "shopper:" + FAKE_PASSWORD + f"@127.0.0.1:{closed_port()}/shopsense"
    built = PostgresRequestStore(DatabaseSettings(url=url, timeout_s=TIMEOUT_S))
    yield built
    built.close()


def database_lines(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.name.startswith("app.db")]


# --- the row -------------------------------------------------------------------------------------


def test_the_stored_json_gives_back_the_same_contracts() -> None:
    result = ex.search_result()
    row = search_row(result.request.session_id, rubric_json(), result_json())
    assert SearchResult.model_validate(json.loads(row.result)) == result
    assert Rubric.model_validate(json.loads(row.rubric)) == ex.rubric()


@pytest.mark.parametrize(
    "session_id",
    ["not-a-session", "sess_x", "sess_abcdef\n", "sess_" + "a" * 41, ""],
)
def test_a_bad_session_id_is_refused(session_id: str) -> None:
    with pytest.raises(ValueError, match="session_id"):
        search_row(session_id, rubric_json(), result_json())


def test_a_plain_model_dump_is_refused_because_it_is_not_json() -> None:
    with pytest.raises(TypeError):
        search_row(SESSION, rubric_json(), ex.search_result().model_dump())


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_a_number_json_cannot_hold_is_refused(value: float) -> None:
    with pytest.raises(ValueError):
        search_row(SESSION, rubric_json(), {"value": value})


@pytest.mark.parametrize("text", ["a\x00b", "\\\x00", "\\\\\x00"])
def test_the_nul_character_is_refused_because_jsonb_cannot_hold_it(text: str) -> None:
    with pytest.raises(ValueError, match="NUL"):
        search_row(SESSION, {"quote": text}, result_json())
    with pytest.raises(ValueError, match="NUL"):
        search_row(SESSION, rubric_json(), {"nested": [{"quote": text}]})


@pytest.mark.parametrize("text", ["\\u0000", "\\\\u0000", "C:\\u0000\\file"])
def test_text_that_only_looks_like_a_nul_escape_is_kept(text: str) -> None:
    row = search_row(SESSION, rubric_json(), {"quote": text})
    assert json.loads(row.result) == {"quote": text}


# --- the stores ----------------------------------------------------------------------------------


def test_with_the_database_off_a_search_is_not_saved_and_one_line_says_so(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO):
        assert NullRequestStore().save_search(SESSION, rubric_json(), result_json()) is False
    [line] = database_lines(caplog)
    assert line.getMessage() == "search not saved: the database is off"


def test_the_in_memory_store_keeps_a_good_search_and_refuses_a_bad_one() -> None:
    store = InMemoryRequestStore()
    assert store.save_search(SESSION, rubric_json(), result_json()) is True
    assert store.save_search("not-a-session", rubric_json(), result_json()) is False
    assert store.save_search(SESSION, rubric_json(), ex.search_result().model_dump()) is False
    assert [row.session_id for row in store.searches] == [SESSION]


def test_a_store_that_is_down_saves_no_search() -> None:
    store = InMemoryRequestStore(fail=True)
    assert store.save_search(SESSION, rubric_json(), result_json()) is False
    assert store.searches == []


def test_an_unreachable_database_answers_false_within_the_time_limit(
    store: PostgresRequestStore, caplog: pytest.LogCaptureFixture
) -> None:
    start = time.monotonic()
    with caplog.at_level(logging.DEBUG):
        assert store.save_search(SESSION, rubric_json(), result_json()) is False
    assert time.monotonic() - start < TIMEOUT_S + 0.5
    [line] = database_lines(caplog)
    assert "search save failed (host=127.0.0.1)" in line.getMessage()


def test_three_failures_open_the_breaker_and_the_next_save_is_skipped_at_once(
    store: PostgresRequestStore, caplog: pytest.LogCaptureFixture
) -> None:
    for _ in range(3):
        assert store.save_search(SESSION, rubric_json(), result_json()) is False
    assert store.breaker.state == "open"
    caplog.clear()
    start = time.monotonic()
    with caplog.at_level(logging.DEBUG):
        assert store.save_search(SESSION, rubric_json(), result_json()) is False
    assert time.monotonic() - start < 0.1  # skipped, not tried
    [line] = database_lines(caplog)
    assert "circuit breaker is open" in line.getMessage()


def test_failed_search_saves_and_failed_request_saves_share_one_breaker(store: PostgresRequestStore) -> None:
    assert store.save_request(SESSION, ex.QUERY, None) == "failed"
    assert store.save_search(SESSION, rubric_json(), result_json()) is False
    assert store.save_request(SESSION, ex.QUERY, None) == "failed"
    assert store.breaker.state == "open"
    assert store.save_search(SESSION, rubric_json(), result_json()) is False


def test_a_request_save_stays_silent_when_the_breaker_is_open(
    store: PostgresRequestStore, caplog: pytest.LogCaptureFixture
) -> None:
    for _ in range(3):
        store.breaker.record_failure()
    with caplog.at_level(logging.DEBUG):
        assert store.save_request(SESSION, ex.QUERY, None) == "failed"
        assert store.health() == "down"
    assert database_lines(caplog) == []  # the health check runs often: only search saves log a skip


@pytest.mark.parametrize(
    ("session_id", "rubric", "result"),
    [
        ("not-a-session", {}, {}),
        (SESSION, {"bad": math.nan}, {}),
        (SESSION, {}, {"bad": "a\x00b"}),
        (SESSION, {}, {"bad": datetime(2026, 11, 6, tzinfo=UTC)}),
        (None, {}, {}),
    ],
)
def test_a_search_that_cannot_fit_never_reaches_the_database_or_the_breaker(
    store: PostgresRequestStore, caplog: pytest.LogCaptureFixture, session_id: Any, rubric: Any, result: Any
) -> None:
    with caplog.at_level(logging.DEBUG):
        assert store.save_search(session_id, rubric, result) is False
    assert store.breaker.state == "closed"  # one bad result must not switch the database off for everyone
    assert store._pool is None
    [line] = database_lines(caplog)
    assert "does not fit the searches table" in line.getMessage()


def test_a_search_with_a_loop_in_it_fails_without_raising(store: PostgresRequestStore) -> None:
    looped: JsonObject = {}
    looped["self"] = looped
    assert store.save_search(SESSION, {}, looped) is False


def test_a_closed_store_answers_false_with_one_line(
    store: PostgresRequestStore, caplog: pytest.LogCaptureFixture
) -> None:
    store.close()
    with caplog.at_level(logging.DEBUG):
        assert store.save_search(SESSION, rubric_json(), result_json()) is False
    [line] = database_lines(caplog)
    assert "the store is closed" in line.getMessage()


def test_logs_never_show_the_url_the_password_or_what_the_shopper_typed(
    store: PostgresRequestStore, caplog: pytest.LogCaptureFixture
) -> None:
    private = {"raw_query": PRIVATE_TEXT}
    with caplog.at_level(logging.DEBUG):
        for _ in range(4):
            store.save_search(SESSION, private, private)
        store.save_search("not-a-session", private, private)
        store.save_search(SESSION, private, {"bad": math.nan, **private})
    assert "skipping the database for 30 s" in caplog.text
    for secret in (FAKE_PASSWORD, "postgresql://", "shopper:", PRIVATE_TEXT, SESSION):
        assert secret not in caplog.text


# --- behind the pipeline's ResultStore -----------------------------------------------------------


def run_search(store: RequestStore) -> SearchResult:
    request = SearchRequest(session_id=ex.SESSION_ID, raw_query=ex.QUERY, rubric=ex.rubric())
    return PipelineRunner(default_search_stages(), StoreBackedResults(store)).run(request)


def test_a_real_pipeline_result_fits_the_save_and_comes_back_equal() -> None:
    store = InMemoryRequestStore()
    result = run_search(store)
    assert result.saved is True
    [row] = store.searches
    assert row.session_id == ex.SESSION_ID
    assert SearchResult.model_validate(json.loads(row.result)) == result


@pytest.mark.parametrize("store", [NullRequestStore(), InMemoryRequestStore(fail=True)])
def test_a_save_that_does_not_happen_reaches_the_shopper_as_saved_false(store: RequestStore) -> None:
    assert run_search(store).saved is False
