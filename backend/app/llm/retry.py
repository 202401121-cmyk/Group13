"""The one retry every outside call gets (US-31).

The rule, for LLM providers and research sources alike:

- one retry, never more;
- only after a timeout, a connection error or a 5xx, after a short random wait (jitter), so many calls that failed
  together don't all come back at the same moment;
- never after a 4xx, except a 429 that says how long to wait (Retry-After): then once, after that wait;
- and only when the retry still has a full attempt's time before its stage's limit. A retry that could not finish
  would only take time from what comes after it (the next provider, the next source), so it is not made.

This module only decides. Each caller makes the request, keeps its own timeouts and counts the retry against its own
caps, as every request is counted.
"""

from __future__ import annotations

import math
import random
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Literal

Kind = Literal["timeout", "connection", "server", "rate_limited", "refused"]

RETRY_JITTER_S = (0.2, 0.6)  # the wait before retrying a timeout, a connection error or a 5xx
RATE_LIMIT_JITTER_S = 0.25  # added to a 429's Retry-After, so the retry doesn't land on the very second it names
TRANSIENT: frozenset[Kind] = frozenset({"timeout", "connection", "server"})


def kind_of_status(status: int) -> Kind | None:
    """What a reply's status says about retrying it, or None for a reply that did not fail."""
    if status == 429:
        return "rate_limited"
    if status >= 500:
        return "server"
    if status >= 400:
        return "refused"
    return None


def parse_retry_after(value: str | None, now: datetime | None = None) -> float | None:
    """Seconds to wait from a Retry-After value: a number of seconds or an HTTP date. None if it says neither."""
    if value is None or not value.strip():
        return None
    text = value.strip()
    try:
        seconds = float(text)
    except ValueError:
        try:
            when = parsedate_to_datetime(text)
        except (TypeError, ValueError, IndexError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        seconds = (when - (now or datetime.now(UTC))).total_seconds()
    if not math.isfinite(seconds):
        return None
    return max(0.0, seconds)


def retry_after_of(headers: Mapping[str, str], now: datetime | None = None) -> float | None:
    """The Retry-After a reply's headers give, in seconds, whatever the header's case."""
    value = next((v for name, v in headers.items() if name.lower() == "retry-after"), None)
    return parse_retry_after(value, now)


def retry_wait(
    kind: Kind | None,
    retry_after_s: float | None,
    *,
    time_left_s: float,
    attempt_s: float,
    rng: random.Random,
) -> float | None:
    """The wait before the one retry, or None when there must be no retry.

    `time_left_s` is what the stage has left now, and `attempt_s` the time the retry itself may take. Both are needed:
    the retry is made only if its wait and a full attempt both fit.
    """
    if kind in TRANSIENT:
        wait = rng.uniform(*RETRY_JITTER_S)
    elif kind == "rate_limited" and retry_after_s is not None:
        wait = retry_after_s + rng.uniform(0.0, RATE_LIMIT_JITTER_S)
    else:
        return None  # any other 4xx, or a 429 that doesn't say how long: the same request would fail the same way
    if wait + attempt_s > time_left_s:
        return None
    return wait


def _no_limit() -> float:
    return math.inf


@dataclass
class RetryClock:
    """What a research source needs to retry once: the time its stage has left, a way to wait, and the jitter's
    randomness. The research stage gives each search's sources its own deadline; tests give fakes."""

    time_left: Callable[[], float] = _no_limit
    sleep: Callable[[float], None] = time.sleep
    rng: random.Random = field(default_factory=random.Random)

    def wait_for(self, kind: Kind | None, retry_after_s: float | None, attempt_s: float) -> float | None:
        """The wait before the one retry, or None for no retry (the rule above)."""
        return retry_wait(kind, retry_after_s, time_left_s=self.time_left(), attempt_s=attempt_s, rng=self.rng)
