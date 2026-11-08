"""Limits we hold ourselves to, so a free tier is not run dry in the middle of a search (US-46).

- DailyTokenBudget: how many tokens each provider or pool member may use per UTC day. The count is kept in memory, so
  it starts again at zero when the app restarts: set the budget below the provider's real allowance.
- RateLimiter: requests and tokens over the last minute for one provider or pool member, shared by every search in the
  process. acquire() waits for room, but never longer than max_wait_s. The pool uses try_acquire() and
  seconds_until_room() instead, so it can offer a call to another member rather than wait for this one.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable, Hashable, Mapping
from datetime import UTC, date, datetime
from typing import Generic, TypeVar

DEFAULT_MAX_WAIT_S = 2.0
WINDOW_S = 60.0

K = TypeVar("K", bound=Hashable)


def utc_today() -> date:
    return datetime.now(UTC).date()


class DailyTokenBudget(Generic[K]):
    """Tokens left today, per key. A key with no limit (None or missing) is never stopped."""

    def __init__(self, limits: Mapping[K, int | None], today: Callable[[], date] = utc_today) -> None:
        self._limits = {key: n for key, n in limits.items() if n is not None}
        self._today = today
        self._lock = threading.Lock()
        self._day = today()
        self._used: dict[K, int] = {}

    def remaining(self, key: K) -> int | None:
        with self._lock:
            self._roll_over()
            return self._left(key)

    def allows(self, key: K, tokens: int) -> bool:
        left = self.remaining(key)
        return left is None or tokens <= left

    def spend(self, key: K, tokens: int) -> None:
        with self._lock:
            self._roll_over()
            self._used[key] = self._used.get(key, 0) + max(0, tokens)

    def reserve(self, key: K, tokens: int) -> bool:
        """Hold `tokens` now if they fit, so calls running at the same time can't pass the budget together."""
        with self._lock:
            self._roll_over()
            left = self._left(key)
            if left is not None and tokens > left:
                return False
            self._used[key] = self._used.get(key, 0) + max(0, tokens)
            return True

    def settle(self, key: K, reserved: int, actual: int) -> None:
        """Swap a reservation for what the call really used. Pass an actual of 0 to give it all back."""
        with self._lock:
            self._roll_over()
            self._used[key] = max(0, self._used.get(key, 0) - reserved + actual)

    def _left(self, key: K) -> int | None:
        limit = self._limits.get(key)
        return None if limit is None else max(0, limit - self._used.get(key, 0))

    def _roll_over(self) -> None:
        today = self._today()
        if today != self._day:
            self._day = today
            self._used.clear()


class RateLimiter:
    """Spaces requests to `requests_per_second`, and keeps the last minute under its request and token limits."""

    def __init__(
        self,
        requests_per_second: float | None = None,
        tokens_per_minute: int | None = None,
        *,
        requests_per_minute: int | None = None,
        max_wait_s: float = DEFAULT_MAX_WAIT_S,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.requests_per_second = requests_per_second
        self.tokens_per_minute = tokens_per_minute
        self.requests_per_minute = requests_per_minute
        self.max_wait_s = max_wait_s
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._last_start: float | None = None
        self._window: deque[tuple[float, int]] = deque()  # (start time, tokens) of the last minute's calls

    def acquire(self, tokens: int) -> bool:
        """Wait until a call of `tokens` fits, then count it. False if that would take longer than max_wait_s."""
        with self._lock:  # held while waiting, so waiting calls go out one at a time, in order
            wait = self._wait_needed(self._clock(), tokens)
            if wait is None or wait > self.max_wait_s:
                return False
            if wait > 0:
                self._sleep(wait)
            self._take(self._clock(), tokens)
            return True

    def try_acquire(self, tokens: int, keep_free: int = 0) -> bool:
        """Count a call of `tokens` only if it fits right now and leaves `keep_free` of the minute. Never waits."""
        with self._lock:
            now = self._clock()
            wait = self._wait_needed(now, tokens + keep_free)
            if wait is None or wait > 0:
                return False
            self._take(now, tokens)
            return True

    def seconds_until_room(self, tokens: int, keep_free: int = 0) -> float | None:
        """Seconds until a call of `tokens` fits and leaves `keep_free` of the minute, or None if it never can."""
        with self._lock:
            return self._wait_needed(self._clock(), tokens + keep_free)

    def tokens_this_minute(self) -> int:
        with self._lock:
            self._forget_old_calls(self._clock())
            return sum(n for _, n in self._window)

    def _take(self, now: float, tokens: int) -> None:
        self._last_start = now
        self._window.append((now, tokens))

    def _forget_old_calls(self, now: float) -> None:
        while self._window and self._window[0][0] <= now - WINDOW_S:
            self._window.popleft()

    def _wait_needed(self, now: float, tokens: int) -> float | None:
        """Seconds until the call fits, or None if it never can (bigger than a whole minute's allowance)."""
        self._forget_old_calls(now)
        waits = [0.0]
        if self.requests_per_second and self._last_start is not None:
            waits.append(self._last_start + 1 / self.requests_per_second - now)
        if self.requests_per_minute and len(self._window) >= self.requests_per_minute:
            waits.append(self._window[-self.requests_per_minute][0] + WINDOW_S - now)
        if self.tokens_per_minute:
            if tokens > self.tokens_per_minute:
                return None
            over = sum(n for _, n in self._window) + tokens - self.tokens_per_minute
            for started, n in self._window:
                if over <= 0:
                    break
                over -= n
                waits.append(started + WINDOW_S - now)
        return max(waits)
