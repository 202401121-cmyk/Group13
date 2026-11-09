"""Extraction sends its own reasoning settings to each model, and no other task does (US-14).

Through the real client and adapter, reading the request body that would go out. No real API is called.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pydantic import BaseModel, ValidationError

from app.contracts import DataClass, Difficulty, LLMTask, LLMUnavailable, Provider
from app.llm import InMemoryCallLog, WrapperLLMClient, load_llm_config
from app.llm.config import LLMConfig, with_env_overrides
from app.llm.providers import OpenAICompatibleAdapter

BIG, SMALL, QWEN = "openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b"
GOOD = '{"answer": "made up"}'


class Answer(BaseModel):
    answer: str


class _Wire:
    """Stands in for every provider's HTTP endpoint, and keeps each request body."""

    def __init__(self, *replies: str | int) -> None:
        self.replies = list(replies) or [GOOD]
        self.bodies: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.bodies.append(json.loads(request.content))
        reply = self.replies[min(len(self.bodies), len(self.replies)) - 1]
        if isinstance(reply, int):
            return httpx.Response(reply, json={"error": {"code": "made_up"}})
        usage = {"prompt_tokens": 9, "completion_tokens": 4}
        return httpx.Response(200, json={"choices": [{"message": {"content": reply}}], "usage": usage})

    def for_model(self, model: str) -> list[dict[str, Any]]:
        return [body for body in self.bodies if body["model"] == model]


def _client(wire: _Wire, *providers: Provider) -> WrapperLLMClient:
    config = load_llm_config()
    http = httpx.Client(transport=httpx.MockTransport(wire))
    adapters = {
        p: OpenAICompatibleAdapter(
            p,
            config.provider(p).base_url,
            "a-made-up-key",
            http,
            json_mode=config.provider(p).json_mode,
            reasoning_effort=config.provider(p).reasoning_effort,
        )
        for p in providers
    }
    return WrapperLLMClient(config, adapters, InMemoryCallLog(), limiters={})


def _extract(client: WrapperLLMClient, data_class: DataClass = DataClass.REDDIT) -> Answer:
    return client.complete(
        LLMTask.EXTRACT_CLAIMS, "Extract.", "Made-up text.", Answer, data_class=data_class, difficulty=Difficulty.LONG
    )


def _each_groq_model(wire: _Wire) -> None:
    client = _client(wire, Provider.GROQ)
    for _ in range(3):  # bulk work goes round the three members in turn
        _extract(client)


@pytest.mark.parametrize("model", [BIG, SMALL])
def test_extraction_sends_low_reasoning_on_the_gpt_oss_models(model: str) -> None:
    wire = _Wire()
    _each_groq_model(wire)
    (body,) = wire.for_model(model)
    assert body["reasoning_effort"] == "low"
    assert body["include_reasoning"] is False


def test_extraction_sends_no_reasoning_on_qwen() -> None:
    wire = _Wire()
    _each_groq_model(wire)
    (body,) = wire.for_model(QWEN)
    assert body["reasoning_effort"] == "none"
    assert "include_reasoning" not in body  # qwen's own settings don't list it


def test_each_extraction_route_sends_its_settings() -> None:
    """The Reddit route's three models, then the public route, whose providers all refuse so each one gets asked."""
    reddit = _Wire()
    _each_groq_model(reddit)
    assert {body["model"]: body["reasoning_effort"] for body in reddit.bodies} == {
        BIG: "low",
        SMALL: "low",
        QWEN: "none",
    }
    wire = _Wire(500)
    client = _client(wire, Provider.GEMINI, Provider.MISTRAL, Provider.OPENROUTER)
    with pytest.raises(LLMUnavailable):
        _extract(client, DataClass.PUBLIC)
    config = load_llm_config()
    models = {p: next(m.model for m in config.models if m.provider is p) for p in Provider}
    assert wire.for_model(models[Provider.GEMINI])[0]["reasoning_effort"] == "minimal"
    for provider in (Provider.MISTRAL, Provider.OPENROUTER):
        body = wire.for_model(models[provider])[0]
        assert "reasoning_effort" not in body, provider
        assert "include_reasoning" not in body, provider
    assert "include_reasoning" not in wire.for_model(models[Provider.GEMINI])[0]


