"""Which providers to skip for now (US-46), so a search never waits on a provider that is down or out of quota.

- HTTP 429: skip the provider for as long as it asks (Retry-After or a rate-limit reset header), held between
  60 and 120 seconds. With no usable header, 90 seconds.
- Three timeouts or other failures in a row: skip it for 90 seconds, then let one trial call through. If the
  trial fails too, skip it for another 90 seconds.
- HTTP 401, 402 or 403: the key is wrong, or the account cannot be used. Skip it for the rest of the run.
Invalid JSON is the model's mistake, not the provider's, so it counts as a working provider.

State is per process and in memory (build rule 10). Thread-safe: concurrent searches share one client.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal

from app.contracts import Provider

from .errors import AuthFailed, NoCredit, RateLimited

COOLDOWN_MIN_S = 60.0
COOLDOWN_MAX_S = 120.0
DEFAULT_COOLDOWN_S = 90.0
FAILURES_BEFORE_COOLDOWN = 3

HealthState = Literal["ok", "cooling", "dead"]

_DURATION_PART = re.compile(r"(\d+(?:\.\d+)?)(ms|h|m|s)")


@dataclass
class _Provider:
    failures: int = 0
    cool_until: float | None = None  # monotonic time; None = not cooling down
    dead: bool = False
    trial_running: bool = False


class ProviderHealth:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._providers: dict[Provider, _Provider] = {}

    def state(self, provider: Provider) -> HealthState:
        with self._lock:
            p = self._providers.get(provider)
            if p is None:
                return "ok"
            if p.dead:
                return "dead"
            return "cooling" if p.cool_until is not None and self._clock() < p.cool_until else "ok"

    def allow(self, provider: Provider) -> bool:
        """May a call go to this provider now? After a cool-down, only one trial call at a time."""
        with self._lock:
            p = self._providers.setdefault(provider, _Provider())
            if p.dead:
                return False
            if p.cool_until is None:
                return True
            if self._clock() < p.cool_until or p.trial_running:
                return False
            p.trial_running = True
            return True

    def release(self, provider: Provider) -> None:
        """An allowed call was not sent after all (for example, the rate limiter said no)."""
        with self._lock:
            self._providers.setdefault(provider, _Provider()).trial_running = False

    def record_success(self, provider: Provider) -> None:
        with self._lock:
            p = self._providers.setdefault(provider, _Provider())
            p.failures = 0
            p.cool_until = None
            p.trial_running = False

    def record_failure(self, provider: Provider, error: Exception) -> None:
        with self._lock:
            p = self._providers.setdefault(provider, _Provider())
            p.trial_running = False
            if isinstance(error, (AuthFailed, NoCredit)):
                p.dead = True
            elif isinstance(error, RateLimited):
                p.cool_until = self._clock() + cooldown_from_headers(error.ratelimit_headers)
            else:
                p.failures += 1
                if p.cool_until is not None or p.failures >= FAILURES_BEFORE_COOLDOWN:
                    p.cool_until = self._clock() + DEFAULT_COOLDOWN_S


def cooldown_from_headers(headers: Mapping[str, str]) -> float:
    """Seconds to wait after a 429: the longest wait any header asks for, held between 60 and 120 seconds.

    Providers say it differently: "retry-after: 7", "x-ratelimit-reset-tokens: 2m59.5s", "x-ratelimit-reset: 7s".
    A header holding a timestamp reads as a very long wait, so it is held at 120 seconds too.
    """
    waits = [
        seconds
        for name, value in headers.items()
        if "reset" in name.lower() or name.lower() == "retry-after"
        if (seconds := _seconds(value)) is not None
    ]
    if not waits:
        return DEFAULT_COOLDOWN_S
    return min(COOLDOWN_MAX_S, max(COOLDOWN_MIN_S, max(waits)))


def _seconds(value: str) -> float | None:
    text = value.strip().lower()
    try:
        return float(text)
    except ValueError:
        pass
    parts = _DURATION_PART.findall(text)
    if not parts or "".join(n + u for n, u in parts) != text:
        return None
    scale = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}
    return sum(float(n) * scale[u] for n, u in parts)
