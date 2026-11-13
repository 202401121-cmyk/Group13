"""DeepSeek as a public fallback, Groq's daily public share, and providers that fail, hang or rate-limit (US-46, D-63).

DeepSeek may train on what we send and runs only on a granted balance (D-11), so it gets public text only, and an HTTP
402 (no balance) switches it off for the run without a retry. Fake adapters and a fake clock: no real API is called.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel

from app.contracts import PERSONAL_TASKS, DataClass, Difficulty, LLMTask, LLMUnavailable, ModelLimits, Provider
from app.llm import InMemoryCallLog, WrapperLLMClient, load_llm_config
from app.llm.config import DEFAULT_CONFIG_PATH, LLMConfig, LLMConfigError
from app.llm.errors import NoCredit, ProviderTimeout, RateLimited, ServerError
from app.llm.health import ProviderHealth
from app.llm.limits import DailyTokenBudget
from app.llm.pool import ModelPool
from tests.fakes.llm import FakeAdapter

OK = '{"product": "Nova Book 14"}'
SYSTEM = "Read the page."
GEMINI, MISTRAL, DEEPSEEK, OPENROUTER = (
    "gemini-3.5-flash-lite",
    "ministral-8b-latest",
    "deepseek-flash",
    "nvidia/nemotron-3.5-lightning:free",
)
BIG, SMALL, QWEN = "openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b"
GROQ = [BIG, SMALL, QWEN]
SHORT_TASK = LLMTask.DISCOVER_ALIASES  # not personal and not bulk work, so it keeps the route's order


class Answer(BaseModel):
    product: str


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _client(
    adapters: dict[Provider, FakeAdapter],
    clock: Clock | None = None,
    budget: DailyTokenBudget[Provider] | None = None,
    config: LLMConfig | None = None,
) -> WrapperLLMClient:
    clock = clock or Clock()
    config = config or load_llm_config()
    pool = ModelPool(config.pool, clock=clock, sleep=clock.sleep, today=lambda: date(2026, 11, 13))
    return WrapperLLMClient(
        config, adapters, InMemoryCallLog(), health=ProviderHealth(clock), budget=budget, limiters={}, pool=pool
    )


def _ok(provider: Provider) -> FakeAdapter:
    return FakeAdapter(provider, replies=(OK,))


def _call(
    client: WrapperLLMClient,
    data_class: DataClass = DataClass.PUBLIC,
    difficulty: Difficulty = Difficulty.SHORT,
    task: LLMTask = SHORT_TASK,
) -> Answer:
    return client.complete(task, SYSTEM, "A made-up page.", Answer, data_class=data_class, difficulty=difficulty)


def _sent(client: WrapperLLMClient) -> list[tuple[str, str]]:
    assert isinstance(client.call_log, InMemoryCallLog)
    return [(r.model, r.outcome) for r in client.call_log.records]


def _names(models: list[ModelLimits]) -> list[str]:
    return [m.model for m in models]


# --- the config ------------------------------------------------------------------------------------------------------


def test_deepseek_is_switched_on_for_public_text_only() -> None:
    config = load_llm_config()
    deepseek = config.provider(Provider.DEEPSEEK)
    assert deepseek.available and deepseek.unavailable_reason is None
    assert deepseek.trains_on_inputs and not deepseek.rotates
    assert deepseek.json_mode  # listed in DeepSeek's API docs (13 Nov 2026)
    assert (deepseek.base_url, deepseek.daily_tokens) == ("https://api.deepseek.com", 500_000)
    assert [m.model for m in config.models if m.provider is Provider.DEEPSEEK] == [DEEPSEEK]


def test_the_routes_are_the_decided_ones() -> None:
    config = load_llm_config()
    p = Provider
    assert config.chain(DataClass.PUBLIC, Difficulty.SHORT) == [p.MISTRAL, p.DEEPSEEK, p.GROQ]
    assert config.chain(DataClass.PUBLIC, Difficulty.MEDIUM) == [p.MISTRAL, p.GEMINI, p.DEEPSEEK, p.GROQ, p.OPENROUTER]
    assert config.chain(DataClass.PUBLIC, Difficulty.LONG) == [p.GEMINI, p.MISTRAL, p.DEEPSEEK, p.GROQ, p.OPENROUTER]
    for data_class in (DataClass.PRIVATE, DataClass.REDDIT):
        assert all(config.chain(data_class, d) == [p.GROQ] for d in Difficulty)


@pytest.mark.parametrize("data_class", ["private", "reddit"])
def test_config_refuses_deepseek_in_a_private_or_reddit_route(tmp_path: Path, data_class: str) -> None:
    data = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    data["routes"][data_class]["short"] = ["groq", "deepseek"]
    path = tmp_path / "models.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(LLMConfigError, match="train on inputs"):
        load_llm_config(path)


# --- who may see what ------------------------------------------------------------------------------------------------


def test_no_private_or_reddit_call_ever_reaches_deepseek() -> None:
    """Only DeepSeek has a key: every private and Reddit call then has no model at all, and DeepSeek is never sent."""
    deepseek = _ok(Provider.DEEPSEEK)
    client = _client({Provider.DEEPSEEK: deepseek})
    for task in LLMTask:
        classes = [DataClass.PRIVATE] if task in PERSONAL_TASKS else [DataClass.PRIVATE, DataClass.REDDIT]
        for data_class in classes:
            for difficulty in Difficulty:
                assert client.order(task, data_class, difficulty) == []
                with pytest.raises(LLMUnavailable):
                    _call(client, data_class, difficulty, task)
    assert deepseek.calls == []


def test_a_public_call_reaches_deepseek_when_mistral_has_no_key() -> None:
    client = _client({Provider.DEEPSEEK: _ok(Provider.DEEPSEEK), Provider.GROQ: _ok(Provider.GROQ)})
    _call(client)
    assert _sent(client) == [(DEEPSEEK, "ok")]


# --- route order -----------------------------------------------------------------------------------------------------


def test_route_order_for_each_length() -> None:
    client = _client({p: _ok(p) for p in Provider})
    assert _names(client.order(SHORT_TASK, DataClass.PUBLIC, Difficulty.SHORT)) == [MISTRAL, DEEPSEEK, *GROQ]
    medium = _names(client.order(SHORT_TASK, DataClass.PUBLIC, Difficulty.MEDIUM))
    assert medium == [MISTRAL, GEMINI, DEEPSEEK, *GROQ, OPENROUTER]
    long = _names(client.order(SHORT_TASK, DataClass.PUBLIC, Difficulty.LONG))
    assert long == [GEMINI, MISTRAL, DEEPSEEK, *GROQ, OPENROUTER]


def test_bulk_work_takes_turns_then_keeps_the_route_order_with_deepseek_before_groq() -> None:
    client = _client({p: _ok(p) for p in Provider})
    first = _names(client.order(LLMTask.EXTRACT_CLAIMS, DataClass.PUBLIC, Difficulty.LONG))
    second = _names(client.order(LLMTask.EXTRACT_CLAIMS, DataClass.PUBLIC, Difficulty.LONG))
    assert first == [GEMINI, MISTRAL, DEEPSEEK, *GROQ, OPENROUTER]
    assert second == [MISTRAL, GEMINI, DEEPSEEK, *GROQ, OPENROUTER]  # DeepSeek takes no turn: it protects its balance


def test_a_public_page_goes_to_deepseek_before_groq_when_gemini_and_mistral_fail() -> None:
    adapters = {
        Provider.GEMINI: FakeAdapter(Provider.GEMINI, replies=(ServerError(Provider.GEMINI, "HTTP 503"),)),
        Provider.MISTRAL: FakeAdapter(Provider.MISTRAL, replies=(ServerError(Provider.MISTRAL, "HTTP 503"),)),
        Provider.DEEPSEEK: _ok(Provider.DEEPSEEK),
        Provider.GROQ: _ok(Provider.GROQ),
    }
    client = _client(adapters)
    _call(client, difficulty=Difficulty.LONG, task=LLMTask.EXTRACT_CLAIMS)
    assert _sent(client) == [(GEMINI, "error"), (MISTRAL, "error"), (DEEPSEEK, "ok")]
    assert adapters[Provider.GROQ].calls == []


# --- HTTP 402 and the daily budget -----------------------------------------------------------------------------------


def test_402_marks_deepseek_dead_and_the_next_provider_answers(caplog: pytest.LogCaptureFixture) -> None:
    deepseek = FakeAdapter(Provider.DEEPSEEK, replies=(NoCredit(Provider.DEEPSEEK, "HTTP 402"),))
    client = _client({Provider.DEEPSEEK: deepseek, Provider.GROQ: _ok(Provider.GROQ)})
    with caplog.at_level(logging.INFO, logger="app.llm"):
        for _ in range(3):
            assert _call(client).product == "Nova Book 14"
    assert len(deepseek.calls) == 1  # never retried, and never tried again this run
    assert _sent(client) == [(DEEPSEEK, "error"), (BIG, "ok"), (BIG, "ok"), (BIG, "ok")]
    assert client.health.state(Provider.DEEPSEEK) == "dead"
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1 and "deepseek" in warnings[0] and "402" in warnings[0]
    assert sum(DEEPSEEK in r.getMessage() and "skipped" in r.getMessage() for r in caplog.records) <= 1


def test_deepseek_out_of_its_daily_budget_is_skipped() -> None:
    deepseek = _ok(Provider.DEEPSEEK)
    budget = DailyTokenBudget({p: (10 if p is Provider.DEEPSEEK else None) for p in Provider})
    client = _client({Provider.DEEPSEEK: deepseek, Provider.GROQ: _ok(Provider.GROQ)}, budget=budget)
    _call(client)
    assert deepseek.calls == []
    assert _sent(client) == [(DEEPSEEK, "skipped_budget"), (BIG, "ok")]


# --- Groq's public share ---------------------------------------------------------------------------------------------


def test_groq_public_share_is_a_quarter_of_each_models_day() -> None:
    pool = load_llm_config().pool
    assert pool.public_daily_share == 0.25
    model = next(m for m in load_llm_config().models if m.model == BIG)
    shared = ModelPool(pool, today=lambda: date(2026, 11, 13))
    assert shared.reserve(model, 50_000, DataClass.PUBLIC)
    assert not shared.reserve(model, 1, DataClass.PUBLIC)  # 25% of 200,000 is held
    assert shared.reserve(model, 150_000, DataClass.PRIVATE)  # the rest is for private and Reddit work
    assert not shared.reserve(model, 1, DataClass.REDDIT)  # and the whole day is now held


def test_groq_public_share_stops_public_use_at_25_percent_while_private_continues() -> None:
    client = _client({Provider.GROQ: _ok(Provider.GROQ)})
    groq_models = [m for m in client.config.models if m.provider is Provider.GROQ]
    for model in groq_models:  # a quarter of each model's day already went to public pages
        assert client.pool.reserve(model, 50_000, DataClass.PUBLIC)
        client.pool.settle(model, 50_000, 50_000, DataClass.PUBLIC)
    with pytest.raises(LLMUnavailable):
        _call(client, DataClass.PUBLIC)
    assert _call(client, DataClass.PRIVATE).product == "Nova Book 14"
    assert _call(client, DataClass.REDDIT).product == "Nova Book 14"
    assert [outcome for _, outcome in _sent(client)][-2:] == ["ok", "ok"]


def test_a_public_call_counts_against_the_share_as_it_does_against_the_day() -> None:
    """A failed call still sent its input, so both count that, and give back the answer it never got."""
    client = _client({Provider.GROQ: FakeAdapter(Provider.GROQ, replies=(ServerError(Provider.GROQ, "HTTP 503"),))})
    model = next(m for m in client.config.models if m.model == BIG)
    with pytest.raises(LLMUnavailable):
        _call(client)
    used_today = 200_000 - (client.pool.tokens_left_today(model) or 0)
    assert 0 < used_today < 1000
    assert client.pool.public_tokens_left_today(model) == 50_000 - used_today


def test_a_private_call_leaves_the_public_share_alone() -> None:
    client = _client({Provider.GROQ: _ok(Provider.GROQ)})
    model = next(m for m in client.config.models if m.model == BIG)
    _call(client, DataClass.PRIVATE)
    assert (client.pool.tokens_left_today(model) or 0) < 200_000
    assert client.pool.public_tokens_left_today(model) == 50_000


# --- failing, slow and rate-limited providers ------------------------------------------------------------------------


def test_failing_slow_and_rate_limited_providers_are_cooled_and_the_next_answers() -> None:
    clock = Clock()
    adapters = {
        Provider.MISTRAL: FakeAdapter(Provider.MISTRAL, replies=(RateLimited(Provider.MISTRAL, "HTTP 429"),)),
        Provider.GEMINI: FakeAdapter(Provider.GEMINI, replies=(ServerError(Provider.GEMINI, "HTTP 500"),)),
        Provider.DEEPSEEK: FakeAdapter(Provider.DEEPSEEK, replies=(ProviderTimeout(Provider.DEEPSEEK, "slow"),)),
        Provider.GROQ: _ok(Provider.GROQ),
    }
    client = _client(adapters, clock)
    for _ in range(3):
        assert _call(client, difficulty=Difficulty.MEDIUM).product == "Nova Book 14"
    health = client.health
    assert health.state(Provider.MISTRAL) == "cooling" and health.state(Provider.GEMINI) == "cooling"
    assert len(adapters[Provider.MISTRAL].calls) == 1 and len(adapters[Provider.GEMINI].calls) == 1
    assert len(adapters[Provider.DEEPSEEK].calls) == 3  # a slow provider cools after 3 failures in a minute
    assert health.state(Provider.DEEPSEEK) == "cooling"
    clock.now += 121  # every cool-down is over (60 to 120 s for 429 and 5xx, 90 s after timeouts)
    assert all(health.state(p) == "ok" for p in (Provider.MISTRAL, Provider.GEMINI, Provider.DEEPSEEK))
