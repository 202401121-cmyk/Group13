"""The OpenAI-compatible adapter (US-26, US-46), against httpx.MockTransport. No real API is called."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable

import httpx
import pytest

from app.contracts import Provider
from app.llm.errors import (
    AuthFailed,
    ModelForbidden,
    ModelNotFound,
    ModelRetired,
    NoCredit,
    ProviderError,
    ProviderFailure,
    ProviderTimeout,
    RateLimited,
    ServerError,
)
from app.llm.fitting import estimate_tokens
from app.llm.providers import OpenAICompatibleAdapter

FAKE_KEY = "sk-test-DO-NOT-LEAK-1234567890"
BASE_URL = "https://api.example.test/v1"

Handler = Callable[[httpx.Request], httpx.Response]


def _adapter(handler: Handler) -> OpenAICompatibleAdapter:
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return OpenAICompatibleAdapter(Provider.GROQ, BASE_URL, FAKE_KEY, http)


def _call(adapter: OpenAICompatibleAdapter) -> object:
    return adapter.call("test-model", "system text", "user text", max_output_tokens=64, timeout_s=5)


def _completion(content: object, usage: dict[str, int] | None = None) -> dict[str, object]:
    body: dict[str, object] = {"choices": [{"message": {"role": "assistant", "content": content}}]}
    if usage is not None:
        body["usage"] = usage
    return body


def test_success_with_usage_sends_the_right_request() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        usage = {"prompt_tokens": 21, "completion_tokens": 5}
        headers = {"x-ratelimit-remaining-requests": "99", "x-request-id": "abc"}
        return httpx.Response(200, json=_completion('{"ok": true}', usage), headers=headers)

    result = _adapter(handler).call("test-model", "be brief", "hello", max_output_tokens=64, timeout_s=5)

    assert result.text == '{"ok": true}'
    assert (result.input_tokens, result.output_tokens) == (21, 5)
    assert result.ratelimit_headers == {"x-ratelimit-remaining-requests": "99"}
    (request,) = seen
    assert str(request.url) == f"{BASE_URL}/chat/completions"
    assert request.headers["Authorization"] == f"Bearer {FAKE_KEY}"
    assert json.loads(request.content) == {
        "model": "test-model",
        "messages": [{"role": "system", "content": "be brief"}, {"role": "user", "content": "hello"}],
        "max_tokens": 64,
        "temperature": 0,
    }


def test_success_without_usage_estimates_tokens() -> None:
    adapter = _adapter(lambda r: httpx.Response(200, json=_completion('{"ok": true}')))
    result = adapter.call("m", "system text", "user text", max_output_tokens=64, timeout_s=5)
    assert result.input_tokens == estimate_tokens("system text") + estimate_tokens("user text")
    assert result.output_tokens == estimate_tokens('{"ok": true}')


def _raise_timeout(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("timed out", request=request)


def _raise_connect(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused", request=request)


# Only a 4xx with no status of its own is read for a code, so this must change nothing on the other statuses.
MODEL_CODE = {"error": {"code": "model_decommissioned"}}

# What Groq answers for a retired model, as seen in public reports: HTTP 400, not 404.
GROQ_RETIRED = {
    "error": {
        "message": f"The model has been decommissioned and is no longer supported. {FAKE_KEY}",
        "type": "invalid_request_error",
        "code": "model_decommissioned",
    }
}

FAILURES: list[tuple[str, Handler, type[ProviderFailure], str]] = [
    ("429", lambda r: httpx.Response(429, headers={"x-ratelimit-reset": "7s"}), RateLimited, "rate_limited"),
    ("500", lambda r: httpx.Response(500), ServerError, "error"),
    ("503", lambda r: httpx.Response(503), ServerError, "error"),
    ("401", lambda r: httpx.Response(401, json={"error": f"Invalid API key {FAKE_KEY}"}), AuthFailed, "error"),
    ("403", lambda r: httpx.Response(403, text=f"forbidden for {FAKE_KEY}"), ModelForbidden, "error"),
    ("404", lambda r: httpx.Response(404, text=f"no such model for {FAKE_KEY}"), ModelNotFound, "error"),
    (
        "model_not_found",
        lambda r: httpx.Response(400, json={"error": {"code": "model_not_found", "message": f"{FAKE_KEY}"}}),
        ModelNotFound,
        "error",
    ),
    ("groq retired model", lambda r: httpx.Response(400, json=GROQ_RETIRED), ModelRetired, "error"),
    (
        "retired on a 404",
        lambda r: httpx.Response(404, json={"error": {"code": "model_decommissioned"}}),
        ModelRetired,
        "error",
    ),
    ("invalid_model", lambda r: httpx.Response(400, json={"error": {"code": "invalid_model"}}), ModelNotFound, "error"),
    ("other 400", lambda r: httpx.Response(400, json={"error": {"code": "invalid_request"}}), ProviderError, "error"),
    ("400 not json", lambda r: httpx.Response(400, text="nope"), ProviderError, "error"),
    ("a code that is not text", lambda r: httpx.Response(400, json={"error": {"code": 404}}), ProviderError, "error"),
    ("a 5xx with a model code", lambda r: httpx.Response(503, json=MODEL_CODE), ServerError, "error"),
    ("a 429 with a model code", lambda r: httpx.Response(429, json=MODEL_CODE), RateLimited, "rate_limited"),
    ("a 401 with a model code", lambda r: httpx.Response(401, json=MODEL_CODE), AuthFailed, "error"),
    ("a 402 with a model code", lambda r: httpx.Response(402, json=MODEL_CODE), NoCredit, "error"),
    ("a 403 with a model code", lambda r: httpx.Response(403, json=MODEL_CODE), ModelForbidden, "error"),
    ("a 200 with a model code", lambda r: httpx.Response(200, json=MODEL_CODE), ProviderError, "error"),
    ("400 a list", lambda r: httpx.Response(400, json=["nope"]), ProviderError, "error"),
    ("402", lambda r: httpx.Response(402, json={"message": "payment required"}), NoCredit, "error"),
    ("400", lambda r: httpx.Response(400, text="bad request"), ProviderError, "error"),
    ("timeout", _raise_timeout, ProviderTimeout, "timeout"),
    ("network", _raise_connect, ProviderError, "error"),
    ("not json", lambda r: httpx.Response(200, text="<html>oops</html>"), ProviderError, "error"),
    ("no choices", lambda r: httpx.Response(200, json={"id": "x"}), ProviderError, "error"),
    ("null content", lambda r: httpx.Response(200, json=_completion(None)), ProviderError, "error"),
]


@pytest.mark.parametrize(("name", "handler", "error", "outcome"), FAILURES, ids=[f[0] for f in FAILURES])
def test_failures_map_to_the_right_exception(
    name: str,
    handler: Handler,
    error: type[ProviderFailure],
    outcome: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    adapter = _adapter(handler)
    with pytest.raises(error) as info:
        _call(adapter)

    exc = info.value
    assert type(exc) is error
    assert exc.outcome == outcome
    assert exc.provider is Provider.GROQ
    # No httpx exception (which could carry request details) travels with it.
    assert exc.__cause__ is None
    assert exc.__context__ is None or exc.__suppress_context__
    # The key never appears in the message, the repr, the logs or the adapter's repr.
    assert FAKE_KEY not in str(exc)
    assert FAKE_KEY not in repr(exc)
    assert FAKE_KEY not in caplog.text
    assert FAKE_KEY not in repr(adapter)


def test_rate_limit_keeps_the_ratelimit_headers() -> None:
    adapter = _adapter(lambda r: httpx.Response(429, headers={"x-ratelimit-reset": "7s", "server": "x"}))
    with pytest.raises(RateLimited) as info:
        _call(adapter)
    assert info.value.ratelimit_headers == {"x-ratelimit-reset": "7s"}


def test_rate_limit_keeps_retry_after_for_the_cool_down() -> None:
    adapter = _adapter(lambda r: httpx.Response(429, headers={"Retry-After": "75"}))
    with pytest.raises(RateLimited) as info:
        _call(adapter)
    assert info.value.ratelimit_headers == {"retry-after": "75"}


def test_json_mode_asks_for_a_json_object() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_completion('{"ok": true}'))

    http = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = OpenAICompatibleAdapter(Provider.MISTRAL, BASE_URL, FAKE_KEY, http, json_mode=True)
    assert adapter.call("ministral-8b-latest", "be brief", "hello", max_output_tokens=64, timeout_s=5).text == (
        '{"ok": true}'
    )
    body = json.loads(seen[0].content)
    assert body["response_format"] == {"type": "json_object"}
    assert body["model"] == "ministral-8b-latest"


def test_a_reasoning_setting_is_sent_when_set() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_completion('{"ok": true}'))

    http = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = OpenAICompatibleAdapter(Provider.GEMINI, BASE_URL, FAKE_KEY, http, reasoning_effort="none")
    adapter.call("gemini-3.5-flash", "be brief", "hello", max_output_tokens=64, timeout_s=5)
    assert json.loads(seen[0].content)["reasoning_effort"] == "none"


def test_without_a_reasoning_setting_none_is_sent() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_completion('{"ok": true}'))

    _call(_adapter(handler))
    assert "reasoning_effort" not in json.loads(seen[0].content)


def test_without_json_mode_no_response_format_is_sent() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_completion('{"ok": true}'))

    _call(_adapter(handler))
    assert "response_format" not in json.loads(seen[0].content)
