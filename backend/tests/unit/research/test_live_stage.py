"""The live research stage in the whole pipeline: progress counts, stub stages, coverage note and the CLI (US-12)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import yaml

from app.cli import main as cli_main
from app.contracts import ProgressEvent, SearchRequest, SourceType, StageName
from app.contracts.research import ResearchResult
from app.pipeline import InMemoryResultStore, PipelineRunner, build_coverage_note, default_search_stages
from app.research import ResearchStage, default_research_stage
from app.research.serper import SerperResult
from app.research.settings import DEFAULT_CONFIG_PATH, ProviderCap, ResearchSettings, load_research_settings
from app.research.stage import LiveResearchStage
from app.research.usage import UsageBook
from tests.fakes.research import FakePages, FakeReddit, FakeSerper, review_source, rubric, thread, web_source

REVIEW = "https://www.reviews.example/nova"


def _stage() -> LiveResearchStage:
    return LiveResearchStage(
        settings=ResearchSettings(review_domains=("reviews.example",), deadline_s=5.0),
        serper=FakeSerper(default=SerperResult(sources=(web_source("https://web.example/a"), web_source(REVIEW)))),  # type: ignore[arg-type]
        pages=FakePages(pages={REVIEW: review_source(REVIEW)}),  # type: ignore[arg-type]
        reddit=FakeReddit(threads=[thread("aaa")]),  # type: ignore[arg-type]
        usage=UsageBook(
            {
                "serper": ProviderCap(per_search=6, per_day=60),
                "reddit_archive": ProviderCap(per_search=10, per_day=500),
                "pages": ProviderCap(per_search=16, per_day=200),
            },
            path=None,
        ),
        pool=ThreadPoolExecutor(max_workers=6),
    )


def _runner() -> PipelineRunner:
    stages = default_search_stages()
    stages.research = _stage()
    return PipelineRunner(stages, InMemoryResultStore())


def _request() -> SearchRequest:
    return SearchRequest(session_id="sess_aaaaaaaaaaaaaaaaaaaaaaaa", raw_query="a made-up request", rubric=rubric())


def test_progress_reports_counts_per_source_type_from_the_live_stage() -> None:
    events: list[ProgressEvent] = []
    _runner().run(_request(), progress=events.append)
    done = next(e for e in events if e.stage is StageName.RESEARCH and e.status != "started")
    assert done.sources_found[SourceType.REDDIT] == 1
    assert done.sources_found[SourceType.WEB_SEARCH] == 1
    assert done.sources_found[SourceType.REVIEW_SITE] == 1


def test_live_research_with_stub_stages_finishes_with_the_sample_label() -> None:
    result = _runner().run(_request())
    assert {s.type for s in result.sources} == {SourceType.REDDIT, SourceType.WEB_SEARCH, SourceType.REVIEW_SITE}
    assert result.coverage_note is not None
    assert "sample results" in result.coverage_note


def test_an_older_archive_is_named_in_the_coverage_note() -> None:
    assert "older archive" not in (build_coverage_note([], [], False, older_archive=False) or "")
    assert "older archive (to May 2025)" in (build_coverage_note([], [], False, older_archive=True) or "")


def test_a_pullpush_thread_puts_the_older_archive_in_the_result() -> None:
    from app.research.reddit.base import make_source
    from tests.fakes.reddit import NOW

    old = make_source(
        origin="pullpush",
        subreddit="laptops",
        thread_id="abc",
        title="A title",
        text="A title",
        published_at=NOW,
        author_digest=None,
        fetched_at=NOW,
    )
    assert old is not None
    stages = default_search_stages()
    stage = _stage()
    stage.reddit = FakeReddit(threads=[old])  # type: ignore[assignment]
    stages.research = stage
    result = PipelineRunner(stages, InMemoryResultStore()).run(_request())
    assert result.coverage_note is not None
    assert "older archive" in result.coverage_note


def test_the_arctic_shift_path_does_not_mention_the_older_archive() -> None:
    result = _runner().run(_request())
    assert "older archive" not in (result.coverage_note or "")


def test_tests_get_the_stub_for_default_stages_so_nothing_can_reach_the_network() -> None:
    assert isinstance(default_search_stages().research, ResearchStage)


def test_the_app_builds_the_live_stage_when_nothing_replaces_it() -> None:
    assert isinstance(default_research_stage(), LiveResearchStage)


def test_the_cli_prints_sources_times_and_usage(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("app.pipeline.default_research_stage", _stage)
    assert cli_main(["best laptop for coding under 70k"]) == 0
    out = capsys.readouterr().out
    assert "Sources (3):" in out
    assert "reddit" in out and "web_search" in out and "review_site" in out
    assert REVIEW in out
    assert "2026-11-03" in out  # each source's fetch time
    assert "Time per source:" in out
    assert "total" in out
    assert "Provider use" in out
    assert "Comparison query: none" in out
    assert "serper" in out and "reddit_archive" in out and "pages" in out


def test_the_cli_shows_no_research_block_for_the_stub(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli_main(["best laptop for coding under 70k"]) == 0
    assert "Time per source:" not in capsys.readouterr().out


# --- settings --------------------------------------------------------------------------------------


def test_the_source_caps_stay_under_the_contract_limit() -> None:
    settings = load_research_settings(env={})
    assert settings.max_reddit_threads + settings.max_web_results + settings.max_review_pages <= 60
    limit = ResearchResult.model_fields["sources"].metadata[0].max_length
    assert limit == 60


def test_caps_over_the_contract_limit_are_refused() -> None:
    with pytest.raises(ValueError, match="60"):
        ResearchSettings(max_reddit_threads=30, max_web_results=30, max_review_pages=12)


def test_the_config_file_names_the_four_required_review_sites() -> None:
    config = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    assert {"91mobiles.com", "smartprix.com", "digit.in", "gadgets360.com"} <= set(config["review_domains"])
    settings = load_research_settings(env={})
    assert settings.pool_size == 6
    assert settings.caps["serper"].per_search == 6
    assert settings.caps["serper"].total == 2400
    # Every page a search may read, plus a robots file per site, must fit its cap, or a normal search would say it ran
    # out of quota.
    assert settings.caps["pages"].per_search >= settings.max_review_pages + len(settings.review_domains)


def test_a_broken_config_file_uses_the_defaults(tmp_path: Path) -> None:
    broken = tmp_path / "research.yaml"
    broken.write_text("pool_size: [not a number", encoding="utf-8")
    assert load_research_settings(broken, env={}).pool_size == 6
    wrong = tmp_path / "wrong.yaml"
    wrong.write_text("pool_size: -3\n", encoding="utf-8")
    assert load_research_settings(wrong, env={}).pool_size == 6


def test_environment_switches_turn_the_cache_and_pages_off() -> None:
    settings = load_research_settings(env={"RESEARCH_CACHE": "off", "RESEARCH_PAGES": "off"})
    assert not settings.cache_on
    assert not settings.pages_on
    on = load_research_settings(env={})
    assert on.cache_on and on.pages_on
