"""Which providers and models to skip for now (US-46), so a search never waits on one that is down or out of quota.

- HTTP 429 or 5xx: skip it for as long as it asks (Retry-After or a rate-limit reset header), held between 60 and 120
  seconds. With no usable header, 90 seconds.
- Three timeouts or other failures within a minute: skip it for 90 seconds, then let one trial call through. If the
  trial fails too, skip it for another 90 seconds. The client doesn't report a timeout it cut short itself.
- HTTP 401 or 402: the key is wrong, or the account cannot be used. Skip the whole provider for the rest of the run.
- HTTP 403 (this key may not use this model) or a retired model: skip that model for the rest of the run.
- A model that can't be found (HTTP 404, or an error code saying so): skip that model for 10 minutes, then let one
  trial call through. It may be a brief outage, so it can recover by itself, with no restart.
Invalid JSON is the model's mistake, not the provider's, so it counts as a working provider.

Each model in the pool has its own record, because its limits are its own (`model=`). A provider outside the pool has
one record for the whole provider. State is per process and in memory (build rule 10). Thread-safe: concurrent
searches share one client.
"""

from __future__ import annotations

import re
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Literal

from app.contracts import Provider

from .errors import AuthFailed, ModelForbidden, ModelNotFound, ModelRetired, NoCredit, RateLimited, ServerError

COOLDOWN_MIN_S = 60.0
COOLDOWN_MAX_S = 120.0
DEFAULT_COOLDOWN_S = 90.0
FAILURES_BEFORE_COOLDOWN = 3
FAILURE_WINDOW_S = 60.0
MODEL_NOT_FOUND_COOLDOWN_S = 600.0

HealthState = Literal["ok", "cooling", "dead"]

_DURATION_PART = re.compile(r"(\d+(?:\.\d+)?)(ms|h|m|s)")


@dataclass
class _Record:
    failures: deque[float] = field(default_factory=deque)  # monotonic times of recent failures
    cool_until: float | None = None  # monotonic time; None = not cooling down
    dead: bool = False
    trial_running: bool = False


class ProviderHealth:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._records: dict[tuple[Provider, str | None], _Record] = {}
        self._dead_providers: set[Provider] = set()

    def state(self, provider: Provider, model: str | None = None) -> HealthState:
        with self._lock:
            p = self._records.get((provider, model))
            if provider in self._dead_providers or (p is not None and p.dead):
                return "dead"
            if p is None:
                return "ok"
            return "cooling" if p.cool_until is not None and self._clock() < p.cool_until else "ok"

    def allow(self, provider: Provider, model: str | None = None) -> bool:
        """May a call go out now? After a cool-down, only one trial call at a time."""
        with self._lock:
            p = self._record(provider, model)
            if provider in self._dead_providers or p.dead:
                return False
            if p.cool_until is None:
                return True
            if self._clock() < p.cool_until or p.trial_running:
                return False
            p.trial_running = True
            return True

    def release(self, provider: Provider, model: str | None = None) -> None:
        """An allowed call was not sent after all (for example, the rate limiter said no)."""
        with self._lock:
            self._record(provider, model).trial_running = False

    def record_success(self, provider: Provider, model: str | None = None) -> None:
        with self._lock:
            p = self._record(provider, model)
            p.failures.clear()
            p.trial_running = False
            # A call already in flight can succeed just after another got a 429: the cool-down still stands.
            if p.cool_until is not None and self._clock() >= p.cool_until:
                p.cool_until = None

    def record_failure(self, provider: Provider, error: Exception, model: str | None = None) -> bool:
        """True when this failure has just switched the whole provider off for the run (HTTP 401 or 402)."""
        with self._lock:
            p = self._record(provider, model)
            p.trial_running = False
            now = self._clock()
            if isinstance(error, (ModelForbidden, ModelRetired)):
                p.dead = True
            elif isinstance(error, ModelNotFound):
                _cool(p, now + MODEL_NOT_FOUND_COOLDOWN_S)
            elif isinstance(error, (AuthFailed, NoCredit)):
                newly = provider not in self._dead_providers
                self._dead_providers.add(provider)
                return newly
            elif isinstance(error, (RateLimited, ServerError)):
                _cool(p, now + cooldown_from_headers(error.ratelimit_headers))
            else:
                p.failures.append(now)
                while p.failures and p.failures[0] <= now - FAILURE_WINDOW_S:
                    p.failures.popleft()
                if p.cool_until is not None or len(p.failures) >= FAILURES_BEFORE_COOLDOWN:
                    _cool(p, now + DEFAULT_COOLDOWN_S)
            return False

    def _record(self, provider: Provider, model: str | None) -> _Record:
        return self._records.setdefault((provider, model), _Record())


def _cool(record: _Record, until: float) -> None:
    """A cool-down only ever gets longer: a late 503 must not cut a 10-minute one down to 90 seconds."""
    record.cool_until = max(record.cool_until or until, until)


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
