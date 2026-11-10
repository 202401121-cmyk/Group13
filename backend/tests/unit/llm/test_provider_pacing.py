"""Gemini is paced to its free-tier limits, so sources running together wait or move on instead of earning a 429.

With a fake clock, except where calls must run side by side. No real API is called.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Any

import pytest
from pydantic import BaseModel

from app.contracts import DataClass, Difficulty, LLMTask, LLMUnavailable, Provider
from app.llm import InMemoryCallLog, WrapperLLMClient, load_llm_config
from app.llm.client import default_limiters
from app.llm.errors import RateLimited, ServerError
from app.llm.health import ProviderHealth
from app.llm.limits import RateLimiter
from app.llm.pool import ModelPool
from tests.fakes.llm import FakeAdapter

REPLY = '{"product": "Nova Book 14"}'


class Answer(BaseModel):
    product: str


class Clock:
    """A clock whose sleep() moves it on, so a wait takes no real time."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []
        self.during_sleep: Callable[[], None] | None = None

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds
        if self.during_sleep is not None:
            self.during_sleep()


def _client(clock: Clock, limiter: RateLimiter | None = None) -> tuple[WrapperLLMClient, FakeAdapter, FakeAdapter]:
    config = load_llm_config()
    gemini, mistral = FakeAdapter(Provider.GEMINI, replies=(REPLY,)), FakeAdapter(Provider.MISTRAL, replies=(REPLY,))
    limiter = limiter or RateLimiter(requests_per_minute=5, tokens_per_minute=250_000, clock=clock, sleep=clock.sleep)
    client = WrapperLLMClient(
        config,
        {Provider.GEMINI: gemini, Provider.MISTRAL: mistral},
        InMemoryCallLog(),
        health=ProviderHealth(clock),
        limiters={Provider.GEMINI: limiter},
        pool=ModelPool(config.pool, clock=clock, sleep=clock.sleep),
    )
    return client, gemini, mistral


def _fill_geminis_minute(client: WrapperLLMClient, calls: int = 5, tokens: int = 1) -> None:
    """Gemini's minute fills up, as five earlier sources' calls would. Bulk work now takes turns across providers
    (US-46, F-3), so earlier calls through the client would not all reach Gemini: the minute is booked directly, and
    the call under test, the client's first, starts at Gemini."""
    limiter = client.limiters[Provider.GEMINI]
    for _ in range(calls):
        assert limiter.try_book(tokens) is not None


def _public(client: WrapperLLMClient, task: LLMTask = LLMTask.EXTRACT_CLAIMS) -> Answer:
    return client.complete(
        task, "Extract.", "Made-up page text.", Answer, data_class=DataClass.PUBLIC, difficulty=Difficulty.LONG
    )


def test_geminis_limits_are_in_the_config_with_their_date() -> None:
    gemini = load_llm_config().provider(Provider.GEMINI)
    assert (gemini.requests_per_minute, gemini.tokens_per_minute) == (15, 250_000)
    assert gemini.limits_checked == date(2026, 11, 9)


def test_the_real_client_paces_gemini() -> None:
    limiter = default_limiters(load_llm_config())[Provider.GEMINI]
    assert (limiter.requests_per_minute, limiter.tokens_per_minute) == (15, 250_000)
    assert limiter.requests_per_second is None


def test_public_extraction_goes_flash_lite_then_mistral_then_groq_then_openrouter() -> None:
    """Each one refuses here, so each one that's on the route gets asked, in order."""
    config = load_llm_config()
    journal: list[Provider] = []

    class Busy(FakeAdapter):
        def call(self, *args: Any, **kwargs: Any) -> Any:
            super().call(*args, **kwargs)
            raise ServerError(self.provider, "HTTP 503")

    every = {name: Busy(name, journal=journal) for name in Provider}
    client = WrapperLLMClient(config, dict(every), InMemoryCallLog(), limiters={})
    with pytest.raises(LLMUnavailable):
        _public(client)
    assert journal == [Provider.GEMINI, Provider.MISTRAL, *[Provider.GROQ] * 3, Provider.OPENROUTER]
    assert every[Provider.GEMINI].calls[0].model == "gemini-3.5-flash-lite"


