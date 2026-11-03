"""The LLM client (US-26, US-46): order, fallback, retry, routing by data class, fitting and the call log.

Every provider is a FakeAdapter; no real API is called.
"""

from __future__ import annotations

import json
import logging
import re

import pytest
from pydantic import BaseModel

from app.contracts import PERSONAL_TASKS, DataClass, Difficulty, LLMTask, LLMUnavailable, Provider
from app.llm import InMemoryCallLog, WrapperLLMClient, build_llm_client, load_llm_config
from app.llm.client import allowed_for_class, check_data_class, ordered_models, satisfies_contract, should_retry
from app.llm.config import LLMConfig
from app.llm.errors import (
    AuthFailed,
    NoCredit,
    ProviderError,
    ProviderFailure,
    ProviderTimeout,
    RateLimited,
    ServerError,
)
from app.llm.fitting import estimate_tokens, input_budget_for
from app.llm.health import ProviderHealth
from app.llm.limits import DailyTokenBudget, RateLimiter
from app.llm.output import build_system_prompt
from app.llm.providers import Adapter
from tests.fakes.llm import FakeAdapter, FakeLLMClient

ORDER = [Provider.GROQ, Provider.GEMINI, Provider.OPENROUTER, Provider.CEREBRAS, Provider.MISTRAL]
TRAINING = {Provider.GEMINI, Provider.OPENROUTER, Provider.MISTRAL}  # trains_on_inputs: true in models.yaml
TASK = LLMTask.DISCOVER_ALIASES  # not personal, not chunkable, so every provider may be tried
PUBLIC, SHORT = DataClass.PUBLIC, Difficulty.SHORT
SYSTEM = "You suggest laptop criteria."
OK = '{"product": "Nova Book 14", "claims": []}'


class Answer(BaseModel):
    product: str
    claims: list[str] = []


def _client(
    adapters: dict[Provider, FakeAdapter],
    config: LLMConfig | None = None,
    *,
    health: ProviderHealth | None = None,
    budget: DailyTokenBudget | None = None,
    limiters: dict[Provider, RateLimiter] | None = None,
) -> WrapperLLMClient:
    typed: dict[Provider, Adapter] = dict(adapters)
    return WrapperLLMClient(
        config or load_llm_config(),
        typed,
        InMemoryCallLog(),
        health=health,
        budget=budget,
        limiters=limiters if limiters is not None else {},  # no real waiting in tests
    )


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
    adapters = {p: _failing(p, journal=journal) for p in ORDER[:4]}
    adapters[Provider.MISTRAL] = FakeAdapter(Provider.MISTRAL, journal=journal)
    result = _client(adapters).complete(TASK, SYSTEM, "laptop for coding", Answer, data_class=PUBLIC, difficulty=SHORT)
    assert result.product == "Nova Book 14"
    assert journal == ORDER


def test_order_helpers() -> None:
    config = load_llm_config()
    models = ordered_models(config)
    assert [m.provider for m in models] == ORDER
    for data_class in (DataClass.PRIVATE, DataClass.REDDIT):
        allowed = {m.provider for m in allowed_for_class(config, models, data_class)}
        assert allowed == {Provider.GROQ, Provider.CEREBRAS}
    assert allowed_for_class(config, models, DataClass.PUBLIC) == models
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
    assert client.complete(TASK, SYSTEM, "hi", Answer, data_class=PUBLIC, difficulty=SHORT).product == "Nova Book 14"
    assert len(groq.calls) == 1  # failures are not retried on the same provider
    assert _records(client) == [(Provider.GROQ, outcome, "as_is"), (Provider.GEMINI, "ok", "as_is")]


def test_an_unexpected_adapter_error_also_moves_on() -> None:
    groq = FakeAdapter(Provider.GROQ, replies=(RuntimeError("adapter bug"),))
    client = _client({Provider.GROQ: groq, Provider.GEMINI: FakeAdapter(Provider.GEMINI)})
    assert client.complete(TASK, SYSTEM, "hi", Answer, data_class=PUBLIC, difficulty=SHORT).product == "Nova Book 14"
    assert _records(client)[0] == (Provider.GROQ, "error", "as_is")


def test_invalid_output_is_retried_once_then_the_next_provider() -> None:
    groq = FakeAdapter(Provider.GROQ, replies=("not json at all",))
    gemini = FakeAdapter(Provider.GEMINI)
    client = _client({Provider.GROQ: groq, Provider.GEMINI: gemini})
    assert client.complete(TASK, SYSTEM, "hi", Answer, data_class=PUBLIC, difficulty=SHORT).product == "Nova Book 14"
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
    assert client.complete(TASK, SYSTEM, "hi", Answer, data_class=PUBLIC, difficulty=SHORT).product == "Nova Book 14"
    assert (len(groq.calls), len(gemini.calls)) == (2, 0)


