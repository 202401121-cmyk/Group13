"""Bulk work takes turns across the providers that rotate, and every provider has a daily token counter (US-46).

Public text takes turns between Gemini and Mistral; Groq's models are the last resort after both, and OpenRouter the
fallback after them all (its free models allow 50 requests a day). Private and Reddit text rotate across Groq's models
only (D-51).
Fake adapters and a fake clock: no real API is called and nothing waits for real time.
"""

from __future__ import annotations

from contextlib import suppress
from datetime import date
from pathlib import Path

import pytest
from pydantic import BaseModel

from app.contracts import (
    DataClass,
    Difficulty,
    ExtractInput,
    LLMTask,
    LLMUnavailable,
    ModelLimits,
    Provider,
    Source,
    StageContext,
)
from app.extract import GuardedExtractStage
from app.llm import InMemoryCallLog, WrapperLLMClient, load_llm_config
from app.llm.client import default_limiters
from app.llm.config import ENV_OVERRIDES, LLMConfig, with_env_overrides
from app.llm.errors import RateLimited, ServerError
from app.llm.health import ProviderHealth
from app.llm.limits import DailyTokenBudget, RateLimiter
from app.llm.pool import ModelPool
from tests.fakes.llm import FakeAdapter
from tests.fakes.research import review_source

SYSTEM = "Extract claims."
OK = '{"product": "Nova Book 14"}'
GEMINI, MISTRAL, OPENROUTER = "gemini-3.5-flash-lite", "ministral-8b-latest", "nvidia/nemotron-3.5-lightning:free"
BIG, SMALL, QWEN = "openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b"
ROOT = Path(__file__).resolve().parents[4]


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


def _client(
    clock: Clock,
    adapters: dict[Provider, FakeAdapter] | None = None,
    budget: DailyTokenBudget[Provider] | None = None,
    config: LLMConfig | None = None,
    limiters: dict[Provider, RateLimiter] | None = None,
) -> WrapperLLMClient:
    config = config or load_llm_config()
    if adapters is None:
        adapters = {p: FakeAdapter(p, replies=(OK,)) for p in Provider}
    pool = ModelPool(config.pool, clock=clock, sleep=clock.sleep, today=lambda: date(2026, 11, 10))
    return WrapperLLMClient(
        config,
        dict(adapters),
        InMemoryCallLog(),
        health=ProviderHealth(clock),
        budget=budget,
        limiters=limiters or {},
        pool=pool,
    )


def _bulk(
    client: WrapperLLMClient,
    data_class: DataClass = DataClass.PUBLIC,
    output: int = 1024,
    text: str = "A made-up page about the Nova Book 14.",
) -> Answer:
    return client.complete(
        LLMTask.EXTRACT_CLAIMS,
        SYSTEM,
        text,
        Answer,
        data_class=data_class,
        difficulty=Difficulty.LONG,
        max_output_tokens=output,
    )


def _sent(client: WrapperLLMClient) -> list[tuple[Provider, str, str]]:
    assert isinstance(client.call_log, InMemoryCallLog)
    return [(r.provider, r.model, r.outcome) for r in client.call_log.records]


def _models(client: WrapperLLMClient) -> list[str]:
    return [model for _, model, outcome in _sent(client) if outcome == "ok"]


def _groq_models(client: WrapperLLMClient) -> list[ModelLimits]:
    return [m for m in client.config.models if m.provider is Provider.GROQ]


def _fill_groq(client: WrapperLLMClient, leave: int) -> None:
    """Book each Groq model's minute so that only `leave` of its tokens are free."""
    for model in _groq_models(client):
        tokens_per_minute = next(m.tokens_per_minute for m in client.config.pool.members if m.model == model.model)
        assert client.pool.try_take(model, tokens_per_minute - leave) is not None


# --- F-3: rotation across providers ---------------------------------------------------------------


def test_public_bulk_work_takes_turns_between_gemini_and_mistral() -> None:
    client = _client(Clock())
    for _ in range(4):
        _bulk(client)
    assert _models(client) == [GEMINI, MISTRAL, GEMINI, MISTRAL]
    order = [m.model for m in client.order(LLMTask.EXTRACT_CLAIMS, DataClass.PUBLIC, Difficulty.LONG)]
    assert order[2:] == [BIG, SMALL, QWEN, OPENROUTER]  # Groq's models as the last resort, then OpenRouter, always last


