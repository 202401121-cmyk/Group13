"""Shared test setup.

No test may ever reach a real LLM provider or a developer's saved criteria, even on a machine whose .env holds
real keys or whose backend/.cache/ holds generated sets:
- the pre-search flow's LLM client is a fake that is always unavailable, so intake uses its keyword rules and
  criteria use the family fallback sets;
- the pre-search flow's criteria store is a fresh in-memory store for every test;
- the request store is the "off" store, even when .env holds a real DATABASE_URL (US-01).
Tests that need LLM answers build a stage with their own FakeLLMClient; tests that need a database build their
own store.
"""

from __future__ import annotations

import pytest

from app.criteria import InMemoryCriteriaStore
from app.db import NullRequestStore
from tests.fakes.llm import FakeLLMClient


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
