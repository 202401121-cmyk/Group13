"""D-53 without a search (US-12 follow-up): at start-up, then every SWEEP_EVERY_S, expired Reddit text goes.

Fake clocks and fake waits only: no test sleeps for real or waits ten minutes.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import anyio
import pytest
from fastapi.testclient import TestClient

from app.api.search_runs import ExpiredRun, RunSettings, SearchRuns, _Run
from app.contracts import SearchRunStatus
from app.main import SWEEP_EVERY_S, Housekeeping, create_app
from app.pipeline import InMemoryResultStore, PipelineRunner, default_search_stages, new_search_id
from app.research.cache import sweep_expired_files
from app.research.reddit.cache import RedditCache, serve_limit
from tests.fakes.research import thread

WAIT_S = 5.0


class Stop(Exception):
    """Raised by a fake wait to end the timer's loop, as a shutdown would."""


class JumpingClock:
    def __init__(self) -> None:
        self.jumped_s = 0.0

    def __call__(self) -> float:
        return time.monotonic() + self.jumped_s


class Runs:
    """A stand-in run store whose first `drop_expired` fails."""

    def __init__(self, fail_first: bool = False) -> None:
        self.calls = 0
        self.fail_first = fail_first

    def drop_expired(self) -> None:
        self.calls += 1
        if self.fail_first and self.calls == 1:
            raise RuntimeError("run srch_secret could not be dropped")


def registry(clock: JumpingClock) -> SearchRuns:
    return SearchRuns(
        lambda: PipelineRunner(default_search_stages(), InMemoryResultStore()), RunSettings(keep_s=60.0), clock=clock
    )


def ended_run(runs: SearchRuns) -> str:
    """A finished run in the registry, without running a search."""
    run = _Run(search_id=new_search_id())
    with runs._lock:
        runs._runs[run.search_id] = run
    runs._end(run, SearchRunStatus.CANCELLED, None, {})
    return run.search_id


def stop_after(ticks: int, waits: list[float]) -> object:
    async def wait(seconds: float) -> None:
        waits.append(seconds)
        if len(waits) >= ticks:
            raise Stop

    return wait


def test_the_start_up_sweep_deletes_an_expired_reddit_file_and_keeps_a_fresh_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reddit = tmp_path / "reddit"
    monkeypatch.setattr("app.research.cache.REDDIT_CACHE_DIR", reddit)
    monkeypatch.setattr("app.research.cache.DEFAULT_CACHE_DIR", tmp_path / "research")
    cache = RedditCache(folder=reddit)
    cache.put(("old",), [thread("aaa", fetched_at=datetime.now(UTC))])
    (old,) = reddit.glob("*.json")
    aged = time.time() - (serve_limit() + timedelta(minutes=1)).total_seconds()
    os.utime(old, (aged, aged))
    cache.put(("fresh",), [thread("bbb", fetched_at=datetime.now(UTC))])
    (fresh,) = set(reddit.glob("*.json")) - {old}

    app = create_app(env={})
    keeper = app.state.housekeeping
    with TestClient(app):  # no search, no request: only the app starting
        assert keeper.ticked.wait(WAIT_S)
        assert not old.exists()
        assert fresh.exists()


def test_one_tick_drops_an_expired_finished_run_with_no_request_made() -> None:
    clock = JumpingClock()
    runs = registry(clock)
    search_id = ended_run(runs)
    waits: list[float] = []

    async def wait(seconds: float) -> None:
        waits.append(seconds)
        if len(waits) == 1:
            assert search_id in runs._runs  # the start-up tick keeps it: it is not past its keep-time yet
            clock.jumped_s = 61.0  # ten minutes pass, and nobody asks for anything
            return
        raise Stop

    keeper = Housekeeping(runs, sweep=lambda: None, wait=wait)
    with pytest.raises(Stop):
        anyio.run(keeper.run)
    assert keeper.ticks == 2
    assert search_id not in runs._runs  # dropped by the timer alone
    with pytest.raises(ExpiredRun):
        runs.get(search_id)


