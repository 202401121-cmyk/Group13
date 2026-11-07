"""The routes table (US-46): every call goes down the chain for its data class and difficulty, and private and
Reddit text never reach a provider that may train on it, whatever the table says. No real API is called.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel

from app.contracts import (
    CriteriaInput,
    DataClass,
    Difficulty,
    IntakeInput,
    LLMTask,
    LLMUnavailable,
    Provider,
    StageContext,
)
from app.criteria import CriteriaStage, InMemoryCriteriaStore
from app.criteria.parsing import RawCriteria
from app.criteria.prompts import RETRY_NOTE, SYSTEM_PROMPT
from app.intake import IntakeStage
from app.llm import InMemoryCallLog, WrapperLLMClient, load_llm_config
from app.llm.config import DEFAULT_CONFIG_PATH, LLMConfig, LLMConfigError
from app.llm.errors import ServerError
from app.llm.output import build_system_prompt
from app.llm.providers import Adapter
from tests.fakes.llm import FakeAdapter

G, C, GE, OR, M = Provider.GROQ, Provider.CEREBRAS, Provider.GEMINI, Provider.OPENROUTER, Provider.MISTRAL
TRAINING = {GE, OR, M}
EXPECTED_ROUTES = {
    DataClass.PRIVATE: {Difficulty.SHORT: [G], Difficulty.MEDIUM: [G], Difficulty.LONG: [G]},
    DataClass.REDDIT: {Difficulty.SHORT: [G], Difficulty.MEDIUM: [G], Difficulty.LONG: [G]},
    DataClass.PUBLIC: {
        Difficulty.SHORT: [M, G],
        Difficulty.MEDIUM: [M, GE, G, OR],
        Difficulty.LONG: [GE, M, C, OR],
    },
}
OK = '{"product": "Nova Book 14"}'
CTX = StageContext(search_id="srch_route00046")


class Answer(BaseModel):
    product: str


def _client(adapters: dict[Provider, FakeAdapter], config: LLMConfig | None = None) -> WrapperLLMClient:
    typed: dict[Provider, Adapter] = dict(adapters)
    return WrapperLLMClient(config or load_llm_config(), typed, InMemoryCallLog(), limiters={})


def _records(client: WrapperLLMClient) -> list[tuple[Provider, str]]:
    assert isinstance(client.call_log, InMemoryCallLog)
    return [(r.provider, r.outcome) for r in client.call_log.records]


def _raw() -> dict[str, object]:
    data: dict[str, object] = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    return data


def _write(tmp_path: Path, data: object) -> Path:
    path = tmp_path / "models.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


# --- the table itself ----------------------------------------------------------------------------


def test_the_routes_table_is_what_the_team_agreed() -> None:
    assert load_llm_config().routes == EXPECTED_ROUTES


@pytest.mark.parametrize("data_class", list(DataClass), ids=lambda c: c.value)
@pytest.mark.parametrize("difficulty", list(Difficulty), ids=lambda d: d.value)
def test_each_call_follows_its_route_in_order(data_class: DataClass, difficulty: Difficulty) -> None:
    journal: list[Provider] = []
    adapters = {p: FakeAdapter(p, replies=(ServerError(p, "HTTP 503"),), journal=journal) for p in Provider}
    with pytest.raises(LLMUnavailable):
        _client(adapters).complete(
            LLMTask.DISCOVER_ALIASES, "s", "u", Answer, data_class=data_class, difficulty=difficulty
        )
    # Groq's three models are tried in turn; Cerebras stays in its public route, but is skipped with no call.
    assert list(dict.fromkeys(journal)) == [p for p in EXPECTED_ROUTES[data_class][difficulty] if p is not C]
    if data_class is not DataClass.PUBLIC:
        assert not TRAINING & set(journal)


def test_the_first_provider_that_answers_wins() -> None:
    mistral = FakeAdapter(M, replies=(OK,))
    groq = FakeAdapter(G, replies=(OK,))
    client = _client({M: mistral, G: groq})
    client.complete(
        LLMTask.DISCOVER_ALIASES, "s", "u", Answer, data_class=DataClass.PUBLIC, difficulty=Difficulty.SHORT
    )
    assert (len(mistral.calls), len(groq.calls)) == (1, 0)


def test_mistral_bad_json_is_retried_once_then_the_next_provider_answers() -> None:
    mistral = FakeAdapter(M, replies=("Sure! Here is the JSON: {",))
    groq = FakeAdapter(G, replies=(OK,))
    client = _client({M: mistral, G: groq})
    answer = client.complete(
        LLMTask.DISCOVER_ALIASES, "s", "u", Answer, data_class=DataClass.PUBLIC, difficulty=Difficulty.SHORT
    )
    assert answer.product == "Nova Book 14"
    assert _records(client) == [(M, "invalid_output"), (M, "invalid_output"), (G, "ok")]


# --- private and Reddit text never reach a training provider -------------------------------------


@pytest.mark.parametrize("data_class", [DataClass.PRIVATE, DataClass.REDDIT], ids=lambda c: c.value)
def test_even_a_bad_route_cannot_send_private_text_to_a_training_provider(data_class: DataClass) -> None:
    good = load_llm_config()
    bad_routes = {**good.routes, data_class: {d: [GE, M, OR, G] for d in Difficulty}}
    config = LLMConfig.model_construct(
        providers=good.providers, models=good.models, routes=bad_routes, pool=good.pool
    )  # unchecked
    trainers = {p: FakeAdapter(p, replies=(OK,)) for p in TRAINING}
    groq = FakeAdapter(G, replies=(ServerError(G, "HTTP 500"),))
    client = _client({**trainers, G: groq}, config)
    with pytest.raises(LLMUnavailable):
        client.complete(
            LLMTask.DISCOVER_ALIASES, "s", "my words", Answer, data_class=data_class, difficulty=Difficulty.SHORT
        )
    assert all(a.calls == [] for a in trainers.values())
    assert _records(client) == [(G, "error")] * 3


@pytest.mark.parametrize(
    ("data_class", "difficulty", "training"),
    [
        ("private", "short", "gemini"),
        ("private", "long", "openrouter"),
        ("reddit", "medium", "mistral"),
        ("reddit", "long", "gemini"),
    ],
)
def test_the_loader_refuses_a_training_provider_on_a_private_or_reddit_route(
    tmp_path: Path, data_class: str, difficulty: str, training: str
) -> None:
    data = _raw()
    data["routes"][data_class][difficulty] = ["groq", training]  # type: ignore[index]
    with pytest.raises(LLMConfigError, match=f"route {data_class}/{difficulty} may not use .*{training}"):
        load_llm_config(_write(tmp_path, data))


def test_the_loader_refuses_a_missing_or_empty_route(tmp_path: Path) -> None:
    data = _raw()
    del data["routes"]["reddit"]["medium"]  # type: ignore[index]
    data["routes"]["public"]["short"] = []  # type: ignore[index]
    with pytest.raises(LLMConfigError, match=r"needs a route.*public/short.*reddit/medium"):
        load_llm_config(_write(tmp_path, data))


def test_the_loader_refuses_a_repeated_or_unknown_provider(tmp_path: Path) -> None:
    data = _raw()
    data["routes"]["public"]["long"] = ["gemini", "gemini"]  # type: ignore[index]
    with pytest.raises(LLMConfigError, match="names a provider twice"):
        load_llm_config(_write(tmp_path, data))
    data["routes"]["public"]["long"] = ["gemini", "unknown-provider"]  # type: ignore[index]
    with pytest.raises(LLMConfigError, match="routes"):
        load_llm_config(_write(tmp_path, data))


def test_the_loader_refuses_an_unknown_data_class(tmp_path: Path) -> None:
    data = _raw()
    data["routes"]["secret"] = {"short": ["groq"]}  # type: ignore[index]
    with pytest.raises(LLMConfigError, match="routes"):
        load_llm_config(_write(tmp_path, data))


# --- the real call sites -------------------------------------------------------------------------


def test_intake_sends_the_shoppers_words_only_to_non_training_providers() -> None:
    adapters = {p: FakeAdapter(p, replies=(ServerError(p, "HTTP 503"),)) for p in Provider}
    client = _client(adapters)
    intent = IntakeStage(client).run(IntakeInput(raw_query="gaming laptop under 80k for my bad back"), CTX)
    assert intent.detected_by == "rules"  # every allowed provider failed, so the keyword rules answered
    assert [r[0] for r in _records(client)] == [G, G, G]
    assert all(adapters[p].calls == [] for p in TRAINING)


@pytest.mark.parametrize(
    "raw",
    [
        "best laptop for coding under 70k",
        "a laptop for my dad, call me on 98765 43210, my name is Asha",
    ],
)
def test_with_groq_blank_the_public_brief_holds_only_our_own_words(raw: str) -> None:
    """The whole message a training provider sees, with the shopper's request gone through the real stages."""
    mistral = FakeAdapter(M, replies=('{"criteria": []}',))
    client = _client({M: mistral})  # no Groq key: intake uses its rules, and the brief is public
    intent = IntakeStage(client).run(IntakeInput(raw_query=raw), CTX)
    assert (intent.detected_by, intent.category, intent.use_case) == ("rules", "laptop", None)
    inp = CriteriaInput(category="laptop", category_label=str(intent.category_label), use_case=intent.use_case)
    CriteriaStage(client, InMemoryCriteriaStore()).run(inp, CTX)

    assert len(mistral.calls) == 2  # the first answer and the one retry
    brief = "<brief>\ncategory: Laptop\nuse: not stated\n</brief>"
    assert [c.user for c in mistral.calls] == [brief, f"{brief}\n{RETRY_NOTE}"]
    fixed = build_system_prompt(SYSTEM_PROMPT, RawCriteria)
    assert {c.system for c in mistral.calls} == {fixed}  # fixed text: nothing of the shopper's


