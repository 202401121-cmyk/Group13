"""A finished search's save has its own time limit (US-52, P-38): DB_SAVE_TIMEOUT_SECONDS, 15 s by default, used only
by save_search. Every other call keeps DB_TIMEOUT_SECONDS. A save that times out counts once against the breaker; an
open or half-open breaker answers False at once (a save never takes the one trial call); a save that only waited for a
worker is not counted. Saves have their own workers and their own pool. No database: the slow work is faked on the
store's own worker threads."""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest

from app.contracts import examples as ex
from app.db import CircuitBreaker, DatabaseSettings, PostgresRequestStore
from app.db.postgres import COMMIT_MARGIN_S, commit_margin

SESSION = "sess_abc123def456"


def _url() -> str:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    return "postgresql://" + "shopper:" + "made-up" + f"@127.0.0.1:{port}/shopsense"


@contextmanager
def _store(
    timeout_s: float, save_timeout_s: float, breaker: CircuitBreaker | None = None
) -> Iterator[PostgresRequestStore]:
    built = PostgresRequestStore(
        DatabaseSettings(url=_url(), timeout_s=timeout_s, save_timeout_s=save_timeout_s), breaker
    )
    try:
        yield built
    finally:
        built.close()


def _slow(seconds: float, done: list[str] | None = None, name: str = "") -> Any:
    def work(*_: Any, **__: Any) -> None:
        time.sleep(seconds)
        if done is not None:
            done.append(name)

    return work


def _save(store: PostgresRequestStore, within_s: float | None = None) -> bool:
    rubric, result = ex.rubric().model_dump(mode="json"), ex.search_result().model_dump(mode="json")
    return store.save_search(SESSION, rubric, result, within_s=within_s)


def test_a_search_save_waits_for_its_own_limit_not_the_other_calls() -> None:
    with _store(timeout_s=0.3, save_timeout_s=2.0) as store:
        store._insert_search = _slow(0.8)  # type: ignore[method-assign]  # longer than 0.3, shorter than 2
        started = time.monotonic()
        assert _save(store) is True
        assert 0.7 < time.monotonic() - started < 1.5
        assert store.breaker.state == "closed"


def test_a_request_save_still_gives_up_at_its_own_limit() -> None:
    with _store(timeout_s=0.3, save_timeout_s=15.0) as store:
        store._insert = _slow(1.0)  # type: ignore[method-assign]
        started = time.monotonic()
        assert store.save_request(SESSION, ex.QUERY, None) == "failed"
        assert time.monotonic() - started < 0.8


@pytest.mark.slow
def test_a_request_save_still_gives_up_at_2_seconds_with_the_defaults() -> None:
    settings = DatabaseSettings(url=_url())
    assert (settings.timeout_s, settings.save_timeout_s) == (2.0, 15.0)
    store = PostgresRequestStore(settings)
    try:
        store._insert = _slow(4.0)  # type: ignore[method-assign]
        started = time.monotonic()
        assert store.save_request(SESSION, ex.QUERY, None) == "failed"
        assert 1.9 < time.monotonic() - started < 2.8
    finally:
        store.close()


def test_a_save_that_times_out_counts_once_against_the_breaker() -> None:
    breaker = CircuitBreaker(max_failures=2)
    with _store(timeout_s=0.3, save_timeout_s=0.4, breaker=breaker) as store:
        finished: list[str] = []
        store._insert_search = _slow(0.8, finished, "late")  # type: ignore[method-assign]
        assert _save(store) is False
        assert breaker.state == "closed"  # one failure of two: counted once
        deadline = time.monotonic() + 3
        while not finished and time.monotonic() < deadline:
            time.sleep(0.05)
        assert finished == ["late"] and breaker.state == "closed"  # its late end is not counted again
        assert _save(store) is False
        assert breaker.state == "open"


