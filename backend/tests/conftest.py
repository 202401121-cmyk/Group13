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
  developer's backend/.cache/;
- no model is downloaded: not the matcher's, the citation check's or memory's. Each loads in a thread of its own,
  which can outlive the test that started it, so a blocker stays in place for the whole session too.
Tests that need LLM answers build a stage with their own FakeLLMClient; tests that need a database build their
own store.
"""

from __future__ import annotations

import os
import threading

os.environ["PYTHON_DOTENV_DISABLED"] = "1"  # before any app import below: app modules call load_dotenv() when used

from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

from app.commerce import CommerceStage
from app.criteria import InMemoryCriteriaStore
from app.db import NullRequestStore, shared_memory_aliases
from app.enrich import EnrichStage
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
def _no_real_usage_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """The stages' shared usage books count in backend/.cache/research/usage.json, the app's real daily caps (US-12).
    A test's fake search must never count there: on 25 Nov 2026 test runs took 38 of the day's 80 Serper units with no
    request sent. So every test gets books with no file; a test that wants a file gives its own."""
    from app.research.settings import load_research_settings
    from app.research.usage import UsageBook

    def book(*_: object) -> UsageBook:
        return UsageBook(load_research_settings(env={}).caps, path=None)

    monkeypatch.setattr("app.commerce.stage._shared_book", book)
    monkeypatch.setattr("app.enrich.stage._shared_book", book)
    monkeypatch.setattr("app.research.stage._shared_book", book)


@pytest.fixture(autouse=True)
def _no_early_search(monkeypatch: pytest.MonkeyPatch) -> None:
    """US-34: the app begins a session's research after its first interview answer. A test's app never does unless
    the test asks (tests/unit/api/test_early_search_api.py), so no other test starts the real stages in a thread."""
    monkeypatch.setattr("app.early_search.DEFAULT_ON", False)


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
    # No YouTube key, whatever a developer's .env holds (an empty value wins over .env), so a live stage built by
    # a test leaves YouTube out unless the test gives it a source (US-38).
    monkeypatch.setenv("YOUTUBE_API_KEY", "")


@pytest.fixture(autouse=True)
def _no_live_commerce(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default stages keep the stub, so no test can ask Serper for prices. Tests of the real stage give it fakes."""
    monkeypatch.setattr("app.pipeline.default_commerce_stage", CommerceStage)


