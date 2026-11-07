"""Our own limits (US-46): the daily token budget per provider and the Mistral rate limiter."""

from __future__ import annotations

from datetime import date

from app.contracts import Provider
from app.llm.limits import DailyTokenBudget, RateLimiter

MISTRAL = Provider.MISTRAL


class Day:
    def __init__(self) -> None:
        self.today = date(2026, 11, 3)

    def __call__(self) -> date:
        return self.today


class FakeTime:
    """A clock whose sleep() moves it on, so waits take no real time."""

    def __init__(self) -> None:
        self.now = 500.0
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def test_the_budget_stops_calls_once_spent_and_resets_the_next_utc_day() -> None:
    day = Day()
    budget = DailyTokenBudget({MISTRAL: 1000, Provider.GROQ: None}, today=day)
    assert budget.allows(MISTRAL, 1000)
    budget.spend(MISTRAL, 700)
    assert budget.remaining(MISTRAL) == 300
    assert budget.allows(MISTRAL, 300)
    assert not budget.allows(MISTRAL, 301)
    budget.spend(MISTRAL, 500)
    assert budget.remaining(MISTRAL) == 0  # never negative

    day.today = date(2026, 11, 4)
    assert budget.remaining(MISTRAL) == 1000


def test_a_provider_without_a_budget_is_never_stopped() -> None:
    budget = DailyTokenBudget({Provider.GROQ: None})
    budget.spend(Provider.GROQ, 10**9)
    assert budget.remaining(Provider.GROQ) is None
    assert budget.allows(Provider.GROQ, 10**9)
    assert budget.allows(Provider.CEREBRAS, 10**9)  # not listed at all


def test_requests_are_spaced_to_the_requests_per_second_limit() -> None:
    t = FakeTime()
    limiter = RateLimiter(requests_per_second=2, clock=t.clock, sleep=t.sleep)
    assert limiter.acquire(10)
    assert limiter.acquire(10)
    assert limiter.acquire(10)
    assert t.slept == [0.5, 0.5]


def test_no_wait_when_the_last_request_was_long_ago() -> None:
    t = FakeTime()
    limiter = RateLimiter(requests_per_second=2, clock=t.clock, sleep=t.sleep)
    limiter.acquire(10)
    t.now += 5
    assert limiter.acquire(10)
    assert t.slept == []


def test_tokens_per_minute_waits_for_old_calls_to_leave_the_window() -> None:
    t = FakeTime()
    limiter = RateLimiter(tokens_per_minute=1000, clock=t.clock, sleep=t.sleep, max_wait_s=60)
    assert limiter.acquire(600)
    t.now += 10
    assert limiter.acquire(300)
    t.now += 10
    assert limiter.acquire(400)  # 1300 in the window: wait until the 600-token call is a minute old
    assert t.slept == [40.0]


def test_a_wait_longer_than_max_wait_is_refused_without_sleeping() -> None:
    t = FakeTime()
    limiter = RateLimiter(tokens_per_minute=1000, clock=t.clock, sleep=t.sleep, max_wait_s=2)
    assert limiter.acquire(900)
    assert not limiter.acquire(200)
    assert t.slept == []
    t.now += 61
    assert limiter.acquire(200)


def test_a_call_bigger_than_a_whole_minute_never_fits() -> None:
    t = FakeTime()
    limiter = RateLimiter(tokens_per_minute=1000, clock=t.clock, sleep=t.sleep)
    assert not limiter.acquire(1001)


def test_requests_per_minute_are_counted_over_the_last_minute() -> None:
    t = FakeTime()
    limiter = RateLimiter(requests_per_minute=2, clock=t.clock, sleep=t.sleep)
    assert limiter.try_acquire(1) and limiter.try_acquire(1)
    assert not limiter.try_acquire(1)
    assert limiter.seconds_until_room(1) == 60
    t.now += 60
    assert limiter.try_acquire(1)


def test_try_acquire_never_waits_or_counts_a_refused_call() -> None:
    t = FakeTime()
    limiter = RateLimiter(tokens_per_minute=100, clock=t.clock, sleep=t.sleep)
    assert limiter.try_acquire(80)
    assert not limiter.try_acquire(30)
    assert t.slept == []
    assert limiter.tokens_this_minute() == 80


def test_seconds_until_room_says_when_a_call_fits_or_that_it_never_can() -> None:
    t = FakeTime()
    limiter = RateLimiter(tokens_per_minute=100, clock=t.clock, sleep=t.sleep)
    assert limiter.seconds_until_room(100) == 0
    limiter.try_acquire(80)
    t.now += 45
    assert limiter.seconds_until_room(30) == 15
    assert limiter.seconds_until_room(101) is None


def test_a_reservation_holds_tokens_and_settles_to_what_was_used() -> None:
    budget = DailyTokenBudget({MISTRAL: 100})
    assert budget.reserve(MISTRAL, 80)
    assert not budget.reserve(MISTRAL, 30)
    budget.settle(MISTRAL, 80, 50)
    assert budget.remaining(MISTRAL) == 50
    budget.settle(MISTRAL, 50, 0)  # given back: the call was never sent
    assert budget.remaining(MISTRAL) == 100


def test_no_limits_means_no_waiting() -> None:
    t = FakeTime()
    limiter = RateLimiter(clock=t.clock, sleep=t.sleep)
    assert all(limiter.acquire(10**6) for _ in range(5))
    assert t.slept == []
