"""Shared test setup.

No test may ever reach the internet, a real LLM provider or a developer's saved criteria, even on a machine whose
.env holds real keys or whose backend/.cache/ holds generated sets:
- any connection or DNS lookup outside this machine fails at once and fails the test (US-32, tests/network_guard.py);
- the pre-search flow's LLM client is a fake that is always unavailable, so intake uses its keyword rules and
  criteria use the family fallback sets;
- the pre-search flow's criteria store is a fresh in-memory store for every test;
- the request store is the "off" store, even when .env holds a real DATABASE_URL (US-01).
Tests that need LLM answers build a stage with their own FakeLLMClient; tests that need a database build their
own store.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from app.criteria import InMemoryCriteriaStore
from app.db import NullRequestStore
from tests.fakes.llm import FakeLLMClient
from tests.network_guard import NetworkGuard, may_use_network


@pytest.fixture(autouse=True)
def network_guard(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[NetworkGuard]:
    guard = NetworkGuard()
    if not may_use_network(request.node):
        guard.install(monkeypatch)
    yield guard
    guard.check()


@pytest.fixture(autouse=True)
def _no_live_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.presearch.default_llm", lambda: FakeLLMClient())


@pytest.fixture(autouse=True)
def _no_saved_criteria(monkeypatch: pytest.MonkeyPatch) -> None:
    store = InMemoryCriteriaStore()
    monkeypatch.setattr("app.presearch.default_criteria_store", lambda: store)


@pytest.fixture(autouse=True)
def _no_real_database(monkeypatch: pytest.MonkeyPatch) -> None:
    store = NullRequestStore()
    monkeypatch.setattr("app.db.default_request_store", lambda: store)