def test_rotation_order_is_the_same_for_the_same_calls() -> None:
    runs = []
    for _ in range(2):
        client = _client(Clock())
        for data_class in (DataClass.PUBLIC, DataClass.REDDIT, DataClass.PUBLIC, DataClass.PRIVATE, DataClass.PUBLIC):
            _bulk(client, data_class)
        runs.append(_models(client))
    assert runs[0] == runs[1] == [GEMINI, BIG, MISTRAL, BIG, GEMINI]  # one turn order per data class


def test_a_route_mixing_providers_gives_turns_to_gemini_and_mistral_only() -> None:
    client = _client(Clock())
    first = client.order(
        LLMTask.EXTRACT_CLAIMS, DataClass.PUBLIC, Difficulty.MEDIUM
    )  # mistral, gemini, groq, openrouter
    second = client.order(LLMTask.EXTRACT_CLAIMS, DataClass.PUBLIC, Difficulty.MEDIUM)
    assert [m.model for m in first] == [MISTRAL, GEMINI, BIG, SMALL, QWEN, OPENROUTER]
    assert [m.model for m in second] == [GEMINI, MISTRAL, BIG, SMALL, QWEN, OPENROUTER]  # Groq takes no public turn


def test_openrouter_is_used_only_when_gemini_mistral_and_groq_have_no_room() -> None:
    clock = Clock()
    paced = (Provider.GEMINI, Provider.MISTRAL)
    full = {p: RateLimiter(requests_per_minute=1, clock=clock, sleep=clock.sleep) for p in paced}
    client = _client(clock, limiters=full)
    _fill_groq(client, leave=0)  # every Groq model's minute is taken
    _bulk(client)  # Gemini takes its one request this minute
    _bulk(client)  # Mistral takes its one
    assert _models(client) == [GEMINI, MISTRAL]
    _bulk(client)  # none has room for a minute, more than the stage can wait: OpenRouter, the fallback, answers
    assert _models(client) == [GEMINI, MISTRAL, OPENROUTER]
    assert clock.slept == []
    clock.now += 61  # both have room again. OpenRouter took no turn: the turn the last call claimed was Gemini's
    _bulk(client)
    _bulk(client)
    assert _models(client) == [GEMINI, MISTRAL, OPENROUTER, MISTRAL, GEMINI]


def test_openrouter_stops_at_its_50_requests_a_day() -> None:
    """With Gemini and Mistral out of the way, OpenRouter answers 50 calls, then the next is never sent."""
    openrouter = FakeAdapter(Provider.OPENROUTER, replies=(OK,))
    client = _client(Clock(), {Provider.OPENROUTER: openrouter})
    for _ in range(50):
        assert _bulk(client).product
    with pytest.raises(LLMUnavailable):
        _bulk(client)
    assert len(openrouter.calls) == 50
    assert _sent(client)[-1] == (Provider.OPENROUTER, OPENROUTER, "skipped_budget")


def test_an_openrouter_call_held_but_never_sent_costs_none_of_its_50() -> None:
    """Only a request that goes out counts. One stopped first, cooling or with no room in its minute, gives it back."""
    clock = Clock()
    openrouter = FakeAdapter(Provider.OPENROUTER, replies=(OK, RateLimited(Provider.OPENROUTER, "HTTP 429"), OK))
    limiter = RateLimiter(requests_per_minute=2, clock=clock, sleep=clock.sleep)
    client = _client(clock, {Provider.OPENROUTER: openrouter}, limiters={Provider.OPENROUTER: limiter})
    assert _bulk(client).product
    with pytest.raises(LLMUnavailable):
        _bulk(client)  # sent, and answered 429: it counts
    assert client.requests.remaining(Provider.OPENROUTER) == 48
    with pytest.raises(LLMUnavailable):
        _bulk(client)  # cooling after the 429: never sent
    clock.now += 125  # the cool-down is over, and the minute's 2 are free again
    assert _bulk(client).product
    assert _bulk(client).product
    with pytest.raises(LLMUnavailable):
        _bulk(client)  # no room in its minute: never sent
    assert len(openrouter.calls) == 4
    assert client.requests.remaining(Provider.OPENROUTER) == 46
    assert clock.slept == []