@pytest.mark.parametrize("part", ["cache sweep", "run store"])
def test_an_error_in_a_tick_is_logged_by_type_and_the_next_tick_still_runs(
    part: str, caplog: pytest.LogCaptureFixture
) -> None:
    sweeps: list[int] = []

    def sweep() -> None:
        sweeps.append(1)
        if part == "cache sweep" and len(sweeps) == 1:
            raise OSError("C:/Users/someone/secret")

    runs = Runs(fail_first=part == "run store")
    keeper = Housekeeping(runs, sweep=sweep, wait=stop_after(2, []))  # type: ignore[arg-type]
    with caplog.at_level("WARNING", logger="app.main"), pytest.raises(Stop):
        anyio.run(keeper.run)
    assert keeper.ticks == 2
    assert len(sweeps) == 2 and runs.calls == 2  # both parts ran on both ticks, the failing one included
    kind = "OSError" if part == "cache sweep" else "RuntimeError"
    assert f"housekeeping: the {part} failed ({kind})" in caplog.text
    assert "secret" not in caplog.text  # the type only, never the message


def test_a_tick_runs_off_the_event_loop() -> None:
    """File work in a worker thread: a sweep never holds up a request on the event loop."""
    seen: dict[str, int] = {}

    def sweep() -> None:
        seen["sweep"] = threading.get_ident()

    async def main() -> None:
        seen["loop"] = threading.get_ident()
        await Housekeeping(Runs(), sweep=sweep, wait=stop_after(1, [])).run()  # type: ignore[arg-type]

    with pytest.raises(Stop):
        anyio.run(main)
    assert seen["sweep"] != seen["loop"]


def test_the_timer_waits_ten_minutes_between_ticks() -> None:
    assert SWEEP_EVERY_S == 600.0  # the team's choice
    waits: list[float] = []
    with pytest.raises(Stop):
        anyio.run(Housekeeping(Runs(), sweep=lambda: None, wait=stop_after(2, waits)).run)  # type: ignore[arg-type]
    assert waits == [600.0, 600.0]


def test_shutdown_stops_the_task() -> None:
    app = create_app(env={})
    keeper = app.state.housekeeping
    with TestClient(app):
        assert keeper.ticked.wait(WAIT_S)  # the start-up tick
        assert keeper.running
    assert not keeper.running  # stopped with the app, in the middle of its ten-minute wait
    assert keeper.ticks == 1


def test_the_apps_timer_works_on_the_apps_own_run_store_and_cache() -> None:
    app = create_app(env={})
    keeper = app.state.housekeeping
    assert keeper.runs is app.state.search_runs
    assert keeper.sweep is sweep_expired_files
    assert keeper.every_s == SWEEP_EVERY_S


def test_shutdown_does_not_wait_for_a_stuck_sweep() -> None:
    """A sweep stuck on a slow disk is left to finish alone; the runs were dropped before it started."""
    started, release = threading.Event(), threading.Event()
    runs = Runs()

    def sweep() -> None:
        started.set()
        release.wait(WAIT_S)

    keeper = Housekeeping(runs, sweep=sweep, wait=stop_after(5, []))  # type: ignore[arg-type]

    async def main() -> None:
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(keeper.run)
            await anyio.to_thread.run_sync(started.wait, WAIT_S)
            tasks.cancel_scope.cancel()  # the app shutting down

    began = time.monotonic()
    anyio.run(main)
    took = time.monotonic() - began
    release.set()
    assert took < WAIT_S / 2
    assert not keeper.running
    assert runs.calls == 1  # the runs went first


def test_a_failure_outside_the_jobs_is_logged_and_the_timer_goes_on(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    real = anyio.to_thread.run_sync
    tries: list[int] = []

    async def no_thread_the_first_time(func, *args, **kwargs):  # type: ignore[no-untyped-def]
        tries.append(1)
        if len(tries) == 1:
            raise RuntimeError("can't start new thread for srch_secret")
        return await real(func, *args, **kwargs)

    monkeypatch.setattr(anyio.to_thread, "run_sync", no_thread_the_first_time)
    keeper = Housekeeping(Runs(), sweep=lambda: None, wait=stop_after(2, []))  # type: ignore[arg-type]
    with caplog.at_level("WARNING", logger="app.main"), pytest.raises(Stop):
        anyio.run(keeper.run)
    assert keeper.ticks == 1  # the first tick never started; the next one ran
    assert "housekeeping: a tick failed (RuntimeError)" in caplog.text
    assert "secret" not in caplog.text
