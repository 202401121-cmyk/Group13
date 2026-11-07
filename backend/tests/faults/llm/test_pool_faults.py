"""Fault tests (US-46): when the pool can't take a call, the stage's fallback answers. The search never fails."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from app.contracts import (
    PERSONAL_TASKS,
    DataClass,
    Difficulty,
    IntakeInput,
    LLMTask,
    LLMUnavailable,
    Provider,
    StageContext,
)
from app.intake import IntakeStage
from app.llm import InMemoryCallLog, WrapperLLMClient, load_llm_config
from app.llm.errors import ProviderError, RateLimited
from app.llm.health import ProviderHealth
from app.llm.pool import ModelPool
from app.llm.providers import Adapter
from tests.fakes.llm import FakeAdapter


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


class Answer(BaseModel):
    product: str


def _client(clock: Clock, adapters: dict[Provider, Adapter] | None = None) -> WrapperLLMClient:
    config = load_llm_config()
    adapters = adapters if adapters is not None else {Provider.GROQ: FakeAdapter(Provider.GROQ)}
    pool = ModelPool(config.pool, clock=clock, sleep=clock.sleep)
    return WrapperLLMClient(config, adapters, InMemoryCallLog(), health=ProviderHealth(clock), limiters={}, pool=pool)


def _groq_models(client: WrapperLLMClient) -> list[str]:
    return [m.model for m in client.config.models if m.provider is Provider.GROQ]


def _intake(client: WrapperLLMClient) -> object:
    return IntakeStage(client).run(IntakeInput(raw_query="gaming laptop under 80k"), StageContext("srch_pool_fault"))


def test_all_members_busy_past_the_time_limit_gives_the_fallback() -> None:
    clock = Clock()
    client = _client(clock)
    for model in client.config.models:
        if client.pool.has(model):  # every member has used its whole minute just now
            assert client.pool.try_take(model, 8000)

    intent = IntakeStage(client).run(IntakeInput(raw_query="gaming laptop under 80k"), StageContext("srch_busy"))

    assert (intent.category, intent.detected_by) == ("laptop", "rules")
    groq = client.adapters[Provider.GROQ]
    assert isinstance(groq, FakeAdapter) and groq.calls == []
    assert clock.slept == []  # the slot opens after the limit, so waiting would only lose time


def test_all_members_cooling_gives_the_fallback() -> None:
    clock = Clock()
    client = _client(clock)
    for model in _groq_models(client):
        client.health.record_failure(Provider.GROQ, RateLimited(Provider.GROQ, "HTTP 429"), model)

    intent = IntakeStage(client).run(IntakeInput(raw_query="gaming laptop under 80k"), StageContext("srch_cool"))

    assert (intent.category, intent.detected_by) == ("laptop", "rules")
    groq = client.adapters[Provider.GROQ]
    assert isinstance(groq, FakeAdapter) and groq.calls == []


def test_no_groq_key_gives_the_fallback() -> None:
    intent = IntakeStage(_client(Clock(), {})).run(
        IntakeInput(raw_query="gaming laptop under 80k"), StageContext("srch_nokey")
    )
    assert (intent.category, intent.detected_by) == ("laptop", "rules")


# Every task that may carry each class: a personal task is always private.
PRIVATE_OR_REDDIT = [
    (task, data_class)
    for task in LLMTask
    for data_class in (DataClass.PRIVATE, DataClass.REDDIT)
    if task not in PERSONAL_TASKS or data_class is DataClass.PRIVATE
]


@pytest.mark.parametrize("difficulty", list(Difficulty))
@pytest.mark.parametrize(("task", "data_class"), PRIVATE_OR_REDDIT)
def test_private_and_reddit_tasks_never_reach_a_training_member_or_cerebras(
    task: LLMTask, data_class: DataClass, difficulty: Difficulty
) -> None:
    adapters: dict[Provider, Adapter] = {
        p: FakeAdapter(p, replies=(ProviderError(p, "HTTP 500"),)) for p in Provider
    }  # every provider has a key and every call fails, so the whole chain is tried
    client = _client(Clock(), adapters)

    with pytest.raises(LLMUnavailable):
        client.complete(task, "x", "y", Answer, data_class=data_class, difficulty=difficulty)

    called = {p for p, a in adapters.items() if isinstance(a, FakeAdapter) and a.calls}
    assert called == {Provider.GROQ}
    groq = adapters[Provider.GROQ]
    assert isinstance(groq, FakeAdapter)
    assert {c.model for c in groq.calls} == set(_groq_models(client))
