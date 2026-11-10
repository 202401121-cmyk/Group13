"""Fault test (US-26): every provider is down. The wrapper raises LLMUnavailable quickly, never hangs or guesses."""

from __future__ import annotations

from time import monotonic

import pytest
from pydantic import BaseModel

from app.contracts import DataClass, Difficulty, LLMTask, LLMUnavailable, Provider
from app.llm import InMemoryCallLog, WrapperLLMClient, load_llm_config
from app.llm.errors import ProviderTimeout, RateLimited, ServerError
from app.llm.providers import Adapter
from tests.fakes.llm import FakeAdapter


class Claims(BaseModel):
    claims: list[str]


def test_all_providers_down_raises_llm_unavailable_and_logs_every_attempt() -> None:
    adapters: dict[Provider, Adapter] = {
        Provider.GROQ: FakeAdapter(Provider.GROQ, replies=(RateLimited(Provider.GROQ, "HTTP 429"),)),
        Provider.GEMINI: FakeAdapter(Provider.GEMINI, replies=(ServerError(Provider.GEMINI, "HTTP 503"),)),
        Provider.OPENROUTER: FakeAdapter(Provider.OPENROUTER, replies=(ProviderTimeout(Provider.OPENROUTER, "30s"),)),
        Provider.CEREBRAS: FakeAdapter(Provider.CEREBRAS, replies=(ServerError(Provider.CEREBRAS, "HTTP 500"),)),
        Provider.MISTRAL: FakeAdapter(Provider.MISTRAL, replies=(ServerError(Provider.MISTRAL, "HTTP 502"),)),
    }
    log = InMemoryCallLog()
    client = WrapperLLMClient(load_llm_config(), adapters, log, limiters={})

    started = monotonic()
    with pytest.raises(LLMUnavailable, match="extract_claims"):
        client.complete(
            LLMTask.EXTRACT_CLAIMS,
            "Extract claims.",
            "Nova Book 14 has a great keyboard.",
            Claims,
            data_class=DataClass.PUBLIC,
            difficulty=Difficulty.LONG,
        )
    assert monotonic() - started < 1

    # Every provider on the public/long route, in route order: Groq's three models as the last resort, then OpenRouter.
    # Cerebras is not on it.
    assert [(r.provider, r.outcome) for r in log.records] == [
        (Provider.GEMINI, "error"),
        (Provider.MISTRAL, "error"),
        (Provider.GROQ, "rate_limited"),
        (Provider.GROQ, "rate_limited"),
        (Provider.GROQ, "rate_limited"),
        (Provider.OPENROUTER, "timeout"),
    ]
