"""The pool holds a call's whole reservation while it runs, then books what it really used (US-14).

Groq doesn't charge a call's full requested output, as measured on 9 Nov 2026 (docs/api-setup.md). One test waits a
third of a second of real time, as it needs calls running side by side. No real API is called.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date

import pytest
from pydantic import BaseModel

from app.contracts import DataClass, Difficulty, LLMTask, LLMUnavailable, ModelLimits, Provider
from app.llm import InMemoryCallLog, WrapperLLMClient, load_llm_config
from app.llm.config import LLMConfig
from app.llm.errors import ProviderError, ProviderTimeout
from app.llm.fitting import estimate_tokens
from app.llm.health import ProviderHealth
from app.llm.limits import RateLimiter
from app.llm.output import build_system_prompt
from app.llm.pool import ModelPool
from app.llm.providers import RawCompletion
from app.llm.usage import TokenMeter

SYSTEM = "Extract claims."
GOOD = '{"product": "Nova Book 14"}'
ROOM = 6000  # a member's 8,000 a minute, less the 2,000 kept for shoppers


class Answer(BaseModel):
    product: str


class Clock:
    """A clock whose sleep() moves it on, so a wait takes no real time."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


@dataclass
class Groq:
    """Answers with fixed usage, and can read the pool while a call runs."""

    provider: Provider = Provider.GROQ
    input_tokens: int = 100
    output_tokens: int = 50
    usage_reported: bool = True
    reply: str = GOOD
    error: Exception | None = None
    delay_s: float = 0.0
    during_a_call: list[int] = field(default_factory=list)
    client: WrapperLLMClient | None = None

    def call(
        self, model: str, system: str, user: str, max_output_tokens: int, timeout_s: float, extra: object = None
    ) -> RawCompletion:
        if self.client is not None:
            self.during_a_call.append(_booked(self.client))
        if self.delay_s:
            time.sleep(self.delay_s)
        if self.error is not None:
            raise self.error
        return RawCompletion(
            text=self.reply,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            usage_reported=self.usage_reported,
        )


def _booked(client: WrapperLLMClient) -> int:
    return sum(client.pool.tokens_this_minute(m) for m in client.config.models if client.pool.has(m))


def _client(groq: Groq, clock: Clock, config: LLMConfig | None = None) -> WrapperLLMClient:
    config = config or load_llm_config()
    pool = ModelPool(config.pool, clock=clock, sleep=clock.sleep, today=lambda: date(2026, 11, 9))
    client = WrapperLLMClient(
        config, {Provider.GROQ: groq}, InMemoryCallLog(), health=ProviderHealth(clock), limiters={}, pool=pool
    )
    groq.client = client
    return client


def _text_booking(tokens: int, output: int) -> str:
    system = estimate_tokens(build_system_prompt(SYSTEM, Answer))
    return "a" * ((tokens - output - system - 8) * 3)


def _bulk(client: WrapperLLMClient, tokens: int = 3000, output: int = 500) -> Answer:
    return client.complete(
        LLMTask.EXTRACT_CLAIMS,
        SYSTEM,
        _text_booking(tokens, output),
        Answer,
        data_class=DataClass.REDDIT,
        difficulty=Difficulty.LONG,
        max_output_tokens=output,
    )


def test_a_short_answer_frees_its_unused_tokens_at_once() -> None:
    client = _client(Groq(input_tokens=100, output_tokens=50), Clock())
    _bulk(client, tokens=3000)
    assert _booked(client) == 150


def test_a_running_call_holds_its_whole_reservation() -> None:
    groq = Groq()
    client = _client(groq, Clock())
    _bulk(client, tokens=3000)
    assert groq.during_a_call == [3000]


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(ProviderError(Provider.GROQ, "HTTP 500"), id="a-provider-error"),
        pytest.param(RuntimeError("an adapter bug"), id="an-adapter-bug"),
    ],
)
def test_a_failed_call_frees_all_of_its_tokens(error: Exception) -> None:
    client = _client(Groq(error=error), Clock())
    with pytest.raises(LLMUnavailable):
        _bulk(client, tokens=3000)
    assert _booked(client) == 0


