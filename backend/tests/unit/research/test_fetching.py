"""Fetches run in one fixed-size pool, so research time is set by the slowest source (US-27)."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from app.research.fetching import FetchRun, shared_pool
from app.research.reddit import PullPushSource, arctic_shift, pullpush
from app.research.reddit.cache import RedditCache
from app.research.serper import SHARED_LIMITER, shared_limiter


def _pool(size: int = 6) -> ThreadPoolExecutor:
    return ThreadPoolExecutor(max_workers=size)


@pytest.mark.slow
def test_three_sources_of_1_2_and_3_seconds_finish_in_under_3_5_seconds() -> None:
    run = FetchRun(_pool(), deadline_s=30)
    finished: list[float] = []
    started = time.monotonic()
    for seconds in (1, 2, 3):
        run.submit("reddit", lambda s=seconds: time.sleep(s), on_done=lambda _: finished.append(time.monotonic()))
    assert run.wait() == []
    assert len(finished) == 3
    assert time.monotonic() - started < 3.5


def test_sources_run_at_the_same_time() -> None:
    """All three must be running at once for the barrier to open. One after another, it would time out."""
    together = threading.Barrier(3, timeout=5)
    run = FetchRun(_pool(), deadline_s=10)
    results: list[object] = []
    for _ in range(3):
        run.submit("web_search", together.wait, on_done=results.append)
    assert run.wait() == []
    assert len(results) == 3


def test_the_pool_never_runs_more_than_its_fixed_size() -> None:
    lock = threading.Lock()
    running = 0
    peak = 0
    full = threading.Barrier(3, timeout=5)

    def work() -> None:
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        full.wait()
        with lock:
            running -= 1

    run = FetchRun(_pool(3), deadline_s=10)
    for _ in range(9):
        run.submit("review_site", work)
    assert run.wait() == []
    assert peak == 3


def test_the_shared_pool_has_the_size_it_was_asked_for_and_is_one_pool() -> None:
    pool = shared_pool(6)
    assert pool._max_workers == 6
    assert shared_pool(6) is pool


def test_a_task_that_raises_is_handed_to_its_error_callback_and_the_rest_go_on() -> None:
    errors: list[Exception] = []
    done: list[object] = []

    def boom() -> None:
        raise RuntimeError("made up")

    run = FetchRun(_pool(), deadline_s=10)
    run.submit("web_search", boom, on_done=done.append, on_error=errors.append)
    run.submit("reddit", lambda: "fine", on_done=done.append)
    assert run.wait() == []
    assert [type(e) for e in errors] == [RuntimeError]
    assert done == ["fine"]


def test_a_task_that_raises_without_an_error_callback_never_stops_the_run() -> None:
    def boom() -> None:
        raise RuntimeError("made up")

    run = FetchRun(_pool(), deadline_s=10)
    run.submit("web_search", boom)
    assert run.wait() == []


def test_a_callback_can_submit_more_work() -> None:
    results: list[str] = []
    run = FetchRun(_pool(), deadline_s=10)

    def second_round(_: object) -> None:
        run.submit("review_site", lambda: "page", on_done=results.append)

    run.submit("web_search", lambda: "search", on_done=second_round)
    assert run.wait() == []
    assert results == ["page"]


def test_the_deadline_returns_what_finished_and_names_what_did_not() -> None:
    release = threading.Event()
    results: list[str] = []
    run = FetchRun(_pool(), deadline_s=0.2)
    run.submit("web_search", lambda: "quick", on_done=results.append)
    run.submit("reddit", lambda: release.wait(10))
    started = time.monotonic()
    unfinished = run.wait()
    elapsed = time.monotonic() - started
    release.set()
    assert results == ["quick"]
    assert unfinished == ["reddit"]
    assert elapsed < 2


def test_queued_work_is_cancelled_at_the_deadline() -> None:
    release = threading.Event()
    pool = _pool(1)
    ran: list[str] = []
    run = FetchRun(pool, deadline_s=0.1)
    run.submit("reddit", lambda: release.wait(10))
    run.submit("review_site", lambda: ran.append("queued"))
    assert sorted(run.wait()) == ["reddit", "review_site"]
    release.set()
    pool.shutdown(wait=True)
    assert ran == []


def test_a_callback_that_raises_never_stops_the_run() -> None:
    def bad(_: object) -> None:
        raise ValueError("made up")

    results: list[str] = []
    run = FetchRun(_pool(), deadline_s=10)
    run.submit("web_search", lambda: "a", on_done=bad)
    run.submit("reddit", lambda: "b", on_done=results.append)
    assert run.wait() == []
    assert results == ["b"]


def test_serper_has_one_limiter_for_the_whole_process() -> None:
    assert SHARED_LIMITER.interval_s == 0.2
    assert shared_limiter(0.2) is SHARED_LIMITER


def test_each_archive_keeps_the_rate_it_asks_for() -> None:
    assert arctic_shift.SHARED_LIMITER.interval_s == 1.0
    assert pullpush.SHARED_LIMITER.interval_s == 2.0
    assert PullPushSource(httpx.Client(), cache=RedditCache(folder=None)).http.limiter is pullpush.SHARED_LIMITER


def test_a_timer_runs_in_the_waiting_thread_when_something_is_still_pending() -> None:
    release = threading.Event()
    seen: list[str] = []
    run = FetchRun(_pool(), deadline_s=5)
    run.submit("reddit", lambda: release.wait(5))

    def timer() -> None:
        seen.append(threading.current_thread().name)
        release.set()

    run.after(0.05, timer)
    started = time.monotonic()
    assert run.wait() == []
    assert time.monotonic() - started < 3
    assert seen == [threading.current_thread().name]


def test_a_timer_that_fails_stops_nothing(caplog: pytest.LogCaptureFixture) -> None:
    run = FetchRun(_pool(), deadline_s=5)
    release = threading.Event()
    run.submit("reddit", lambda: release.wait(5))

    def bad() -> None:
        raise RuntimeError("made up")

    run.after(0.0, bad)
    run.after(0.05, release.set)
    assert run.wait() == []
    assert "RuntimeError" in caplog.text


def test_a_timer_after_the_work_is_done_never_runs() -> None:
    seen: list[int] = []
    run = FetchRun(_pool(), deadline_s=5)
    run.submit("reddit", lambda: 1)
    run.after(60, lambda: seen.append(1))
    assert run.wait() == []
    assert seen == []


def test_the_shared_pool_really_runs_at_the_size_asked_for() -> None:
    together = threading.Barrier(3, timeout=5)
    pool = shared_pool(7)
    assert pool is shared_pool(7)
    futures = [pool.submit(together.wait) for _ in range(3)]
    assert sorted(f.result(timeout=10) for f in futures) == [0, 1, 2]  # one worker could never fill the barrier