def test_an_open_breaker_answers_false_at_once_and_sends_nothing() -> None:
    breaker = CircuitBreaker(max_failures=1)
    breaker.record_failure()
    with _store(timeout_s=0.3, save_timeout_s=15.0, breaker=breaker) as store:
        sent: list[str] = []
        store._insert_search = _slow(0.0, sent, "sent")  # type: ignore[method-assign]
        started = time.monotonic()
        assert _save(store) is False
        assert time.monotonic() - started < 0.1
        assert sent == []


def test_slow_saves_never_hold_up_the_other_calls() -> None:
    """Saves run on workers of their own: two saves waiting on a slow database leave a request save its 0.3 s."""
    release = threading.Event()
    with _store(timeout_s=0.3, save_timeout_s=3.0) as store:
        store._insert_search = lambda *_a, **_k: release.wait(5)  # type: ignore[method-assign]
        store._insert = _slow(0.0)  # type: ignore[method-assign]
        saves = [threading.Thread(target=_save, args=(store,)) for _ in range(2)]
        for thread in saves:
            thread.start()
        time.sleep(0.1)
        try:
            assert store.save_request(SESSION, ex.QUERY, None) == "saved"
        finally:
            release.set()
            for thread in saves:
                thread.join(5)


class _Conn:
    def __init__(self, insert_s: float = 0.0) -> None:
        self.sent: list[str] = []
        self.ended: list[str] = []
        self.insert_s = insert_s

    def execute(self, query: Any, params: Any = None) -> _Conn:
        text = query if isinstance(query, str) else query.as_string(None)
        self.sent.append(text)
        if text.lstrip().lower().startswith("insert into"):
            time.sleep(self.insert_s)
        return self

    @contextmanager
    def transaction(self) -> Iterator[None]:
        try:
            yield
        except BaseException:
            self.ended.append("rolled back")
            raise
        self.ended.append("committed")


class _Pool:
    def __init__(self, insert_s: float = 0.0) -> None:
        self.conn = _Conn(insert_s)
        self.waits: list[float | None] = []

    @contextmanager
    def connection(self, timeout: float | None = None) -> Iterator[_Conn]:
        self.waits.append(timeout)
        yield self.conn


def _no_pool() -> Any:
    raise AssertionError("a save took a connection from the other calls' pool")


def test_the_save_sets_its_own_statement_timeout_inside_its_transaction() -> None:
    """On its own pool, with what is left of 15 s less the commit's margin: the commit lands inside the 15 s."""
    pool = _Pool()
    with _store(timeout_s=2.0, save_timeout_s=15.0) as store:
        store._get_save_pool = lambda: pool  # type: ignore[assignment, method-assign, return-value]
        store._get_pool = _no_pool  # type: ignore[method-assign]
        assert _save(store) is True
    first, insert = pool.conn.sent
    milliseconds = int(first.rsplit(" ", 1)[1])
    work_s = 15.0 - COMMIT_MARGIN_S
    assert first.startswith("set local statement_timeout = ") and work_s - 1 < milliseconds / 1000 <= work_s
    assert insert.lstrip().lower().startswith("insert into")
    assert pool.waits[0] is not None and work_s - 1 < pool.waits[0] <= work_s  # waiting for a connection counts too
    assert pool.conn.ended == ["committed"]


def test_the_commit_margin_is_a_tenth_of_the_time_and_at_most_one_second() -> None:
    assert commit_margin(15.0) == COMMIT_MARGIN_S == 1.0
    assert commit_margin(2.0) == pytest.approx(0.2)


def test_a_row_whose_time_runs_out_during_its_insert_is_rolled_back_never_committed() -> None:
    """The caller is told "not saved", so the row must never appear later: past the deadline, it is rolled back."""
    pool = _Pool(insert_s=1.0)
    with _store(timeout_s=0.3, save_timeout_s=2.0) as store:
        store._get_save_pool = lambda: pool  # type: ignore[assignment, method-assign, return-value]
        assert _save(store, within_s=1.0) is False
        deadline = time.monotonic() + 3
        while not pool.conn.ended and time.monotonic() < deadline:
            time.sleep(0.05)
    assert pool.conn.ended == ["rolled back"]


