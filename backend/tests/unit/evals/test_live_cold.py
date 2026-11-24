"""S5-6a (US-14, US-46): what the live briefs' record says about a cold run. The funnel in eight steps, every model
attempt minute by minute per provider, and a cold start that sets the research cache aside without touching its usage
counts or Reddit's cache. Offline: the research stub's example sources and fake models."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.contracts import DataClass, LLMCallRecord, LLMTask, Provider
from evals import live_briefs

T0 = datetime(2026, 11, 24, 0, 30, tzinfo=UTC)


def call(provider: Provider, outcome: str, seconds: float, sent: bool = True) -> LLMCallRecord:
    return LLMCallRecord(
        task=LLMTask.EXTRACT_CLAIMS,
        data_class=DataClass.PUBLIC,
        provider=provider,
        model="m",
        input_tokens=900 if sent else 0,
        output_tokens=200 if outcome == "ok" else 0,
        duration_ms=1000,
        outcome=outcome,  # type: ignore[arg-type]
        at=T0 + timedelta(seconds=seconds),
    )


def test_an_attempt_held_by_our_own_pacing_is_no_room_and_a_providers_refusal_is_a_429() -> None:
    assert live_briefs.attempt_kind(call(Provider.GROQ, "rate_limited", 0, sent=False)) == "no_room"
    assert live_briefs.attempt_kind(call(Provider.GROQ, "skipped_budget", 0, sent=False)) == "no_room"
    assert live_briefs.attempt_kind(call(Provider.GROQ, "rate_limited", 0)) == "refused_429"
    assert live_briefs.attempt_kind(call(Provider.GROQ, "ok", 0)) == "answered"
    assert live_briefs.attempt_kind(call(Provider.GEMINI, "timeout", 0)) == "timeout"
    assert live_briefs.attempt_kind(call(Provider.MISTRAL, "invalid_output", 0)) == "other"


def test_attempts_are_counted_minute_by_minute_per_provider() -> None:
    calls = [
        call(Provider.GROQ, "ok", 5),
        call(Provider.GROQ, "rate_limited", 20, sent=False),
        call(Provider.GROQ, "rate_limited", 50),
        call(Provider.GEMINI, "timeout", 59),
        call(Provider.GROQ, "ok", 61),
        call(Provider.GROQ, "ok", -30),  # before the brief began: another brief's
        call(Provider.MISTRAL, "ok", 200),
    ]
    assert live_briefs.attempts_by_minute(calls, T0) == [
        {
            "minute": 0,
            "providers": {
                "gemini": {"sent": 1, "timeout": 1},
                "groq": {"sent": 2, "answered": 1, "no_room": 1, "refused_429": 1},
            },
        },
        {"minute": 1, "providers": {"groq": {"sent": 1, "answered": 1}}},
        {"minute": 3, "providers": {"mistral": {"sent": 1, "answered": 1}}},
    ]


def test_a_cold_start_sets_the_research_cache_aside_and_keeps_its_usage_counts(tmp_path: Path) -> None:
    cache = tmp_path / ".cache" / "research"
    reddit = tmp_path / ".cache" / "reddit"
    for folder in ("searches", "sources", "text"):
        (cache / folder).mkdir(parents=True)
        (cache / folder / "one.json").write_text("{}", encoding="utf-8")
    (cache / "usage.json").write_text('{"today": {"serper": 12}}', encoding="utf-8")
    reddit.mkdir(parents=True)
    (reddit / "listing.json").write_text("{}", encoding="utf-8")
    aside = tmp_path / ".cache" / "research-aside"

    assert live_briefs.set_aside_research_cache(cache, aside) == 3
    assert sorted(p.name for p in cache.iterdir()) == ["usage.json"]  # the caps still hold
    assert (cache / "usage.json").read_text(encoding="utf-8") == '{"today": {"serper": 12}}'
    (moved,) = list(aside.iterdir())
    assert sorted(p.name for p in moved.iterdir()) == ["searches", "sources", "text"]  # moved, never deleted
    assert (reddit / "listing.json").is_file()  # Reddit's cache is never touched
    assert live_briefs.set_aside_research_cache(tmp_path / "none", aside) == 0


def test_the_cold_option_is_read_and_each_cold_brief_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    opts = live_briefs.options(["--categories", "laptop", "--cold", "--budget-llm", "45"])
    assert opts.cold is True
    moved: list[int] = []
    restored: list[int] = []
    monkeypatch.setattr(live_briefs, "set_aside_research_cache", lambda: moved.append(1) or 0)
    monkeypatch.setattr(live_briefs, "restore_research_cache", lambda: restored.append(1) or 0)
    monkeypatch.setattr("scripts.eval_trust.current_commit", lambda: "abc1234")
    budget = live_briefs.LiveBudget(opts.budget)
    record: dict[str, Any] = live_briefs.run_all(opts, budget, lambda c, q: {"category": c, "finished": False})
    assert moved == [1]
    assert restored == [1, 1]  # an earlier run's leftovers first, then this brief's folders, back under the expiry
    assert record["briefs"][0]["cache"] == "cold"


def test_a_brief_records_its_eight_steps_and_its_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.extract import GuardedExtractStage
    from app.extract.settings import ExtractSettings, load_extract_settings
    from tests.unit.evals.test_live_funnel import _QuotingClient

    client = _QuotingClient()
    settings = load_extract_settings(env={})
    small = ExtractSettings.model_validate({**settings.model_dump(), "max_sources_per_search": 4})
    monkeypatch.setattr("app.pipeline.default_extract_stage", lambda: GuardedExtractStage(client, small))
    brief = live_briefs.run_brief("laptop", live_briefs.QUERIES["laptop"])
    steps = brief["steps"]
    assert list(steps) == [
        "sources_found",
        "read",
        "extracted",
        "claims_kept",
        "products_kept",
        "with_a_price",
        "with_any_score",
        "ranked",
    ]
    assert steps["sources_found"] >= steps["read"] >= steps["extracted"] > 0
    assert steps["claims_kept"] == brief["funnel"]["claims"]["kept"]
    assert steps["products_kept"] >= steps["with_any_score"] >= steps["ranked"]
    assert isinstance(brief["attempts_by_minute"], list)
    assert set(brief["began_utc"]) == {"hour", "minute"}
    assert live_briefs.text_in(brief) == []


def test_what_s5_6a_adds_passes_the_fixed_label_check_and_a_refusal_names_only_paths() -> None:
    """S5-6a's first live run was refused for its own new fields ("429" as a key, "cold" as a value)."""
    minutes = live_briefs.attempts_by_minute(
        [call(Provider.GROQ, "rate_limited", 5), call(Provider.GEMINI, "timeout", 7)], T0
    )
    brief = {"cache": "cold", "attempts_by_minute": minutes, "began_utc": {"hour": 0, "minute": 30}}
    assert live_briefs.text_in(brief) == []
    assert live_briefs.text_in({**brief, "cache": "as_found"}) == []
    leaky = {"briefs": [{"cache": "Nova Book 14", "per": {"A quote!": 1}}]}
    assert live_briefs.text_paths(leaky) == ["briefs.0.per", "briefs.0.cache"] or sorted(
        live_briefs.text_paths(leaky)
    ) == ["briefs.0.cache", "briefs.0.per"]
    assert all("Nova" not in p and "quote" not in p for p in live_briefs.text_paths(leaky))


