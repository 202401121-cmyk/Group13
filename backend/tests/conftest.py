"""Shared test setup.

No test may ever reach the internet, a real LLM provider or a developer's saved criteria, even on a machine whose
.env holds real keys or whose backend/.cache/ holds generated sets:
- any connection or DNS lookup outside this machine fails at once and fails the test (US-32, tests/network_guard.py);
- the pre-search flow's LLM client is a fake that is always unavailable, so intake uses its keyword rules and
  criteria use the family fallback sets;
- the pre-search flow's criteria store is a fresh in-memory store for every test;
- the request store is the "off" store, even when .env holds a real DATABASE_URL (US-01);
- default stages keep the research stub, so no test can search the web (US-12), and the extract stub, so none can
  call a model (US-14);
- python-dotenv reads no .env (PYTHON_DOTENV_DISABLED, set before the app is imported), so a test sees the same
  settings on a laptop with a .env as in CI, which has none. The live tests turn it back on for themselves;
- the cache sweep (at every app start-up and at each search, US-12) sees only empty folders of its own, never a
  developer's backend/.cache/.
Tests that need LLM answers build a stage with their own FakeLLMClient; tests that need a database build their
own store.
"""

from __future__ import annotations

import os

os.environ["PYTHON_DOTENV_DISABLED"] = "1"  # before any app import below: app modules call load_dotenv() when used

from collections.abc import Iterator
from pathlib import Path

import pytest

from app.criteria import InMemoryCriteriaStore
from app.db import NullRequestStore, shared_memory_aliases
from app.extract import ExtractStage
from app.research import ResearchStage
from app.research.reddit.factory import shared_breakers
from tests.fakes.llm import FakeLLMClient
from tests.network_guard import NetworkGuard, may_use_network


@pytest.fixture(autouse=True)
def network_guard(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[NetworkGuard]:
    guard = NetworkGuard()
    if not may_use_network(request.node):
        guard.install(monkeypatch)
    yield guard
    guard.check()


@pytest.fixture(scope="session")
def _empty_cache_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("cache")


@pytest.fixture(autouse=True)
def _no_real_cache_sweep(monkeypatch: pytest.MonkeyPatch, _empty_cache_root: Path) -> None:
    """The app sweeps the cache folders as it starts (US-12): in tests, empty ones. A test may point them elsewhere."""
    monkeypatch.setattr("app.research.cache.DEFAULT_CACHE_DIR", _empty_cache_root / "research")
    monkeypatch.setattr("app.research.cache.REDDIT_CACHE_DIR", _empty_cache_root / "reddit")


@pytest.fixture(autouse=True)
def _no_live_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.presearch.default_llm", lambda: FakeLLMClient())
    monkeypatch.setattr("app.cli.default_llm", lambda: FakeLLMClient())


@pytest.fixture(autouse=True)
def _fresh_reddit_breakers() -> None:
    """They last as long as the process, so one test's failures must not pause an archive for the next."""
    shared_breakers.cache_clear()


@pytest.fixture(autouse=True)
def _no_live_research(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default stages keep the stub, so no test can search the web. Tests of the live stage give it fakes."""
    monkeypatch.setattr("app.pipeline.default_research_stage", ResearchStage)


@pytest.fixture(autouse=True)
def _no_live_extract(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default stages keep the stub, so no test can call a model. Tests of the real stage give it a fake client."""
    monkeypatch.setattr("app.pipeline.default_extract_stage", ExtractStage)


@pytest.fixture(autouse=True)
def _no_saved_criteria(monkeypatch: pytest.MonkeyPatch) -> None:
    store = InMemoryCriteriaStore()
    monkeypatch.setattr("app.presearch.default_criteria_store", lambda: store)


@pytest.fixture(autouse=True)
def _no_real_database(monkeypatch: pytest.MonkeyPatch) -> None:
    store = NullRequestStore()
    monkeypatch.setattr("app.db.default_request_store", lambda: store)


@pytest.fixture(autouse=True)
def _no_real_matcher_model(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """US-15 (S3-15): every test sees the default matcher, whatever the shell sets, and none downloads the model.

    Tests of the model stage give it a fake loader or fake downloads; only tests marked `live` may fetch the real one.
    The model holders last as long as the process, so each test starts with none.
    """
    from app.entities import model

    monkeypatch.delenv("MATCHER", raising=False)
    monkeypatch.delenv("MATCHER_FORCE_FAIL", raising=False)
    tried: list[str] = []
    if not may_use_network(request.node):

        def no_download(**kwargs: object) -> str:
            tried.append(str(kwargs.get("filename")))
            raise RuntimeError("tests never download the matcher model")

        monkeypatch.setattr(model, "_hf_download", no_download)
    model.clear_shared_models()
    yield
    model.clear_shared_models()
    assert not tried, f"a test tried to download the matcher model: {tried}"


@pytest.fixture(autouse=True)
def _fresh_aliases() -> Iterator[None]:
    """The in-memory alias store lives as long as the process (US-15): one test's aliases never reach another."""
    shared_memory_aliases().clear()
    yield
    shared_memory_aliases().clear()
