"""US-34 (S5-7, D-80): the exact-match cache for whole searches. Every request, result and user is made up."""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.cache import results as cache_module
from app.cache.results import CacheKey, ResultCache, as_hit, cache_on, cacheable, key_for, normalised
from app.contracts import SearchRequest, SearchResult, examples
from app.pipeline import SAMPLE_NOTE
from app.research.retention import REDDIT_TEXT_NOT_KEPT

NOW = datetime(2026, 11, 28, 6, 0, tzinfo=UTC)
USER_A, USER_B = str(uuid.UUID(int=1)), str(uuid.UUID(int=2))


def _result(**changes: Any) -> SearchResult:
    """The example result as a full search: every stage ran as itself (the example's enrich fell back)."""
    base = examples.search_result().model_dump()
    reports = [{**report, "status": "ok", "fallback_reason": None} for report in base["stage_reports"]]
    return SearchResult.model_validate({**base, "stage_reports": reports, **changes})


def _request(user: str | None = None, query: str | None = None, **rubric: Any) -> SearchRequest:
    base = examples.search_result().request
    data = {**base.model_dump(), "user_id": user, "session_id": "sess_" + "b" * 24}
    if query is not None:
        data["raw_query"] = query
    if rubric:
        data["rubric"] = {**base.rubric.model_dump(), **rubric}
    return SearchRequest.model_validate(data)


def _cache(tmp_path: Path, now: datetime = NOW, prefs: dict[str, str] | None = None, **kw: Any) -> ResultCache:
    hashes = prefs if prefs is not None else {}
    return ResultCache(tmp_path, preferences=lambda user: hashes.get(user, ""), now=lambda: now, **kw)


def _kept(cache: ResultCache, request: SearchRequest, made: datetime = NOW) -> None:
    assert cache.put(request, _result(finished_at=made, request=request.model_dump()))


# --- the key ----------------------------------------------------------------------------------------------------------


