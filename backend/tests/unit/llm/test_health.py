"""Provider health (US-46): cool-downs after 429s and repeated failures, and dead for the run on a bad key."""

from __future__ import annotations

import pytest

from app.contracts import Provider
from app.llm.errors import (
    AuthFailed,
    ModelForbidden,
    ModelNotFound,
    NoCredit,
    ProviderError,
    ProviderTimeout,
    RateLimited,
    ServerError,
)
from app.llm.health import (
    COOLDOWN_MAX_S,
    COOLDOWN_MIN_S,
    DEFAULT_COOLDOWN_S,
    FAILURE_WINDOW_S,
    ProviderHealth,
    cooldown_from_headers,
)

GROQ = Provider.GROQ


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _health() -> tuple[ProviderHealth, Clock]:
    clock = Clock()
    return ProviderHealth(clock), clock


@pytest.mark.parametrize(
    ("headers", "seconds"),
    [
        ({}, DEFAULT_COOLDOWN_S),
        ({"retry-after": "75"}, 75.0),
        ({"retry-after": "2"}, COOLDOWN_MIN_S),  # held at the 60 s floor
        ({"x-ratelimit-reset-tokens": "1m30.5s"}, 90.5),
        ({"x-ratelimit-reset-requests": "2m59.56s", "x-ratelimit-reset-tokens": "7.66s"}, COOLDOWN_MAX_S),
        ({"x-ratelimit-reset": "1730000000000"}, COOLDOWN_MAX_S),  # a timestamp reads as a long wait
        ({"x-ratelimit-reset": "soon"}, DEFAULT_COOLDOWN_S),
        ({"x-ratelimit-reset": "500ms"}, COOLDOWN_MIN_S),
        ({"x-ratelimit-remaining-requests": "0"}, DEFAULT_COOLDOWN_S),  # not a reset header
    ],
)
def test_cooldown_from_headers(headers: dict[str, str], seconds: float) -> None:
    assert cooldown_from_headers(headers) == pytest.approx(seconds)


def test_a_rate_limit_cools_down_then_comes_back() -> None:
    health, clock = _health()
    health.record_failure(GROQ, RateLimited(GROQ, "HTTP 429", {"retry-after": "80"}))
    assert (health.state(GROQ), health.allow(GROQ)) == ("cooling", False)
    clock.now += 79
    assert not health.allow(GROQ)
    clock.now += 2
    assert health.allow(GROQ)  # the trial call
    health.record_success(GROQ)
    assert (health.state(GROQ), health.allow(GROQ)) == ("ok", True)


@pytest.mark.parametrize("error", [ProviderTimeout, ProviderError])
def test_three_failures_in_a_row_switch_a_provider_off_for_90_seconds(error: type[ProviderError]) -> None:
    health, clock = _health()
    for _ in range(2):
        health.record_failure(GROQ, error(GROQ, "simulated"))
        assert health.allow(GROQ)
    health.record_failure(GROQ, error(GROQ, "simulated"))
    assert not health.allow(GROQ)
    clock.now += DEFAULT_COOLDOWN_S - 1
    assert not health.allow(GROQ)
    clock.now += 2
    assert health.allow(GROQ)


def test_a_success_resets_the_failure_count() -> None:
    health, _ = _health()
    for _ in range(2):
        health.record_failure(GROQ, ProviderTimeout(GROQ, "no reply"))
    health.record_success(GROQ)
    for _ in range(2):
        health.record_failure(GROQ, ProviderTimeout(GROQ, "no reply"))
    assert health.allow(GROQ)


def test_failures_only_count_within_the_window() -> None:
    health, clock = _health()
    for _ in range(2):
        health.record_failure(GROQ, ProviderTimeout(GROQ, "no reply"))
    clock.now += FAILURE_WINDOW_S + 1
    health.record_failure(GROQ, ProviderTimeout(GROQ, "no reply"))
    assert health.state(GROQ) == "ok"


@pytest.mark.parametrize(("headers", "seconds"), [({}, DEFAULT_COOLDOWN_S), ({"retry-after": "5"}, COOLDOWN_MIN_S)])
def test_one_server_error_cools_for_60_to_120_seconds(headers: dict[str, str], seconds: float) -> None:
    health, clock = _health()
    health.record_failure(GROQ, ServerError(GROQ, "HTTP 503", headers))
    assert health.state(GROQ) == "cooling"
    clock.now += seconds
    assert health.state(GROQ) == "ok"