def test_a_timeout_frees_all_of_its_tokens() -> None:
    client = _client(Groq(error=ProviderTimeout(Provider.GROQ, "no reply")), Clock())
    with pytest.raises(LLMUnavailable):
        _bulk(client, tokens=3000)
    assert _booked(client) == 0


def test_invalid_output_books_what_it_really_used() -> None:
    """The model did answer, so its tokens were spent. A retry and each other model add theirs."""
    client = _client(Groq(reply="not json", input_tokens=100, output_tokens=50), Clock())
    with pytest.raises(LLMUnavailable):
        _bulk(client, tokens=1000)
    assert _booked(client) == 150 * 2 * 3  # asked twice on each of the three members


def test_a_reply_with_no_usage_keeps_its_reservation() -> None:
    """A reply with no token counts: the adapter's estimates can't see hidden thinking, so what was held stands."""
    client = _client(Groq(input_tokens=100, output_tokens=50, usage_reported=False), Clock())
    _bulk(client, tokens=3000)
    assert _booked(client) == 3000


def test_a_meter_adds_up_every_attempt_across_retries_and_members() -> None:
    client = _client(Groq(reply="not json", input_tokens=100, output_tokens=50), Clock())
    meter = TokenMeter()
    with meter.counting(), pytest.raises(LLMUnavailable):
        _bulk(client, tokens=1000)
    assert (meter.attempts, meter.tokens) == (6, 150 * 6)


def test_a_meter_counts_what_was_held_when_the_use_is_unknown() -> None:
    client = _client(Groq(usage_reported=False), Clock())
    meter = TokenMeter()
    with meter.counting():
        _bulk(client, tokens=3000)
    assert (meter.attempts, meter.tokens) == (1, 3000)


def test_time_already_spent_comes_off_a_calls_limit() -> None:
    client = _client(Groq(), Clock())
    late = TokenMeter(already_spent_s=14.5)  # the stage's limit is 15, and no call starts in its last second
    with late.counting(), pytest.raises(LLMUnavailable):
        _bulk(client, tokens=1000)
    assert late.attempts == 0
    on_time = TokenMeter(already_spent_s=5)
    with on_time.counting():
        _bulk(client, tokens=1000)
    assert on_time.attempts == 1


def test_a_reply_with_no_text_books_what_it_really_used() -> None:
    """A model that spent everything on thinking sends no text, and Groq still charged for it."""
    used = ProviderError(Provider.GROQ, "the reply had no message text", used_tokens=2470)
    client = _client(Groq(error=used), Clock())
    with pytest.raises(LLMUnavailable):
        _bulk(client, tokens=3000)
    assert _booked(client) == 2470 * 3  # once on each member: a failure isn't retried on the same one


def test_a_reply_with_no_text_or_usage_keeps_its_reservation() -> None:
    unknown = ProviderError(Provider.GROQ, "the reply had no message text", used_tokens=None)
    client = _client(Groq(error=unknown), Clock())
    with pytest.raises(LLMUnavailable):
        _bulk(client, tokens=3000)
    assert _booked(client) == 3000 * 3


def test_freed_tokens_let_a_waiting_call_go_sooner() -> None:
    """Three calls of 5,000 fill the pool by booking alone, but real use leaves room at once for a fourth."""
    clock = Clock()
    client = _client(Groq(input_tokens=100, output_tokens=50), clock)
    for _ in range(4):
        _bulk(client, tokens=ROOM - 1000)
    assert clock.slept == []


def test_with_the_switch_off_the_whole_reservation_stays() -> None:
    base = load_llm_config()
    pool = {**base.pool.model_dump(), "book_real_use": False}
    config = LLMConfig.model_validate(
        {"providers": base.providers, "models": base.models, "routes": base.routes, "pool": pool}
    )
    client = _client(Groq(), Clock(), config)
    _bulk(client, tokens=3000)
    assert _booked(client) == 3000


def test_the_switch_is_on_in_the_config() -> None:
    assert load_llm_config().pool.book_real_use is True


def _hold_every_member(client: WrapperLLMClient) -> None:
    """Each member's whole minute, held by a call that is still running."""
    for member in client.config.models:
        if client.pool.has(member):
            assert client.pool.try_take(member, 8000)


