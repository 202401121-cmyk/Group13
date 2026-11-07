"""The model pool (US-46): rotation, per-model health, pacing and daily budgets, with fake providers and a fake clock.

No test here waits for real time, and no real API is called.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date
from threading import Barrier

import pytest
from pydantic import BaseModel

from app.contracts import DataClass, Difficulty, LLMTask, LLMUnavailable, ModelLimits, Provider
from app.llm import InMemoryCallLog, WrapperLLMClient, load_llm_config
from app.llm.config import LLMConfig
from app.llm.errors import (
    AuthFailed,
    ModelForbidden,
    ModelNotFound,
    ProviderError,
    ProviderTimeout,
    RateLimited,
    ServerError,
)
from app.llm.fitting import estimate_tokens
from app.llm.health import COOLDOWN_MAX_S, COOLDOWN_MIN_S, FAILURE_WINDOW_S, ProviderHealth
from app.llm.output import build_system_prompt
from app.llm.pool import ATTEMPT_SHARE, BULK_TASKS, CALL_MARGIN_S, TASK_STAGES, ModelPool, time_limit_s
from app.llm.providers import Adapter, RawCompletion
from tests.fakes.llm import FakeAdapter

BIG, SMALL, QWEN = "openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b"
SYSTEM = "Extract claims."
OK = '{"product": "Nova Book 14", "claims": []}'


class Answer(BaseModel):
    product: str
    claims: list[str] = []


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
class GroqFake:
    """Groq with scripted failures per model. Each model answers OK once its failures are used up."""

    failures: dict[str, list[Exception]] = field(default_factory=dict)
    input_tokens: int | None = None
    output_tokens: int = 10
    reply: str = OK
    provider: Provider = Provider.GROQ
    calls: list[str] = field(default_factory=list)
    sent: list[str] = field(default_factory=list)
    timeouts: list[float] = field(default_factory=list)

    def call(self, model: str, system: str, user: str, max_output_tokens: int, timeout_s: float) -> RawCompletion:
        self.calls.append(model)
        self.sent.append(user)
        self.timeouts.append(timeout_s)
        waiting = self.failures.get(model)
        if waiting:
            raise waiting.pop(0)
        used = estimate_tokens(system) + estimate_tokens(user) if self.input_tokens is None else self.input_tokens
        return RawCompletion(text=self.reply, input_tokens=used, output_tokens=self.output_tokens)


@dataclass
class HangingGroq(GroqFake):
    """A model in `hangs` takes its whole timeout, on the fake clock, before it gives up."""

    clock: Clock = field(default_factory=Clock)
    hangs: frozenset[str] = frozenset()
    hang_s: float | None = None  # how long it takes; None means its whole timeout

    def call(self, model: str, system: str, user: str, max_output_tokens: int, timeout_s: float) -> RawCompletion:
        if model in self.hangs:
            self.calls.append(model)
            self.timeouts.append(timeout_s)
            self.clock.now += timeout_s if self.hang_s is None else self.hang_s
            raise ProviderTimeout(Provider.GROQ, "no reply")
        return super().call(model, system, user, max_output_tokens, timeout_s)


def _config(**limits: int) -> LLMConfig:
    """The real config, with some limits changed on every pool member."""
    base = load_llm_config()
    if not limits:
        return base
    pool = base.pool.model_dump()
    pool["members"] = [{**m, **limits} for m in pool["members"]]
    pool["reddit_tokens_per_search"] = 1
    data = {"providers": base.providers, "models": base.models, "routes": base.routes, "pool": pool}
    return LLMConfig.model_validate(data)


def _client(adapter: Adapter, clock: Clock, config: LLMConfig | None = None) -> WrapperLLMClient:
    config = config or _config()
    pool = ModelPool(config.pool, clock=clock, sleep=clock.sleep, today=lambda: date(2026, 11, 7))
    return WrapperLLMClient(
        config, {adapter.provider: adapter}, InMemoryCallLog(), health=ProviderHealth(clock), limiters={}, pool=pool
    )


def _extract(client: WrapperLLMClient, text: str = "Nova Book 14 is great.", output: int = 1024) -> Answer:
    """Bulk work: a Reddit extraction call, which rotates."""
    return client.complete(
        LLMTask.EXTRACT_CLAIMS,
        SYSTEM,
        text,
        Answer,
        data_class=DataClass.REDDIT,
        difficulty=Difficulty.LONG,
        max_output_tokens=output,
    )


def _ask(client: WrapperLLMClient, output: int = 1024) -> Answer:
    """Not bulk: a shopper is waiting, so the first model is always tried first."""
    return client.complete(
        LLMTask.DETECT_INTENT,
        SYSTEM,
        "gaming laptop under 80k",
        Answer,
        data_class=DataClass.PRIVATE,
        difficulty=Difficulty.SHORT,
        max_output_tokens=output,
    )


def _outcomes(client: WrapperLLMClient) -> list[tuple[str, str]]:
    assert isinstance(client.call_log, InMemoryCallLog)
    return [(r.model, r.outcome) for r in client.call_log.records]


def _member(client: WrapperLLMClient, model: str) -> ModelLimits:
    return next(m for m in client.config.models if m.model == model)


# --- rotation ------------------------------------------------------------------------------------


def test_members_rotate_in_order_across_three_members() -> None:
    groq = GroqFake()
    client = _client(groq, Clock())
    for _ in range(4):
        _extract(client)
    assert groq.calls == [BIG, SMALL, QWEN, BIG]


def test_a_skipped_member_does_not_give_the_next_two_turns() -> None:
    groq = GroqFake({SMALL: [RateLimited(Provider.GROQ, "HTTP 429")]})
    client = _client(groq, Clock())
    for _ in range(4):
        _extract(client)
    assert groq.calls == [BIG, SMALL, QWEN, BIG, QWEN]


def test_interactive_tasks_keep_config_order() -> None:
    groq = GroqFake()
    client = _client(groq, Clock())
    for _ in range(3):
        _ask(client, output=100)
    assert groq.calls == [BIG, BIG, BIG]


def test_a_backup_model_answers_an_interactive_task_when_the_first_is_cooling() -> None:
    groq = GroqFake({BIG: [RateLimited(Provider.GROQ, "HTTP 429")]})
    client = _client(groq, Clock())
    _ask(client)
    _ask(client)
    assert groq.calls == [BIG, SMALL, SMALL]


def test_bulk_tasks_rotate() -> None:
    assert {LLMTask.EXTRACT_CLAIMS, LLMTask.ENRICH_EXTRACT} == BULK_TASKS
    groq = GroqFake()
    client = _client(groq, Clock())
    for _ in range(3):
        client.complete(
            LLMTask.ENRICH_EXTRACT, SYSTEM, "A thread.", Answer, data_class=DataClass.REDDIT, difficulty=Difficulty.LONG
        )
    assert groq.calls == [BIG, SMALL, QWEN]


def test_public_bulk_work_keeps_its_chain() -> None:
    clock = Clock()
    config = _config()
    adapters: dict[Provider, Adapter] = {p: FakeAdapter(p, replies=(OK,)) for p in (Provider.GEMINI, Provider.MISTRAL)}
    pool = ModelPool(config.pool, clock=clock, sleep=clock.sleep)
    client = WrapperLLMClient(config, adapters, InMemoryCallLog(), limiters={}, pool=pool)
    for _ in range(3):
        client.complete(
            LLMTask.ENRICH_EXTRACT, SYSTEM, "A page.", Answer, data_class=DataClass.PUBLIC, difficulty=Difficulty.LONG
        )
    assert isinstance(client.call_log, InMemoryCallLog)
    assert [r.provider for r in client.call_log.records] == [Provider.GEMINI] * 3


def test_a_route_mixing_pool_members_and_others_rotates_only_the_members() -> None:
    clock = Clock()
    config = _config()
    pool = ModelPool(config.pool, clock=clock, sleep=clock.sleep)
    mistral = next(m for m in config.models if m.provider is Provider.MISTRAL)
    members = [m for m in config.models if m.provider is Provider.GROQ]
    order = [mistral, *members]
    pool.order(order, DataClass.PUBLIC, rotate=True)
    turned = pool.order(order, DataClass.PUBLIC, rotate=True)
    assert [m.model for m in turned] == [mistral.model, SMALL, QWEN, BIG]


def test_a_shoppers_call_does_not_move_the_rotation() -> None:
    groq = GroqFake({BIG: [RateLimited(Provider.GROQ, "HTTP 429")]})
    client = _client(groq, Clock())
    _ask(client)  # BIG is rate limited, so SMALL answers: that is not a turn in the rotation
    client.complete(
        LLMTask.EXTRACT_CLAIMS, SYSTEM, "A note.", Answer, data_class=DataClass.PRIVATE, difficulty=Difficulty.LONG
    )
    assert groq.calls == [BIG, SMALL, SMALL]  # the rotation starts at BIG, which is cooling, so SMALL


def test_a_model_outside_the_pool_does_not_reset_the_rotation() -> None:
    clock = Clock()
    config = _config()
    pool = ModelPool(config.pool, clock=clock, sleep=clock.sleep)
    members = [m for m in config.models if m.provider is Provider.GROQ]
    mistral = next(m for m in config.models if m.provider is Provider.MISTRAL)
    pool.order(members, DataClass.PUBLIC, rotate=True)
    pool.served(mistral, DataClass.PUBLIC)
    assert pool.order(members, DataClass.PUBLIC, rotate=True)[0].model == SMALL


def test_parallel_extractions_start_on_different_members() -> None:
    clock = Clock()
    config = _config()
    pool = ModelPool(config.pool, clock=clock, sleep=clock.sleep)
    members = [m for m in config.models if m.provider is Provider.GROQ]

    def first_choice(_: int) -> str:
        return pool.order(members, DataClass.REDDIT, rotate=True)[0].model

    with ThreadPoolExecutor(max_workers=9) as threads:
        chosen = Counter(threads.map(first_choice, range(9)))
    assert chosen == {BIG: 3, SMALL: 3, QWEN: 3}


# --- health, per member --------------------------------------------------------------------------


@pytest.mark.parametrize(("asked", "cools_for"), [("5", 60.0), ("90", 90.0), ("600", 120.0)])
def test_a_rate_limited_member_cools_60_to_120_seconds_and_is_skipped(asked: str, cools_for: float) -> None:
    assert COOLDOWN_MIN_S == 60 and COOLDOWN_MAX_S == 120
    clock = Clock()
    groq = GroqFake({BIG: [RateLimited(Provider.GROQ, "HTTP 429", {"retry-after": asked})]})
    client = _client(groq, clock)
    _extract(client)
    assert groq.calls == [BIG, SMALL]
    clock.now += cools_for - 1
    assert client.health.state(Provider.GROQ, BIG) == "cooling"
    clock.now += 1
    assert client.health.state(Provider.GROQ, BIG) == "ok"


def test_a_member_comes_back_after_its_cool_down() -> None:
    clock = Clock()
    groq = GroqFake({BIG: [RateLimited(Provider.GROQ, "HTTP 429")]})
    client = _client(groq, clock)
    _ask(client)
    clock.now += COOLDOWN_MAX_S
    _ask(client)
    assert groq.calls == [BIG, SMALL, BIG]


def test_one_member_cooling_leaves_the_others_working() -> None:
    groq = GroqFake({BIG: [RateLimited(Provider.GROQ, "HTTP 429")]})
    client = _client(groq, Clock())
    _extract(client)
    assert client.health.state(Provider.GROQ, BIG) == "cooling"
    assert client.health.state(Provider.GROQ, SMALL) == "ok"
    assert client.health.state(Provider.GROQ, QWEN) == "ok"


def test_a_slow_member_times_out_and_the_next_answers() -> None:
    clock = Clock()
    groq = HangingGroq(clock=clock, hangs=frozenset({BIG}))
    client = _client(groq, clock)
    started = clock.now
    assert _ask(client).product == "Nova Book 14"
    limit = time_limit_s(LLMTask.DETECT_INTENT)
    assert groq.calls == [BIG, SMALL]
    assert groq.timeouts == [limit * ATTEMPT_SHARE, limit * (1 - ATTEMPT_SHARE)]
    assert clock.now - started < limit  # the next member answered inside the stage's limit
    assert _outcomes(client) == [(BIG, "timeout"), (SMALL, "ok")]


def test_a_slow_member_during_extraction_leaves_time_for_the_next() -> None:
    clock = Clock()
    groq = HangingGroq(clock=clock, hangs=frozenset({BIG}))
    client = _client(groq, clock)
    assert _extract(client).product == "Nova Book 14"
    assert groq.timeouts[0] == time_limit_s(LLMTask.EXTRACT_CLAIMS) * ATTEMPT_SHARE
    assert groq.calls == [BIG, SMALL]


def test_the_first_attempt_is_long_enough_for_the_real_calls_we_have_seen() -> None:
    assert time_limit_s(LLMTask.DETECT_INTENT) * ATTEMPT_SHARE > 1.9  # Sprint 1's slowest was 1.8 s
    assert time_limit_s(LLMTask.GENERATE_CRITERIA) * ATTEMPT_SHARE > 5.2  # and 5.1 s


def test_no_call_starts_in_the_last_moments_of_the_stage_limit() -> None:
    clock = Clock()
    groq = HangingGroq(clock=clock, hangs=frozenset({BIG, SMALL, QWEN}))
    client = _client(groq, clock)
    with pytest.raises(LLMUnavailable):
        _ask(client)
    assert groq.calls == [BIG, SMALL]  # the third would start after the limit, for an answer nobody reads


def test_no_call_starts_within_a_second_of_the_limit() -> None:
    clock = Clock()
    limit = time_limit_s(LLMTask.DETECT_INTENT)
    groq = HangingGroq(clock=clock, hangs=frozenset({BIG}), hang_s=limit - CALL_MARGIN_S + 0.1)
    client = _client(groq, clock)
    with pytest.raises(LLMUnavailable):
        _ask(client)
    assert groq.calls == [BIG]


def test_a_wait_never_runs_into_the_last_second() -> None:
    clock = Clock()
    groq = GroqFake()
    client = _client(groq, clock, _config(requests_per_minute=1))
    for _ in range(3):
        _extract(client)
    limit = time_limit_s(LLMTask.EXTRACT_CLAIMS)
    clock.now += 60 - limit + CALL_MARGIN_S - 0.1  # the first slot opens 0.1 s into the last second
    with pytest.raises(LLMUnavailable):
        _extract(client)
    assert clock.slept == []


def test_a_timeout_we_cut_short_does_not_count_against_the_member() -> None:
    clock = Clock()
    groq = HangingGroq(clock=clock, hangs=frozenset({BIG, SMALL}))
    client = _client(groq, clock)
    for _ in range(3):
        with pytest.raises(LLMUnavailable):
            _ask(client)
        clock.now += 1
    assert client.health.state(Provider.GROQ, BIG) == "cooling"  # it had a fair time, three times
    assert client.health.state(Provider.GROQ, SMALL) == "ok"  # it only ever got what was left


def test_a_failing_member_trips_the_breaker() -> None:
    failures: list[Exception] = [ProviderError(Provider.GROQ, "HTTP 400") for _ in range(3)]
    groq = GroqFake({BIG: failures})
    client = _client(groq, Clock())
    for _ in range(4):
        _ask(client)
    assert groq.calls.count(BIG) == 3
    assert client.health.state(Provider.GROQ, BIG) == "cooling"


def test_failures_outside_the_window_do_not_trip_the_breaker() -> None:
    clock = Clock()
    groq = GroqFake({BIG: [ProviderError(Provider.GROQ, "HTTP 400") for _ in range(3)]})
    client = _client(groq, clock)
    for _ in range(3):
        _ask(client, output=100)
        clock.now += FAILURE_WINDOW_S + 1
    assert client.health.state(Provider.GROQ, BIG) == "ok"


def test_a_server_error_cools_a_member_at_once() -> None:
    groq = GroqFake({BIG: [ServerError(Provider.GROQ, "HTTP 503")]})
    client = _client(groq, Clock())
    _ask(client)
    assert client.health.state(Provider.GROQ, BIG) == "cooling"


def test_a_bad_key_marks_every_member_of_that_provider_dead_for_the_run() -> None:
    clock = Clock()
    groq = GroqFake({BIG: [AuthFailed(Provider.GROQ, "HTTP 401")]})
    client = _client(groq, clock)
    with pytest.raises(LLMUnavailable):
        _ask(client)
    clock.now += 10_000
    with pytest.raises(LLMUnavailable):
        _ask(client)
    assert groq.calls == [BIG]  # the three share one key, so the other two are never tried
    assert {client.health.state(Provider.GROQ, m) for m in (BIG, SMALL, QWEN)} == {"dead"}


def test_a_withdrawn_model_is_called_once_and_the_pool_carries_on() -> None:
    clock = Clock()
    groq = GroqFake({QWEN: [ModelNotFound(Provider.GROQ, "HTTP 404") for _ in range(10)]})
    client = _client(groq, clock)
    for _ in range(6):
        _extract(client)
        clock.now += COOLDOWN_MAX_S  # long past any cool-down: a tripped breaker would try it again
    assert groq.calls.count(QWEN) == 1
    assert client.health.state(Provider.GROQ, QWEN) == "dead"
    assert client.health.state(Provider.GROQ, BIG) == "ok"
    assert client.health.state(Provider.GROQ, SMALL) == "ok"


def test_a_model_we_may_not_use_is_dead_alone() -> None:
    groq = GroqFake({QWEN: [ModelForbidden(Provider.GROQ, "HTTP 403")]})
    client = _client(groq, Clock())
    for _ in range(4):
        _extract(client)
    assert client.health.state(Provider.GROQ, QWEN) == "dead"
    assert client.health.state(Provider.GROQ, BIG) == "ok"
    assert groq.calls == [BIG, SMALL, QWEN, BIG, SMALL]


def test_a_cut_short_timeout_on_a_trial_call_lets_the_next_trial_through() -> None:
    clock = Clock()
    groq = HangingGroq(clock=clock, hangs=frozenset({BIG, SMALL}))
    client = _client(groq, clock)
    client.health.record_failure(Provider.GROQ, RateLimited(Provider.GROQ, "HTTP 429"), SMALL)
    clock.now += COOLDOWN_MAX_S  # SMALL's next call is a trial, and it only gets what BIG left
    with pytest.raises(LLMUnavailable):
        _ask(client)
    assert client.health.allow(Provider.GROQ, SMALL)  # the trial isn't stuck on


def test_a_busy_trial_lets_the_next_trial_through() -> None:
    clock = Clock()
    groq = GroqFake()
    client = _client(groq, clock, _config(requests_per_minute=1))
    client.health.record_failure(Provider.GROQ, RateLimited(Provider.GROQ, "HTTP 429"), SMALL)
    clock.now += COOLDOWN_MAX_S
    assert client.pool.try_take(_member(client, SMALL), 1)  # SMALL has no room left this minute
    _ask(client)
    _ask(client)  # BIG is out of requests, SMALL's trial finds no room, so QWEN answers
    assert groq.calls == [BIG, QWEN]
    assert client.health.allow(Provider.GROQ, SMALL)  # the trial that never went out isn't stuck on


def test_a_skipped_member_gets_its_tokens_back() -> None:
    clock = Clock()
    client = _client(GroqFake({BIG: [RateLimited(Provider.GROQ, "HTTP 429")]}), clock)
    _ask(client)
    left = client.pool.tokens_left_today(_member(client, BIG))
    _ask(client)  # BIG is cooling: its plan is held, then given back
    assert client.pool.tokens_left_today(_member(client, BIG)) == left


def test_a_busy_member_gets_its_tokens_back() -> None:
    clock = Clock()
    client = _client(GroqFake(input_tokens=100, output_tokens=0), clock, _config(requests_per_minute=1))
    _ask(client)
    left = client.pool.tokens_left_today(_member(client, BIG))
    _ask(client)  # BIG has no room, so SMALL answers
    assert client.pool.tokens_left_today(_member(client, BIG)) == left


@dataclass
class TogetherGroq(GroqFake):
    """Holds every call until three are in flight, so they really do run at the same time."""

    gate: Barrier = field(default_factory=lambda: Barrier(3, timeout=10))

    def call(self, model: str, system: str, user: str, max_output_tokens: int, timeout_s: float) -> RawCompletion:
        self.gate.wait()
        return super().call(model, system, user, max_output_tokens, timeout_s)


def test_three_extractions_at_once_go_to_three_members() -> None:
    groq = TogetherGroq()
    client = _client(groq, Clock())
    with ThreadPoolExecutor(max_workers=3) as threads:
        answers = list(threads.map(lambda _: _extract(client), range(3)))
    assert len(answers) == 3
    assert Counter(groq.calls) == {BIG: 1, SMALL: 1, QWEN: 1}


# --- pacing --------------------------------------------------------------------------------------


def test_a_token_per_minute_limit_makes_the_next_call_wait_not_fail() -> None:
    clock = Clock()
    groq = GroqFake()
    client = _client(groq, clock, _config(tokens_per_minute=2000))
    for _ in range(3):  # each call plans over 1,000 tokens, so each member takes one
        _extract(client)
    clock.now += 50  # the first slot opens at 60 s
    assert _extract(client).product == "Nova Book 14"
    assert clock.slept == [pytest.approx(10)]
    assert groq.calls == [BIG, SMALL, QWEN, BIG]
    assert "rate_limited" not in {outcome for _, outcome in _outcomes(client)}


def test_a_request_per_minute_limit_makes_the_next_call_wait() -> None:
    clock = Clock()
    groq = GroqFake()
    client = _client(groq, clock, _config(requests_per_minute=1))
    for _ in range(3):
        _extract(client)
    clock.now += 55
    _extract(client)
    assert clock.slept == [pytest.approx(5)]
    assert len(groq.calls) == 4


def test_a_free_member_is_used_before_waiting_for_a_busy_one() -> None:
    clock = Clock()
    groq = GroqFake()
    client = _client(groq, clock, _config(requests_per_minute=1))
    _ask(client)
    _ask(client)
    assert groq.calls == [BIG, SMALL]
    assert clock.slept == []


def test_pacing_counts_input_plus_requested_output() -> None:
    groq = GroqFake(input_tokens=1, output_tokens=1)  # the real use is tiny: the plan is what is counted
    client = _client(groq, Clock())
    _extract(client, "Nova Book 14 is great.", output=3000)
    system = build_system_prompt(SYSTEM, Answer)
    planned = estimate_tokens(system) + estimate_tokens("Nova Book 14 is great.") + 3000
    assert client.pool.tokens_this_minute(_member(client, BIG)) == planned


def test_input_is_fitted_to_the_members_token_rate() -> None:
    groq = GroqFake()
    client = _client(groq, Clock())
    text = "\n\n".join(f"Paragraph {i} about a laptop keyboard and its battery life." for i in range(1500))
    answer = client.complete(
        LLMTask.DETECT_INTENT, SYSTEM, text, Answer, data_class=DataClass.PRIVATE, difficulty=Difficulty.SHORT
    )
    assert answer.product == "Nova Book 14"
    system = build_system_prompt(SYSTEM, Answer)
    (sent,) = groq.sent
    assert estimate_tokens(system) + estimate_tokens(sent) + 1024 <= 8000
    assert _outcomes(client) == [(BIG, "ok")]


def test_a_long_source_is_trimmed_to_one_call_on_a_pool_member() -> None:
    groq = GroqFake()
    client = _client(groq, Clock())
    text = "\n\n".join(f"Paragraph {i} about a laptop keyboard and its battery life." for i in range(400))
    _extract(client, text)
    (sent,) = groq.sent
    assert sent.startswith("Paragraph 0 ") and "Paragraph 399 " not in sent
    assert isinstance(client.call_log, InMemoryCallLog)
    assert client.call_log.records[0].fitted == "trimmed"


def test_a_full_size_source_still_goes_out_when_a_big_answer_is_requested() -> None:
    groq = GroqFake()
    client = _client(groq, Clock())
    text = "\n\n".join("A comment about the laptop keyboard." for _ in range(320))  # about 12,000 characters
    assert _extract(client, text, output=4096).product == "Nova Book 14"
    assert groq.calls == [BIG]


def test_invalid_output_is_retried_once_even_after_waiting_for_a_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.llm.client.time_limit_s", lambda task: 200.0)
    clock = Clock()
    groq = GroqFake(reply="not json")
    client = _client(groq, clock, _config(requests_per_minute=1))
    with pytest.raises(LLMUnavailable):
        _ask(client)
    assert Counter(groq.calls) == {BIG: 2, SMALL: 2, QWEN: 2}
    assert clock.slept  # the retries waited for room


# --- daily budgets -------------------------------------------------------------------------------


def test_a_daily_budget_stops_calls_before_it_is_exceeded() -> None:
    groq = GroqFake(input_tokens=1000, output_tokens=900)
    client = _client(groq, Clock(), _config(tokens_per_day=2000))
    _ask(client)  # uses 1,900, so the next plan of about 1,100 no longer fits
    _ask(client)
    assert groq.calls == [BIG, SMALL]
    assert (BIG, "skipped_budget") in _outcomes(client)
    assert client.pool.tokens_left_today(_member(client, BIG)) == 100


def test_every_budget_spent_ends_in_the_fallback() -> None:
    groq = GroqFake()
    client = _client(groq, Clock(), _config(tokens_per_day=10))
    with pytest.raises(LLMUnavailable):
        _ask(client)
    assert groq.calls == []


def test_pooled_calls_count_towards_the_daily_budget() -> None:
    groq = GroqFake(input_tokens=300, output_tokens=200)
    client = _client(groq, Clock())
    for _ in range(4):
        _extract(client)
    assert client.pool.tokens_left_today(_member(client, BIG)) == 200_000 - 1000
    assert client.pool.tokens_left_today(_member(client, SMALL)) == 200_000 - 500


def test_a_failed_call_still_counts_what_it_sent() -> None:
    groq = GroqFake({BIG: [ProviderTimeout(Provider.GROQ, "no reply")]})
    client = _client(groq, Clock())
    _ask(client)
    system = build_system_prompt(SYSTEM, Answer)
    sent = estimate_tokens(system) + estimate_tokens("gaming laptop under 80k")
    assert client.pool.tokens_left_today(_member(client, BIG)) == 200_000 - sent


def test_parallel_calls_cannot_overshoot_the_daily_budget() -> None:
    clock = Clock()
    pool = ModelPool(_config(tokens_per_day=1000).pool, clock=clock, sleep=clock.sleep)
    member = next(m for m in load_llm_config().models if m.model == BIG)
    with ThreadPoolExecutor(max_workers=10) as threads:
        taken = list(threads.map(lambda _: pool.reserve(member, 300), range(10)))
    assert taken.count(True) == 3
    assert pool.tokens_left_today(member) == 100


def test_a_reservation_is_settled_to_what_the_call_used() -> None:
    clock = Clock()
    pool = ModelPool(_config(tokens_per_day=1000).pool, clock=clock, sleep=clock.sleep)
    member = next(m for m in load_llm_config().models if m.model == BIG)
    assert pool.reserve(member, 600)
    assert not pool.reserve(member, 600)
    pool.settle(member, 600, 100)
    assert pool.tokens_left_today(member) == 900


def test_a_new_utc_day_refills_the_budget() -> None:
    today = {"now": date(2026, 11, 7)}
    clock = Clock()
    pool = ModelPool(_config(tokens_per_day=1000).pool, clock=clock, sleep=clock.sleep, today=lambda: today["now"])
    member = next(m for m in load_llm_config().models if m.model == BIG)
    assert pool.reserve(member, 1000)
    assert pool.tokens_left_today(member) == 0
    today["now"] = date(2026, 11, 8)
    assert pool.tokens_left_today(member) == 1000


# --- time limits ---------------------------------------------------------------------------------


def test_every_task_has_a_stage_time_limit() -> None:
    assert set(TASK_STAGES) == set(LLMTask)
    assert all(time_limit_s(task) > CALL_MARGIN_S for task in LLMTask)


def test_a_model_outside_the_pool_has_no_pacing() -> None:
    clock = Clock()
    pool = ModelPool(_config().pool, clock=clock, sleep=clock.sleep)
    mistral = next(m for m in load_llm_config().models if m.provider is Provider.MISTRAL)
    assert not pool.has(mistral)
    assert pool.token_ceiling(mistral) is None
    assert pool.tokens_left_today(mistral) is None
