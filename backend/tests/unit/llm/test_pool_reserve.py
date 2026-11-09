"""Bulk extraction keeps room for shoppers: it never takes a member below the reserve (US-14 item 10).

No test here waits for real time, and no real API is called.
"""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import BaseModel

from app.contracts import DataClass, Difficulty, LLMTask, LLMUnavailable, Provider
from app.llm import InMemoryCallLog, WrapperLLMClient, load_llm_config
from app.llm.config import LLMConfig
from app.llm.fitting import estimate_tokens
from app.llm.health import ProviderHealth
from app.llm.output import build_system_prompt
from app.llm.pool import BULK_TASKS, ModelPool
from tests.fakes.llm import FakeAdapter

SYSTEM = "Extract claims."
OK = '{"product": "Nova Book 14"}'
RESERVE = 2000
ROOM = 8000 - RESERVE


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


def _text_booking(tokens: int, output: int) -> str:
    """User text that makes one call book exactly `tokens`."""
    system = estimate_tokens(build_system_prompt(SYSTEM, Answer))
    return "a" * ((tokens - output - system - 8) * 3)


def _client(
    clock: Clock, config: LLMConfig | None = None, adapters: dict[Provider, FakeAdapter] | None = None
) -> tuple[WrapperLLMClient, InMemoryCallLog]:
    config = config or _with_reserve(RESERVE)
    pool = ModelPool(config.pool, clock=clock, sleep=clock.sleep, today=lambda: date(2026, 11, 9))
    log = InMemoryCallLog()
    client = WrapperLLMClient(
        config,
        adapters or {Provider.GROQ: FakeAdapter(Provider.GROQ, replies=(OK,))},
        log,
        health=ProviderHealth(clock),
        limiters={},
        pool=pool,
    )
    return client, log


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


def _shopper(client: WrapperLLMClient) -> Answer:
    return client.complete(
        LLMTask.DETECT_INTENT,
        SYSTEM,
        "a made-up request",
        Answer,
        data_class=DataClass.PRIVATE,
        difficulty=Difficulty.SHORT,
    )


def _booked(client: WrapperLLMClient) -> list[int]:
    return [client.pool.tokens_this_minute(m) for m in client.config.models if client.pool.has(m)]


def _with_reserve(reserve: int) -> LLMConfig:
    base = load_llm_config()
    # Bookings stay whole here, so these tests pin the reserve alone: test_real_use_booking.py has them together.
    pool = {**base.pool.model_dump(), "extract_reserve_tokens_per_minute": reserve, "book_real_use": False}
    return LLMConfig.model_validate(
        {"providers": base.providers, "models": base.models, "routes": base.routes, "pool": pool}
    )


def test_bulk_work_leaves_the_reserve_free() -> None:
    """Each member takes two calls of 3,000. A third would still fit its 8,000, but not above the reserve."""
    client, _ = _client(Clock())
    for _ in range(6):
        _bulk(client)
    assert _booked(client) == [ROOM, ROOM, ROOM]
    with pytest.raises(LLMUnavailable):
        _bulk(client, tokens=1000)


def test_bulk_may_use_every_token_above_the_reserve() -> None:
    client, _ = _client(Clock())
    for _ in range(3):
        _bulk(client, tokens=ROOM)
    assert _booked(client) == [ROOM, ROOM, ROOM]


def test_one_token_over_the_room_has_to_wait() -> None:
    client, _ = _client(Clock())
    for _ in range(3):
        _bulk(client, tokens=ROOM - 800)
    with pytest.raises(LLMUnavailable):
        _bulk(client, tokens=801, output=100)
    _bulk(client, tokens=800, output=100)  # exactly the room left on the next member
    assert sorted(_booked(client)) == [ROOM - 800, ROOM - 800, ROOM]


def test_a_shopper_call_may_use_the_reserve() -> None:
    client, _ = _client(Clock())
    for _ in range(3):
        _bulk(client, tokens=ROOM)
    assert _shopper(client).product == "Nova Book 14"
    assert sum(_booked(client)) > 3 * ROOM


def test_the_reserve_is_never_booked_against_a_call() -> None:
    """It is only checked: a bulk call books its own tokens and no more."""
    client, _ = _client(Clock())
    _bulk(client, tokens=1000)
    assert sorted(_booked(client)) == [0, 0, 1000]


def test_bulk_waits_for_room_above_the_reserve() -> None:
    clock = Clock()
    client, _ = _client(clock)
    for _ in range(3):
        _bulk(client, tokens=ROOM)
    clock.now += 50  # the minute's first calls have 10 seconds left, inside the stage's limit
    assert _bulk(client, tokens=1000).product == "Nova Book 14"
    assert clock.slept
    assert sum(clock.slept) == pytest.approx(10, abs=0.1)


def test_bulk_skips_when_no_room_opens_in_time() -> None:
    clock = Clock()
    client, log = _client(clock)
    for _ in range(3):
        _bulk(client, tokens=ROOM)
    with pytest.raises(LLMUnavailable):
        _bulk(client, tokens=1000)
    assert clock.slept == []  # 60 seconds away, so it does not wait at all
    assert log.records[-1].outcome == "rate_limited"


def test_a_long_input_is_trimmed_under_the_reserve() -> None:
    client, log = _client(Clock())
    blocks = "\n\n".join(f"Made-up paragraph {i} " + "a" * 900 for i in range(40))
    client.complete(
        LLMTask.EXTRACT_CLAIMS,
        SYSTEM,
        blocks,
        Answer,
        data_class=DataClass.REDDIT,
        difficulty=Difficulty.LONG,
        max_output_tokens=500,
    )
    assert log.records[-1].fitted == "trimmed"
    assert max(_booked(client)) <= ROOM


def test_a_model_outside_the_pool_has_no_reserve() -> None:
    clock = Clock()
    adapters = {Provider.GEMINI: FakeAdapter(Provider.GEMINI, replies=(OK,))}
    client, log = _client(clock, adapters=adapters)
    client.complete(
        LLMTask.EXTRACT_CLAIMS,
        SYSTEM,
        "a" * (9000 * 3),  # more than a pool member's whole minute
        Answer,
        data_class=DataClass.PUBLIC,
        difficulty=Difficulty.LONG,
    )
    assert log.records[-1].fitted == "as_is"
    assert log.records[-1].provider is Provider.GEMINI


def test_only_bulk_tasks_keep_the_reserve_free() -> None:
    pool = ModelPool(load_llm_config().pool)
    for task in LLMTask:
        assert pool.keep_free(task) == (RESERVE if task in BULK_TASKS else 0), task


def test_enrichment_is_held_to_the_reserve_too() -> None:
    assert LLMTask.ENRICH_EXTRACT in BULK_TASKS


def test_the_reserve_comes_from_config() -> None:
    assert load_llm_config().pool.extract_reserve_tokens_per_minute == RESERVE
    client, _ = _client(Clock(), _with_reserve(500))
    for _ in range(3):
        _bulk(client, tokens=7500)
    assert _booked(client) == [7500, 7500, 7500]


def test_with_no_reserve_bulk_may_fill_the_minute() -> None:
    client, _ = _client(Clock(), _with_reserve(0))
    for _ in range(3):
        _bulk(client, tokens=8000)
    assert _booked(client) == [8000, 8000, 8000]