def test_flash_lite_is_the_only_gemini_model_and_public_text_only() -> None:
    config = load_llm_config()
    assert [m.model for m in config.models if m.provider is Provider.GEMINI] == ["gemini-3.5-flash-lite"]
    assert config.provider(Provider.GEMINI).trains_on_inputs is True
    for data_class in (DataClass.PRIVATE, DataClass.REDDIT):
        for difficulty in Difficulty:
            assert Provider.GEMINI not in config.routes[data_class][difficulty]


def test_each_paced_provider_gets_its_own_limit_a_minute() -> None:
    """Each from the provider's own numbers: Gemini 15 (9 Nov 2026), OpenRouter's free models 20 (3 Nov 2026), and
    Mistral 2, from its replies' rate-limit headers (10 Nov 2026)."""
    limiters = default_limiters(load_llm_config())
    assert {name: limiter.requests_per_minute for name, limiter in limiters.items() if limiter.requests_per_minute} == {
        Provider.GEMINI: 15,
        Provider.OPENROUTER: 20,
        Provider.MISTRAL: 2,
    }


def test_mistral_is_paced_at_the_limits_its_replies_report() -> None:
    """x-ratelimit-limit-req-minute: 2 and x-ratelimit-limit-tokens-minute: 40000, read on 10 Nov 2026."""
    config = load_llm_config()
    mistral = config.provider(Provider.MISTRAL)
    assert (mistral.requests_per_minute, mistral.tokens_per_minute, mistral.requests_per_second) == (2, 40_000, None)
    assert mistral.limits_checked == date(2026, 11, 10)
    real = default_limiters(config)[Provider.MISTRAL]
    assert (real.requests_per_minute, real.tokens_per_minute, real.requests_per_second) == (2, 40_000, None)
    # Six public sources in a minute: Mistral's turn comes three times, and it is sent only the two its limit allows.
    clock = Clock()
    limiter = RateLimiter(requests_per_minute=2, tokens_per_minute=40_000, clock=clock, sleep=clock.sleep)
    gemini, mistral_api = (
        FakeAdapter(Provider.GEMINI, replies=(REPLY,)),
        FakeAdapter(Provider.MISTRAL, replies=(REPLY,)),
    )
    client = WrapperLLMClient(
        config,
        {Provider.GEMINI: gemini, Provider.MISTRAL: mistral_api},
        InMemoryCallLog(),
        health=ProviderHealth(clock),
        limiters={Provider.MISTRAL: limiter},
        pool=ModelPool(config.pool, clock=clock, sleep=clock.sleep),
    )
    for _ in range(6):
        assert _public(client).product
    assert (len(gemini.calls), len(mistral_api.calls)) == (4, 2)
    assert clock.slept == []  # its next slot is most of a minute away, so the turn passes on instead of waiting


def test_a_full_minute_moves_the_next_call_on_with_no_call_to_gemini() -> None:
    clock = Clock()
    client, gemini, mistral = _client(clock)
    _fill_geminis_minute(client)
    assert _public(client).product
    assert (len(gemini.calls), len(mistral.calls)) == (0, 1)
    assert clock.slept == []  # the next slot is a minute away, so waiting would only lose time
    outcomes = [(r.provider, r.outcome) for r in client.call_log.records]  # type: ignore[attr-defined]
    assert outcomes.count((Provider.GEMINI, "rate_limited")) == 1
    assert client.health.state(Provider.GEMINI) == "ok"  # no 429 came back, so it stays healthy


def test_a_call_waits_when_a_slot_opens_in_time() -> None:
    clock = Clock()
    client, gemini, mistral = _client(clock)
    _fill_geminis_minute(client)
    clock.now += 56
    assert _public(client).product
    assert (len(gemini.calls), len(mistral.calls)) == (1, 0)
    assert clock.slept == [pytest.approx(4)]


def test_a_call_moves_on_when_waiting_would_cut_its_attempt_short() -> None:
    """The stage allows 15 s, an attempt may take 9 of them, and a margin of 1 stays free, so a call waits at most 5."""
    clock = Clock()
    client, gemini, mistral = _client(clock)
    _fill_geminis_minute(client)
    clock.now += 54
    assert _public(client).product
    assert (len(gemini.calls), len(mistral.calls)) == (0, 1)
    assert clock.slept == []


