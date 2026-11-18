"""Test doubles for the LLM wrapper. No real provider is ever called in tests.

- FakeLLMClient: stands in for the whole wrapper when testing a stage (returns canned answers by task).
- FakeAdapter: stands in for one provider when testing the wrapper itself (scripted replies or failures).
- with_public_route / with_lane_share: the real config, changed for a test of something else (S4-31).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TypeVar

from pydantic import BaseModel

from app.contracts import DataClass, Difficulty, LLMTask, LLMUnavailable, Provider
from app.llm.config import LLMConfig
from app.llm.errors import ProviderError
from app.llm.fitting import estimate_tokens
from app.llm.providers import RawCompletion

T = TypeVar("T", bound=BaseModel)

# The public route before D-76 (18 Nov 2026), when DeepSeek and OpenRouter were on it. The code that handles them is
# unchanged, so tests of their own behaviour (402, a daily request cap, retries) still run on this route.
PRE_D76_PUBLIC_ROUTE: dict[Difficulty, list[Provider]] = {
    Difficulty.SHORT: [Provider.MISTRAL, Provider.DEEPSEEK, Provider.GROQ],
    Difficulty.MEDIUM: [Provider.MISTRAL, Provider.GEMINI, Provider.DEEPSEEK, Provider.GROQ, Provider.OPENROUTER],
    Difficulty.LONG: [Provider.GEMINI, Provider.MISTRAL, Provider.DEEPSEEK, Provider.GROQ, Provider.OPENROUTER],
}


def with_public_route(
    config: LLMConfig, route: Mapping[Difficulty, list[Provider]] = PRE_D76_PUBLIC_ROUTE
) -> LLMConfig:
    """The config with another public route, checked again by its loader's rules."""
    data = config.model_dump()
    data["routes"] = {**data["routes"], DataClass.PUBLIC: dict(route)}
    return LLMConfig.model_validate(data)


def with_lane_share(config: LLMConfig, share: float = 1.0) -> LLMConfig:
    """The config with another Reddit lane share. 1.0 lets Reddit-class calls use a whole minute, for tests of other
    rules (tests/unit/llm/test_reddit_lane.py covers the lane, D-76)."""
    data = config.model_dump()
    data["pool"] = {**data["pool"], "reddit_minute_share": share}
    return LLMConfig.model_validate(data)


# One scripted reply: the text to return, an exception to raise, or a function of the user text.
Reply = str | Exception | Callable[[str], str]


@dataclass
class FakeCall:
    task: LLMTask
    system: str
    user: str
    schema: str
    search_id: str | None
    data_class: DataClass
    difficulty: Difficulty
    max_output_tokens: int = 1024


class FakeLLMClient:
    """Implements LLMClient. Returns the canned answer for the task; raises LLMUnavailable if there is none."""

    def __init__(self, canned: Mapping[LLMTask, BaseModel] | None = None) -> None:
        self.canned = dict(canned or {})
        self.calls: list[FakeCall] = []

    def complete(
        self,
        task: LLMTask,
        system: str,
        user: str,
        schema: type[T],
        *,
        data_class: DataClass,
        difficulty: Difficulty,
        search_id: str | None = None,
        max_output_tokens: int = 1024,
    ) -> T:
        self.calls.append(
            FakeCall(task, system, user, schema.__name__, search_id, data_class, difficulty, max_output_tokens)
        )
        if task not in self.canned:
            raise LLMUnavailable(f"fake: no canned answer for {task.value}")
        answer = self.canned[task]
        if not isinstance(answer, schema):
            raise TypeError(f"fake: canned answer for {task.value} is not a {schema.__name__}")
        return answer


@dataclass
class SentCall:
    model: str
    system: str
    user: str
    max_output_tokens: int
    extra: Mapping[str, object] | None = None


@dataclass
class FakeAdapter:
    """Implements the Adapter protocol with scripted replies.

    `replies` are used in order; the last one repeats. If `context_window` is set, a call whose input plus
    output reserve is over it is rejected the way a real provider would (HTTP 400).
    """

    provider: Provider
    replies: Sequence[Reply] = ('{"product": "Nova Book 14", "claims": []}',)
    context_window: int | None = None
    journal: list[Provider] | None = None  # shared across adapters to check the order of calls
    calls: list[SentCall] = field(default_factory=list)

    def call(
        self,
        model: str,
        system: str,
        user: str,
        max_output_tokens: int,
        timeout_s: float,
        extra: Mapping[str, object] | None = None,
    ) -> RawCompletion:
        self.calls.append(SentCall(model, system, user, max_output_tokens, extra))
        if self.journal is not None:
            self.journal.append(self.provider)
        used = estimate_tokens(system) + estimate_tokens(user)
        if self.context_window is not None and used + max_output_tokens > self.context_window:
            raise ProviderError(self.provider, "HTTP 400 (context length exceeded)")
        reply = self.replies[min(len(self.calls), len(self.replies)) - 1]
        if isinstance(reply, Exception):
            raise reply
        text = reply(user) if callable(reply) else reply
        return RawCompletion(text=text, input_tokens=used, output_tokens=estimate_tokens(text))
