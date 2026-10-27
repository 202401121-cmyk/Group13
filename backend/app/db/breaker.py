"""A small circuit breaker (US-01): stop waiting on a database that keeps failing.

After 3 failures in a row the breaker opens: calls are skipped at once for 30 seconds. Then one trial call is let
through. If it works the breaker closes; if not, it stays open for another 30 seconds. Thread-safe.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Literal

BreakerState = Literal["closed", "open", "half_open"]


class CircuitBreaker:
    def __init__(
        self, max_failures: int = 3, cooldown_s: float = 30.0, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.max_failures = max_failures
        self.cooldown_s = cooldown_s
        self._clock = clock
        self._lock = threading.Lock()
        self._failures = 0
        self._open_until: float | None = None
        self._trial_running = False

    @property
    def state(self) -> BreakerState:
        with self._lock:
            if self._open_until is None:
                return "closed"
            return "open" if self._clock() < self._open_until else "half_open"

    def allow(self) -> bool:
        """May a call go to the database now? While half-open, only one trial call at a time."""
        with self._lock:
            if self._open_until is None:
                return True
            if self._clock() < self._open_until or self._trial_running:
                return False
            self._trial_running = True
            return True

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._open_until = None
            self._trial_running = False

    def record_failure(self) -> bool:
        """Count a failure. True when this failure opened the breaker (or kept it open after a failed trial)."""
        with self._lock:
            self._trial_running = False
            self._failures += 1
            if self._open_until is not None or self._failures >= self.max_failures:
                self._open_until = self._clock() + self.cooldown_s
                return True
            return False
