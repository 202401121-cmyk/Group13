"""Room for a public page (US-46, S4-29): the client looks, booking nothing, for a public provider that can take an
extraction call now, and waits for one up to a limit. Inside extraction's send window a public page may wait for Groq,
its last resort; outside one it is still skipped at once. Limits still hold, a 429 still moves on, and Reddit and
private text still go to Groq only (D-51). Fake adapters and a fake clock: nothing calls a real API or waits for real.
"""

from __future__ import annotations

import itertools

import pytest

from app.contracts import DataClass, LLMTask, LLMUnavailable, Provider
from app.llm.client import ROOM_LOOK_AGAIN_S
from app.llm.errors import AuthFailed, RateLimited
from app.llm.limits import DailyTokenBudget, RateLimiter
from app.llm.window import send_window
from tests.fakes.llm import FakeAdapter
from tests.unit.llm.test_provider_rotation import (
    BIG,
    MISTRAL,
    OK,
    Clock,
    _bulk,
    _client,
    _fill_groq,
    _groq_models,
    _limited_to_one_a_minute,
    _sent,
)

TASK = LLMTask.EXTRACT_CLAIMS
PAGE_IN, PAGE_OUT = 1_000, 2_000  # a small public page's input, and extraction's public answer size


def _only(*providers: Provider) -> dict[Provider, FakeAdapter]:
    return {p: FakeAdapter(p, replies=(OK,)) for p in providers}


def test_room_now_returns_at_once() -> None:
    clock = Clock()
    client = _client(clock, _only(Provider.GEMINI, Provider.MISTRAL))
    assert client.wait_for_public_room(TASK, PAGE_IN, PAGE_OUT, within_s=30) is True
    assert client.public_room_in(TASK, PAGE_IN, PAGE_OUT) == 0
    assert clock.slept == []


def test_room_waits_for_the_next_free_minute() -> None:
    clock = Clock()
    client = _client(
        clock, _only(Provider.GEMINI, Provider.MISTRAL, Provider.GROQ), limiters=_limited_to_one_a_minute(clock)
    )
    _fill_groq(client, leave=0)
    _bulk(client)
    _bulk(client)  # Gemini's and Mistral's one request this minute; Groq's minute is full
    assert client.public_room_in(TASK, PAGE_IN, PAGE_OUT) == pytest.approx(60)
    assert client.wait_for_public_room(TASK, PAGE_IN, PAGE_OUT, within_s=120) is True
    assert sum(clock.slept) == pytest.approx(60)  # it waited for the minute to turn, and no longer


def test_room_gives_up_at_the_window() -> None:
    clock = Clock()
    client = _client(
        clock, _only(Provider.GEMINI, Provider.MISTRAL, Provider.GROQ), limiters=_limited_to_one_a_minute(clock)
    )
    _fill_groq(client, leave=0)
    _bulk(client)
    _bulk(client)
    assert client.wait_for_public_room(TASK, PAGE_IN, PAGE_OUT, within_s=10) is False
    assert sum(clock.slept) == pytest.approx(10)


def test_room_never_counts_a_cooling_switched_off_or_spent_provider() -> None:
    clock = Clock()
    client = _client(clock, _only(Provider.MISTRAL))
    client.health.record_failure(Provider.MISTRAL, RateLimited(Provider.MISTRAL, "HTTP 429"))
    cooling = client.public_room_in(TASK, PAGE_IN, PAGE_OUT)
    assert cooling is not None and cooling >= ROOM_LOOK_AGAIN_S  # it may come back, so it is looked at again soon

    dead = _client(clock, _only(Provider.MISTRAL))
    dead.health.record_failure(Provider.MISTRAL, AuthFailed(Provider.MISTRAL, "HTTP 401"))
    assert dead.public_room_in(TASK, PAGE_IN, PAGE_OUT) is None
    assert dead.wait_for_public_room(TASK, PAGE_IN, PAGE_OUT, within_s=30) is True  # waiting can't help: it may go

    spent = _client(clock, _only(Provider.MISTRAL), budget=DailyTokenBudget({Provider.MISTRAL: 100}))
    assert spent.public_room_in(TASK, PAGE_IN, PAGE_OUT) is None
    assert clock.slept == []


def test_room_on_groq_keeps_the_shoppers_reserve_and_the_public_share() -> None:
    clock = Clock()
    client = _client(clock, _only(Provider.GROQ))
    reserve = client.config.pool.extract_reserve_tokens_per_minute
    answer = client.config.pool.public_output_tokens
    assert answer is not None
    planned = PAGE_IN + min(PAGE_OUT, answer)  # a public page on Groq asks for the pool's public answer size
    _fill_groq(client, leave=planned + reserve - 1)
    waiting = client.public_room_in(TASK, PAGE_IN, PAGE_OUT)
    assert waiting is not None and waiting > 0  # one token short of leaving the shoppers' reserve free

    roomy = _client(Clock(), _only(Provider.GROQ))
    _fill_groq(roomy, leave=planned + reserve)
    assert roomy.public_room_in(TASK, PAGE_IN, PAGE_OUT) == 0

    for model in _groq_models(roomy):  # the public share of each model's day is spent
        left = roomy.pool.public_tokens_left_today(model)
        assert left is not None and roomy.pool.reserve(model, left, DataClass.PUBLIC)
    assert roomy.public_room_in(TASK, PAGE_IN, PAGE_OUT) is None


