"""One GET to a Reddit archive, with every limit applied (US-12). Shared by Arctic Shift and PullPush.

In order: the search's request budget, the archive's circuit breaker, the app-wide interval limiter, then the
request (10 s timeout, our User-Agent). A rate limit (HTTP 429, or Arctic Shift's 422 "slow down") waits for the
archive's reset time, at most 30 seconds, and retries once. Nothing here raises: a failure comes back as a
SourceFailure, and the archive's name and the HTTP status are the only details it carries.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.contracts import SourceFailure
from app.db.breaker import CircuitBreaker

from .base import IntervalLimiter, RequestBudget, failure
from .settings import REQUEST_TIMEOUT_S, archive_user_agent

log = logging.getLogger(__name__)

MAX_RESET_WAIT_S = 30.0
DEFAULT_RETRY_WAIT_S = 2.0  # when a rate-limited reply gives no reset time


@dataclass(frozen=True)
class Fetched:
    items: list[dict[str, Any]] = field(default_factory=list)
    failure: SourceFailure | None = None


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
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.label = label
        self.base_url = base_url.rstrip("/")
        self.http = http
        self.limiter = limiter
        self.breaker = breaker
        self.rate_limit_statuses = rate_limit_statuses
        self._sleep = sleep
        self._headers = {"User-Agent": archive_user_agent(), "Accept": "application/json"}

    def get(self, path: str, params: Mapping[str, str | int], budget: RequestBudget | None) -> Fetched:
        if budget is not None and budget.left == 0:
            return Fetched(
                failure=failure("quota_exhausted", f"{self.label}: this search's Reddit requests are used up")
            )
        if not self.breaker.allow():
            return Fetched(failure=failure("error", f"{self.label} is paused after repeated failures"))
        result = self._send(path, params, budget)
        if result.failure is None or result.failure.reason == "not_found":
            self.breaker.record_success()  # the archive answered
        else:
            self.breaker.record_failure()
            log.info("reddit %s failed: %s", self.label, result.failure.detail)
        return result

    def _send(self, path: str, params: Mapping[str, str | int], budget: RequestBudget | None) -> Fetched:
        url = f"{self.base_url}{path}"
        for attempt in (1, 2):
            if budget is not None and not budget.take():
                return Fetched(
                    failure=failure("rate_limited", f"{self.label}: rate limited, and no request left to retry")
                )
            self.limiter.wait()
            try:
                response = self.http.get(url, params=dict(params), headers=self._headers, timeout=REQUEST_TIMEOUT_S)
            except httpx.TimeoutException:
                return Fetched(failure=failure("timeout", f"{self.label}: no reply within {REQUEST_TIMEOUT_S:g}s"))
            except (httpx.HTTPError, httpx.InvalidURL) as exc:
                return Fetched(failure=failure("error", f"{self.label}: request failed ({type(exc).__name__})"))
            status = response.status_code
            if status in self.rate_limit_statuses:
                wait = reset_wait(response.headers)
                if attempt == 1 and wait <= MAX_RESET_WAIT_S:
                    self._sleep(wait)
                    continue
                return Fetched(failure=failure("rate_limited", f"{self.label}: HTTP {status} (rate limited)"))
            return self._read(response)
        raise AssertionError("unreachable")  # pragma: no cover

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


def reset_wait(headers: httpx.Headers) -> float:
    """Seconds the archive asks us to wait (X-RateLimit-Reset, else Retry-After), or a short default."""
    for name in ("x-ratelimit-reset", "retry-after"):
        raw = headers.get(name)
        if raw is None:
            continue
        try:
            seconds = float(raw)
        except ValueError:
            continue
        if seconds >= 0:
            return seconds
    return DEFAULT_RETRY_WAIT_S