def test_a_late_success_does_not_cancel_a_cool_down() -> None:
    health, clock = _health()
    health.record_failure(GROQ, RateLimited(GROQ, "HTTP 429"), "model-a")
    health.record_success(GROQ, "model-a")  # a call already in flight comes back after the 429
    assert health.state(GROQ, "model-a") == "cooling"
    assert not health.allow(GROQ, "model-a")
    clock.now += DEFAULT_COOLDOWN_S
    assert health.allow(GROQ, "model-a")
    health.record_success(GROQ, "model-a")  # the trial worked
    assert health.state(GROQ, "model-a") == "ok"


def test_each_model_has_its_own_record() -> None:
    health, _ = _health()
    health.record_failure(GROQ, RateLimited(GROQ, "HTTP 429"), "model-a")
    assert health.state(GROQ, "model-a") == "cooling"
    assert health.state(GROQ, "model-b") == "ok"
    assert health.allow(GROQ, "model-b")


def test_a_bad_key_is_dead_for_every_model_of_that_provider() -> None:
    health, _ = _health()
    health.record_failure(GROQ, AuthFailed(GROQ, "HTTP 401"), "model-a")
    assert health.state(GROQ, "model-b") == "dead"
    assert not health.allow(GROQ, "model-b")


def test_a_withdrawn_model_is_dead_alone_and_skips_the_breaker() -> None:
    health, clock = _health()
    health.record_failure(GROQ, ModelNotFound(GROQ, "HTTP 404"), "model-a")
    assert health.state(GROQ, "model-a") == "dead"
    assert health.state(GROQ, "model-b") == "ok"
    clock.now += COOLDOWN_MAX_S * 10  # no cool-down to wait out: it never comes back in this run
    assert not health.allow(GROQ, "model-a")


def test_a_model_not_found_on_a_provider_outside_the_pool_is_dead_too() -> None:
    health, clock = _health()
    mistral = Provider.MISTRAL  # one record for the whole provider, so no model name
    health.record_failure(mistral, ModelNotFound(mistral, "HTTP 404"))
    assert health.state(mistral) == "dead"
    clock.now += COOLDOWN_MAX_S * 10
    assert not health.allow(mistral)


def test_a_forbidden_model_is_dead_alone() -> None:
    health, _ = _health()
    health.record_failure(GROQ, ModelForbidden(GROQ, "HTTP 403"), "model-a")
    assert health.state(GROQ, "model-a") == "dead"
    assert health.state(GROQ, "model-b") == "ok"


def test_after_a_cool_down_only_one_trial_goes_through_and_a_failed_trial_cools_again() -> None:
    health, clock = _health()
    for _ in range(3):
        health.record_failure(GROQ, ServerError(GROQ, "HTTP 503"))
    clock.now += DEFAULT_COOLDOWN_S + 1
    assert health.allow(GROQ)
    assert not health.allow(GROQ)  # a second search waits for the trial's result
    health.record_failure(GROQ, ProviderTimeout(GROQ, "30s"))  # one failure is enough after a trial
    assert health.state(GROQ) == "cooling"
    clock.now += DEFAULT_COOLDOWN_S + 1
    assert health.allow(GROQ)
    health.release(GROQ)  # the trial was not sent after all
    assert health.allow(GROQ)


@pytest.mark.parametrize("error", [AuthFailed, NoCredit])
def test_a_bad_key_or_an_account_without_credit_is_dead_for_the_run(error: type[AuthFailed]) -> None:
    health, clock = _health()
    health.record_failure(GROQ, error(GROQ, "simulated"))
    clock.now += 10_000
    assert (health.state(GROQ), health.allow(GROQ)) == ("dead", False)
    health.record_success(GROQ)  # nothing brings it back within the run
    assert not health.allow(GROQ)


def test_providers_are_tracked_separately() -> None:
    health, _ = _health()
    health.record_failure(GROQ, AuthFailed(GROQ, "HTTP 401"))
    assert health.allow(Provider.MISTRAL)
    assert health.state(Provider.CEREBRAS) == "ok"
