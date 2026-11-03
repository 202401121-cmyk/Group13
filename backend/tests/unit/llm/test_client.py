"""The LLM client (US-26): order, fallback, retry, personal-task routing, fitting and the call log.

Every provider is a FakeAdapter; no real API is called.
"""

from __future__ import annotations

import json
import logging
import re

import pytest
from pydantic import BaseModel

from app.contracts import PERSONAL_TASKS, LLMTask, LLMUnavailable, Provider
from app.llm import InMemoryCallLog, WrapperLLMClient, build_llm_client, load_llm_config
from app.llm.client import allowed_for_task, ordered_models, satisfies_contract, should_retry
from app.llm.config import LLMConfig
from app.llm.errors import AuthFailed, ProviderError, ProviderFailure, ProviderTimeout, RateLimited, ServerError
from app.llm.fitting import estimate_tokens, input_budget_for
from app.llm.output import build_system_prompt
from app.llm.providers import Adapter
from tests.fakes.llm import FakeAdapter, FakeLLMClient

ORDER = [Provider.GROQ, Provider.GEMINI, Provider.OPENROUTER, Provider.CEREBRAS]
TASK = LLMTask.DISCOVER_ALIASES  # not personal, not chunkable, so every provider may be tried
SYSTEM = "You suggest laptop criteria."
OK = '{"product": "Nova Book 14", "claims": []}'


class Answer(BaseModel):
    product: str
    claims: list[str] = []


def _client(adapters: dict[Provider, FakeAdapter], config: LLMConfig | None = None) -> WrapperLLMClient:
    typed: dict[Provider, Adapter] = dict(adapters)
    return WrapperLLMClient(config or load_llm_config(), typed, InMemoryCallLog())


def _records(client: WrapperLLMClient) -> list[tuple[Provider, str, str]]:
    assert isinstance(client.call_log, InMemoryCallLog)
    return [(r.provider, r.outcome, r.fitted) for r in client.call_log.records]


def _failing(provider: Provider, error: type[ProviderFailure] = ServerError, **kw: object) -> FakeAdapter:
    return FakeAdapter(provider, replies=(error(provider, "simulated"),), **kw)  # type: ignore[arg-type]


def _config_with(**limits: tuple[int, int]) -> LLMConfig:
    """The real config with some models' (context window, max output) replaced."""
    base = load_llm_config()
    models = []
    for m in base.models:
        row = m.model_dump()
        if m.provider.value in limits:
            row["context_window_tokens"], row["max_output_tokens"] = limits[m.provider.value]
        models.append(row)
    return LLMConfig.model_validate({"providers": [p.model_dump() for p in base.providers], "models": models})


# --- order and fallback -------------------------------------------------------------------------


def test_providers_are_tried_in_order() -> None:
    journal: list[Provider] = []
    adapters = {p: _failing(p, journal=journal) for p in ORDER[:3]}
    adapters[Provider.CEREBRAS] = FakeAdapter(Provider.CEREBRAS, journal=journal)
    result = _client(adapters).complete(TASK, SYSTEM, "laptop for coding", Answer)
    assert result.product == "Nova Book 14"
    assert journal == ORDER


def test_order_helpers() -> None:
    models = ordered_models(load_llm_config())
    assert [m.provider for m in models] == ORDER
    personal = allowed_for_task(models, LLMTask.CLASSIFY_REPLY)
    assert Provider.GEMINI not in {m.provider for m in personal}
    assert allowed_for_task(models, LLMTask.EXTRACT_CLAIMS) == models
    assert should_retry("invalid_output", 0)
    assert not should_retry("invalid_output", 1)
    assert not should_retry("rate_limited", 0)


@pytest.mark.parametrize(
    ("error", "outcome"),
    [
        (RateLimited, "rate_limited"),
        (ProviderTimeout, "timeout"),
        (ServerError, "error"),
        (AuthFailed, "error"),
        (ProviderError, "error"),
    ],
)
def test_a_provider_failure_moves_to_the_next_provider(error: type[ProviderFailure], outcome: str) -> None:
    groq = _failing(Provider.GROQ, error)
    gemini = FakeAdapter(Provider.GEMINI)
    client = _client({Provider.GROQ: groq, Provider.GEMINI: gemini})
    assert client.complete(TASK, SYSTEM, "hi", Answer).product == "Nova Book 14"
    assert len(groq.calls) == 1  # failures are not retried on the same provider
    assert _records(client) == [(Provider.GROQ, outcome, "as_is"), (Provider.GEMINI, "ok", "as_is")]


