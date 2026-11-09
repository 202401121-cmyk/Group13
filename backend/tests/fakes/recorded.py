"""Replays what scripts/record_run.py wrote to tests/fixtures/recorded/<name>/ (US-32).

    recording = load_recording("laptop_coding_70k")
    llm = WrapperLLMClient(config, {Provider.GROQ: RecordedAdapter(recording, Provider.GROQ)}, limiters={})
    http = httpx.Client(transport=recorded_transport(recording, "serper"))  # for a source adapter

Calls are answered in the order they were recorded, whatever is asked: a replay proves that our code handles a real
reply, not that it sends the same request. A call that failed or was refused replays as a failure. Reddit text is
never stored, so tests that need it use synthetic threads.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.contracts import Provider
from app.llm.providers import OpenAICompatibleAdapter, RawCompletion

RECORDINGS = Path(__file__).resolve().parents[1] / "fixtures" / "recorded"
MANIFEST = "manifest.json"


@dataclass(frozen=True)
class Recording:
    name: str
    request: str
    by_service: dict[str, list[dict[str, Any]]]

    @property
    def services(self) -> list[str]:
        return sorted(self.by_service)

    def calls(self, service: str) -> list[dict[str, Any]]:
        return self.by_service.get(service, [])


def load_recording(name: str, root: Path = RECORDINGS) -> Recording:
    folder = root / name
    if not (folder / MANIFEST).is_file():
        raise FileNotFoundError(f'No recording "{name}" in {root}. Make one with: python -m scripts.record_run')
    manifest = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
    by_service = {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(folder.glob("*.json"))
        if path.name != MANIFEST
    }
    return Recording(name, manifest["request"], by_service)


def recorded_transport(recording: Recording, service: str) -> httpx.MockTransport:
    """An httpx transport that answers with this service's calls, one by one.

    Running out fails the test. pytest.fail isn't an Exception, so the LLM client can't swallow it and fall back.
    """
    calls = recording.calls(service)
    left = iter(calls)

    def answer(request: httpx.Request) -> httpx.Response:
        call = next(left, None)
        if call is None:
            pytest.fail(f"recording {recording.name} has only {len(calls)} call(s) for {service}", pytrace=False)
        if call.get("error"):
            raise _failure(call["error"])(f"recorded failure: {call['error']}", request=request)
        body = call["response_body"]
        headers = call["response_headers"]
        if isinstance(body, str):
            return httpx.Response(call["status"], text=body, headers=headers)
        return httpx.Response(call["status"], json=body, headers=headers)

    return httpx.MockTransport(answer)


def _failure(name: str) -> type[httpx.TransportError]:
    """The error the call met, so a timeout replays as a timeout. A refused call replays as a dropped connection."""
    kind = getattr(httpx, name, None)
    is_transport_error = isinstance(kind, type) and issubclass(kind, httpx.TransportError)
    return kind if is_transport_error else httpx.ConnectError  # type: ignore[return-value]


class RecordedAdapter:
    """An LLM adapter for WrapperLLMClient: the real one on recorded replies, so a 429 or bad JSON acts as live."""

    def __init__(self, recording: Recording, provider: Provider) -> None:
        self.provider = provider
        http = httpx.Client(transport=recorded_transport(recording, provider.value))
        self._adapter = OpenAICompatibleAdapter(provider, "https://recorded.invalid/v1", "recorded", http)

    def call(
        self,
        model: str,
        system: str,
        user: str,
        max_output_tokens: int,
        timeout_s: float,
        extra: Mapping[str, object] | None = None,
    ) -> RawCompletion:
        return self._adapter.call(model, system, user, max_output_tokens, timeout_s, extra)
