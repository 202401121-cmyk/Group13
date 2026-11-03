"""Limits we hold ourselves to, so a free tier is not run dry in the middle of a search (US-46).

- DailyTokenBudget: how many tokens each provider may use per UTC day. The count is kept in memory, so it starts
  again at zero when the app restarts: set the budget below the provider's real allowance.
- RateLimiter: requests per second and tokens per minute for one provider, shared by every search in the
  process. A call waits for room, but never longer than max_wait_s; then it is not sent and the wrapper moves on.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime

from app.contracts import Provider

DEFAULT_MAX_WAIT_S = 2.0
WINDOW_S = 60.0


def _utc_today() -> date:
    return datetime.now(UTC).date()


class DailyTokenBudget:
    """Tokens left per provider today. A provider with no limit (None or missing) is never stopped."""

    def __init__(self, limits: Mapping[Provider, int | None], today: Callable[[], date] = _utc_today) -> None:
        self._limits = {p: n for p, n in limits.items() if n is not None}
        self._today = today
        self._lock = threading.Lock()
        self._day = today()
        self._used: dict[Provider, int] = {}

    def remaining(self, provider: Provider) -> int | None:
        with self._lock:
            self._roll_over()
            limit = self._limits.get(provider)
            return None if limit is None else max(0, limit - self._used.get(provider, 0))

    def allows(self, provider: Provider, tokens: int) -> bool:
        left = self.remaining(provider)
        return left is None or tokens <= left

    def spend(self, provider: Provider, tokens: int) -> None:
        with self._lock:
            self._roll_over()
            self._used[provider] = self._used.get(provider, 0) + max(0, tokens)

    def _roll_over(self) -> None:
        today = self._today()
        if today != self._day:
            self._day = today
            self._used.clear()


class RateLimiter:
    """Spaces requests to `requests_per_second` and keeps the tokens of the last minute under `tokens_per_minute`."""

    def __init__(
        self,
        requests_per_second: float | None = None,
        tokens_per_minute: int | None = None,
        *,
        max_wait_s: float = DEFAULT_MAX_WAIT_S,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.requests_per_second = requests_per_second
        self.tokens_per_minute = tokens_per_minute
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
            now = self._clock()
            self._last_start = now
            self._window.append((now, tokens))
            return True

    def _wait_needed(self, now: float, tokens: int) -> float | None:
        """Seconds until the call fits, or None if it never can (bigger than a whole minute's allowance)."""
        waits = [0.0]
        if self.requests_per_second and self._last_start is not None:
            waits.append(self._last_start + 1 / self.requests_per_second - now)
        if self.tokens_per_minute:
            if tokens > self.tokens_per_minute:
                return None
            while self._window and self._window[0][0] <= now - WINDOW_S:
                self._window.popleft()
            over = sum(n for _, n in self._window) + tokens - self.tokens_per_minute
            for started, n in self._window:
                if over <= 0:
                    break
                over -= n
                waits.append(started + WINDOW_S - now)
        return max(waits)