def test_a_save_takes_no_longer_than_the_time_its_caller_has_left() -> None:
    with _store(timeout_s=0.3, save_timeout_s=15.0) as store:
        store._insert_search = _slow(3.0)  # type: ignore[method-assign]
        started = time.monotonic()
        assert _save(store, within_s=0.5) is False
        assert 0.4 < time.monotonic() - started < 1.0


def test_a_caller_with_more_time_than_the_limit_still_gets_only_the_limit() -> None:
    with _store(timeout_s=0.3, save_timeout_s=0.5) as store:
        store._insert_search = _slow(3.0)  # type: ignore[method-assign]
        started = time.monotonic()
        assert _save(store, within_s=20.0) is False
        assert time.monotonic() - started < 1.0


def test_a_half_open_breaker_keeps_its_one_trial_for_a_quick_call() -> None:
    """A save would hold the trial for up to 15 s: it answers False at once, and the trial is still there."""
    now = [0.0]
    breaker = CircuitBreaker(max_failures=1, cooldown_s=30.0, clock=lambda: now[0])
    breaker.record_failure()
    now[0] = 31.0
    assert breaker.state == "half_open"
    with _store(timeout_s=0.3, save_timeout_s=15.0, breaker=breaker) as store:
        sent: list[str] = []
        store._insert_search = _slow(0.0, sent, "sent")  # type: ignore[method-assign]
        started = time.monotonic()
        assert _save(store) is False
        assert time.monotonic() - started < 0.1 and sent == []
        store._insert = _slow(0.0)  # type: ignore[method-assign]
        assert store.save_request(SESSION, ex.QUERY, None) == "saved"  # the quick call took the trial
    assert breaker.state == "closed"


def test_a_save_that_only_waited_for_a_worker_is_not_counted() -> None:
    """Two saves hold both save workers past their limit (two failures); a third only waits, so it is not a third."""
    release = threading.Event()
    breaker = CircuitBreaker(max_failures=3)
    with _store(timeout_s=0.3, save_timeout_s=0.5, breaker=breaker) as store:
        store._insert_search = lambda *_a, **_k: release.wait(5)  # type: ignore[method-assign]
        answers: list[bool] = []
        holding = [threading.Thread(target=lambda: answers.append(_save(store))) for _ in range(2)]
        for thread in holding:
            thread.start()
        time.sleep(0.1)
        try:
            assert _save(store) is False  # queued behind them until its 0.5 s ran out
            for thread in holding:
                thread.join(2)
            assert answers == [False, False]
            assert breaker.state == "closed"  # 2 failures of 3: the queued save was not counted
        finally:
            release.set()


def test_a_quick_call_queued_behind_a_hung_database_still_counts() -> None:
    """Only saves are let off: a hung database still opens the breaker through the other calls."""
    release = threading.Event()
    breaker = CircuitBreaker(max_failures=3)
    with _store(timeout_s=0.3, save_timeout_s=15.0, breaker=breaker) as store:
        store._insert = lambda *_a, **_k: release.wait(5)  # type: ignore[method-assign]
        calls = [threading.Thread(target=store.save_request, args=(SESSION, ex.QUERY, None)) for _ in range(3)]
        try:
            for thread in calls:
                thread.start()
            for thread in calls:
                thread.join(2)
            assert breaker.state == "open"
        finally:
            release.set()


def test_saves_have_a_pool_of_their_own_with_their_own_timeouts() -> None:
    with _store(timeout_s=2.0, save_timeout_s=15.0) as store:
        main, saves = store._get_pool(), store._get_save_pool()
        assert main is not saves
        assert (main.max_size, saves.max_size) == (2, 2)
        assert (main.timeout, saves.timeout) == (2.0, 15.0)
        assert (main.kwargs["connect_timeout"], saves.kwargs["connect_timeout"]) == (2, 15)
