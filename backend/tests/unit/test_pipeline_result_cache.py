"""US-34 (S5-7, D-80): the runner answers an exact repeat from the result cache, and keeps full results for one.

Every stage is its stub and every request is made up; the cache lives in the test's own folder."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.search_runs import RunSettings, SearchRuns, get_search_runs
from app.cache.results import CacheKey, Lookup, ResultCache
from app.contracts import SearchRequest, SearchResult, examples
from app.main import create_app
from app.pipeline import CancelFlag, InMemoryResultStore, PipelineRunner, SearchCancelled
from tests.unit.test_sample_label import stub_stages


def _request(session: str = "sess_" + "d" * 24) -> SearchRequest:
    return SearchRequest.model_validate({**examples.search_result().request.model_dump(), "session_id": session})


def _full(**changes: Any) -> SearchResult:
    """The example result as a full search: every stage ran as itself (the example's enrich fell back)."""
    base = examples.search_result().model_dump()
    reports = [{**report, "status": "ok", "fallback_reason": None} for report in base["stage_reports"]]
    return SearchResult.model_validate({**base, "stage_reports": reports, **changes})


def _seeded(folder: Path, request: SearchRequest, **kw: Any) -> ResultCache:
    """A cache holding the example result for this request, made just now."""
    cache = ResultCache(folder, preferences=lambda user: "", **kw)
    made = _full(request=request.model_dump(), finished_at=datetime.now(UTC))
    assert ResultCache(folder, preferences=lambda user: "").put(request, made)
    return cache


class Untouched:
    """Stages that fail the test if a search runs them."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"a cached search ran its {name} stage")


def test_a_repeat_answers_from_the_cache_as_a_new_saved_search(tmp_path: Path) -> None:
    first = _request()
    store = InMemoryResultStore()
    runner = PipelineRunner(Untouched(), store, cache=_seeded(tmp_path, first))  # type: ignore[arg-type]
    repeat = _request("sess_" + "e" * 24)  # another session: the same request, category and priorities
    result = runner.run(repeat)
    assert result.cached and result.cached_at is not None and result.request == repeat
    assert result.search_id != examples.search_result().search_id and result.saved
    assert store.results == [result]  # saved as a normal search is


def test_a_miss_runs_the_stages_and_offers_the_saved_result_to_the_cache() -> None:
    key = CacheKey("made up", "laptop", "r", None, "")

    class Recording:
        def __init__(self) -> None:
            self.looked: list[SearchRequest] = []
            self.kept: list[tuple[CacheKey | None, SearchResult]] = []

        def lookup(self, request: SearchRequest) -> Lookup:
            self.looked.append(request)
            return Lookup(key)

        def keep(self, found: CacheKey | None, result: SearchResult) -> bool:
            self.kept.append((found, result))
            return False

    cache = Recording()
    result = PipelineRunner(stub_stages(), InMemoryResultStore(), cache=cache).run(_request())  # type: ignore[arg-type]
    assert not result.cached and cache.looked == [_request()]
    assert cache.kept == [(key, result)]  # the result as saved, under the key looked up once at the start


def test_with_the_cache_off_every_search_runs(tmp_path: Path) -> None:
    request = _request()
    cache = _seeded(tmp_path, request, enabled=False)
    store = InMemoryResultStore()
    runner = PipelineRunner(stub_stages(), store, cache=cache)
    assert not runner.run(request).cached and not runner.run(request).cached
    assert len(store.results) == 2 and len(list(tmp_path.glob("*.json"))) == 1  # the kept one is never served


def test_a_cancelled_repeat_is_never_saved(tmp_path: Path) -> None:
    request = _request()
    flag = CancelFlag()
    flag.request()
    store = InMemoryResultStore()
    with pytest.raises(SearchCancelled):
        PipelineRunner(Untouched(), store, cache=_seeded(tmp_path, request)).run(request, cancel=flag)  # type: ignore[arg-type]

    class LateCancel:
        """Not cancelled when looked at first; cancelled by the time the save would start."""

        def __init__(self) -> None:
            self.looks = 0

        @property
        def requested(self) -> bool:
            self.looks += 1
            return self.looks > 1

        def start_saving(self) -> bool:
            return False

    with pytest.raises(SearchCancelled):
        PipelineRunner(Untouched(), store, cache=_seeded(tmp_path, request)).run(request, cancel=LateCancel())  # type: ignore[arg-type]
    assert store.results == []


def test_a_repeat_through_the_api_answers_from_the_cache_within_3_seconds(tmp_path: Path) -> None:
    request = _request()
    cache = _seeded(tmp_path, request)
    store = InMemoryResultStore()
    runs = SearchRuns(lambda: PipelineRunner(Untouched(), store, cache=cache), RunSettings(workers=2))  # type: ignore[arg-type]
    app = create_app(env={})
    app.dependency_overrides[get_search_runs] = lambda: runs
    seconds: list[float] = []
    try:
        with TestClient(app) as client:
            for _ in range(10):  # D-80 item 4: 10 repeats, end to end
                started = time.monotonic()
                search_id = client.post("/api/search/runs", json=request.model_dump(mode="json")).json()["search_id"]
                while (state := client.get(f"/api/search/runs/{search_id}").json())["status"] in ("queued", "running"):
                    time.sleep(0.01)
                seconds.append(time.monotonic() - started)
                assert state["status"] == "done" and state["result"]["cached"] is True
                assert state["result"]["cached_at"] is not None
    finally:
        runs.close()
    assert max(seconds) <= 3.0 and len(store.results) == 10


def test_the_apis_runners_carry_the_result_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api.search_runs import default_runner

    monkeypatch.setenv("RESULT_CACHE", "on")
    runner = default_runner()
    assert isinstance(runner.cache, ResultCache) and runner.cache.enabled
    monkeypatch.setenv("RESULT_CACHE", "off")
    assert default_runner().cache is not None and not default_runner().cache.enabled  # type: ignore[union-attr]


def test_a_kept_result_that_cannot_be_served_runs_the_search(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app import pipeline

    def broken(*args: Any) -> SearchResult:
        raise ValueError("made-up mismatch")

    monkeypatch.setattr(pipeline, "as_hit", broken)
    request = _request()
    result = PipelineRunner(stub_stages(), InMemoryResultStore(), cache=_seeded(tmp_path, request)).run(request)
    assert not result.cached  # the search ran instead (build rule 9)


def test_a_cached_runs_sources_come_from_its_kept_citations(tmp_path: Path) -> None:
    request = _request()
    runs = SearchRuns(lambda: PipelineRunner(Untouched(), InMemoryResultStore(), cache=_seeded(tmp_path, request)))  # type: ignore[arg-type]
    app = create_app(env={})
    app.dependency_overrides[get_search_runs] = lambda: runs
    try:
        with TestClient(app) as client:
            search_id = client.post("/api/search/runs", json=request.model_dump(mode="json")).json()["search_id"]
            while client.get(f"/api/search/runs/{search_id}").json()["status"] in ("queued", "running"):
                time.sleep(0.01)
            value = next(
                v
                for p in examples.search_result().products
                for v in p.values
                if v.state.value == "known" and v.supporting_claim_ids
            )
            params = {"product_id": value.product_id, "criterion_key": value.criterion_key}
            response = client.get(f"/api/search/runs/{search_id}/evidence", params=params)
    finally:
        runs.close()
    assert response.status_code == 200, response.text  # never "sources gone" on a repeat (US-21, US-34)
    assert response.json()["claims"]


def test_post_api_search_carries_the_result_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.main as main

    made: list[Any] = []

    class Runner:
        def __init__(self, stages: Any, store: Any, cache: Any = None) -> None:
            made.append(cache)

        def run(self, request: SearchRequest) -> SearchResult:
            return examples.search_result()

    monkeypatch.setattr(main, "PipelineRunner", Runner)
    monkeypatch.setattr(main, "default_search_stages", lambda: None)
    with TestClient(create_app(env={})) as client:
        assert client.post("/api/search", json=_request().model_dump(mode="json")).status_code == 200
    assert isinstance(made[0], ResultCache) and not made[0].enabled  # RESULT_CACHE=off in tests
