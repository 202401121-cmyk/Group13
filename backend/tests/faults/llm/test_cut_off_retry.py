"""S5-6a (US-14): an answer cut off at its room gets its one retry with more room, not the same room again.

In the cold live runs of 24 Nov 2026, 12 of 13 unusable answers stopped at 1,996 of Gemini's 2,000 tokens (its thinking
counts against the room) on store and maker pages, and the one retry, asking for the same 2,000, was cut off again.
Fake providers only: each writes at most the room it is given and stops there, as a real one does."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from time import monotonic

import pytest
from pydantic import BaseModel

from app.contracts import DataClass, Difficulty, LLMTask, LLMUnavailable, Provider
from app.llm import InMemoryCallLog, WrapperLLMClient, load_llm_config
from app.llm.client import CUT_OFF_ROOM
from app.llm.config import LLMConfig
from app.llm.errors import RateLimited
from app.llm.fitting import estimate_tokens
from app.llm.output import build_system_prompt
from app.llm.pool import ModelPool
from app.llm.providers import Adapter, RawCompletion

PUBLIC, REDDIT, LONG = DataClass.PUBLIC, DataClass.REDDIT, Difficulty.LONG
TASK = LLMTask.EXTRACT_CLAIMS
SYSTEM = "You read a page about laptops."
PAGE = "Nova Book 14 costs Rs 64,990 at the store. " * 20


class Answer(BaseModel):
    product: str
    claims: list[str] = []


@dataclass
class CuttingAdapter:
    """Writes an answer of `needs` tokens. Given less room, it stops at the room, mid-answer, as a provider does."""

    provider: Provider
    needs: int
    rooms: list[int] = field(default_factory=list)
    refuse_after: int | None = None  # calls after this many answer 429

    def call(
        self,
        model: str,
        system: str,
        user: str,
        max_output_tokens: int,
        timeout_s: float,
        extra: Mapping[str, object] | None = None,
    ) -> RawCompletion:
        self.rooms.append(max_output_tokens)
        if self.refuse_after is not None and len(self.rooms) > self.refuse_after:
            raise RateLimited(self.provider, "simulated 429")
        used = estimate_tokens(system) + estimate_tokens(user)
        if max_output_tokens < self.needs:
            return RawCompletion(text='{"product": "Nova Bo', input_tokens=used, output_tokens=max_output_tokens - 4)
        return RawCompletion(text='{"product": "Nova Book 14"}', input_tokens=used, output_tokens=self.needs)


def _config(route: list[Provider], *, roomy_pool: bool = False, daily: Mapping[str, int] | None = None) -> LLMConfig:
    """The real config, with the public route set to `route` (Reddit's stays Groq: build rule 8) and nothing paced.

    roomy_pool: Groq's models get a minute big enough that their Reddit lane never holds a call. daily: a provider's
    daily token budget."""
    config = load_llm_config()
    raw = config.model_dump()
    if roomy_pool:
        for member in raw["pool"]["members"]:
            member["tokens_per_minute"], member["requests_per_minute"] = 1_000_000, 1_000
    for provider in raw["providers"]:
        if daily and provider["name"] in daily:
            provider["daily_tokens"] = daily[provider["name"]]
    raw["routes"] = {**raw["routes"], "public": {key: [p.value for p in route] for key in ("short", "medium", "long")}}
    for provider in raw["providers"]:
        for limit in ("requests_per_minute", "tokens_per_minute", "requests_per_second"):
            provider[limit] = None
    return LLMConfig.model_validate(raw)


def _client(
    adapters: Mapping[Provider, CuttingAdapter], route: list[Provider], config: LLMConfig | None = None
) -> WrapperLLMClient:
    config = config or _config(route)
    typed: dict[Provider, Adapter] = dict(adapters)
    return WrapperLLMClient(
        config, typed, InMemoryCallLog(), limiters={}, pool=ModelPool(config.pool, sleep=lambda _: None)
    )


def _outcomes(client: WrapperLLMClient) -> list[tuple[str, str, int]]:
    assert isinstance(client.call_log, InMemoryCallLog)
    return [(r.provider.value, r.outcome, r.output_tokens) for r in client.call_log.records]


def test_a_public_answer_cut_off_at_its_room_is_retried_once_with_more_room() -> None:
    gemini = CuttingAdapter(Provider.GEMINI, needs=2600)
    client = _client({Provider.GEMINI: gemini}, [Provider.GEMINI])
    answer = client.complete(TASK, SYSTEM, PAGE, Answer, data_class=PUBLIC, difficulty=LONG, max_output_tokens=2000)
    assert answer.product == "Nova Book 14"
    assert gemini.rooms == [2000, 2000 * CUT_OFF_ROOM]  # the retry asked for the room the answer needed
    assert _outcomes(client) == [("gemini", "invalid_output", 1996), ("gemini", "ok", 2600)]


def test_an_answer_that_was_not_cut_off_is_retried_with_the_same_room_as_before() -> None:
    """An unusable answer well inside its room is a bad answer, not a short room: nothing about it changes."""
    gemini = CuttingAdapter(Provider.GEMINI, needs=10)
    gemini.call = _bad_short_answer(gemini)  # type: ignore[method-assign]
    client = _client({Provider.GEMINI: gemini}, [Provider.GEMINI])
    with pytest.raises(LLMUnavailable):
        client.complete(TASK, SYSTEM, PAGE, Answer, data_class=PUBLIC, difficulty=LONG, max_output_tokens=2000)
    assert gemini.rooms == [2000, 2000]


def _bad_short_answer(adapter: CuttingAdapter):  # type: ignore[no-untyped-def]
    def call(model: str, system: str, user: str, max_output_tokens: int, timeout_s: float, extra: object = None):  # type: ignore[no-untyped-def]
        adapter.rooms.append(max_output_tokens)
        return RawCompletion(text="not json", input_tokens=50, output_tokens=12)

    return call


def test_the_retry_never_asks_more_than_the_model_can_write() -> None:
    config = _config([Provider.GEMINI])
    gemini_model = next(m for m in config.models if m.provider is Provider.GEMINI)
    most = gemini_model.max_output_tokens
    gemini = CuttingAdapter(Provider.GEMINI, needs=most + 10_000)
    client = _client({Provider.GEMINI: gemini}, [Provider.GEMINI])
    with pytest.raises(LLMUnavailable):
        client.complete(TASK, SYSTEM, PAGE, Answer, data_class=PUBLIC, difficulty=LONG, max_output_tokens=most)
    assert gemini.rooms == [most, most]  # no more room to be had: the retry is as before


def test_reddit_text_keeps_its_room_on_the_retry() -> None:
    """Reddit-class work keeps the room its search's Reddit budget counts on (US-14, D-76). The lane has room here,
    so each Groq model's retry really goes, at the same room."""
    config = _config([Provider.GROQ], roomy_pool=True)
    groq = CuttingAdapter(Provider.GROQ, needs=1500)
    client = _client({Provider.GROQ: groq}, [Provider.GROQ], config)
    with pytest.raises(LLMUnavailable):
        client.complete(TASK, SYSTEM, PAGE, Answer, data_class=REDDIT, difficulty=LONG, max_output_tokens=1200)
    assert groq.rooms == [1200, 1200] * len(config.pool.members)  # each model: its answer and its one retry


def test_a_bigger_room_the_daily_budget_cant_give_leaves_the_retry_at_the_room_it_had() -> None:
    """US-14: invalid output is retried once. When the provider's day can give the same room but not twice it, the one
    retry still goes, as before."""
    asked = estimate_tokens(build_system_prompt(SYSTEM, Answer)) + estimate_tokens(PAGE)
    day = 2 * asked + 2000 + 1996 + 500  # the first answer's real use, and room for one more of the same size only
    config = _config([Provider.GEMINI], daily={"gemini": day})
    gemini = CuttingAdapter(Provider.GEMINI, needs=2600)
    client = _client({Provider.GEMINI: gemini}, [Provider.GEMINI], config)
    with pytest.raises(LLMUnavailable):
        client.complete(TASK, SYSTEM, PAGE, Answer, data_class=PUBLIC, difficulty=LONG, max_output_tokens=2000)
    assert gemini.rooms == [2000, 2000]


def test_a_model_busy_for_the_bigger_retry_waits_for_room_of_that_size() -> None:
    """A pool member's minute holds the first answer's size but not the bigger retry's: the call waits for room of the
    bigger size, finds none before its time limit, and says so, instead of looking again every moment."""
    groq = CuttingAdapter(Provider.GROQ, needs=5000)
    config = _config([Provider.GROQ])
    client = _client({Provider.GROQ: groq}, [Provider.GROQ], config)
    started = monotonic()
    with pytest.raises(LLMUnavailable):
        client.complete(
            LLMTask.WRITE_EVIDENCE,
            SYSTEM,
            PAGE,
            Answer,
            data_class=PUBLIC,
            difficulty=Difficulty.MEDIUM,
            max_output_tokens=3000,
        )
    assert monotonic() - started < 5
    assert groq.rooms == [3000] * len(config.pool.members)  # no bigger retry could go in this minute
    outcomes = [outcome for _, outcome, _ in _outcomes(client)]
    assert outcomes.count("rate_limited") == len(config.pool.members)  # each waited for the bigger room, and said so


def test_groq_as_a_public_pages_last_resort_keeps_the_pools_answer_size() -> None:
    """A pool member reads a public page at the pool's public answer size (F-6): the retry can't go past it."""
    config = _config([Provider.GROQ])
    cap = config.pool.public_output_tokens
    assert cap is not None
    groq = CuttingAdapter(Provider.GROQ, needs=cap + 500)
    client = _client({Provider.GROQ: groq}, [Provider.GROQ])
    with pytest.raises(LLMUnavailable):
        client.complete(TASK, SYSTEM, PAGE, Answer, data_class=PUBLIC, difficulty=LONG, max_output_tokens=2000)
    assert groq.rooms and max(groq.rooms) <= cap


def test_a_roomier_retry_that_is_refused_falls_through_to_the_next_provider() -> None:
    """A 429 on the bigger retry ends that model's turn as any 429 does; the next provider still gets the page."""
    gemini = CuttingAdapter(Provider.GEMINI, needs=2600, refuse_after=1)
    mistral = CuttingAdapter(Provider.MISTRAL, needs=500)
    client = _client({Provider.GEMINI: gemini, Provider.MISTRAL: mistral}, [Provider.GEMINI, Provider.MISTRAL])
    answer = client.complete(TASK, SYSTEM, PAGE, Answer, data_class=PUBLIC, difficulty=LONG, max_output_tokens=2000)
    assert answer.product == "Nova Book 14"
    assert gemini.rooms == [2000, 4000]
    assert mistral.rooms == [2000]