def test_every_provider_failing_raises_llm_unavailable() -> None:
    client = _client({p: _failing(p) for p in ORDER})
    with pytest.raises(LLMUnavailable) as info:
        client.complete(TASK, SYSTEM, "my private words", Answer, data_class=PUBLIC, difficulty=SHORT)
    assert "my private words" not in str(info.value)
    assert len(_records(client)) == 5


def test_a_provider_without_a_key_is_skipped_and_reported_once(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="app.llm.client")
    groq = _failing(Provider.GROQ)
    openrouter = FakeAdapter(Provider.OPENROUTER)
    client = _client({Provider.GROQ: groq, Provider.OPENROUTER: openrouter})  # no gemini or cerebras key
    client.complete(TASK, SYSTEM, "hi", Answer, data_class=PUBLIC, difficulty=SHORT)
    client.complete(TASK, SYSTEM, "hi again", Answer, data_class=PUBLIC, difficulty=SHORT)
    assert [r[0] for r in _records(client)] == [Provider.GROQ, Provider.OPENROUTER] * 2
    assert caplog.text.count("GEMINI_API_KEY is not set") == 1


def test_call_records_carry_the_details() -> None:
    client = _client({Provider.GROQ: FakeAdapter(Provider.GROQ)})
    client.complete(
        TASK, SYSTEM, "laptop for coding", Answer, data_class=PUBLIC, difficulty=SHORT, search_id="srch_abc123def4"
    )
    assert isinstance(client.call_log, InMemoryCallLog)
    (rec,) = client.call_log.records
    assert (rec.task, rec.data_class) == (TASK, DataClass.PUBLIC)
    assert (rec.provider, rec.model) == (Provider.GROQ, "openai/gpt-oss-120b")
    assert rec.search_id == "srch_abc123def4"
    assert (rec.outcome, rec.fitted) == ("ok", "as_is")
    assert rec.input_tokens > 0 and rec.output_tokens > 0
    assert rec.at.tzinfo is not None


def test_the_call_log_line_has_no_prompt_text(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="app.llm.client")
    _client({Provider.GROQ: FakeAdapter(Provider.GROQ)}).complete(
        TASK, "secret system", "secret user", Answer, data_class=PUBLIC, difficulty=SHORT
    )
    assert "provider=groq" in caplog.text and "class=public" in caplog.text and "outcome=ok" in caplog.text
    assert "secret" not in caplog.text


# --- private and Reddit text never reach a provider that trains on it (build rule 8) ------------


PRIVATE_CALLS = [(task, DataClass.PRIVATE) for task in sorted(PERSONAL_TASKS)] + [
    (LLMTask.GENERATE_CRITERIA, DataClass.PRIVATE),
    (LLMTask.EXTRACT_CLAIMS, DataClass.REDDIT),
    (LLMTask.DISCOVER_ALIASES, DataClass.REDDIT),
]


@pytest.mark.parametrize(("task", "data_class"), PRIVATE_CALLS, ids=lambda v: str(v))
def test_private_and_reddit_text_never_reach_a_training_provider(task: LLMTask, data_class: DataClass) -> None:
    trainers = {p: FakeAdapter(p) for p in TRAINING}  # would succeed if they were ever called
    others = {p: _failing(p, RateLimited) for p in ORDER if p not in TRAINING}
    client = _client({**trainers, **others})
    with pytest.raises(LLMUnavailable):
        client.complete(
            task, SYSTEM, "I want something light for my back", Answer, data_class=data_class, difficulty=SHORT
        )
    assert all(a.calls == [] for a in trainers.values())
    assert {r[0] for r in _records(client)} == {Provider.GROQ, Provider.CEREBRAS}


@pytest.mark.parametrize("task", sorted(PERSONAL_TASKS), ids=lambda t: t.value)
@pytest.mark.parametrize("data_class", [DataClass.PUBLIC, DataClass.REDDIT], ids=lambda c: c.value)
def test_a_task_with_the_shoppers_words_must_be_private(task: LLMTask, data_class: DataClass) -> None:
    groq = FakeAdapter(Provider.GROQ)
    client = _client({Provider.GROQ: groq})
    with pytest.raises(ValueError, match="must be private"):
        client.complete(task, SYSTEM, "my words", Answer, data_class=data_class, difficulty=SHORT)
    assert groq.calls == []
    check_data_class(task, DataClass.PRIVATE)  # the only class allowed


