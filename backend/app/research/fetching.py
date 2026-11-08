"""Source fetches run in one thread pool of a fixed size, so research time follows the slowest source (US-27).

A FetchRun is one search's work on that pool. The stage's own thread runs wait(): results and errors are handed to their
callbacks there, one at a time, so callbacks need no locks and may submit more work. The stage's thread never holds a
worker, so a callback waiting on nothing can't deadlock the pool. At the deadline, queued work is cancelled and whatever
is running is left to finish in the background (Python can't kill a thread), and its result is ignored. after() runs
something in the stage's thread at a set time, so waiting for it holds no worker.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from time import monotonic
from typing import Any

log = logging.getLogger(__name__)

_pool_lock = threading.Lock()
_pools: dict[int, ThreadPoolExecutor] = {}


def shared_pool(size: int) -> ThreadPoolExecutor:
    """The process's one pool. A stage is built per search, but the workers are shared by all of them."""
    with _pool_lock:
        if size not in _pools:
            _pools[size] = ThreadPoolExecutor(max_workers=size, thread_name_prefix="research-fetch")
        return _pools[size]


@dataclass
class _Task:
    label: str
    on_done: Callable[[Any], None] | None
    on_error: Callable[[Exception], None] | None


class FetchRun:
    def __init__(self, pool: ThreadPoolExecutor, deadline_s: float, clock: Callable[[], float] = monotonic) -> None:
        self.pool = pool
        self._clock = clock
        self._ends_at = clock() + max(0.0, deadline_s)
        self._pending: dict[Future[Any], _Task] = {}
        self._timers: list[tuple[float, Callable[[], None]]] = []

    def time_left(self) -> float:
        return max(0.0, self._ends_at - self._clock())

    def submit(
        self,
        label: str,
        fn: Callable[[], Any],
        on_done: Callable[[Any], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        self._pending[self.pool.submit(fn)] = _Task(label, on_done, on_error)

    def after(self, seconds: float, fn: Callable[[], None]) -> None:
        """Call `fn` from wait() after this long, if anything is still pending then."""
        self._timers.append((self._clock() + seconds, fn))

    def wait(self) -> list[str]:
        """Run until nothing is pending or the deadline. Returns the labels of whatever did not finish."""
        while self._pending:
            left = self.time_left()
            if left <= 0:
                break
            self._fire_timers()
            soonest = min((at - self._clock() for at, _ in self._timers), default=left)
            done, _ = wait(list(self._pending), timeout=max(0.0, min(left, soonest)), return_when=FIRST_COMPLETED)
            for future in done:
                self._finish(future, self._pending.pop(future))
        unfinished = [task.label for task in self._pending.values()]
        for future in self._pending:
            future.cancel()
        self._pending = {}
        return unfinished

    def _fire_timers(self) -> None:
        now = self._clock()
        due = [fn for at, fn in self._timers if at <= now]
        self._timers = [timer for timer in self._timers if timer[0] > now]
        for fn in due:
            try:
                fn()
            except Exception as exc:  # a timer's bug must not stop the sources (build rule 9)
                log.warning("research: a timer failed: %s", type(exc).__name__)

    def _finish(self, future: Future[Any], task: _Task) -> None:
        error = future.exception()
        try:
            if error is None:
                if task.on_done is not None:
                    task.on_done(future.result())
                return
            if not isinstance(error, Exception):
                raise error  # KeyboardInterrupt and friends are not fetch failures
            log.warning("research: a %s fetch failed: %s", task.label, type(error).__name__)
            if task.on_error is not None:
                task.on_error(error)
        except Exception as exc:  # a callback's bug must not stop the other sources (build rule 9)
            log.warning("research: handling a %s result failed: %s", task.label, type(exc).__name__)
