"""No attempt outlives its time, and a 429 moves the call on at once (US-46, F-6).

A provider that keeps sending keep-alive bytes resets httpx's read timer, so a 9 s attempt once ran 27.6 s and the
extract stage timed out (P18's rate-limit drill). The adapter now checks its own deadline as the reply arrives.
A fake transport and a fake clock: no real API is called and nothing waits for real time.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date

import httpx
import pytest
from pydantic import BaseModel

from app.contracts import DataClass, Difficulty, LLMTask, LLMUnavailable, Provider
from app.llm import InMemoryCallLog, WrapperLLMClient, load_llm_config
from app.llm.errors import ProviderTimeout, RateLimited
from app.llm.health import COOLDOWN_MAX_S, COOLDOWN_MIN_S, ProviderHealth
from app.llm.pool import ModelPool, time_limit_s
from app.llm.providers import OpenAICompatibleAdapter
from tests.fakes.llm import FakeAdapter

OK = '{"product": "Nova Book 14"}'
BODY = json.dumps({"choices": [{"message": {"content": OK}}], "usage": {"prompt_tokens": 40, "completion_tokens": 9}})


class Answer(BaseModel):
    product: str


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


class Trickle(httpx.SyncByteStream):
    """A reply that sends one keep-alive byte every `step_s` seconds (on the fake clock), then its body."""

    def __init__(self, clock: Clock, keep_alives: int, step_s: float) -> None:
        self.clock, self.keep_alives, self.step_s = clock, keep_alives, step_s
        self.sent = 0

    def __iter__(self) -> Iterator[bytes]:
        for _ in range(self.keep_alives):
            self.clock.now += self.step_s
            self.sent += 1
            yield b" "
        yield BODY.encode()


def _adapter(clock: Clock, stream: Trickle) -> OpenAICompatibleAdapter:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "application/json"}, stream=stream)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    return OpenAICompatibleAdapter(Provider.OPENROUTER, "https://openrouter.ai/api/v1", "made-up", http, clock=clock)


def test_a_reply_that_trickles_past_its_time_is_cut_off() -> None:
    clock = Clock()
    stream = Trickle(clock, keep_alives=20, step_s=2)
    adapter = _adapter(clock, stream)
    with pytest.raises(ProviderTimeout):
        adapter.call("nvidia/nemotron-3.5-lightning:free", "Extract.", "A page.", 100, timeout_s=9)
    assert stream.sent == 5  # cut off at the first byte past 9 s, not after all 40 s
    assert clock.now - 1000.0 == 10


def test_a_reply_that_finishes_inside_its_time_is_read_whole() -> None:
    clock = Clock()
    raw = _adapter(clock, Trickle(clock, keep_alives=3, step_s=2)).call(
        "nvidia/nemotron-3.5-lightning:free", "Extract.", "A page.", 100, timeout_s=9
    )
    assert raw.text == OK
    assert (raw.input_tokens, raw.output_tokens) == (40, 9)


def _client(clock: Clock, adapters: dict[Provider, FakeAdapter]) -> WrapperLLMClient:
    config = load_llm_config()
    pool = ModelPool(config.pool, clock=clock, sleep=clock.sleep, today=lambda: date(2026, 11, 10))
    return WrapperLLMClient(
        config, dict(adapters), InMemoryCallLog(), health=ProviderHealth(clock), limiters={}, pool=pool
    )


def _public(client: WrapperLLMClient) -> Answer:
    return client.complete(
        LLMTask.EXTRACT_CLAIMS,
        "Extract.",
        "A made-up page.",
        Answer,
        data_class=DataClass.PUBLIC,
        difficulty=Difficulty.LONG,
    )


def test_a_429_moves_the_call_to_the_next_member_at_once() -> None:
    clock = Clock()
    gemini = FakeAdapter(Provider.GEMINI, replies=(RateLimited(Provider.GEMINI, "HTTP 429", {"retry-after": "5"}),))
    mistral = FakeAdapter(Provider.MISTRAL, replies=(OK,))
    client = _client(clock, {Provider.GEMINI: gemini, Provider.MISTRAL: mistral})
    assert _public(client).product == "Nova Book 14"
    assert (len(gemini.calls), len(mistral.calls)) == (1, 1)
    assert clock.slept == []  # no wait between the 429 and the next member
    assert client.health.state(Provider.GEMINI) == "cooling"
    clock.now += COOLDOWN_MIN_S - 1
    assert client.health.state(Provider.GEMINI) == "cooling"
    clock.now += COOLDOWN_MAX_S
    assert client.health.state(Provider.GEMINI) == "ok"


@pytest.mark.parametrize(("keep_alives", "answers"), [(3, True), (20, False)])
def test_a_429_never_makes_the_extract_stage_time_out(keep_alives: int, answers: bool) -> None:
    """Gemini and Mistral answer 429, so the call moves on at once to OpenRouter, which is slow: it trickles for 6 s
    and answers, or trickles for 40 s. Either way the call ends inside the stage's limit, with no wait."""
    clock = Clock()
    limit = time_limit_s(LLMTask.EXTRACT_CLAIMS)
    gemini = FakeAdapter(Provider.GEMINI, replies=(RateLimited(Provider.GEMINI, "HTTP 429"),))
    mistral = FakeAdapter(Provider.MISTRAL, replies=(RateLimited(Provider.MISTRAL, "HTTP 429"),))
    openrouter = _adapter(clock, Trickle(clock, keep_alives=keep_alives, step_s=2))
    config = load_llm_config()
    pool = ModelPool(config.pool, clock=clock, sleep=clock.sleep, today=lambda: date(2026, 11, 10))
    client = WrapperLLMClient(
        config,
        {Provider.GEMINI: gemini, Provider.MISTRAL: mistral, Provider.OPENROUTER: openrouter},
        InMemoryCallLog(),
        health=ProviderHealth(clock),
        limiters={},
        pool=pool,
    )
    started = clock.now
    if answers:
        assert _public(client).product == "Nova Book 14"
    else:
        with pytest.raises(LLMUnavailable):
            _public(client)
    assert clock.now - started < limit
    assert clock.slept == []
    assert (len(gemini.calls), len(mistral.calls)) == (1, 1)