def test_a_call_waits_while_others_are_still_running() -> None:
    """Calls start together, so each member is full of reservations that will shrink. Giving up now would waste them."""
    config = load_llm_config()
    groq = Groq(input_tokens=100, output_tokens=50, delay_s=0.3)
    client = WrapperLLMClient(
        config, {Provider.GROQ: groq}, InMemoryCallLog(), limiters={}, pool=ModelPool(config.pool)
    )
    groq.client = client
    with ThreadPoolExecutor(max_workers=6) as pool:
        answers = list(pool.map(lambda _: _bulk(client, tokens=ROOM - 1000), range(6)))
    assert len(answers) == 6
    assert _booked(client) == 150 * 6


def test_a_call_gives_up_at_its_limit_if_nothing_frees_room() -> None:
    clock = Clock()
    client = _client(Groq(), clock)
    _hold_every_member(client)
    started, left_today = clock.now, client.budget.remaining(Provider.GROQ)
    with pytest.raises(LLMUnavailable):
        _bulk(client, tokens=1000)
    assert client.budget.remaining(Provider.GROQ) == left_today  # a wait holds none of today's tokens
    assert clock.now - started <= 15  # the stage's limit
    assert clock.slept
    assert max(clock.slept) <= 1  # it looks again often, because a rebook wakes nobody
    outcomes = [record.outcome for record in client.call_log.records]  # type: ignore[attr-defined]
    assert outcomes == ["rate_limited"] * 3  # it stops by itself at the limit, and says why for each member


def test_a_shoppers_call_never_waits_on_a_running_one() -> None:
    """Its fallback is quicker than waiting, and a late start could only time out."""
    clock = Clock()
    client = _client(Groq(), clock)
    _hold_every_member(client)
    with pytest.raises(LLMUnavailable):
        client.complete(
            LLMTask.DETECT_INTENT,
            SYSTEM,
            "a made-up request",
            Answer,
            data_class=DataClass.PRIVATE,
            difficulty=Difficulty.SHORT,
        )
    assert clock.slept == []


def test_a_call_gives_up_at_once_when_no_shrink_could_help() -> None:
    """Each member has really used 3,600, as a full-size thread does. 4,000 more would fit its 8,000, but not above the
    2,000 kept for shoppers, even if its running call used nothing."""
    clock = Clock()
    client = _client(Groq(), clock)
    for member in client.config.models:
        if client.pool.has(member):
            finished = client.pool.try_take(member, 3600)
            assert finished is not None
            client.pool.book_real_use(member, finished, 3600)
            assert client.pool.try_take(member, 50)
    with pytest.raises(LLMUnavailable):
        _bulk(client, tokens=4000)
    assert clock.slept == []


@pytest.mark.parametrize(
    ("running", "finished", "tokens", "keep_free", "may"),
    [
        (8000, 0, 1000, 2000, True),  # a running call could shrink to make room
        (50, 3600, 2400, 2000, True),
        (50, 3600, 4000, 2000, False),  # 3,600 + 4,000 fits, but not with the 2,000 kept free, whatever it uses
        (50, 3600, 4400, 0, True),
        (50, 3600, 4401, 0, False),
        (0, 8000, 100, 0, False),  # nothing is running, so nothing can shrink
    ],
)
def test_when_running_calls_could_free_room(
    running: int, finished: int, tokens: int, keep_free: int, may: bool
) -> None:
    limiter = RateLimiter(tokens_per_minute=8000, clock=Clock())
    if finished:
        done = limiter.try_book(finished)
        assert done is not None
        limiter.rebook(done, finished)
    if running:
        assert limiter.try_book(running)
    assert limiter.running_calls_may_free_room(tokens, keep_free) is may


def test_a_running_call_never_frees_a_request() -> None:
    limiter = RateLimiter(tokens_per_minute=8000, requests_per_minute=1, clock=Clock())
    assert limiter.try_book(100)
    assert limiter.running_calls_may_free_room(100) is False