@pytest.fixture(autouse=True)
def _no_live_enrich(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default stages keep the stub, so no test can ask Serper for prices (US-41). Tests of the real stage give it
    fakes."""
    monkeypatch.setattr("app.pipeline.default_enrich_stage", EnrichStage)


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


# Each model loads in a thread of its own, by name: matcher-load (app/entities/model.py), nli-load (app/explain/nli.py)
# and memory-load (app/memory/embedder.py).
_REAL_DOWNLOADS: dict[str, object] = {}


def _late_download(**kwargs: object) -> str:
    raise RuntimeError("no model is downloaded in the tests: this load outlived the test that started it")


@pytest.fixture(autouse=True, scope="session")
def _no_model_downloads_this_session() -> Iterator[None]:
    """A model load runs in a thread of its own, which can reach its download after the test that started it has ended
    and that test's patches are undone (S5-12's CI flake: a late memory download reached huggingface.co during another
    test). For the whole session no download reaches the network; a test marked `live` gets the real one back."""
    from app.entities import model
    from app.explain import nli
    from app.memory import embedder

    found = {module: module._hf_download for module in (model, nli, embedder)}
    for module, download in found.items():
        _REAL_DOWNLOADS.setdefault(module.__name__, download)
        module._hf_download = _late_download  # type: ignore[assignment]
    yield
    for module, download in found.items():
        # A session run inside a test (pytester) gives back that test's guard. The real download never comes back:
        # a load thread still alive as the run ends must not reach the network either.
        if download is not _REAL_DOWNLOADS[module.__name__]:
            module._hf_download = download  # type: ignore[assignment]


class _DownloadGuard:
    """One test's guard on one model's download. Blocks every download; counts, against this test, only those from
    threads started during it, so a load that outlived an earlier test is never blamed here. At the end it waits a
    moment for this test's own load thread, so its attempts are counted where they belong. Threads are kept as
    objects, not idents: a thread that ends can hand its ident to a new one."""

    def __init__(
        self,
        request: pytest.FixtureRequest,
        monkeypatch: pytest.MonkeyPatch,
        module: ModuleType,
        what: str,
        thread_name: str,
    ) -> None:
        self.what = what
        self.tried: list[str] = []
        self._thread_name = thread_name
        self._earlier = set(threading.enumerate()) - {threading.current_thread()}
        if may_use_network(request.node):
            monkeypatch.setattr(module, "_hf_download", _REAL_DOWNLOADS.get(module.__name__, module._hf_download))
        else:
            monkeypatch.setattr(module, "_hf_download", self._no_download)

    def _no_download(self, **kwargs: object) -> str:
        if threading.current_thread() not in self._earlier:
            self.tried.append(str(kwargs.get("filename")))
        raise RuntimeError(f"tests never download the {self.what}")

    def finish(self) -> None:
        for thread in threading.enumerate():
            if thread.name == self._thread_name and thread not in self._earlier:
                thread.join(timeout=2.0)
        assert not self.tried, f"a test tried to download the {self.what}: {self.tried}"


@pytest.fixture(autouse=True)
def _no_real_matcher_model(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """US-15 (S3-15): every test sees the default matcher, whatever the shell sets, and none downloads the model.

    Tests of the model stage give it a fake loader or fake downloads; only tests marked `live` may fetch the real one.
    The model holders last as long as the process, so each test starts with none.
    """
    from app.entities import model

    monkeypatch.delenv("MATCHER", raising=False)
    monkeypatch.delenv("MATCHER_FORCE_FAIL", raising=False)
    guard = _DownloadGuard(request, monkeypatch, model, "matcher model", "matcher-load")
    model.clear_shared_models()
    yield
    guard.finish()
    model.clear_shared_models()


@pytest.fixture(autouse=True)
def _no_real_nli_model(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """US-22 (S5-4): no test downloads the citation check's model, whatever the shell sets.

    The default stage sees NLI=off, so no test loads the model: evidence sentences face the 6-word rule only.
    Tests of the check give it settings and a fake scorer; only tests marked `live` may fetch the real one. The model
    holders last as long as the process, so each test starts with none.
    """
    from app.explain import nli

    if not may_use_network(request.node):
        monkeypatch.setenv("NLI", "off")
    guard = _DownloadGuard(request, monkeypatch, nli, "citation check's model", "nli-load")
    nli.clear_shared_nli()
    yield
    guard.finish()
    nli.clear_shared_nli()


@pytest.fixture(autouse=True)
def _no_real_memory_model(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """US-06: no test downloads memory's embedding model, whatever the shell sets.

    The app's own memory service sees MEMORY=off, so no test starts loading the model; tests of memory build their own
    service with a fake or ready model. Only tests marked `live` may fetch the real one. The service and the model
    holders last as long as the process, so each test starts with none.
    """
    from app import memory
    from app.memory import embedder

    if not may_use_network(request.node):
        monkeypatch.setenv("MEMORY", "off")
    guard = _DownloadGuard(request, monkeypatch, embedder, "memory model", "memory-load")
    memory.default_memory.cache_clear()
    embedder.clear_shared_embedders()
    yield
    guard.finish()
    memory.default_memory.cache_clear()
    embedder.clear_shared_embedders()


@pytest.fixture(autouse=True)
def _fresh_aliases() -> Iterator[None]:
    """The in-memory alias store lives as long as the process (US-15): one test's aliases never reach another."""
    shared_memory_aliases().clear()
    yield
    shared_memory_aliases().clear()