def _small_openrouter() -> LLMConfig:
    """OpenRouter with a 3,000-token window, so a long page is split into chunks."""
    base = load_llm_config()
    models = [
        {**m.model_dump(), "context_window_tokens": 3000, "max_output_tokens": 500}
        if m.provider is Provider.OPENROUTER
        else m
        for m in base.models
    ]
    return LLMConfig.model_validate(
        {"providers": base.providers, "models": models, "routes": base.routes, "pool": base.pool}
    )


def _long_page() -> str:
    return "\n\n".join(f"claim-{i}: " + "the battery lasts all day and the keyboard is good " * 3 for i in range(200))


def test_a_split_openrouter_call_counts_each_chunk_it_sends() -> None:
    """OpenRouter's 50 a day counts HTTP requests, and a split page sends one per chunk, up to the first that fails."""
    whole = FakeAdapter(Provider.OPENROUTER, replies=(OK,), context_window=3000)
    client = _client(Clock(), {Provider.OPENROUTER: whole}, config=_small_openrouter())
    assert _bulk(client, text=_long_page(), output=500).product
    assert len(whole.calls) > 2  # several chunks, none rejected by the fake
    assert client.requests.remaining(Provider.OPENROUTER) == 50 - len(whole.calls)

    cut = FakeAdapter(
        Provider.OPENROUTER, replies=(OK, ServerError(Provider.OPENROUTER, "HTTP 500")), context_window=3000
    )
    client = _client(Clock(), {Provider.OPENROUTER: cut}, config=_small_openrouter())
    with pytest.raises(LLMUnavailable):
        _bulk(client, text=_long_page(), output=500)
    assert len(cut.calls) == 2  # the second chunk failed, so the rest were never sent
    assert client.requests.remaining(Provider.OPENROUTER) == 48


def test_a_shoppers_task_keeps_the_route_order() -> None:
    config = load_llm_config()
    pool = ModelPool(config.pool)
    models = [
        m
        for provider in config.chain(DataClass.PUBLIC, Difficulty.LONG)
        for m in config.models
        if m.provider is provider
    ]
    route = [GEMINI, MISTRAL, BIG, SMALL, QWEN, OPENROUTER]
    for _ in range(3):
        assert [m.model for m in pool.order(models, DataClass.PUBLIC, rotate=False)] == route


def test_a_member_that_answers_out_of_turn_moves_the_rotation_on() -> None:
    """Gemini's turn is claimed but its answers are no use, so Mistral answers: the next call starts after Mistral."""
    adapters = {p: FakeAdapter(p, replies=(OK,)) for p in Provider}
    adapters[Provider.GEMINI] = FakeAdapter(Provider.GEMINI, replies=("not JSON", "not JSON", OK))
    client = _client(Clock(), adapters)
    _bulk(client)
    _bulk(client)
    assert _models(client) == [MISTRAL, GEMINI]


@pytest.mark.parametrize("data_class", [DataClass.REDDIT, DataClass.PRIVATE])
def test_private_and_reddit_bulk_work_rotate_across_groq_only(data_class: DataClass) -> None:
    client = _client(Clock())
    for _ in range(4):
        _bulk(client, data_class)
    assert {provider for provider, _, _ in _sent(client)} == {Provider.GROQ}
    assert _models(client) == [BIG, SMALL, QWEN, BIG]


@pytest.mark.parametrize("data_class", [DataClass.REDDIT, DataClass.PRIVATE])
def test_no_private_or_reddit_call_reaches_a_training_provider_under_rotation(data_class: DataClass) -> None:
    """Every Groq model fails, and the rotation still never reaches a provider that trains on what it is sent."""
    adapters = {p: FakeAdapter(p, replies=(OK,)) for p in Provider}
    adapters[Provider.GROQ] = FakeAdapter(Provider.GROQ, replies=(ServerError(Provider.GROQ, "HTTP 503"),))
    client = _client(Clock(), adapters)
    for _ in range(3):
        with pytest.raises(LLMUnavailable):
            _bulk(client, data_class)
    config = load_llm_config()
    for provider, adapter in adapters.items():
        if config.provider(provider).trains_on_inputs:
            assert adapter.calls == [], provider