@pytest.mark.parametrize(
    "task",
    [
        LLMTask.DETECT_INTENT,
        LLMTask.GENERATE_CRITERIA,
        LLMTask.CLASSIFY_REPLY,
        LLMTask.ANSWER_USER_QUESTION,
        LLMTask.WRITE_QUESTION,
        LLMTask.DISCOVER_ALIASES,
        LLMTask.ENRICH_EXTRACT,
        LLMTask.WRITE_EXPLANATION,
    ],
)
def test_no_other_task_gets_these_settings(task: LLMTask) -> None:
    wire = _Wire()
    client = _client(wire, Provider.GROQ)
    client.complete(
        task, "A prompt.", "Made-up text.", Answer, data_class=DataClass.PRIVATE, difficulty=Difficulty.SHORT
    )
    (body,) = wire.bodies
    assert "reasoning_effort" not in body
    assert "include_reasoning" not in body


def test_only_extraction_has_task_settings() -> None:
    assert set(load_llm_config().task_settings) == {LLMTask.EXTRACT_CLAIMS}


def test_each_model_with_settings_is_on_one_provider() -> None:
    """Settings are found by the model's name, so a name shared with another provider would pass them on."""
    config = load_llm_config()
    for model in config.task_settings[LLMTask.EXTRACT_CLAIMS]:
        assert [m.provider for m in config.models if m.model == model] == [Provider.GROQ], model


def test_a_retry_sends_the_same_settings() -> None:
    wire = _Wire("not json", GOOD)
    _extract(_client(wire, Provider.GROQ))
    first, second = wire.bodies
    assert first["model"] == second["model"]
    assert (first["reasoning_effort"], second["reasoning_effort"]) == (
        load_llm_config().request_fields(LLMTask.EXTRACT_CLAIMS, first["model"])["reasoning_effort"],
    ) * 2


def test_a_tasks_setting_wins_over_the_providers_own() -> None:
    wire = _Wire()
    http = httpx.Client(transport=httpx.MockTransport(wire))
    adapter = OpenAICompatibleAdapter(
        Provider.GEMINI, "https://made-up.example/v1", "a-made-up-key", http, reasoning_effort="none"
    )
    adapter.call("m", "system", "user", 64, 5.0, extra={"reasoning_effort": "low"})
    assert wire.bodies[0]["reasoning_effort"] == "low"


def test_gemini_keeps_its_own_thinking_setting() -> None:
    """Its setting is the provider's, for every task, and this build leaves it alone."""
    config = load_llm_config()
    assert config.provider(Provider.GEMINI).reasoning_effort == "minimal"  # Flash Lite's lowest: it has no "none"
    assert config.request_fields(LLMTask.EXTRACT_CLAIMS, "gemini-3.5-flash-lite") == {}


def test_the_settings_come_from_models_yaml() -> None:
    config = load_llm_config()
    low = {"reasoning_effort": "low", "include_reasoning": False}
    assert config.request_fields(LLMTask.EXTRACT_CLAIMS, BIG) == low
    assert config.request_fields(LLMTask.EXTRACT_CLAIMS, SMALL) == low
    assert config.request_fields(LLMTask.EXTRACT_CLAIMS, QWEN) == {"reasoning_effort": "none"}
    assert config.request_fields(LLMTask.DETECT_INTENT, BIG) == {}


def _with_settings(settings: object) -> dict[str, object]:
    base = load_llm_config()
    return {
        "providers": base.providers,
        "models": base.models,
        "routes": base.routes,
        "pool": base.pool,
        "task_settings": settings,
    }


def test_config_refuses_a_setting_for_an_unknown_model() -> None:
    with pytest.raises(ValidationError, match="no such model"):
        LLMConfig.model_validate(_with_settings({"extract_claims": {"no/such-model": {"reasoning_effort": "low"}}}))


def test_config_refuses_an_unknown_field() -> None:
    with pytest.raises(ValidationError):
        LLMConfig.model_validate(_with_settings({"extract_claims": {BIG: {"temperature": 2}}}))


def test_config_refuses_an_unknown_task() -> None:
    with pytest.raises(ValidationError):
        LLMConfig.model_validate(_with_settings({"no_such_task": {BIG: {"reasoning_effort": "low"}}}))


def test_env_overrides_keep_the_settings() -> None:
    config = with_env_overrides(load_llm_config(), {"MISTRAL_RPS": "1"})
    assert config.request_fields(LLMTask.EXTRACT_CLAIMS, BIG) == {"reasoning_effort": "low", "include_reasoning": False}