def test_the_same_request_with_other_spacing_or_case_hits(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    request = _request()
    _kept(cache, request)
    again = _request(query=f"  {request.raw_query.upper()}  ")
    assert cache.get(again) is not None
    assert normalised("  Best   PHONE\tunder 30k ") == "best phone under 30k"


def test_a_changed_weight_misses(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    request = _request()
    _kept(cache, request)
    weights = [w.model_dump() for w in request.rubric.weights]
    weights[0], weights[1] = (
        {**weights[0], "normalized": weights[1]["normalized"]},
        {
            **weights[1],
            "normalized": weights[0]["normalized"],
        },
    )
    if weights[0]["normalized"] == weights[1]["normalized"]:  # a tie would swap nothing: move the weight instead
        weights[0] = {**weights[0], "weight": (weights[0]["weight"] + 1) % 10}
    assert cache.get(_request(weights=weights)) is None


def test_a_changed_budget_misses(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    request = _request()
    _kept(cache, request)
    budget = request.rubric.budget
    assert budget is not None
    other = {**budget.model_dump(), "max": {**budget.max.model_dump(), "amount": budget.max.amount + 1000}}
    assert cache.get(_request(budget=other)) is None


def test_another_category_hard_rule_or_request_misses(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    request = _request()
    _kept(cache, request)
    assert cache.get(_request(hard_rules=[])) is None or not request.rubric.hard_rules
    assert cache.get(_request(query="a different made-up request")) is None
    assert cache.get(_request(category="made_up_kind")) is None


def test_a_different_signed_in_user_misses_and_a_guest_never_gets_a_signed_in_result(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    _kept(cache, _request(user=USER_A))
    assert cache.get(_request(user=USER_A)) is not None
    assert cache.get(_request(user=USER_B)) is None
    assert cache.get(_request()) is None  # a guest: no user id in the key, so never user A's entry
    _kept(cache, _request())
    assert cache.get(_request(user=USER_B)) is None  # nor a guest's result for a signed-in shopper


def test_changed_stored_preferences_miss_and_unread_ones_never_serve_or_keep(tmp_path: Path) -> None:
    prefs = {USER_A: "one"}
    cache = _cache(tmp_path, prefs=prefs)
    _kept(cache, _request(user=USER_A))
    prefs[USER_A] = "two"  # an edit to a stored preference (US-06)
    assert cache.get(_request(user=USER_A)) is None

    def unread(user: str) -> str:
        raise ConnectionError("made-up database outage")

    broken = ResultCache(tmp_path, preferences=unread, now=lambda: NOW)
    assert broken.get(_request(user=USER_A)) is None and broken.key(_request(user=USER_A)) is None
    assert not broken.put(_request(user=USER_A), _result())
    assert broken.key(_request()) is not None  # a guest needs no preferences


def test_the_key_holds_no_user_or_preferences_for_a_guest() -> None:
    key = key_for(_request(), "ignored")
    assert key.user is None and key.preferences == ""
    assert CacheKey("a", "b", "c", None, "").name != CacheKey("a", "b", "c", USER_A, "").name


# --- what is kept, and for how long ----------------------------------------------------------------------------------


def test_a_kept_result_holds_no_reddit_text_no_user_and_no_session(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    request = _request(user=USER_A)
    original = _result(request=request.model_dump(), finished_at=NOW)
    reddit = next(s for s in original.sources if s.type.value == "reddit")
    assert cache.put(request, original)
    (path,) = tmp_path.glob("*.json")
    text = path.read_text(encoding="utf-8")
    assert reddit.text not in text and (reddit.title or "x" * 50) not in text and USER_A not in text
    assert request.session_id not in text
    kept, _ = cache.get(request) or (None, None)
    assert kept is not None
    assert next(s for s in kept.sources if s.id == reddit.id).title == REDDIT_TEXT_NOT_KEPT


def test_an_entry_older_than_serve_limit_misses_and_is_deleted(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    request = _request()
    _kept(cache, request, made=NOW - cache.ttl)
    assert cache.get(request) is None and list(tmp_path.glob("*.json")) == []
    assert cache.ttl == cache_module.serve_limit() and cache.ttl < timedelta(hours=24)  # D-53, not a full day
    fresh = _cache(tmp_path)
    _kept(fresh, request, made=NOW - cache.ttl + timedelta(minutes=1))
    assert fresh.get(request) is not None


def test_a_broken_entry_is_deleted_and_the_search_runs(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    request = _request()
    _kept(cache, request)
    (path,) = tmp_path.glob("*.json")
    path.write_text("{not json", encoding="utf-8")
    assert cache.get(request) is None and not path.exists()
    _kept(cache, request)
    entry = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**entry, "key": {**entry["key"], "text": "another"}}), encoding="utf-8")
    assert cache.get(request) is None and not path.exists()  # never another key's result, even on a hash clash


def test_only_a_full_real_result_is_kept(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    request = _request()
    assert cacheable(_result())
    assert not cache.put(request, _result(partial=True))
    assert not cache.put(request, _result(llm_unavailable=True))
    assert not cache.put(request, _result(decision=None))
    assert not cache.put(request, _result(coverage_note=SAMPLE_NOTE))
    assert not cache.put(request, _result(cached=True, cached_at=NOW))  # a hit is never kept again: no new limit
    assert list(tmp_path.glob("*.json")) == []
    assert cache_module.SAMPLE_WORDS in SAMPLE_NOTE.casefold()


def test_the_cache_off_keeps_and_serves_nothing(tmp_path: Path) -> None:
    cache = _cache(tmp_path, enabled=False)
    assert not cache.put(_request(), _result()) and cache.get(_request()) is None
    assert not ResultCache(None).enabled


def test_a_result_that_cannot_be_written_is_not_kept(tmp_path: Path) -> None:
    blocked = tmp_path / "a-file"
    blocked.write_text("x", encoding="utf-8")
    assert not _cache(blocked).put(_request(), _result())  # its folder is a file: nothing kept, nothing raised


def test_an_entry_that_cannot_be_deleted_is_left_for_the_sweep(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = _cache(tmp_path)
    request = _request()
    _kept(cache, request, made=NOW - timedelta(days=2))

    def refuse(self: Path, missing_ok: bool = False) -> None:
        raise PermissionError("made up")

    monkeypatch.setattr(Path, "unlink", refuse)
    assert cache.get(request) is None  # still a miss


def test_the_sweep_deletes_entries_past_the_limit(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    _kept(cache, _request())
    (path,) = tmp_path.glob("*.json")
    old = (NOW - cache.ttl - timedelta(minutes=1)).timestamp()
    os.utime(path, (old, old))
    cache_module.sweep_results(tmp_path, now=lambda: NOW)
    assert not path.exists()
    cache_module.sweep_results()  # the app's own folder (a test's empty one): nothing to do, nothing raised


# --- a hit, and the switch -------------------------------------------------------------------------------------------


def test_a_hit_is_a_new_search_for_this_request_marked_cached(tmp_path: Path) -> None:
    made = NOW - timedelta(hours=3)
    request = _request(user=USER_B)
    hit = as_hit(_result(), made, request, "srch_" + "c" * 24, NOW)
    assert hit.search_id == "srch_" + "c" * 24 and hit.request == request
    assert hit.cached and hit.cached_at == made and hit.started_at == hit.finished_at == NOW and hit.saved


@pytest.mark.parametrize(
    ("value", "on"),
    [(None, True), ("", True), ("on", True), ("ON", True), ("off", False), ("Off ", False), ("x", True)],
)
def test_result_cache_on_or_off(value: str | None, on: bool) -> None:
    assert cache_on({} if value is None else {"RESULT_CACHE": value}) is on


def test_the_default_cache_follows_the_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RESULT_CACHE", "on")
    on = cache_module.default_result_cache()
    assert on.enabled and on.folder == cache_module.RESULTS_DIR
    monkeypatch.setenv("RESULT_CACHE", "off")
    assert not cache_module.default_result_cache().enabled
    assert cache_on() is False


def test_stored_preferences_are_hashed_by_what_can_change_a_search() -> None:
    from types import SimpleNamespace

    rows = [
        SimpleNamespace(
            id=uuid.UUID(int=9),
            kind="weight",
            category="smartphone",
            criterion_key="battery_life",
            text="battery matters",
            weight=8.0,
            rule=None,
            always=False,
            updated_at=NOW,
        )
    ]
    first = cache_module.stored_preferences_hash(USER_A, lambda user: list(rows))
    rows[0].weight = 5.0
    assert cache_module.stored_preferences_hash(USER_A, lambda user: list(rows)) != first
    # The app's own memory (MEMORY=off in tests): no stored preferences, the hash of none.
    assert cache_module.stored_preferences_hash(USER_A) == cache_module.stored_preferences_hash(USER_B, lambda u: [])


def test_with_memory_on_the_users_own_stored_preferences_are_read(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from app import memory

    read: list[uuid.UUID] = []
    service = SimpleNamespace(usable=True, listing=lambda user: read.append(user) or [])

    def fake() -> Any:
        return service

    fake.cache_clear = lambda: None  # type: ignore[attr-defined]  # as the real one, which the test setup clears
    monkeypatch.setattr(memory, "default_memory", fake)
    assert cache_module.stored_preferences_hash(USER_A) == cache_module.stored_preferences_hash(USER_B, lambda u: [])
    assert read == [uuid.UUID(USER_A)]


def test_a_degraded_search_is_never_kept() -> None:
    reports = [r.model_dump() for r in _result().stage_reports]
    for status in ("fallback", "timeout", "failed"):
        changed = [{**reports[0], "status": status, "fallback_reason": "made up"}, *reports[1:]]
        assert not cacheable(_result(stage_reports=changed))
    skipped = [{**reports[0], "status": "skipped", "fallback_reason": "nothing to do"}, *reports[1:]]
    assert cacheable(_result(stage_reports=skipped))  # nothing to do is still a full search


def test_an_entry_with_a_time_without_a_zone_is_broken_and_deleted(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    request = _request()
    _kept(cache, request)
    (path,) = tmp_path.glob("*.json")
    entry = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**entry, "made_at": "2026-11-28T05:00:00"}), encoding="utf-8")
    assert cache.get(request) is None and not path.exists()  # never a crash into the search (rule 9)


def test_a_write_that_fails_leaves_no_half_written_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(source: Any, target: Any) -> None:
        raise PermissionError("made-up lock on Windows")

    monkeypatch.setattr(cache_module.os, "replace", refuse)
    assert not _cache(tmp_path).put(_request(), _result(finished_at=NOW))
    assert list(tmp_path.iterdir()) == []  # its words never stay behind


def test_the_sweep_deletes_old_half_written_files_and_keeps_new_ones(tmp_path: Path) -> None:
    old, new = tmp_path / "a.json.1.writing", tmp_path / "b.json.2.writing"
    for path in (old, new):
        path.write_text("x", encoding="utf-8")
    stamp = (NOW - timedelta(hours=1)).timestamp()
    os.utime(old, (stamp, stamp))
    os.utime(new, (NOW.timestamp(), NOW.timestamp()))
    _cache(tmp_path).sweep()
    assert not old.exists() and new.exists()
    ResultCache(None).sweep()  # no folder: nothing to do


def test_the_sweep_and_forgetting_carry_on_past_a_folder_or_file_they_cannot_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = _cache(tmp_path)
    _kept(cache, _request(user=USER_A))
    (tmp_path / "x.json.3.writing").write_text("x", encoding="utf-8")
    real_stat = Path.stat

    def stat(self: Path, **kw: Any) -> Any:
        if self.name.endswith(".writing"):
            raise OSError("made up")
        return real_stat(self, **kw)

    monkeypatch.setattr(Path, "stat", stat)
    cache.sweep()  # one file it can't look at: skipped, never raised
    monkeypatch.undo()

    def unreadable(self: Path, pattern: str) -> Any:
        raise OSError("made up")

    monkeypatch.setattr(Path, "glob", unreadable)
    cache.sweep()
    assert cache.forget_user(USER_A) == 0  # the folder can't be read: nothing deleted, nothing raised


def test_a_deleted_accounts_kept_results_are_forgotten(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = _cache(tmp_path)
    _kept(cache, _request(user=USER_A))
    _kept(cache, _request(user=USER_B))
    _kept(cache, _request())
    (tmp_path / "broken.json").write_text("{", encoding="utf-8")
    assert cache.forget_user(USER_A) == 1
    assert cache.get(_request(user=USER_A)) is None and cache.get(_request(user=USER_B)) is not None
    assert cache.get(_request()) is not None
    assert ResultCache(None).forget_user(USER_A) == 0
    monkeypatch.setattr(cache_module, "RESULTS_DIR", tmp_path)
    assert cache_module.forget_user(USER_B) == 1  # the account route's call, whatever RESULT_CACHE says