def test_a_public_member_whose_window_the_input_does_not_fit_is_skipped() -> None:
    """Mistral's window here can't hold the prompt and the answer, so its turn goes to the next member."""
    base = load_llm_config()
    models = [
        {**m.model_dump(), "context_window_tokens": 2048, "max_output_tokens": 2048}
        if m.provider is Provider.MISTRAL
        else m
        for m in base.models
    ]
    config = LLMConfig.model_validate(
        {"providers": base.providers, "models": models, "routes": base.routes, "pool": base.pool}
    )
    client = _client(Clock(), config=config)
    for _ in range(3):
        _bulk(client, output=2000)
    assert _models(client) == [GEMINI, GEMINI, GEMINI]  # Mistral's turns pass to Gemini, never to the fallback
    assert all(model != MISTRAL for _, model, _ in _sent(client))  # skipped before anything was sent


# --- F-6: Groq as the last resort for public pages -------------------------------------------------
# Groq doesn't train on what we send, so public text may go to it (D-51). Its minute belongs first to private and Reddit
# work, so a public page goes to Groq only when Gemini and Mistral have no room, and only if Groq has room right now.


def _limited_to_one_a_minute(clock: Clock) -> dict[Provider, RateLimiter]:
    return {
        p: RateLimiter(requests_per_minute=1, clock=clock, sleep=clock.sleep)
        for p in (Provider.GEMINI, Provider.MISTRAL)
    }


def test_public_pages_go_to_groq_only_when_gemini_and_mistral_have_no_room() -> None:
    clock = Clock()
    adapters = {p: FakeAdapter(p, replies=(OK,)) for p in Provider}
    client = _client(clock, adapters, limiters=_limited_to_one_a_minute(clock))
    _bulk(client)  # Gemini's one request this minute
    _bulk(client)  # Mistral's one
    assert adapters[Provider.GROQ].calls == []
    _bulk(client)  # neither has room for a minute: Groq, which has room now, reads the page
    _bulk(client)
    assert _models(client) == [GEMINI, MISTRAL, BIG, BIG]  # its models in their fixed order: the first with room
    assert adapters[Provider.OPENROUTER].calls == []
    assert clock.slept == []


def test_with_gemini_healthy_no_public_page_reaches_groq() -> None:
    clock = Clock()
    adapters = {p: FakeAdapter(p, replies=(OK,)) for p in Provider}
    mistral_once = {Provider.MISTRAL: RateLimiter(requests_per_minute=1, clock=clock, sleep=clock.sleep)}
    client = _client(clock, adapters, limiters=mistral_once)
    for _ in range(6):
        _bulk(client)
    assert _models(client) == [GEMINI, MISTRAL, GEMINI, GEMINI, GEMINI, GEMINI]  # Mistral is full, Gemini is not
    assert adapters[Provider.GROQ].calls == []
    assert adapters[Provider.OPENROUTER].calls == []


def test_a_groq_model_with_no_room_is_skipped_at_once_and_openrouter_is_tried() -> None:
    clock = Clock()
    adapters = {p: FakeAdapter(p, replies=(OK,)) for p in Provider}
    client = _client(clock, adapters, limiters=_limited_to_one_a_minute(clock))
    _fill_groq(client, leave=0)
    _bulk(client)
    _bulk(client)
    _bulk(client)  # Gemini and Mistral are full, and so is every Groq model: no wait, OpenRouter reads it
    assert _models(client) == [GEMINI, MISTRAL, OPENROUTER]
    assert adapters[Provider.GROQ].calls == []
    assert clock.slept == []  # never waited for a Groq slot
    assert isinstance(client.call_log, InMemoryCallLog)
    groq = [r for r in client.call_log.records if r.provider is Provider.GROQ]
    assert [(r.model, r.outcome, r.input_tokens) for r in groq] == [
        (BIG, "rate_limited", 0),
        (SMALL, "rate_limited", 0),
        (QWEN, "rate_limited", 0),
    ]  # each was skipped before anything was sent


