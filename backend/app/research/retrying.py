"""The one retry a research request gets (US-31): app/llm/retry.py's rule, around one httpx request.

Each source keeps its own timeouts, caps and reading of the reply; this only decides whether to send the request once
more. A retry is a request like any other, so it is counted: `take` asks the source's cap first, and with no unit left
there is no retry.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import httpx

from app.llm.retry import Kind, RetryClock, kind_of_status, retry_after_of

log = logging.getLogger(__name__)

Outcome = httpx.Response | httpx.HTTPError


def kind_of(outcome: Outcome) -> Kind | None:
    """What a request's outcome says about retrying it."""
    if isinstance(outcome, httpx.Response):
        return kind_of_status(outcome.status_code)
    if isinstance(outcome, httpx.TimeoutException):
        return "timeout"
    if isinstance(outcome, (httpx.NetworkError, httpx.RemoteProtocolError)):
        return "connection"
    return None


def send_once_more(
    send: Callable[[], httpx.Response],
    retry: RetryClock,
    attempt_s: float,
    take: Callable[[], bool] = lambda: True,
    label: str = "research",
    *,
    rate_limits: bool = True,
) -> Outcome:
    """Send, and send once more if the rule allows it (app/llm/retry.py): the last reply, or the last httpx error.

    `attempt_s` is the longest the retry itself may take. Any error that isn't httpx's (a bad URL) is raised as before.
    """
    return send_tracked(send, retry, attempt_s, take, label, rate_limits=rate_limits)[0]


def send_tracked(
    send: Callable[[], httpx.Response],
    retry: RetryClock,
    attempt_s: float,
    take: Callable[[], bool] = lambda: True,
    label: str = "research",
    *,
    rate_limits: bool = True,
) -> tuple[Outcome, bool]:
    """send_once_more, and whether the retry was really sent: a failure counts as repeated only if it was.

    `rate_limits=False` never retries a 429: for a service whose 429 pauses it for the whole process (Firecrawl).
    """
    first = _send(send)
    kind = kind_of(first)
    if kind == "rate_limited" and not rate_limits:
        return first, False
    after = retry_after_of(first.headers) if isinstance(first, httpx.Response) and kind == "rate_limited" else None
    wait = retry.wait_for(kind, after, attempt_s)
    if wait is None or not take():
        return first, False
    if isinstance(first, httpx.Response):
        first.close()
    log.info("%s: %s; trying once more in %.1fs", label, kind, wait)
    retry.sleep(wait)
    return _send(send), True


def _send(send: Callable[[], httpx.Response]) -> Outcome:
    try:
        return send()
    except httpx.HTTPError as exc:
        return exc