def test_an_unexpected_adapter_error_also_moves_on() -> None:
    groq = FakeAdapter(Provider.GROQ, replies=(RuntimeError("adapter bug"),))
    client = _client({Provider.GROQ: groq, Provider.GEMINI: FakeAdapter(Provider.GEMINI)})
    assert client.complete(TASK, SYSTEM, "hi", Answer).product == "Nova Book 14"
    assert _records(client)[0] == (Provider.GROQ, "error", "as_is")


def test_invalid_output_is_retried_once_then_the_next_provider() -> None:
    groq = FakeAdapter(Provider.GROQ, replies=("not json at all",))
    gemini = FakeAdapter(Provider.GEMINI)
    client = _client({Provider.GROQ: groq, Provider.GEMINI: gemini})
    assert client.complete(TASK, SYSTEM, "hi", Answer).product == "Nova Book 14"
    assert len(groq.calls) == 2
    assert _records(client) == [
        (Provider.GROQ, "invalid_output", "as_is"),
        (Provider.GROQ, "invalid_output", "as_is"),
        (Provider.GEMINI, "ok", "as_is"),
    ]


def test_a_retry_that_succeeds_stays_on_the_same_provider() -> None:
    groq = FakeAdapter(Provider.GROQ, replies=('{"wrong": 1}', OK))
    gemini = FakeAdapter(Provider.GEMINI)
    client = _client({Provider.GROQ: groq, Provider.GEMINI: gemini})
    assert client.complete(TASK, SYSTEM, "hi", Answer).product == "Nova Book 14"
    assert (len(groq.calls), len(gemini.calls)) == (2, 0)


def test_every_provider_failing_raises_llm_unavailable() -> None:
    client = _client({p: _failing(p) for p in ORDER})
    with pytest.raises(LLMUnavailable) as info:
        client.complete(TASK, SYSTEM, "my private words", Answer)
    assert "my private words" not in str(info.value)
    assert len(_records(client)) == 4


def test_a_provider_without_a_key_is_skipped_and_reported_once(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="app.llm.client")
    groq = _failing(Provider.GROQ)
    openrouter = FakeAdapter(Provider.OPENROUTER)
    client = _client({Provider.GROQ: groq, Provider.OPENROUTER: openrouter})  # no gemini or cerebras key
    client.complete(TASK, SYSTEM, "hi", Answer)
    client.complete(TASK, SYSTEM, "hi again", Answer)
    assert [r[0] for r in _records(client)] == [Provider.GROQ, Provider.OPENROUTER] * 2
    assert caplog.text.count("GEMINI_API_KEY is not set") == 1


def test_call_records_carry_the_details() -> None:
    client = _client({Provider.GROQ: FakeAdapter(Provider.GROQ)})
    client.complete(TASK, SYSTEM, "laptop for coding", Answer, search_id="srch_abc123def4")
    assert isinstance(client.call_log, InMemoryCallLog)
    (rec,) = client.call_log.records
    assert rec.task is TASK
    assert (rec.provider, rec.model) == (Provider.GROQ, "openai/gpt-oss-120b")
    assert rec.search_id == "srch_abc123def4"
    assert (rec.outcome, rec.fitted) == ("ok", "as_is")
    assert rec.input_tokens > 0 and rec.output_tokens > 0
    assert rec.at.tzinfo is not None


def test_the_call_log_line_has_no_prompt_text(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="app.llm.client")
    _client({Provider.GROQ: FakeAdapter(Provider.GROQ)}).complete(TASK, "secret system", "secret user", Answer)
    assert "provider=groq" in caplog.text and "outcome=ok" in caplog.text
    assert "secret" not in caplog.text


# --- personal tasks never reach Gemini (build rule 8) -------------------------------------------


@pytest.mark.parametrize("task", sorted(PERSONAL_TASKS), ids=lambda t: t.value)
def test_personal_tasks_never_reach_gemini(task: LLMTask) -> None:
    gemini = FakeAdapter(Provider.GEMINI)  # would succeed if it were ever called
    adapters = {p: _failing(p, RateLimited) for p in ORDER if p is not Provider.GEMINI}
    client = _client({**adapters, Provider.GEMINI: gemini})
    with pytest.raises(LLMUnavailable):
        client.complete(task, SYSTEM, "I want something light for my back", Answer)
    assert gemini.calls == []
    assert Provider.GEMINI not in {r[0] for r in _records(client)}


# --- fitting inside the client -------------------------------------------------------------------


def _blocks(n: int) -> str:
    return "\n\n".join(f"claim-{i}: " + "the battery lasts all day and the keyboard is good " * 3 for i in range(n))