def test_a_public_page_never_uses_the_shoppers_reserve() -> None:
    clock = Clock()
    adapters = {p: FakeAdapter(p, replies=(OK,)) for p in Provider if p is not Provider.OPENROUTER}
    client = _client(clock, adapters, limiters=_limited_to_one_a_minute(clock))
    reserve = client.config.pool.extract_reserve_tokens_per_minute
    _fill_groq(client, leave=reserve)  # only the shopper's reserve is left in each Groq model's minute
    _bulk(client)
    _bulk(client)
    with pytest.raises(LLMUnavailable):
        _bulk(client)  # a public page would have to use the reserve: no Groq model takes it
    assert adapters[Provider.GROQ].calls == []
    answer = client.complete(
        LLMTask.DETECT_INTENT,
        SYSTEM,
        "a made-up request",
        Answer,
        data_class=DataClass.PRIVATE,
        difficulty=Difficulty.SHORT,
        max_output_tokens=300,
    )
    assert answer.product  # the shopper's own call still fits in the reserve
    assert [c.model for c in adapters[Provider.GROQ].calls] == [BIG]


def test_a_long_public_page_goes_to_groq_whole_with_the_pools_public_answer_size() -> None:
    """About 4,400 tokens: whole beside the reserve with a 1,200-token answer, but not with extraction's 2,000."""
    clock = Clock()
    adapters = {p: FakeAdapter(p, replies=(OK,)) for p in Provider}
    client = _client(clock, adapters, limiters=_limited_to_one_a_minute(clock))
    page = "Nova Book 14 lasts nine hours. " * 425
    _bulk(client)
    _bulk(client)
    assert _bulk(client, output=2000, text=page).product
    [sent] = adapters[Provider.GROQ].calls
    assert sent.model == BIG
    assert sent.user == page  # whole, never trimmed
    assert sent.max_output_tokens == client.config.pool.public_output_tokens == 1200


def test_a_public_page_too_long_for_groq_whole_is_skipped_never_trimmed() -> None:
    clock = Clock()
    adapters = {p: FakeAdapter(p, replies=(OK,)) for p in Provider}
    client = _client(clock, adapters, limiters=_limited_to_one_a_minute(clock))
    page = "Nova Book 14 lasts nine hours. " * 650  # about 6,700 tokens: no Groq model can take it whole
    _bulk(client)
    _bulk(client)
    assert _bulk(client, output=2000, text=page).product
    assert adapters[Provider.GROQ].calls == []
    assert [c.user for c in adapters[Provider.OPENROUTER].calls] == [page]  # OpenRouter read it whole
    assert clock.slept == []


def test_a_public_call_never_moves_the_groq_rotation() -> None:
    def next_reddit_and_private(client: WrapperLLMClient) -> list[str]:
        _bulk(client, DataClass.REDDIT)
        _bulk(client, DataClass.PRIVATE)
        return _models(client)[-2:]

    untouched = _client(Clock())
    clock = Clock()
    client = _client(clock, limiters=_limited_to_one_a_minute(clock))
    _bulk(client)  # Gemini's turn
    _bulk(client)  # Mistral's turn
    _bulk(client)  # Gemini's turn is claimed, but neither has room: Groq reads the page
    assert _models(client) == [GEMINI, MISTRAL, BIG]
    assert next_reddit_and_private(client) == next_reddit_and_private(untouched) == [BIG, BIG]
    clock.now += 61  # Gemini and Mistral have room again
    _bulk(client)
    assert _models(client)[-1] == MISTRAL  # the public turn after Gemini's: Groq's answer moved nothing


def test_no_private_or_reddit_call_reaches_gemini_mistral_or_openrouter() -> None:
    clock = Clock()
    adapters = {p: FakeAdapter(p, replies=(OK,)) for p in Provider}
    client = _client(clock, adapters)
    _fill_groq(client, leave=0)  # every Groq model is full: private and Reddit work still never leave Groq
    for data_class in (DataClass.PRIVATE, DataClass.REDDIT):
        with suppress(LLMUnavailable):  # it may wait for Groq's next minute, or give up: either way, never elsewhere
            _bulk(client, data_class)
    for provider in (Provider.GEMINI, Provider.MISTRAL, Provider.OPENROUTER, Provider.CEREBRAS):
        assert adapters[provider].calls == [], provider


# --- F-4: a daily token counter for every provider -------------------------------------------------