def test_a_limiter_with_no_token_limit_has_nothing_to_free() -> None:
    limiter = RateLimiter(requests_per_minute=5, clock=Clock())
    assert limiter.try_book(100)
    assert limiter.running_calls_may_free_room(100) is False


def test_a_finished_call_with_no_usage_is_no_longer_running() -> None:
    limiter = RateLimiter(tokens_per_minute=8000, clock=Clock())
    booking = limiter.try_book(8000)
    assert booking is not None
    assert limiter.running_calls_may_free_room(100) is True
    limiter.rebook(booking, None)
    assert limiter.running_calls_may_free_room(100) is False
    assert limiter.tokens_this_minute() == 8000


def test_a_call_gives_up_at_once_when_nothing_is_running() -> None:
    clock = Clock()
    client = _client(Groq(input_tokens=2800, output_tokens=100), clock)
    for _ in range(6):
        _bulk(client, tokens=3000)
    with pytest.raises(LLMUnavailable):
        _bulk(client, tokens=1000)
    assert clock.slept == []


def test_with_the_switch_off_a_call_never_waits_on_a_running_one() -> None:
    base = load_llm_config()
    pool = {**base.pool.model_dump(), "book_real_use": False}
    config = LLMConfig.model_validate(
        {"providers": base.providers, "models": base.models, "routes": base.routes, "pool": pool}
    )
    clock = Clock()
    client = _client(Groq(), clock, config)
    _hold_every_member(client)
    with pytest.raises(LLMUnavailable):
        _bulk(client, tokens=1000)
    assert clock.slept == []


def test_a_failed_call_still_counts_as_a_request() -> None:
    """Its tokens come back, but the provider did get the request, so it stays in requests a minute."""
    base = load_llm_config()
    pool = base.pool.model_dump()
    pool["members"] = [{**m, "requests_per_minute": 1} for m in pool["members"]]
    config = LLMConfig.model_validate(
        {"providers": base.providers, "models": base.models, "routes": base.routes, "pool": pool}
    )
    groq = Groq(error=ProviderError(Provider.GROQ, "HTTP 500"))
    client = _client(groq, Clock(), config)
    with pytest.raises(LLMUnavailable):
        _bulk(client, tokens=1000)
    assert _booked(client) == 0
    for member in config.pool.members:
        limits = next(m for m in config.models if m.model == member.model)
        assert isinstance(limits, ModelLimits)
        assert not client.pool.try_take(limits, 1)


def test_the_reserve_still_holds_with_real_use() -> None:
    """Each member really used 5,800. Another 1,000 would still fit its 8,000, but not above the reserve."""
    clock = Clock()
    client = _client(Groq(input_tokens=2800, output_tokens=100), clock)
    for _ in range(6):
        _bulk(client, tokens=3000)
    assert _booked(client) == 5800 * 3
    with pytest.raises(LLMUnavailable):
        _bulk(client, tokens=1000)
    assert client.complete(
        LLMTask.DETECT_INTENT,
        SYSTEM,
        "a made-up request",
        Answer,
        data_class=DataClass.PRIVATE,
        difficulty=Difficulty.SHORT,
    ).product


def test_a_model_outside_the_pool_is_unaffected() -> None:
    @dataclass
    class Gemini(Groq):
        provider: Provider = Provider.GEMINI

    gemini = Gemini()
    config = load_llm_config()
    pool = ModelPool(config.pool, clock=Clock(), today=lambda: date(2026, 11, 9))
    client = WrapperLLMClient(config, {Provider.GEMINI: gemini}, InMemoryCallLog(), limiters={}, pool=pool)
    assert client.complete(
        LLMTask.EXTRACT_CLAIMS, SYSTEM, "Made-up text.", Answer, data_class=DataClass.PUBLIC, difficulty=Difficulty.LONG
    ).product


def test_tokens_given_back_can_be_used_at_once() -> None:
    limiter = RateLimiter(tokens_per_minute=100, clock=Clock())
    booking = limiter.try_book(100)
    assert booking is not None
    assert not limiter.try_acquire(1)
    assert limiter.seconds_until_room(100) == pytest.approx(60)

    limiter.rebook(booking, 0)
    assert limiter.seconds_until_room(100) == 0
    assert limiter.try_acquire(100)