@pytest.mark.parametrize(
    "inp",
    [
        CriteriaInput(category="laptop", category_label="Laptop", use_case="coding on trains"),
        CriteriaInput(category="laptop", category_label="laptop for my dad"),
        CriteriaInput(category="telescope", category_label="Telescope"),
    ],
    ids=["their use case", "their words as the label", "a category we do not know"],
)
def test_a_brief_with_the_shoppers_words_never_reaches_a_training_provider(inp: CriteriaInput) -> None:
    adapters = {p: FakeAdapter(p, replies=(ServerError(p, "HTTP 503"),)) for p in Provider}
    client = _client(adapters)
    CriteriaStage(client, InMemoryCriteriaStore()).run(inp, CTX)
    assert all(adapters[p].calls == [] for p in TRAINING)
    assert adapters[G].calls


def test_criteria_with_a_use_case_is_private_and_without_one_is_public() -> None:
    reply = '{"criteria": []}'  # too few criteria: the stage retries once, then uses its fallback set
    adapters = {p: FakeAdapter(p, replies=(reply,)) for p in Provider}
    client = _client(adapters)
    stage = CriteriaStage(client, InMemoryCriteriaStore())

    stage.run(CriteriaInput(category="laptop", category_label="Laptop", use_case="video editing"), CTX)
    assert {r[0] for r in _records(client)} == {G}
    assert isinstance(client.call_log, InMemoryCallLog)
    assert {r.data_class for r in client.call_log.records} == {DataClass.PRIVATE}

    client.call_log.records.clear()
    stage.run(CriteriaInput(category="laptop", category_label="Laptop"), CTX)
    assert {r[0] for r in _records(client)} == {M}
    assert {r.data_class for r in client.call_log.records} == {DataClass.PUBLIC}


def test_call_records_carry_class_and_tokens_but_never_the_prompt(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    client = _client({G: FakeAdapter(G, replies=(OK,))})
    secret = "I have a bad back and my budget is tight"
    client.complete(
        LLMTask.DETECT_INTENT, "system words", secret, Answer, data_class=DataClass.PRIVATE, difficulty=Difficulty.SHORT
    )
    assert isinstance(client.call_log, InMemoryCallLog)
    (rec,) = client.call_log.records
    assert (rec.provider, rec.model, rec.data_class) == (G, "openai/gpt-oss-120b", DataClass.PRIVATE)
    assert rec.input_tokens > 0 and rec.output_tokens > 0
    assert secret not in rec.model_dump_json()
    assert secret not in caplog.text and "system words" not in caplog.text
    assert "class=private" in caplog.text