def test_answer_sizes_give_each_answers_tokens_and_seconds_per_provider_and_outcome() -> None:
    calls = [
        call(Provider.GEMINI, "invalid_output", 3),
        call(Provider.GEMINI, "ok", 9),
        call(Provider.GROQ, "rate_limited", 10, sent=False),  # held: nothing answered, so no size
        call(Provider.GEMINI, "ok", -5),  # before the brief began
    ]
    sizes = live_briefs.answer_sizes(calls, T0)
    assert sizes == {"gemini": {"invalid_output": [[0, 1.0]], "ok": [[200, 1.0]]}}
    assert live_briefs.text_in({"answer_sizes": sizes}) == []


def test_set_aside_files_go_back_under_the_caches_own_expiry(tmp_path: Path) -> None:
    """D-53: some research folders hold Reddit titles, links and threads, and only the cache's own sweep deletes them in
    time. Every set-aside file goes back; one whose key the cache holds again goes beside it, never served, and the
    sweep deletes it by its own age."""
    import os
    import time

    from app.research.cache import sweep_expired_files

    cache, aside = tmp_path / "research", tmp_path / "research-aside"
    (cache / "searches").mkdir(parents=True)
    (cache / "searches" / "k1.json").write_text('{"new": 1}', encoding="utf-8")
    stamp = aside / "20261124T000000"
    (stamp / "searches").mkdir(parents=True)
    (stamp / "searches" / "k1.json").write_text('{"old": 1}', encoding="utf-8")
    (stamp / "searches" / "k2.json").write_text('{"old": 2}', encoding="utf-8")
    two_days_ago = time.time() - 2 * 86_400
    os.utime(stamp / "searches" / "k1.json", (two_days_ago, two_days_ago))

    assert live_briefs.restore_research_cache(cache, aside) == 2
    assert list(aside.iterdir()) == []
    assert sorted(p.name for p in (cache / "searches").iterdir()) == [
        "k1.aside-20261124T000000.json",
        "k1.json",
        "k2.json",
    ]
    assert (cache / "searches" / "k1.json").read_text(encoding="utf-8") == '{"new": 1}'  # the cache's own copy stays
    sweep_expired_files(cache, tmp_path / "reddit")
    assert sorted(p.name for p in (cache / "searches").iterdir()) == ["k1.json", "k2.json"]
    assert live_briefs.restore_research_cache(cache, tmp_path / "none") == 0


def test_an_answer_between_the_two_rooms_came_from_the_bigger_retry_and_was_not_cut_off() -> None:
    from app.contracts import DataClass

    def sized(tokens: int) -> LLMCallRecord:
        return LLMCallRecord.model_validate(
            {**call(Provider.GEMINI, "invalid_output", 1).model_dump(), "output_tokens": tokens}
        )

    reserve = {DataClass.PUBLIC: 2000}
    assert live_briefs._cut_off(sized(1996), reserve, {})
    assert not live_briefs._cut_off(sized(2600), reserve, {})  # the bigger retry's answer, unusable but whole
    assert live_briefs._cut_off(sized(3996), reserve, {})  # the bigger retry, cut off too
    assert not live_briefs._cut_off(sized(800), reserve, {})
