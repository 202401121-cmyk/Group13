"""The circuit breaker (US-01): 3 failures in a row open it for 30 seconds, then one trial call decides."""

from __future__ import annotations

from app.db import CircuitBreaker


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_it_opens_after_three_failures_in_a_row() -> None:
    breaker = CircuitBreaker(clock=FakeClock())
    assert breaker.allow() and breaker.record_failure() is False
    assert breaker.allow() and breaker.record_failure() is False
    assert breaker.state == "closed"
    assert breaker.allow() and breaker.record_failure() is True
    assert breaker.state == "open"
    assert breaker.allow() is False  # skipped at once


def test_a_success_resets_the_count() -> None:
    breaker = CircuitBreaker(clock=FakeClock())
    breaker.record_failure()
    breaker.record_failure()
    breaker.record_success()
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state == "closed"  # only two in a row since the success


def test_after_30_seconds_one_trial_is_let_through_and_success_closes_it() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(clock=clock)
    for _ in range(3):
        breaker.record_failure()
    clock.now += 29.9
    assert breaker.allow() is False
    clock.now += 0.2
    assert breaker.state == "half_open"
    assert breaker.allow() is True  # the one trial
    assert breaker.allow() is False  # nobody else while the trial runs
    breaker.record_success()
    assert breaker.state == "closed"
    assert breaker.allow() is True


def test_a_failed_trial_keeps_it_open_for_another_30_seconds() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(clock=clock)
    for _ in range(3):
        breaker.record_failure()
    clock.now += 30.0
    assert breaker.allow() is True
    assert breaker.record_failure() is True
    assert breaker.state == "open"
    clock.now += 29.0
    assert breaker.allow() is False
    clock.now += 1.0
    assert breaker.allow() is True
