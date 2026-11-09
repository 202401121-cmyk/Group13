"""The LLM smoke script's pieces (US-46), offline: httpx.MockTransport stands in for every provider."""

from __future__ import annotations

import json

import httpx
import pytest

from app.contracts import Provider
from app.llm import load_llm_config
from scripts.smoke_llm import check, main, our_limits

FAKE_KEY = "sk-test-smoke-DO-NOT-LEAK"


def _http(seen: list[httpx.Request], response: httpx.Response) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return response

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_mistral_is_checked_in_json_mode_and_marked_as_training() -> None:
    seen: list[httpx.Request] = []
    reply = {
        "choices": [{"message": {"content": '{"ok": true}'}}],
        "usage": {"prompt_tokens": 9, "completion_tokens": 4},
    }
    http = _http(seen, httpx.Response(200, json=reply, headers={"x-ratelimit-limit-req-minute": "188"}))
    settings = load_llm_config().provider(Provider.MISTRAL)

    row = check(settings, "ministral-8b-latest", FAKE_KEY, http)

    assert (row.provider, row.model, row.trains, row.works, row.tokens) == (
        "mistral",
        "ministral-8b-latest",
        "yes",
        "yes",
        "9 in / 4 out",
    )
    assert row.ratelimit_headers == {"x-ratelimit-limit-req-minute": "188"}
    assert json.loads(seen[0].content)["response_format"] == {"type": "json_object"}
    assert str(seen[0].url) == "https://api.mistral.ai/v1/chat/completions"


def test_a_provider_is_checked_with_its_reasoning_setting() -> None:
    """This script says it uses the app's settings. If Gemini ever refuses the app's setting, it must show here."""
    seen: list[httpx.Request] = []
    reply = {
        "choices": [{"message": {"content": '{"ok": true}'}}],
        "usage": {"prompt_tokens": 9, "completion_tokens": 4},
    }
    settings = load_llm_config().provider(Provider.GEMINI)
    check(settings, "gemini-3.5-flash-lite", FAKE_KEY, _http(seen, httpx.Response(200, json=reply)))
    assert json.loads(seen[0].content)["reasoning_effort"] == "minimal"


def test_a_failing_provider_is_reported_without_its_key() -> None:
    http = _http([], httpx.Response(401, text=f"bad key {FAKE_KEY}"))
    row = check(load_llm_config().provider(Provider.GROQ), "openai/gpt-oss-120b", FAKE_KEY, http)
    assert (row.trains, row.works) == ("no", "no")
    assert "401" in row.note and FAKE_KEY not in row.note


def test_our_limits_lists_mistral() -> None:
    assert "  mistral: 2 requests/s, 500,000 tokens/min, 1,000,000 tokens/day" in our_limits(load_llm_config())


def test_our_limits_lists_geminis_requests_a_minute() -> None:
    assert any(line.startswith("  gemini: ") and "15 requests/min" in line for line in our_limits(load_llm_config()))


def test_check_sends_the_settings_it_is_given() -> None:
    seen: list[httpx.Request] = []
    reply = {
        "choices": [{"message": {"content": '{"ok": true}'}}],
        "usage": {"prompt_tokens": 9, "completion_tokens": 4},
    }
    settings = load_llm_config().provider(Provider.GROQ)
    extra = {"reasoning_effort": "low", "include_reasoning": False}
    check(settings, "openai/gpt-oss-20b", FAKE_KEY, _http(seen, httpx.Response(200, json=reply)), extra)
    body = json.loads(seen[0].content)
    assert (body["reasoning_effort"], body["include_reasoning"]) == ("low", False)


def test_main_sends_each_models_extraction_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    config = load_llm_config()
    for settings in config.providers:
        monkeypatch.delenv(settings.api_key_env, raising=False)
    monkeypatch.setenv(config.provider(Provider.GROQ).api_key_env, FAKE_KEY)
    monkeypatch.setattr("scripts.smoke_llm.load_dotenv", lambda: None)
    seen: list[httpx.Request] = []
    reply = {
        "choices": [{"message": {"content": '{"ok": true}'}}],
        "usage": {"prompt_tokens": 9, "completion_tokens": 4},
    }
    monkeypatch.setattr("scripts.smoke_llm.build_http_client", lambda _: _http(seen, httpx.Response(200, json=reply)))
    main()
    bodies = {json.loads(r.content)["model"]: json.loads(r.content) for r in seen}
    assert bodies["openai/gpt-oss-20b"]["reasoning_effort"] == "low"
    assert bodies["qwen/qwen3.8-27b"]["reasoning_effort"] == "none"


def test_main_builds_its_client_the_way_the_app_does(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no keys every row is skipped, so no call is made even on a machine with a real .env."""
    config = load_llm_config()
    for settings in config.providers:
        monkeypatch.delenv(settings.api_key_env, raising=False)
    monkeypatch.setattr("scripts.smoke_llm.load_dotenv", lambda: None)
    built: list[bool] = []

    def build(force_ipv4: bool) -> httpx.Client:
        built.append(force_ipv4)
        return httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(599)))

    monkeypatch.setattr("scripts.smoke_llm.build_http_client", build)
    main()
    assert built == [config.force_ipv4]