def test_a_wait_gives_way_at_exactly_a_full_attempts_time() -> None:
    clock = Clock()
    client, gemini, _ = _client(clock)
    _fill_geminis_minute(client)
    clock.now += 55
    assert _public(client).product
    assert len(gemini.calls) == 1
    assert clock.slept == [pytest.approx(5)]


def _late_for_mistral(clock: Clock, late_by: float) -> tuple[WrapperLLMClient, FakeAdapter]:
    """Gemini hangs, then fails. Meanwhile another source takes Mistral's slot, which comes round every half second."""
    config = load_llm_config()
    spacing = RateLimiter(requests_per_second=2, clock=clock, sleep=clock.sleep)

    class Hung(FakeAdapter):
        def call(self, *args: Any, **kwargs: Any) -> Any:
            clock.now += late_by
            assert spacing.try_book(1)
            raise ServerError(Provider.GEMINI, "HTTP 503")

    mistral = FakeAdapter(Provider.MISTRAL, replies=(REPLY,))
    client = WrapperLLMClient(
        config,
        {Provider.GEMINI: Hung(Provider.GEMINI), Provider.MISTRAL: mistral},
        InMemoryCallLog(),
        health=ProviderHealth(clock),
        limiters={Provider.MISTRAL: spacing},
        pool=ModelPool(config.pool, clock=clock, sleep=clock.sleep),
    )
    return client, mistral


def test_a_short_wait_is_taken_however_late_the_call_is() -> None:
    clock = Clock()
    client, mistral = _late_for_mistral(clock, late_by=7)
    assert _public(client).product
    assert len(mistral.calls) == 1
    assert clock.slept == [pytest.approx(0.5)]


def test_a_short_wait_still_keeps_to_the_stages_limit() -> None:
    clock = Clock()
    client, mistral = _late_for_mistral(clock, late_by=13.9)
    with pytest.raises(LLMUnavailable):
        _public(client)
    assert mistral.calls == []


def test_a_wait_ends_when_gemini_starts_cooling() -> None:
    """Another source just got a 429, so going ahead would only earn another."""
    clock = Clock()
    client, gemini, mistral = _client(clock)
    _fill_geminis_minute(client)
    clock.now += 56
    clock.during_sleep = lambda: client.health.record_failure(Provider.GEMINI, RateLimited(Provider.GEMINI, "HTTP 429"))
    assert _public(client).product
    assert (len(gemini.calls), len(mistral.calls)) == (0, 1)


def test_a_shoppers_call_keeps_the_short_wait() -> None:
    """Only bulk work waits for a full attempt's time: other tasks keep the limiter's own short wait."""
    clock = Clock()
    limiter = RateLimiter(requests_per_minute=5, max_wait_s=0.5, clock=clock, sleep=clock.sleep)
    client, gemini, mistral = _client(clock, limiter)
    _fill_geminis_minute(client)
    clock.now += 59.1  # the slot is 0.9 s away: more than this limiter's wait, less than this stage's 1 s to spare
    assert _public(client, LLMTask.DISCOVER_ALIASES).product
    assert (len(gemini.calls), len(mistral.calls)) == (0, 1)
    assert clock.slept == []


def test_tokens_a_minute_are_kept_too() -> None:
    clock = Clock()
    limiter = RateLimiter(tokens_per_minute=2000, clock=clock, sleep=clock.sleep)
    client, gemini, mistral = _client(clock, limiter)
    _fill_geminis_minute(client, calls=1, tokens=2000)
    _public(client)
    assert (len(gemini.calls), len(mistral.calls)) == (0, 1)


def test_sources_running_together_never_pass_the_limit() -> None:
    """Twelve sources at once: the six whose turn starts at Gemini want it, and only five of them may have it."""
    config = load_llm_config()
    gemini, mistral = FakeAdapter(Provider.GEMINI, replies=(REPLY,)), FakeAdapter(Provider.MISTRAL, replies=(REPLY,))
    client = WrapperLLMClient(
        config,
        {Provider.GEMINI: gemini, Provider.MISTRAL: mistral},
        InMemoryCallLog(),
        limiters={Provider.GEMINI: RateLimiter(requests_per_minute=5)},
    )
    with ThreadPoolExecutor(max_workers=12) as pool:
        answers = list(pool.map(lambda _: _public(client), range(12)))
    assert len(answers) == 12
    assert (len(gemini.calls), len(mistral.calls)) == (5, 7)
