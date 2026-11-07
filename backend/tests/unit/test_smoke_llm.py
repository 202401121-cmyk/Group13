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


def test_a_failing_provider_is_reported_without_its_key() -> None:
    http = _http([], httpx.Response(401, text=f"bad key {FAKE_KEY}"))
    row = check(load_llm_config().provider(Provider.GROQ), "openai/gpt-oss-120b", FAKE_KEY, http)
    assert (row.trains, row.works) == ("no", "no")
    assert "401" in row.note and FAKE_KEY not in row.note


def test_our_limits_lists_mistral() -> None:
    assert our_limits(load_llm_config()) == ["  mistral: 2 requests/s, 500,000 tokens/min, 1,000,000 tokens/day"]


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