def test_a_last_resort_public_page_waits_for_groq_inside_the_window() -> None:
    clock = Clock()
    client = _client(clock, _only(Provider.GROQ))
    _fill_groq(client, leave=0)
    clock.now += 56  # Groq's minute frees up in 4 s: a full attempt's time is still left after that
    with send_window(clock.now + 120):
        assert _bulk(client).product == "Nova Book 14"
    assert sum(clock.slept) == pytest.approx(4, abs=1)  # it waited for that Groq minute, inside the window
    assert [(model, outcome) for _, model, outcome in _sent(client)][-1] == (BIG, "ok")


def test_a_last_resort_wait_that_would_leave_no_full_attempt_is_not_taken() -> None:
    clock = Clock()
    client = _client(clock, _only(Provider.GROQ))
    _fill_groq(client, leave=0)
    clock.now += 50  # free in 10 s: the call would then start with too little of its 15 s left to finish
    with send_window(clock.now + 120), pytest.raises(LLMUnavailable):
        _bulk(client)
    assert sum(clock.slept) < 10


def test_a_last_resort_wait_ends_with_the_window() -> None:
    clock = Clock()
    client = _client(clock, _only(Provider.GROQ))
    _fill_groq(client, leave=0)
    clock.now += 56  # free in 4 s, but the window ends in 2
    with send_window(clock.now + 2), pytest.raises(LLMUnavailable):
        _bulk(client)
    assert sum(clock.slept) <= 2


def test_outside_a_window_a_last_resort_page_is_still_skipped_at_once() -> None:
    clock = Clock()
    client = _client(clock, _only(Provider.GROQ))
    _fill_groq(client, leave=0)
    with pytest.raises(LLMUnavailable):
        _bulk(client)
    assert clock.slept == []
    assert {outcome for _, _, outcome in _sent(client)} == {"rate_limited"}


def test_a_429_inside_the_window_still_moves_on_at_once() -> None:
    clock = Clock()
    gemini = FakeAdapter(Provider.GEMINI, replies=(RateLimited(Provider.GEMINI, "HTTP 429"),))
    client = _client(clock, {Provider.GEMINI: gemini, Provider.MISTRAL: FakeAdapter(Provider.MISTRAL, replies=(OK,))})
    with send_window(clock.now + 120):
        _bulk(client)
    assert [(p, outcome) for p, _, outcome in _sent(client)] == [
        (Provider.GEMINI, "rate_limited"),
        (Provider.MISTRAL, "ok"),
    ]
    assert clock.slept == []  # no waiting on a provider that answered 429


def test_waiting_never_passes_a_minute_or_daily_limit() -> None:
    clock = Clock()
    mistral = {Provider.MISTRAL: RateLimiter(requests_per_minute=1, clock=clock, sleep=clock.sleep)}
    adapter = FakeAdapter(Provider.MISTRAL, replies=(OK,))
    client = _client(clock, {Provider.MISTRAL: adapter}, limiters=mistral)
    sent_at: list[float] = []
    with send_window(clock.now + 600):
        for _ in range(3):
            assert client.wait_for_public_room(TASK, PAGE_IN, PAGE_OUT, within_s=120) is True
            _bulk(client)
            sent_at.append(clock.now)
    assert all(later - earlier >= 60 for earlier, later in itertools.pairwise(sent_at))

    adapter = FakeAdapter(Provider.MISTRAL, replies=(OK,))
    day = _client(Clock(), {Provider.MISTRAL: adapter}, budget=DailyTokenBudget({Provider.MISTRAL: 100}))
    with send_window(clock.now + 600), pytest.raises(LLMUnavailable):
        _bulk(day)  # the page doesn't fit what is left of today's tokens: never sent, however long the window
    assert adapter.calls == []
    assert [outcome for _, _, outcome in _sent(day)] == ["skipped_budget"]


@pytest.mark.parametrize("data_class", [DataClass.REDDIT, DataClass.PRIVATE])
def test_reddit_and_private_text_never_reach_a_public_provider(data_class: DataClass) -> None:
    clock = Clock()
    adapters = _only(Provider.GEMINI, Provider.MISTRAL, Provider.GROQ)
    client = _client(clock, adapters)
    _fill_groq(client, leave=0)
    with send_window(clock.now + 120), pytest.raises(LLMUnavailable):
        _bulk(client, data_class=data_class)
    assert adapters[Provider.GEMINI].calls == [] and adapters[Provider.MISTRAL].calls == []
    assert {p for p, _, _ in _sent(client)} == {Provider.GROQ}
    assert MISTRAL not in [model for _, model, _ in _sent(client)]