def test_oversized_input_is_trimmed_for_a_normal_task() -> None:
    config = _config_with(groq=(3000, 500))
    groq = FakeAdapter(Provider.GROQ, context_window=3000)
    text = _blocks(200)
    full_system = build_system_prompt(SYSTEM, Answer)
    with pytest.raises(ProviderError):  # the fake really rejects what does not fit
        FakeAdapter(Provider.GROQ, context_window=3000).call("m", full_system, text, 500, 30)

    client = _client({Provider.GROQ: groq}, config)
    assert client.complete(TASK, SYSTEM, text, Answer, max_output_tokens=500).product == "Nova Book 14"
    (sent,) = groq.calls
    assert sent.user.startswith("claim-0:")  # the first (most important) block is kept
    assert len(sent.user) < len(text)
    assert _records(client) == [(Provider.GROQ, "ok", "trimmed")]


def test_oversized_extraction_input_is_chunked_and_merged() -> None:
    def echo_claims(user: str) -> str:
        return json.dumps({"product": "Nova Book 14", "claims": re.findall(r"claim-\d+", user)})

    config = _config_with(groq=(3000, 500))
    groq = FakeAdapter(Provider.GROQ, replies=(echo_claims,), context_window=3000)
    client = _client({Provider.GROQ: groq}, config)
    result = client.complete(LLMTask.EXTRACT_CLAIMS, SYSTEM, _blocks(200), Answer, max_output_tokens=500)

    assert len(groq.calls) > 1  # several chunks, none rejected by the fake
    assert result.claims == [f"claim-{i}" for i in range(200)]  # every chunk's claims, in order
    assert _records(client) == [(Provider.GROQ, "ok", "chunked")]  # one record for the whole attempt


def test_falling_back_to_a_smaller_window_refits_the_input() -> None:
    # Its own windows, so the test does not depend on the models in config/models.yaml.
    config = _config_with(groq=(131072, 8192), cerebras=(8192, 2048))
    groq = _failing(Provider.GROQ, RateLimited)
    cerebras = FakeAdapter(Provider.CEREBRAS, context_window=8192)
    client = _client({Provider.GROQ: groq, Provider.CEREBRAS: cerebras}, config)
    text = _blocks(400)  # about 20,000 tokens: fits groq as is, too big for cerebras

    client.complete(TASK, SYSTEM, text, Answer)

    assert groq.calls[0].user == text
    (sent,) = cerebras.calls
    cerebras_model = next(m for m in config.models if m.provider is Provider.CEREBRAS)
    assert estimate_tokens(sent.system) + estimate_tokens(sent.user) <= input_budget_for(cerebras_model, 1024)
    assert _records(client) == [(Provider.GROQ, "rate_limited", "as_is"), (Provider.CEREBRAS, "ok", "trimmed")]


def test_a_model_whose_window_cannot_hold_the_system_prompt_is_skipped() -> None:
    config = _config_with(groq=(400, 50))
    groq = FakeAdapter(Provider.GROQ)
    gemini = FakeAdapter(Provider.GEMINI)
    client = _client({Provider.GROQ: groq, Provider.GEMINI: gemini}, config)
    assert client.complete(TASK, "rules " * 400, "hi", Answer).product == "Nova Book 14"
    assert (len(groq.calls), len(gemini.calls)) == (0, 1)


# --- building the real client --------------------------------------------------------------------


def test_build_uses_only_providers_with_a_key() -> None:
    client = build_llm_client(env={"GROQ_API_KEY": "sk-fake-groq", "CEREBRAS_API_KEY": "  "})
    assert set(client.adapters) == {Provider.GROQ}
    assert "sk-fake-groq" not in repr(client.adapters[Provider.GROQ])
    assert satisfies_contract(client) is client


# --- the fake client for stage tests -------------------------------------------------------------


def test_fake_llm_client_returns_canned_answers_and_records_calls() -> None:
    fake = FakeLLMClient({TASK: Answer(product="Nova Book 14")})
    assert fake.complete(TASK, SYSTEM, "hi", Answer, search_id="srch_abc123def4").product == "Nova Book 14"
    assert [(c.task, c.schema, c.search_id) for c in fake.calls] == [(TASK, "Answer", "srch_abc123def4")]
    with pytest.raises(LLMUnavailable):
        fake.complete(LLMTask.EXTRACT_CLAIMS, SYSTEM, "hi", Answer)

    class Other(BaseModel):
        x: int

    with pytest.raises(TypeError):
        fake.complete(TASK, SYSTEM, "hi", Other)
