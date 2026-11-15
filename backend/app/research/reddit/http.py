"""One GET to a Reddit archive, with every limit applied (US-12). Shared by Arctic Shift and PullPush.

In order: the search's request budget, the archive's circuit breaker, the app-wide interval limiter, then the
request (10 s timeout, our User-Agent). Every request gets one retry (US-31, app/llm/retry.py):

- after a timeout, a broken connection or a 5xx, following a short random wait (jitter);
- after a rate limit (HTTP 429) that says how long to wait (X-RateLimit-Reset or Retry-After), following that time,
  at most 30 seconds, plus a little jitter; one that doesn't say is not retried. Arctic
  Shift's 422 "slow down" is its own rate-limit reply: it comes often and can ask for 15 s, which would use up
  research's time, so it waits at most 5 seconds;
- never after another 4xx;
- and only if the wait and a whole request (10 s) still fit before research's deadline.

After that the caller moves on, to the next subreddit for a listing. Each retry takes a request from the search's
budget, as every request does. Nothing here raises: a failure comes back as a SourceFailure, and the archive's name
and the HTTP status are the only details it carries.
"""

from __future__ import annotations

import logging
import math
import random
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.contracts import SourceFailure
from app.db.breaker import CircuitBreaker
from app.llm.retry import RATE_LIMIT_JITTER_S, Kind, parse_retry_after, retry_wait

from .base import IntervalLimiter, RequestBudget, failure
from .settings import REQUEST_TIMEOUT_S, archive_user_agent

log = logging.getLogger(__name__)

MAX_RESET_WAIT_S = 30.0
SHORT_RESET_WAIT_S = 5.0  # for the statuses in short_wait_statuses


@dataclass(frozen=True)
class Fetched:
    items: list[dict[str, Any]] = field(default_factory=list)
    failure: SourceFailure | None = None
    still_answering: bool = False  # it refused, but it is up: this says nothing against it to the breaker


class ArchiveHttp:
    def __init__(
        self,
        label: str,
        base_url: str,
        http: httpx.Client,
        limiter: IntervalLimiter,
        breaker: CircuitBreaker,
        *,
        rate_limit_statuses: frozenset[int] = frozenset({429}),
        short_wait_statuses: frozenset[int] = frozenset(),
        sleep: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
    ) -> None:
        self.label = label
        self.base_url = base_url.rstrip("/")
        self.http = http
        self.limiter = limiter
        self.breaker = breaker
        self.rate_limit_statuses = rate_limit_statuses
        self.short_wait_statuses = short_wait_statuses
        self._sleep = sleep
        self.rng = rng if rng is not None else random.Random()  # the retry's jitter (US-31)
        self._headers = {"User-Agent": archive_user_agent(), "Accept": "application/json"}

    def get(self, path: str, params: Mapping[str, str | int], budget: RequestBudget | None) -> Fetched:
        if budget is not None and budget.left == 0:
            return Fetched(
                failure=failure("quota_exhausted", f"{self.label}: this search's Reddit requests are used up")
            )
        if not self.breaker.allow():
            return Fetched(failure=failure("error", f"{self.label} is paused after repeated failures"))
        result = self._send(path, params, budget)
        if result.failure is None or result.failure.reason == "not_found" or result.still_answering:
            self.breaker.record_success()  # the archive answered
        else:
            self.breaker.record_failure()
            log.info("reddit %s failed: %s", self.label, result.failure.detail)
        return result

    def _send(self, path: str, params: Mapping[str, str | int], budget: RequestBudget | None) -> Fetched:
        url = f"{self.base_url}{path}"
        time_left = budget.time_left() if budget is not None else math.inf
        earlier: Fetched | None = None  # a failure being retried: it stands if the budget can't pay for the retry
        for attempt in (1, 2):
            if budget is not None and not budget.take():
                if earlier is not None:  # the retry can't be paid for: the first reply stands
                    return earlier
                return Fetched(
                    failure=failure("rate_limited", f"{self.label}: rate limited, and no request left to retry")
                )
            self.limiter.wait()
            kind: Kind
            try:
                response = self.http.get(url, params=dict(params), headers=self._headers, timeout=REQUEST_TIMEOUT_S)
            except httpx.TimeoutException:
                kind = "timeout"
                failed = Fetched(failure=failure("timeout", f"{self.label}: no reply within {REQUEST_TIMEOUT_S:g}s"))
            except (httpx.NetworkError, httpx.RemoteProtocolError) as exc:
                kind = "connection"
                failed = Fetched(failure=failure("error", f"{self.label}: request failed ({type(exc).__name__})"))
            except (httpx.HTTPError, httpx.InvalidURL) as exc:
                return Fetched(failure=failure("error", f"{self.label}: request failed ({type(exc).__name__})"))
            else:
                status = response.status_code
                if status in self.rate_limit_statuses:
                    wait = self._wait_for(status, response.headers)
                    refused = failure("rate_limited", f"{self.label}: HTTP {status} (rate limited)")
                    limited = Fetched(failure=refused, still_answering=status in self.short_wait_statuses)
                    if attempt == 1 and wait is not None:
                        wait += self.rng.uniform(0.0, RATE_LIMIT_JITTER_S)
                        if budget is None or wait + REQUEST_TIMEOUT_S <= budget.time_left():
                            earlier = limited
                            self._sleep(wait)
                            continue
                    return limited
                if status < 500:
                    return self._read(response)
                kind = "server"
                failed = self._read(response)
            if attempt == 2:
                return failed
            if budget is not None:
                time_left = budget.time_left()
            wait_s = retry_wait(kind, None, time_left_s=time_left, attempt_s=REQUEST_TIMEOUT_S, rng=self.rng)
            if wait_s is None:
                return failed
            log.info("reddit %s: %s; trying once more in %.1fs", self.label, kind, wait_s)
            earlier = failed
            self._sleep(wait_s)
        raise AssertionError("unreachable")  # pragma: no cover

    def _wait_for(self, status: int, headers: httpx.Headers) -> float | None:
        """How long to wait before the retry, or None: it doesn't say how long, or asks too long to be worth it."""
        asked = reset_wait(headers)
        if asked is None:
            return None  # US-31: a rate limit is retried only after the wait it names
        if status in self.short_wait_statuses:
            return min(asked, SHORT_RESET_WAIT_S)
        return asked if asked <= MAX_RESET_WAIT_S else None

    def _read(self, response: httpx.Response) -> Fetched:
        status = response.status_code
        if status == 404:
            return Fetched(failure=failure("not_found", f"{self.label}: HTTP 404"))
        if status in (401, 403):
            return Fetched(failure=failure("blocked", f"{self.label}: HTTP {status} (refused)"))
        if not 200 <= status < 300:
            return Fetched(failure=failure("error", f"{self.label}: HTTP {status}"))
        try:
            body = response.json()
        except ValueError:
            return Fetched(failure=failure("error", f"{self.label}: the reply was not JSON"))
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, list):
            return Fetched(failure=failure("error", f"{self.label}: the reply had no data list"))
        return Fetched(items=[item for item in data if isinstance(item, dict)])


def reset_wait(headers: httpx.Headers) -> float | None:
    """Seconds the archive asks us to wait (X-RateLimit-Reset, else Retry-After in seconds or as a date), or None."""
    raw = headers.get("x-ratelimit-reset")
    if raw is not None:
        try:
            seconds = float(raw)
        except ValueError:
            seconds = -1.0
        if seconds >= 0:
            return seconds
    return parse_retry_after(headers.get("retry-after"))
