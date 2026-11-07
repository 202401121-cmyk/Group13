"""One adapter for every provider, through their OpenAI-compatible chat completions API (US-26).

This module and client.py are the only code that talks to an LLM provider (build rule 1). No provider SDK is
used: Groq, Gemini, OpenRouter, Cerebras and Mistral all accept the same request over plain HTTP.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from app.contracts import Provider

from .errors import (
    AuthFailed,
    ModelForbidden,
    ModelNotFound,
    NoCredit,
    ProviderError,
    ProviderTimeout,
    RateLimited,
    ServerError,
)
from .fitting import estimate_tokens

# What providers answer for a model that is retired, withdrawn or unknown, usually with HTTP 400 or 404.
MODEL_GONE_CODES = frozenset({"model_not_found", "model_decommissioned", "invalid_model"})


@dataclass(frozen=True)
class RawCompletion:
    """The model's reply text, before it is parsed against a schema."""

    text: str
    input_tokens: int
    output_tokens: int
    ratelimit_headers: dict[str, str] = field(default_factory=dict)  # response headers only, for the smoke test


class Adapter(Protocol):
    """What the wrapper needs from a provider. Tests pass fakes that implement it."""

    provider: Provider

    def call(self, model: str, system: str, user: str, max_output_tokens: int, timeout_s: float) -> RawCompletion: ...


class OpenAICompatibleAdapter:
    """POST {base_url}/chat/completions with a Bearer key. Used for every provider.

    json_mode adds response_format {"type": "json_object"}, for providers that support it (Mistral). The reply is
    still parsed and checked against the schema exactly as for the others.
    """

    def __init__(
        self, provider: Provider, base_url: str, api_key: str, http: httpx.Client, json_mode: bool = False
    ) -> None:
        self.provider = provider
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._api_key = api_key
        self._http = http
        self._json_mode = json_mode

    def __repr__(self) -> str:
        return f"OpenAICompatibleAdapter(provider={self.provider.value!r}, url={self._url!r})"  # never the key

    def call(self, model: str, system: str, user: str, max_output_tokens: int, timeout_s: float) -> RawCompletion:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "max_tokens": max_output_tokens,
            "temperature": 0,
        }
        if self._json_mode:
            payload["response_format"] = {"type": "json_object"}
        try:
            response = self._http.post(
                self._url,
                json=payload,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=timeout_s,
            )
        except httpx.TimeoutException:
            raise ProviderTimeout(self.provider, f"no reply within {timeout_s:g}s") from None
        except httpx.HTTPError as exc:
            raise ProviderError(self.provider, f"request failed ({type(exc).__name__})") from None

        limits = _ratelimit_headers(response.headers)
        status = response.status_code
        if status == 429:
            raise RateLimited(self.provider, "HTTP 429 (rate limited)", limits)
        if status == 401:
            raise AuthFailed(self.provider, "HTTP 401 (key missing, wrong or not allowed)", limits)
        if status == 403:
            raise ModelForbidden(self.provider, "HTTP 403 (key missing, wrong or not allowed)", limits)
        if status == 402:
            raise NoCredit(self.provider, "HTTP 402 (the account needs a plan or credits)", limits)
        if _model_not_found(response):
            raise ModelNotFound(self.provider, f"HTTP {status} (this model is not available)", limits)
        if status >= 500:
            raise ServerError(self.provider, f"HTTP {status}", limits)
        if not 200 <= status < 300:
            raise ProviderError(self.provider, f"HTTP {status}", limits)

        text, usage = self._read_body(response)
        return RawCompletion(
            text=text,
            input_tokens=_int(usage.get("prompt_tokens"), estimate_tokens(system) + estimate_tokens(user)),
            output_tokens=_int(usage.get("completion_tokens"), estimate_tokens(text)),
            ratelimit_headers=limits,
        )

    def _read_body(self, response: httpx.Response) -> tuple[str, dict[str, Any]]:
        try:
            data = response.json()
            text = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise ProviderError(self.provider, "the reply was not a chat completion") from None
        if not isinstance(text, str):
            raise ProviderError(self.provider, "the reply had no message text")
        usage = data.get("usage")
        return text, usage if isinstance(usage, dict) else {}


def _model_not_found(response: httpx.Response) -> bool:
    """A withdrawn or unknown model: HTTP 404, or any other 4xx whose error code says so. Reads only that code."""
    if response.status_code == 404:
        return True
    if not 400 <= response.status_code < 500:
        return False
    try:
        body = response.json()
    except ValueError:
        return False
    error = body.get("error") if isinstance(body, dict) else None
    return isinstance(error, dict) and error.get("code") in MODEL_GONE_CODES


def _ratelimit_headers(headers: httpx.Headers) -> dict[str, str]:
    """The rate-limit headers, and Retry-After: the wrapper reads them to time a cool-down."""
    return {
        name: value for name, value in headers.items() if "ratelimit" in name.lower() or name.lower() == "retry-after"
    }


def _int(value: object, fallback: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else fallback