def test_generate_criteria_may_be_public() -> None:
    check_data_class(LLMTask.GENERATE_CRITERIA, DataClass.PUBLIC)  # when it sends only our category label


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
    assert (
        client.complete(TASK, SYSTEM, text, Answer, data_class=PUBLIC, difficulty=SHORT, max_output_tokens=500).product
        == "Nova Book 14"
    )
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
    result = client.complete(
        LLMTask.EXTRACT_CLAIMS, SYSTEM, _blocks(200), Answer, data_class=PUBLIC, difficulty=SHORT, max_output_tokens=500
    )

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

    client.complete(TASK, SYSTEM, text, Answer, data_class=PUBLIC, difficulty=SHORT)

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
    assert (
        client.complete(TASK, "rules " * 400, "hi", Answer, data_class=PUBLIC, difficulty=SHORT).product
        == "Nova Book 14"
    )
    assert (len(groq.calls), len(gemini.calls)) == (0, 1)


# --- provider health, budgets and rate limits (US-46) ------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_a_rate_limited_provider_is_skipped_until_its_cool_down_ends() -> None:
    clock = Clock()
    groq = FakeAdapter(Provider.GROQ, replies=(RateLimited(Provider.GROQ, "HTTP 429", {"retry-after": "70"}), OK))
    gemini = FakeAdapter(Provider.GEMINI)
    client = _client({Provider.GROQ: groq, Provider.GEMINI: gemini}, health=ProviderHealth(clock))
    for _ in range(3):
        client.complete(TASK, SYSTEM, "hi", Answer, data_class=PUBLIC, difficulty=SHORT)
    assert (len(groq.calls), len(gemini.calls)) == (1, 3)  # groq is not asked again while it cools down
    clock.now += 71
    client.complete(TASK, SYSTEM, "hi", Answer, data_class=PUBLIC, difficulty=SHORT)
    assert (len(groq.calls), len(gemini.calls)) == (2, 3)  # and comes back after it


@pytest.mark.parametrize("error", [AuthFailed, NoCredit])
def test_a_bad_key_is_never_tried_again_in_the_run(error: type[ProviderFailure]) -> None:
    clock = Clock()
    groq = _failing(Provider.GROQ, error)
    adapters = {Provider.GROQ: groq, Provider.GEMINI: FakeAdapter(Provider.GEMINI)}
    client = _client(adapters, health=ProviderHealth(clock))
    client.complete(TASK, SYSTEM, "hi", Answer, data_class=PUBLIC, difficulty=SHORT)
    clock.now += 86_400
    client.complete(TASK, SYSTEM, "hi", Answer, data_class=PUBLIC, difficulty=SHORT)
    assert len(groq.calls) == 1
    assert client.health.state(Provider.GROQ) == "dead"


def test_three_server_errors_switch_a_provider_off() -> None:
    groq = _failing(Provider.GROQ, ServerError)
    client = _client({Provider.GROQ: groq, Provider.GEMINI: FakeAdapter(Provider.GEMINI)})
    for _ in range(5):
        client.complete(TASK, SYSTEM, "hi", Answer, data_class=PUBLIC, difficulty=SHORT)
    assert len(groq.calls) == 3
    assert client.health.state(Provider.GROQ) == "cooling"


def test_invalid_output_does_not_count_against_a_provider() -> None:
    groq = FakeAdapter(Provider.GROQ, replies=("not json",))
    client = _client({Provider.GROQ: groq, Provider.GEMINI: FakeAdapter(Provider.GEMINI)})
    for _ in range(3):
        client.complete(TASK, SYSTEM, "hi", Answer, data_class=PUBLIC, difficulty=SHORT)
    assert len(groq.calls) == 6  # asked twice every time (the retry), never cooled down
    assert client.health.state(Provider.GROQ) == "ok"


def test_a_spent_daily_budget_skips_the_provider_and_is_logged() -> None:
    groq = FakeAdapter(Provider.GROQ)
    gemini = FakeAdapter(Provider.GEMINI)
    budget = DailyTokenBudget({Provider.GROQ: 50})
    client = _client({Provider.GROQ: groq, Provider.GEMINI: gemini}, budget=budget)
    client.complete(TASK, SYSTEM, "hi", Answer, data_class=PUBLIC, difficulty=SHORT, max_output_tokens=10)
    assert groq.calls == []
    assert _records(client) == [(Provider.GROQ, "skipped_budget", "as_is"), (Provider.GEMINI, "ok", "as_is")]
    assert isinstance(client.call_log, InMemoryCallLog)
    assert client.call_log.records[0].input_tokens == 0  # nothing was sent