DAILY = {
    Provider.GROQ: 600_000,
    Provider.GEMINI: 2_500_000,
    Provider.OPENROUTER: 250_000,
    Provider.CEREBRAS: 1_000_000,
    Provider.MISTRAL: 1_000_000,
    Provider.DEEPSEEK: 500_000,  # D-63
}
NAMES = {
    Provider.GROQ: "Groq",
    Provider.GEMINI: "Gemini",
    Provider.OPENROUTER: "OpenRouter",
    Provider.CEREBRAS: "Cerebras",
    Provider.MISTRAL: "Mistral",
    Provider.DEEPSEEK: "DeepSeek",
}


def test_every_provider_has_a_daily_token_counter() -> None:
    config = load_llm_config()
    assert {p.name: p.daily_tokens for p in config.providers} == DAILY
    client = _client(Clock(), config=config)
    for provider in Provider:
        assert client.budget.remaining(provider) == DAILY[provider]


def test_each_daily_limit_is_written_down_with_where_it_came_from() -> None:
    text = (ROOT / "docs" / "api-setup.md").read_text(encoding="utf-8")
    section = text.split("### Daily token budgets", 1)[1].split("\n## ", 1)[0]
    for provider, tokens in DAILY.items():
        row = next(line for line in section.splitlines() if line.startswith(f"| {NAMES[provider]} |"))
        assert f"{tokens:,}" in row, provider
        assert "team's choice" in row or "http" in row, provider


def test_a_provider_out_of_its_daily_budget_is_skipped_and_the_next_member_answers() -> None:
    budget = DailyTokenBudget({Provider.GEMINI: 10, Provider.MISTRAL: 1_000_000, Provider.OPENROUTER: 250_000})
    client = _client(Clock(), budget=budget)
    assert _bulk(client).product
    assert _sent(client) == [(Provider.GEMINI, GEMINI, "skipped_budget"), (Provider.MISTRAL, MISTRAL, "ok")]


def test_every_member_out_of_budget_gives_the_fallback_with_a_note() -> None:
    budget = DailyTokenBudget({p: 10 for p in Provider})
    client = _client(Clock(), budget=budget)
    stage = GuardedExtractStage(client)
    page: Source = review_source("https://made-up-review.example/", text="Nova Book 14 lasts nine hours.")
    ctx = StageContext(search_id="srch_aaaaaaaaaaaaaaaaaaaaaaaa")
    result = stage.run(ExtractInput(source=page, criterion_keys=["battery_life"], category_label="laptop"), ctx)
    assert result.claims == []
    note = stage.coverage_note(ctx.search_id)
    assert note is not None and "1 source could not be read" in note
    sent = _sent(client)
    assert [outcome for _, _, outcome in sent] == ["skipped_budget"] * 6  # Gemini, Mistral, Groq's 3, OpenRouter
    assert [model for _, model, _ in sent] == [GEMINI, MISTRAL, BIG, SMALL, QWEN, OPENROUTER]


def test_every_provider_has_a_daily_token_override() -> None:
    config = load_llm_config()
    for provider in Provider:
        name = f"{provider.value.upper()}_DAILY_TOKENS"
        assert ENV_OVERRIDES[provider][name] == "daily_tokens"
        assert with_env_overrides(config, {name: "1234"}).provider(provider).daily_tokens == 1234


def test_the_env_example_names_every_daily_token_override() -> None:
    """Names only for the new ones; Mistral's line keeps the default it always had."""
    lines = (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    for provider in Provider:
        name = f"{provider.value.upper()}_DAILY_TOKENS="
        line = next((line for line in lines if line.startswith(name)), None)
        assert line is not None, provider
        if provider is not Provider.MISTRAL:
            assert line.removeprefix(name).split("#", 1)[0].strip() == "", provider


def test_openrouter_is_paced_at_its_published_limit() -> None:
    config = load_llm_config()
    openrouter = config.provider(Provider.OPENROUTER)
    assert (openrouter.requests_per_minute, openrouter.limits_checked) == (20, date(2026, 11, 3))
    assert default_limiters(config)[Provider.OPENROUTER].requests_per_minute == 20
    assert (openrouter.requests_per_day, openrouter.rotates) == (50, False)
    assert [p.name for p in config.providers if not p.rotates] == [Provider.OPENROUTER, Provider.DEEPSEEK]  # D-63
    docs = (ROOT / "docs" / "api-setup.md").read_text(encoding="utf-8")
    assert "`requests_per_day: 50`" in docs
    assert "https://openrouter.ai/docs/api-reference/limits" in docs
