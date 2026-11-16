"""US-31: the one retry rule every outside call follows (app/llm/retry.py)."""

from __future__ import annotations

import math
import random
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import pytest

from app.llm.retry import (
    RATE_LIMIT_JITTER_S,
    RETRY_JITTER_S,
    RetryClock,
    kind_of_status,
    parse_retry_after,
    retry_after_of,
    retry_wait,
)

NOW = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("status", "kind"),
    [(200, None), (301, None), (400, "refused"), (401, "refused"), (403, "refused"), (404, "refused"),
     (422, "refused"), (429, "rate_limited"), (500, "server"), (502, "server"), (503, "server"), (522, "server")],
)  # fmt: skip
def test_a_status_says_what_kind_of_failure_it_is(status: int, kind: str | None) -> None:
    assert kind_of_status(status) == kind


@pytest.mark.parametrize("kind", ["timeout", "connection", "server"])
def test_a_timeout_a_connection_error_or_a_5xx_is_retried_once_after_a_jitter(kind: str) -> None:
    waits = {
        retry_wait(kind, None, time_left_s=30, attempt_s=9, rng=random.Random(seed))  # type: ignore[arg-type]
        for seed in range(50)
    }
    assert None not in waits
    assert all(RETRY_JITTER_S[0] <= w <= RETRY_JITTER_S[1] for w in waits if w is not None)
    assert len(waits) > 10  # it really varies: calls that failed together don't come back together


def test_a_4xx_is_never_retried() -> None:
    assert retry_wait("refused", 1.0, time_left_s=math.inf, attempt_s=1, rng=random.Random(1)) is None
    assert retry_wait(None, None, time_left_s=math.inf, attempt_s=1, rng=random.Random(1)) is None


def test_a_429_is_retried_only_after_its_retry_after() -> None:
    wait = retry_wait("rate_limited", 4.0, time_left_s=30, attempt_s=9, rng=random.Random(3))
    assert wait is not None and 4.0 <= wait <= 4.0 + RATE_LIMIT_JITTER_S
    assert retry_wait("rate_limited", None, time_left_s=30, attempt_s=9, rng=random.Random(3)) is None


def test_a_retry_needs_its_wait_and_a_full_attempt_before_the_stages_limit() -> None:
    rng = random.Random(7)
    assert retry_wait("rate_limited", 5.0, time_left_s=14.0, attempt_s=9.0, rng=rng) is None  # 5 + 9 + jitter > 14
    assert retry_wait("rate_limited", 5.0, time_left_s=14.3, attempt_s=9.0, rng=random.Random(0)) is not None
    assert retry_wait("timeout", None, time_left_s=6.0, attempt_s=9.0, rng=rng) is None  # after a 9 s timeout of 15
    assert retry_wait("server", None, time_left_s=RETRY_JITTER_S[1] + 9.0, attempt_s=9.0, rng=rng) is not None


@pytest.mark.parametrize(
    ("value", "seconds"),
    [("7", 7.0), (" 2.5 ", 2.5), ("0", 0.0), ("-3", 0.0), ("", None), (None, None), ("soon", None), ("inf", None),
     ("nan", None)],
)  # fmt: skip
def test_retry_after_in_seconds(value: str | None, seconds: float | None) -> None:
    assert parse_retry_after(value, NOW) == seconds


def test_retry_after_as_an_http_date() -> None:
    assert parse_retry_after(format_datetime(NOW + timedelta(seconds=30), usegmt=True), NOW) == 30.0
    assert parse_retry_after(format_datetime(NOW - timedelta(seconds=30), usegmt=True), NOW) == 0.0  # already past
    assert parse_retry_after("Sun, 15 Nov 2026 12:00:12", NOW) == 12.0  # no zone: read as UTC


def test_retry_after_is_found_whatever_the_headers_case() -> None:
    assert retry_after_of({"Retry-After": "3"}) == 3.0
    assert retry_after_of({"retry-after": "4", "x-ratelimit-reset": "9"}) == 4.0
    assert retry_after_of({"x-ratelimit-reset": "9"}) is None


def test_a_retry_clock_asks_its_stage_for_the_time_left() -> None:
    left = [20.0]
    clock = RetryClock(time_left=lambda: left[0], sleep=lambda _: None, rng=random.Random(2))
    assert clock.wait_for("timeout", None, attempt_s=10.0) is not None
    left[0] = 10.0
    assert clock.wait_for("timeout", None, attempt_s=10.0) is None
    assert RetryClock().wait_for("server", None, attempt_s=1e9) is not None  # no stage: no limit