def test_every_call_is_counted_against_the_budget() -> None:
    budget = DailyTokenBudget({Provider.GROQ: 1_000_000})
    client = _client({Provider.GROQ: FakeAdapter(Provider.GROQ)}, budget=budget)
    client.complete(TASK, SYSTEM, "hi", Answer, data_class=PUBLIC, difficulty=SHORT)
    assert isinstance(client.call_log, InMemoryCallLog)
    (rec,) = client.call_log.records
    assert budget.remaining(Provider.GROQ) == 1_000_000 - rec.input_tokens - rec.output_tokens


def test_a_full_rate_limiter_moves_on_without_sending() -> None:
    limiter = RateLimiter(tokens_per_minute=10, max_wait_s=0)  # no prompt fits in 10 tokens
    mistral = FakeAdapter(Provider.MISTRAL)
    client = _client({Provider.MISTRAL: mistral}, limiters={Provider.MISTRAL: limiter})
    with pytest.raises(LLMUnavailable):
        client.complete(TASK, SYSTEM, "hi", Answer, data_class=PUBLIC, difficulty=SHORT)
    assert mistral.calls == []
    assert _records(client) == [(Provider.MISTRAL, "rate_limited", "as_is")]
    assert client.health.state(Provider.MISTRAL) == "ok"  # our own limit says nothing about Mistral's health


def test_the_real_client_limits_mistral() -> None:
    client = WrapperLLMClient(load_llm_config(), {})
    limiter = client.limiters[Provider.MISTRAL]
    assert (limiter.requests_per_second, limiter.tokens_per_minute) == (2, 500_000)
    assert set(client.limiters) == {Provider.MISTRAL}
    assert client.budget.remaining(Provider.MISTRAL) == 1_000_000
    assert client.budget.remaining(Provider.GROQ) is None


def test_mistral_invalid_json_is_retried_once() -> None:
    mistral = FakeAdapter(Provider.MISTRAL, replies=('{"product": ', OK))
    client = _client({Provider.MISTRAL: mistral})
    assert client.complete(TASK, SYSTEM, "hi", Answer, data_class=PUBLIC, difficulty=SHORT).product == "Nova Book 14"
    assert _records(client) == [(Provider.MISTRAL, "invalid_output", "as_is"), (Provider.MISTRAL, "ok", "as_is")]


# --- building the real client --------------------------------------------------------------------


def test_build_uses_only_providers_with_a_key() -> None:
    client = build_llm_client(env={"GROQ_API_KEY": "sk-fake-groq", "CEREBRAS_API_KEY": "  "})
    assert set(client.adapters) == {Provider.GROQ}
    assert "sk-fake-groq" not in repr(client.adapters[Provider.GROQ])
    assert satisfies_contract(client) is client


def test_build_applies_the_mistral_settings_from_the_environment() -> None:
    env = {
        "MISTRAL_API_KEY": "sk-fake-mistral",
        "MISTRAL_MODEL": "ministral-3b-latest",
        "MISTRAL_RPS": "0.5",
        "MISTRAL_TPM": "1000",
        "MISTRAL_DAILY_TOKENS": "5000",
    }
    client = build_llm_client(env=env)
    assert set(client.adapters) == {Provider.MISTRAL}
    assert [m.model for m in client.config.models if m.provider is Provider.MISTRAL] == ["ministral-3b-latest"]
    limiter = client.limiters[Provider.MISTRAL]
    assert (limiter.requests_per_second, limiter.tokens_per_minute) == (0.5, 1000)
    assert client.budget.remaining(Provider.MISTRAL) == 5000


# --- the fake client for stage tests -------------------------------------------------------------


def test_fake_llm_client_returns_canned_answers_and_records_calls() -> None:
    fake = FakeLLMClient({TASK: Answer(product="Nova Book 14")})
    assert (
        fake.complete(
            TASK, SYSTEM, "hi", Answer, data_class=PUBLIC, difficulty=SHORT, search_id="srch_abc123def4"
        ).product
        == "Nova Book 14"
    )
    assert [(c.task, c.schema, c.search_id) for c in fake.calls] == [(TASK, "Answer", "srch_abc123def4")]
    with pytest.raises(LLMUnavailable):
        fake.complete(LLMTask.EXTRACT_CLAIMS, SYSTEM, "hi", Answer, data_class=PUBLIC, difficulty=SHORT)

    class Other(BaseModel):
        x: int

    with pytest.raises(TypeError):
        fake.complete(TASK, SYSTEM, "hi", Other, data_class=PUBLIC, difficulty=SHORT)
