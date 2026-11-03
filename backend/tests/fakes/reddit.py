"""Test doubles for the Reddit sources (US-12). No real archive or Reddit API is ever called in tests.

- Archive: an httpx.MockTransport that answers by path with scripted responses and records every request.
- Clock: a fake time for limiters, breakers and sleeps, so waits take no real time.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "reddit"
NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)

Answer = httpx.Response | Exception | Callable[[httpx.Request], httpx.Response]


def fixture(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return data


def ok(body: object, **headers: str) -> httpx.Response:
    return httpx.Response(200, json=body, headers=headers)


@dataclass
class Archive:
    """Answers each path with its scripted responses in order; the last one repeats. Unknown paths get 404."""

    routes: dict[str, list[Answer]] = field(default_factory=dict)
    requests: list[httpx.Request] = field(default_factory=list)

    def on(self, path: str, *answers: Answer) -> Archive:
        self.routes[path] = list(answers)
        return self

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self._handle))

    def calls(self, path: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path == path]

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answers = self.routes.get(request.url.path)
        if not answers:
            return httpx.Response(404, json={"error": "not found"})
        used = sum(1 for r in self.requests if r.url.path == request.url.path)
        answer = answers[min(used, len(answers)) - 1]
        if isinstance(answer, Exception):
            raise answer
        if callable(answer) and not isinstance(answer, httpx.Response):
            return answer(request)
        return answer


@dataclass
class Clock:
    """monotonic() and sleep() for limiters and breakers: sleeping moves the clock on at once."""

    now: float = 1_000.0
    slept: list[float] = field(default_factory=list)

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds
