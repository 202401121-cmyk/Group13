"""The LLM smoke script's pieces (US-46), offline: httpx.MockTransport stands in for every provider."""

from __future__ import annotations

import json

import httpx

from app.contracts import Provider
from app.llm import load_llm_config
from scripts.smoke_llm import check, our_limits

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
