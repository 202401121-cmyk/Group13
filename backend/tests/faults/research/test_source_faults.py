"""One failing source never stops the others, and the stage always returns before its limit (US-12, US-27)."""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.contracts import (
    STAGE_TIME_LIMITS_S,
    ResearchInput,
    ResearchResult,
    SourceFailure,
    SourceType,
    StageContext,
    StageName,
)
from app.pipeline import build_coverage_note
from app.research.client import ClientUnavailable
from app.research.pages import PageResult
from app.research.serper import SerperResult, shared_limiter
from app.research.settings import ProviderCap, ResearchSettings
from app.research.stage import LiveResearchStage
from app.research.usage import UsageBook
from tests.fakes.research import FakePages, FakeReddit, FakeSerper, review_source, rubric, thread, web_source

REVIEW = "https://www.reviews.example/nova"


def _settings(**overrides: object) -> ResearchSettings:
    values: dict[str, object] = {"review_domains": ("reviews.example",), "deadline_s": 5.0}
    return ResearchSettings(**{**values, **overrides})  # type: ignore[arg-type]


def _book(serper: int = 6, per_day: int = 60) -> UsageBook:
    return UsageBook(
        {
            "serper": ProviderCap(per_search=serper, per_day=per_day),
            "reddit_archive": ProviderCap(per_search=10, per_day=500),
            "pages": ProviderCap(per_search=16, per_day=200),
        },
        path=None,
    )


def _stage(
    *,
    serper: object = None,
    pages: object = None,
    reddit: object = None,
    settings: ResearchSettings | None = None,
    book: UsageBook | None = None,
) -> LiveResearchStage:
    return LiveResearchStage(
        settings=settings or _settings(),
        serper=serper if serper is not None else FakeSerper(default=_web()),  # type: ignore[arg-type]
        pages=pages if pages is not None else FakePages(pages={REVIEW: review_source(REVIEW)}),  # type: ignore[arg-type]
        reddit=reddit if reddit is not None else FakeReddit(threads=[thread("aaa")]),  # type: ignore[arg-type]
        usage=book or _book(),
        pool=ThreadPoolExecutor(max_workers=6),
    )


def _web() -> SerperResult:
    return SerperResult(sources=(web_source("https://web.example/a"), web_source(REVIEW)))


def _run(stage: LiveResearchStage) -> ResearchResult:
    return stage.run(ResearchInput(rubric=rubric()), StageContext(search_id="srch_test"))


def _types(result: ResearchResult) -> set[SourceType]:
    return {s.type for s in result.sources}


def _reasons(result: ResearchResult) -> dict[SourceType, str]:
    return {f.source_type: f.reason for f in result.failures}


def _note(result: ResearchResult) -> str:
    return build_coverage_note([], result.failures, False) or ""


# --- every source reached, when all is well --------------------------------------------------------


def test_at_least_three_source_types_are_reached() -> None:
    result = _run(_stage())
    assert _types(result) == {SourceType.REDDIT, SourceType.WEB_SEARCH, SourceType.REVIEW_SITE}
    assert result.failures == []


def test_the_live_stage_returns_a_valid_research_result() -> None:
    result = _run(_stage())
    assert ResearchResult.model_validate(result.model_dump()) == result
    assert all(s.fetched_at.tzinfo is not None and s.url for s in result.sources)
    assert 4 <= len(result.queries) <= 7


def test_a_review_page_replaces_its_snippet() -> None:
    result = _run(_stage())
    same_url = [s for s in result.sources if str(s.url) == REVIEW]
    assert [s.type for s in same_url] == [SourceType.REVIEW_SITE]


def test_a_failed_page_keeps_its_snippet() -> None:
    result = _run(_stage(pages=FakePages(reasons={REVIEW: "blocked"})))
    same_url = [s for s in result.sources if str(s.url) == REVIEW]
    assert [s.type for s in same_url] == [SourceType.WEB_SEARCH]
    assert _reasons(result)[SourceType.REVIEW_SITE] == "blocked"


def test_a_result_is_fetched_as_a_page_once_however_many_queries_found_it() -> None:
    pages = FakePages(pages={REVIEW: review_source(REVIEW)})
    _run(_stage(pages=pages))
    assert pages.calls == [REVIEW]


def test_review_queries_ask_for_the_configured_sites() -> None:
    serper = FakeSerper(default=_web())
    _run(_stage(serper=serper))
    sites = {query: found for query, found in serper.calls}
    assert sites["laptop for coding review India"] == ("reviews.example",)
    assert sites["best laptop for coding under 70000 rupees India"] == ()


def test_the_page_caps_hold() -> None:
    links = tuple(web_source(f"https://reviews.example/p{i}") for i in range(10))
    links += tuple(web_source(f"https://{d}.reviews2.example/p{i}") for i in range(3) for d in "ab")
    pages = FakePages()
    settings = _settings(
        max_pages_per_domain=2, max_review_pages=3, review_domains=("reviews.example", "reviews2.example")
    )
    _run(_stage(serper=FakeSerper(default=SerperResult(sources=links)), pages=pages, settings=settings))
    assert len(pages.calls) == 3
    assert sum("reviews.example" in url for url in pages.calls) == 2


def test_the_source_totals_stay_within_each_cap() -> None:
    links = tuple(web_source(f"https://web.example/{i}") for i in range(30))
    threads = [thread(f"t{i}", f"Topic number{i} words{i}") for i in range(30)]
    settings = _settings(max_reddit_threads=4, max_web_results=7)
    result = _run(
        _stage(
            serper=FakeSerper(default=SerperResult(sources=links)),
            reddit=FakeReddit(threads=threads),
            settings=settings,
        )
    )
    assert sum(s.type is SourceType.REDDIT for s in result.sources) == 4
    assert sum(s.type is SourceType.WEB_SEARCH for s in result.sources) == 7


class _SlowFirst(FakeSerper):
    """The earlier queries answer last, so the order of finishing is the reverse of the order of asking."""

    def search(self, query, region, usage, sites=()):  # type: ignore[no-untyped-def]
        slow = {"best laptop for coding under 70000 rupees India": 0.2, "laptop for coding review India": 0.1}
        time.sleep(slow.get(query, 0.0))
        return super().search(query, region, usage, sites)


def test_the_same_inputs_give_the_same_order_however_the_threads_finish() -> None:
    def answers(q: str) -> SerperResult:
        return SerperResult(sources=(web_source(f"https://web.example/{queries.index(q)}"),))

    queries = [
        "best laptop for coding under 70000 rupees India",
        "laptop for coding review India",
        "laptop buying guide India",
        "laptop for coding recommendations",
        "laptop under 70000 rupees review India",
        "laptop for coding comparison India",
    ]
    fixed = {q: answers(q) for q in queries}
    first = _run(_stage(serper=FakeSerper(answers=fixed)))
    second = _run(_stage(serper=_SlowFirst(answers=fixed)))
    assert [str(s.url) for s in first.sources] == [str(s.url) for s in second.sources]
    web = [str(s.url) for s in second.sources if s.type is SourceType.WEB_SEARCH]
    assert web == sorted(web)


# --- the comparison query and Serper's cap ---------------------------------------------------------


def _titled(*titles: str) -> SerperResult:
    return SerperResult(sources=tuple(web_source(f"https://web.example/{i}", t) for i, t in enumerate(titles)))


def test_the_comparison_query_never_takes_serper_past_6() -> None:
    serper = FakeSerper(default=_titled("Acer Swift 3 review", "Asus Vivobook 15 review"))
    reddit = FakeReddit(threads=[thread("aaa", "Acer Swift 3 owner"), thread("bbb", "Asus Vivobook 15 owner")])
    result = _run(_stage(serper=serper, reddit=reddit))
    assert len(serper.calls) == 6
    assert serper.queries[-1] == "Acer Swift 3 vs Asus Vivobook 15"
    assert "laptop for coding comparison India" not in serper.queries
    assert "Acer Swift 3 vs Asus Vivobook 15" in result.queries


def test_without_candidates_the_held_slot_runs_the_sixth_query() -> None:
    serper = FakeSerper(default=_web())
    _run(_stage(serper=serper))
    assert len(serper.calls) == 6
    assert serper.queries[-1] == "laptop for coding comparison India"


def test_the_held_slot_waits_for_reddit_so_its_titles_count() -> None:
    """Serper answers long before Reddit's listing does. Without waiting, Reddit's titles could never count."""
    serper = FakeSerper(default=SerperResult())
    threads = [thread("aaa", "Acer Swift 3 owner"), thread("bbb", "Asus Vivobook 15 owner")]
    threads += [thread("ccc", "Acer Swift 3 notes", text="x"), thread("ddd", "Asus Vivobook 15 notes", text="x")]
    result = _run(_stage(serper=serper, reddit=FakeReddit(threads=threads, delay_s=0.2)))
    assert len(serper.calls) == 6  # the slot ran once, after the listing
    assert serper.queries[-1] == "Acer Swift 3 vs Asus Vivobook 15"
    assert "Acer Swift 3 vs Asus Vivobook 15" in result.queries


def test_titles_cut_by_the_thread_cap_still_count_for_candidates() -> None:
    serper = FakeSerper(default=SerperResult())
    threads = [thread(f"t{i}", f"Topic number{i} words{i}") for i in range(4)]
    threads += [thread("aaa", "Acer Swift 3 owner"), thread("bbb", "Asus Vivobook 15 owner")]
    threads += [thread("ccc", "Acer Swift 3 notes"), thread("ddd", "Asus Vivobook 15 notes")]
    _run(_stage(serper=serper, reddit=FakeReddit(threads=threads), settings=_settings(max_reddit_threads=2)))
    assert serper.queries[-1] == "Acer Swift 3 vs Asus Vivobook 15"


def test_the_held_slot_still_runs_when_reddit_fails() -> None:
    serper = FakeSerper(default=_web())
    _run(_stage(serper=serper, reddit=FakeReddit(error=RuntimeError("x"))))
    assert serper.queries[-1] == "laptop for coding comparison India"


def test_the_held_slot_still_runs_with_reddit_off() -> None:
    serper = FakeSerper(default=_web())
    stage = LiveResearchStage(
        settings=_settings(),
        serper=serper,  # type: ignore[arg-type]
        pages=FakePages(),  # type: ignore[arg-type]
        reddit=None,
        usage=_book(),
        pool=ThreadPoolExecutor(max_workers=6),
    )
    _run(stage)
    assert serper.queries[-1] == "laptop for coding comparison India"


def test_a_comment_task_that_raises_keeps_the_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("a made-up failure")

    monkeypatch.setattr("app.research.stage.read_comments", boom)
    result = _run(_stage())
    assert SourceType.REDDIT in _types(result)
    assert _reasons(result) == {SourceType.REDDIT: "error"}


def test_titles_of_results_pointing_at_reddit_count_for_candidates_and_nothing_else() -> None:
    answer = SerperResult(reddit_titles=("Acer Swift 3 owner", "Asus Vivobook 15 owner"))
    serper = FakeSerper(default=SerperResult())
    serper.answers["laptop for coding recommendations"] = answer
    serper.answers["laptop buying guide India"] = _titled("Acer Swift 3 review", "Asus Vivobook 15 review")
    result = _run(_stage(serper=serper, reddit=FakeReddit()))
    assert "Acer Swift 3 vs Asus Vivobook 15" in result.queries
    assert not any("owner" in s.title for s in result.sources)


def test_known_candidates_are_used_first() -> None:
    serper = FakeSerper(default=_web())
    stage = _stage(serper=serper)
    stage.run(
        ResearchInput(rubric=rubric(), known_candidates=["Nova Book 14", "Kestrel Air 2"]),
        StageContext(search_id="srch_test"),
    )
    assert serper.queries[-1] == "Nova Book 14 vs Kestrel Air 2"


def test_reddit_is_asked_the_recommendations_query() -> None:
    reddit = FakeReddit()
    _run(_stage(reddit=reddit))
    assert reddit.queries == ["laptop for coding recommendations"]


# --- faults ----------------------------------------------------------------------------------------


def test_a_source_that_raises_never_stops_the_others() -> None:
    result = _run(_stage(reddit=FakeReddit(error=RuntimeError("a made-up failure"))))
    assert SourceType.WEB_SEARCH in _types(result)
    assert SourceType.REVIEW_SITE in _types(result)
    assert _reasons(result) == {SourceType.REDDIT: "error"}
    assert "Reddit had an error." in _note(result)


def test_serper_raising_never_stops_reddit() -> None:
    result = _run(_stage(serper=FakeSerper(error=RuntimeError("a made-up failure"))))
    assert _types(result) == {SourceType.REDDIT}
    assert _reasons(result)[SourceType.WEB_SEARCH] == "error"
    assert _reasons(result)[SourceType.REVIEW_SITE] == "error"


def test_a_page_that_raises_never_stops_the_others() -> None:
    result = _run(_stage(pages=FakePages(error=RuntimeError("a made-up failure"))))
    assert SourceType.REDDIT in _types(result)
    assert SourceType.WEB_SEARCH in _types(result)
    assert _reasons(result)[SourceType.REVIEW_SITE] == "error"


def test_a_source_that_times_out_never_stops_the_others() -> None:
    result = _run(_stage(reddit=FakeReddit(failure_reason="timeout")))
    assert SourceType.WEB_SEARCH in _types(result)
    assert _reasons(result) == {SourceType.REDDIT: "timeout"}
    assert "Reddit timed out." in _note(result)


def test_a_rate_limited_source_never_stops_the_others() -> None:
    limited = SerperResult(failure=SourceFailure(source_type=SourceType.WEB_SEARCH, reason="rate_limited"))
    result = _run(_stage(serper=FakeSerper(default=limited)))
    assert SourceType.REDDIT in _types(result)
    assert _reasons(result)[SourceType.WEB_SEARCH] == "rate_limited"
    assert "Web search hit a rate limit." in _note(result)


def test_one_query_failing_keeps_the_others_results_and_names_the_failure() -> None:
    bad = SerperResult(failure=SourceFailure(source_type=SourceType.WEB_SEARCH, reason="error"))
    serper = FakeSerper(default=_web(), answers={"laptop buying guide India": bad})
    result = _run(_stage(serper=serper))
    assert SourceType.WEB_SEARCH in _types(result)
    assert _reasons(result)[SourceType.WEB_SEARCH] == "error"


def test_the_deadline_returns_what_finished_and_names_what_did_not() -> None:
    release = threading.Event()
    reddit = FakeReddit(wait_for=release)
    stage = _stage(reddit=reddit, settings=_settings(deadline_s=0.3))
    started = time.monotonic()
    result = _run(stage)
    elapsed = time.monotonic() - started
    release.set()
    assert elapsed < 3
    assert SourceType.WEB_SEARCH in _types(result)
    assert _reasons(result) == {SourceType.REDDIT: "timeout"}
    assert "Reddit timed out." in _note(result)


def test_threads_found_before_the_deadline_are_kept_when_their_comments_are_not_done() -> None:
    reddit = FakeReddit(threads=[thread("aaa")], comment_delay_s=2.0)
    result = _run(_stage(reddit=reddit, settings=_settings(deadline_s=0.3)))
    assert SourceType.REDDIT in _types(result)
    assert _reasons(result) == {SourceType.REDDIT: "timeout"}


def test_a_slow_page_times_out_alone() -> None:
    pages = FakePages(pages={REVIEW: review_source(REVIEW)}, delay_s=2.0)
    result = _run(_stage(pages=pages, reddit=FakeReddit(), settings=_settings(deadline_s=0.3)))
    assert SourceType.WEB_SEARCH in _types(result)
    assert _reasons(result) == {SourceType.REVIEW_SITE: "timeout"}


def test_the_default_deadline_is_inside_the_stages_40_second_limit() -> None:
    settings = ResearchSettings()
    assert settings.deadline_s < STAGE_TIME_LIMITS_S[StageName.RESEARCH]


def test_the_stage_stops_early_when_the_whole_search_is_nearly_out_of_time() -> None:
    release = threading.Event()
    stage = _stage(reddit=FakeReddit(wait_for=release))
    ctx = StageContext(search_id="srch_test", time_limit_s=3.3)
    started = time.monotonic()
    result = stage.run(ResearchInput(rubric=rubric()), ctx)
    elapsed = time.monotonic() - started
    release.set()
    assert elapsed < 2
    assert _reasons(result)[SourceType.REDDIT] == "timeout"


def test_every_source_failing_still_gives_a_result() -> None:
    result = _run(
        _stage(
            serper=FakeSerper(error=RuntimeError("x")),
            pages=FakePages(error=RuntimeError("x")),
            reddit=FakeReddit(error=RuntimeError("x")),
        )
    )
    assert result.sources == []
    assert len(result.queries) == 6
    assert set(_reasons(result)) == {SourceType.REDDIT, SourceType.WEB_SEARCH, SourceType.REVIEW_SITE}


def test_a_capped_serper_skips_with_a_coverage_note() -> None:
    serper = FakeSerper(default=_web())
    result = _run(_stage(serper=serper, book=_book(serper=0)))
    assert SourceType.REDDIT in _types(result)
    assert _reasons(result)[SourceType.WEB_SEARCH] == "quota_exhausted"
    assert "Web search ran out of quota." in _note(result)


def test_a_capped_review_provider_is_named() -> None:
    pages = FakePages(reasons={REVIEW: "quota_exhausted"})
    assert _reasons(_run(_stage(pages=pages)))[SourceType.REVIEW_SITE] == "quota_exhausted"


def test_a_page_cap_reached_partway_is_named_even_with_some_pages_read() -> None:
    other = "https://www.reviews.example/other"
    links = SerperResult(sources=(web_source(REVIEW), web_source(other)))
    pages = FakePages(pages={REVIEW: review_source(REVIEW)}, reasons={other: "quota_exhausted"})
    result = _run(_stage(serper=FakeSerper(default=links), pages=pages))
    assert SourceType.REVIEW_SITE in _types(result)
    assert _reasons(result)[SourceType.REVIEW_SITE] == "quota_exhausted"


def test_one_page_failing_while_others_are_read_names_nothing() -> None:
    other = "https://www.reviews.example/other"
    links = SerperResult(sources=(web_source(REVIEW), web_source(other)))
    pages = FakePages(pages={REVIEW: review_source(REVIEW)}, reasons={other: "blocked"})
    result = _run(_stage(serper=FakeSerper(default=links), pages=pages))
    assert SourceType.REVIEW_SITE not in _reasons(result)


def test_no_results_on_a_review_site_is_named_as_found_nothing() -> None:
    serper = FakeSerper(default=SerperResult(sources=(web_source("https://web.example/a"),)))
    assert _reasons(_run(_stage(serper=serper)))[SourceType.REVIEW_SITE] == "not_found"


def test_reddit_off_is_named_in_the_failures() -> None:
    stage = LiveResearchStage(
        settings=_settings(),
        serper=FakeSerper(default=_web()),  # type: ignore[arg-type]
        pages=FakePages(pages={REVIEW: review_source(REVIEW)}),  # type: ignore[arg-type]
        reddit=None,
        usage=_book(),
        pool=ThreadPoolExecutor(max_workers=6),
    )
    result = _run(stage)
    assert SourceType.REDDIT not in _types(result)
    assert _reasons(result) == {SourceType.REDDIT: "blocked"}
    assert "Reddit could not be read." in _note(result)


def test_a_client_that_cannot_be_built_skips_the_sources_with_a_note(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def no_client() -> object:
        raise ClientUnavailable("ImportError")

    monkeypatch.setattr("app.research.stage.build_research_client", no_client)
    monkeypatch.setenv("SERPER_API_KEY", "not-a-real-key")
    monkeypatch.setattr("app.research.stage.load_reddit_settings", lambda: _reddit_archive_settings())
    monkeypatch.setattr("app.research.stage.build_reddit_source", lambda settings, http=None: no_client())
    stage = LiveResearchStage(settings=_settings(), usage=_book(), pool=ThreadPoolExecutor(max_workers=2))
    result = _run(stage)
    assert result.sources == []
    assert set(_reasons(result)) == {SourceType.REDDIT, SourceType.WEB_SEARCH, SourceType.REVIEW_SITE}
    assert "not-a-real-key" not in caplog.text


def _reddit_archive_settings() -> object:
    from app.research.reddit.settings import RedditSettings

    return RedditSettings(mode="archive")


def test_no_serper_key_skips_web_and_review_with_a_note(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    asked: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        asked.append(request)
        return httpx.Response(500)

    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    monkeypatch.setattr("app.research.stage.load_dotenv", lambda: None)
    monkeypatch.setattr(
        "app.research.stage.build_research_client", lambda: httpx.Client(transport=httpx.MockTransport(handle))
    )
    stage = LiveResearchStage(
        settings=_settings(cache_on=False),
        reddit=FakeReddit(threads=[thread("aaa")]),  # type: ignore[arg-type]
        usage=_book(),
        pool=ThreadPoolExecutor(max_workers=6),
    )
    result = _run(stage)
    assert asked == []
    assert _reasons(result) == {SourceType.WEB_SEARCH: "blocked", SourceType.REVIEW_SITE: "blocked"}


def test_page_fetching_off_is_named_and_results_stay_as_snippets() -> None:
    pages = FakePages()
    result = _run(_stage(pages=pages, settings=_settings(pages_on=False)))
    assert pages.calls == []
    assert SourceType.REVIEW_SITE not in _types(result)
    assert _reasons(result)[SourceType.REVIEW_SITE] == "blocked"
    assert str(result.sources[-1].url)


def test_a_failed_stage_falls_back_to_the_baseline() -> None:
    stage = _stage()
    out = stage.fallback(ResearchInput(rubric=rubric()), StageContext(search_id="srch_test"), RuntimeError("x"))
    assert out.sources == []
    assert out.queries
    assert stage.baseline.is_placeholder
    assert not getattr(stage, "is_placeholder", False)


def test_errors_are_logged_by_type_never_by_their_words(caplog: pytest.LogCaptureFixture) -> None:
    secret = "a-shopper-phrase-and-a-key"
    with caplog.at_level(logging.DEBUG):
        _run(_stage(reddit=FakeReddit(error=RuntimeError(secret)), serper=FakeSerper(error=ValueError(secret))))
    assert secret not in caplog.text
    assert "RuntimeError" in caplog.text


def test_the_last_report_says_times_and_counts_per_source_type() -> None:
    stage = _stage()
    _run(stage)
    report = stage.last_report
    assert report is not None
    assert set(report.seconds) == {SourceType.REDDIT, SourceType.WEB_SEARCH, SourceType.REVIEW_SITE}
    assert report.total_seconds >= max(report.seconds.values())
    assert report.counts[SourceType.REDDIT] == 1
    assert report.used["serper"] == 6
    assert report.today["serper"] == 6


def test_the_report_names_the_comparison_query_it_chose() -> None:
    serper = FakeSerper(default=_titled("Acer Swift 3 review", "Asus Vivobook 15 review"))
    reddit = FakeReddit(threads=[thread("aaa", "Acer Swift 3 owner"), thread("bbb", "Asus Vivobook 15 owner")])
    stage = _stage(serper=serper, reddit=reddit)
    _run(stage)
    assert stage.last_report is not None
    assert stage.last_report.comparison == "Acer Swift 3 vs Asus Vivobook 15"


def test_the_report_says_when_there_was_no_comparison() -> None:
    stage = _stage()
    _run(stage)
    assert stage.last_report is not None
    assert stage.last_report.comparison is None


def test_the_review_sites_note_reads_as_plural() -> None:
    result = _run(_stage(pages=FakePages(reasons={REVIEW: "blocked"})))
    assert "Review sites could not be read." in _note(result)


def test_the_report_holds_no_query_text_or_url() -> None:
    stage = _stage()
    _run(stage)
    text = repr(stage.last_report)
    assert "coding" not in text
    assert "https://" not in text


# --- what the review found --------------------------------------------------------------------------


@pytest.mark.slow
def test_sources_of_1_2_and_3_seconds_through_the_whole_stage_stay_close_to_the_slowest() -> None:
    serper = FakeSerper(default=SerperResult(sources=(web_source(REVIEW),)), delay_s=1.0)
    pages = FakePages(pages={REVIEW: review_source(REVIEW)}, delay_s=2.0)
    reddit = FakeReddit(delay_s=3.0)
    started = time.monotonic()
    result = _run(_stage(serper=serper, pages=pages, reddit=reddit, settings=_settings(deadline_s=30.0)))
    # Reddit's 3 s, then the held query's 1 s, and everything else alongside. One after another would take 11 s.
    assert time.monotonic() - started < 5.0
    assert SourceType.REVIEW_SITE in _types(result)


def test_with_no_pool_given_the_stage_uses_the_shared_pool_of_the_configured_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sizes: list[int] = []
    pool = ThreadPoolExecutor(max_workers=2)

    def fake_pool(size: int) -> ThreadPoolExecutor:
        sizes.append(size)
        return pool

    monkeypatch.setattr("app.research.stage.shared_pool", fake_pool)
    stage = LiveResearchStage(
        settings=_settings(pool_size=4),
        serper=FakeSerper(default=_web()),  # type: ignore[arg-type]
        pages=FakePages(),  # type: ignore[arg-type]
        reddit=FakeReddit(),  # type: ignore[arg-type]
        usage=_book(),
    )
    _run(stage)
    assert sizes == [4]


def test_the_real_wiring_reads_the_key_and_shares_one_client(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    import httpx

    from app.research.reddit.settings import RedditSettings
    from tests.fakes.research import result as serper_result
    from tests.fakes.research import serper_reply

    key = "not-a-real-key"
    asked: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        asked.append(request)
        return httpx.Response(200, json=serper_reply(serper_result("https://web.example/a")))

    client = httpx.Client(transport=httpx.MockTransport(handle))
    built: list[httpx.Client] = []

    def build() -> httpx.Client:
        built.append(client)
        return client

    monkeypatch.setattr("app.research.stage.build_research_client", build)
    monkeypatch.setattr("app.research.stage.load_dotenv", lambda: None)
    monkeypatch.setenv("SERPER_API_KEY", key)
    monkeypatch.setattr("app.research.stage.load_reddit_settings", lambda: RedditSettings(mode="off"))
    stage = LiveResearchStage(settings=_settings(cache_on=False), usage=_book(), pool=ThreadPoolExecutor(max_workers=2))
    result = _run(stage)
    assert len(built) == 1
    assert len(asked) == 6
    assert stage._wire(_settings(cache_on=False)).serper.limiter is shared_limiter(0.2)  # type: ignore[union-attr]
    assert all(r.headers["X-API-KEY"] == key for r in asked)
    assert SourceType.WEB_SEARCH in _types(result)
    assert client.is_closed  # nothing was left running, so the client is closed


def test_a_client_still_in_use_at_the_deadline_is_left_open(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from app.research.reddit.settings import RedditSettings

    release = threading.Event()

    def handle(_: httpx.Request) -> httpx.Response:
        release.wait(5)
        return httpx.Response(500)

    client = httpx.Client(transport=httpx.MockTransport(handle))
    monkeypatch.setattr("app.research.stage.build_research_client", lambda: client)
    monkeypatch.setattr("app.research.stage.load_dotenv", lambda: None)
    monkeypatch.setenv("SERPER_API_KEY", "not-a-real-key")
    monkeypatch.setattr("app.research.stage.load_reddit_settings", lambda: RedditSettings(mode="off"))
    stage = LiveResearchStage(
        settings=_settings(cache_on=False, deadline_s=0.2), usage=_book(), pool=ThreadPoolExecutor(max_workers=2)
    )
    _run(stage)
    assert not client.is_closed
    release.set()


def test_a_reddit_source_that_cannot_be_built_is_skipped_with_a_note(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from app.research.reddit.settings import RedditSettings

    secret = "a-made-up-praw-secret"

    def broken(settings: object, http: object = None) -> object:
        raise ValueError(secret)

    monkeypatch.setattr("app.research.stage.load_reddit_settings", lambda: RedditSettings(mode="official"))
    monkeypatch.setattr("app.research.stage.build_reddit_source", broken)
    stage = LiveResearchStage(
        settings=_settings(),
        serper=FakeSerper(default=_web()),  # type: ignore[arg-type]
        pages=FakePages(pages={REVIEW: review_source(REVIEW)}),  # type: ignore[arg-type]
        usage=_book(),
        pool=ThreadPoolExecutor(max_workers=6),
    )
    with caplog.at_level(logging.DEBUG):
        result = _run(stage)
    assert SourceType.WEB_SEARCH in _types(result)
    assert _reasons(result) == {SourceType.REDDIT: "error"}
    assert secret not in caplog.text


def test_every_different_failure_of_a_type_is_named() -> None:
    bad = SerperResult(failure=SourceFailure(source_type=SourceType.WEB_SEARCH, reason="error"))
    capped = SerperResult(failure=SourceFailure(source_type=SourceType.WEB_SEARCH, reason="quota_exhausted"))
    serper = FakeSerper(
        default=_web(), answers={"laptop buying guide India": bad, "laptop for coding recommendations": capped}
    )
    note = _note(_run(_stage(serper=serper)))
    assert "Web search had an error." in note
    assert "Web search ran out of quota." in note


def test_a_timeout_does_not_hide_a_cap() -> None:
    other = "https://www.reviews.example/other"
    links = SerperResult(sources=(web_source(other), web_source(REVIEW)))

    class Mixed(FakePages):
        def fetch(self, url, usage):  # type: ignore[no-untyped-def]
            if url == other:
                return PageResult(reason="quota_exhausted")
            time.sleep(2.0)
            return PageResult(source=review_source(url))

    result = _run(
        _stage(serper=FakeSerper(default=links), pages=Mixed(), reddit=FakeReddit(), settings=_settings(deadline_s=0.3))
    )
    note = _note(result)
    assert "Review sites timed out." in note
    assert "Review sites ran out of quota." in note


def test_a_comment_failure_reaches_the_coverage_note() -> None:
    reddit = FakeReddit(threads=[thread("aaa")], comment_failure_reason="rate_limited")
    result = _run(_stage(reddit=reddit))
    assert SourceType.REDDIT in _types(result)
    assert "Reddit hit a rate limit." in _note(result)


def test_one_reddit_result_found_by_two_queries_counts_as_one_source_for_candidates() -> None:
    """Five replies naming the same two threads are still only one source for each name."""
    answer = SerperResult(reddit_titles=("Acer Swift 3 owner", "Asus Vivobook 15 owner"))
    serper = FakeSerper(default=answer)
    _run(_stage(serper=serper, reddit=FakeReddit()))
    assert not any(" vs " in q for q in serper.queries)


def test_a_link_with_and_without_a_slash_read_as_a_page_gives_one_source() -> None:
    final = "https://www.reviews.example/a/"
    bare = "https://www.reviews.example/a"
    links = SerperResult(sources=(web_source(bare), web_source(final)))
    pages = FakePages(pages={bare: review_source(final), final: review_source(final)})
    result = _run(_stage(serper=FakeSerper(default=links), pages=pages))
    urls = [str(s.url) for s in result.sources]
    assert urls.count(final) == 1
    assert bare not in urls
    assert len({s.id for s in result.sources}) == len(result.sources)


def test_a_link_with_and_without_a_slash_is_one_source() -> None:
    links = SerperResult(sources=(web_source("https://web.example/a"), web_source("https://web.example/a/")))
    result = _run(_stage(serper=FakeSerper(default=links)))
    assert len([s for s in result.sources if "web.example" in str(s.url)]) == 1


def test_a_link_found_by_two_queries_takes_its_best_place() -> None:
    shared = web_source("https://web.example/shared")
    middle = web_source("https://web.example/middle")
    late = "laptop for coding recommendations"
    answers = {
        "best laptop for coding under 70000 rupees India": SerperResult(
            sources=(web_source("https://web.example/first"), shared)
        ),
        "laptop for coding review India": SerperResult(sources=(middle,)),
        late: SerperResult(sources=(shared,)),
    }

    class LaterFirst(FakeSerper):
        def search(self, query, region, usage, sites=()):  # type: ignore[no-untyped-def]
            if query != late:
                time.sleep(0.1)
            return super().search(query, region, usage, sites)

    result = _run(_stage(serper=LaterFirst(answers=answers)))
    web = [str(s.url) for s in result.sources if s.type is SourceType.WEB_SEARCH]
    assert web == ["https://web.example/first", "https://web.example/shared", "https://web.example/middle"]


def test_a_redirect_onto_another_results_page_gives_one_source_even_if_that_page_fails_alone() -> None:
    old = "https://www.reviews.example/old"
    new = "https://www.reviews.example/new"
    links = SerperResult(sources=(web_source(old), web_source(new)))
    pages = FakePages(pages={old: review_source(new)}, reasons={new: "blocked"})
    result = _run(_stage(serper=FakeSerper(default=links), pages=pages))
    urls = [str(s.url) for s in result.sources if "reviews.example" in str(s.url)]
    assert urls == [new]
    assert result.sources[-1].type is SourceType.REVIEW_SITE


def test_a_page_found_again_by_an_earlier_query_takes_that_place() -> None:
    first = "https://www.reviews.example/a"
    second = "https://www.reviews.example/b"
    late = "laptop for coding recommendations"
    answers = {
        "best laptop for coding under 70000 rupees India": SerperResult(sources=(web_source(second),)),
        late: SerperResult(sources=(web_source(first), web_source(second))),
    }

    class LaterFirst(FakeSerper):
        def search(self, query, region, usage, sites=()):  # type: ignore[no-untyped-def]
            if query != late:
                time.sleep(0.7)  # past halfway, so the page is read before this reply comes
            return super().search(query, region, usage, sites)

    pages = FakePages(pages={first: review_source(first), second: review_source(second)})
    result = _run(_stage(serper=LaterFirst(answers=answers), pages=pages, settings=_settings(deadline_s=1.0)))
    assert [str(s.url) for s in result.sources if s.type is SourceType.REVIEW_SITE] == [second, first]


# --- what the second review found -------------------------------------------------------------------


def test_a_stuck_listing_does_not_cost_the_comparison_query() -> None:
    """Serper answered long ago and two names are known. An archive stuck to the deadline must not lose that."""
    release = threading.Event()
    known = SerperResult(
        sources=(
            web_source("https://one.example/a", "Acer Swift 3 review"),
            web_source("https://two.example/a", "Acer Swift 3 notes"),
            web_source("https://one.example/b", "Asus Vivobook 15 review"),
            web_source("https://two.example/b", "Asus Vivobook 15 notes"),
        )
    )
    serper = FakeSerper(default=known)
    result = _run(_stage(serper=serper, reddit=FakeReddit(wait_for=release), settings=_settings(deadline_s=1.0)))
    release.set()
    assert serper.queries[-1] == "Acer Swift 3 vs Asus Vivobook 15"
    assert len(serper.calls) == 6
    assert _reasons(result) == {SourceType.REDDIT: "timeout", SourceType.REVIEW_SITE: "not_found"}


def test_a_listing_arriving_after_the_slot_ran_does_not_run_it_again() -> None:
    serper = FakeSerper(default=SerperResult())
    _run(_stage(serper=serper, reddit=FakeReddit(delay_s=0.7), settings=_settings(deadline_s=1.0)))
    assert len(serper.calls) == 6


def test_too_little_time_left_spends_no_credit_on_the_held_slot() -> None:
    serper = FakeSerper(default=_web(), delay_s=1.0)
    result = _run(_stage(serper=serper, reddit=FakeReddit(), settings=_settings(deadline_s=1.05)))
    assert len(serper.calls) == 5
    assert SourceType.WEB_SEARCH not in _reasons(result)


def test_the_caps_pick_the_same_pages_whichever_reply_arrived_first() -> None:
    early = "https://www.reviews.example/early"
    late = "https://www.reviews.example/late"
    first_query = "best laptop for coding under 70000 rupees India"
    answers = {first_query: SerperResult(sources=(web_source(early),))}
    answers["laptop buying guide India"] = SerperResult(sources=(web_source(late),))

    class FirstIsSlow(FakeSerper):
        def search(self, query, region, usage, sites=()):  # type: ignore[no-untyped-def]
            if query == first_query:
                time.sleep(0.2)
            return super().search(query, region, usage, sites)

    pages = FakePages(pages={early: review_source(early), late: review_source(late)})
    _run(_stage(serper=FirstIsSlow(answers=answers), pages=pages, settings=_settings(max_review_pages=1)))
    assert pages.calls == [early]


def test_pages_are_still_read_if_one_query_has_not_answered_by_half_the_deadline() -> None:
    release = threading.Event()
    stuck = "laptop buying guide India"

    class OneIsStuck(FakeSerper):
        def search(self, query, region, usage, sites=()):  # type: ignore[no-untyped-def]
            if query == stuck:
                release.wait(5)
            return super().search(query, region, usage, sites)

    pages = FakePages(pages={REVIEW: review_source(REVIEW)})
    result = _run(_stage(serper=OneIsStuck(default=_web()), pages=pages, settings=_settings(deadline_s=1.0)))
    release.set()
    assert pages.calls == [REVIEW]
    assert SourceType.REVIEW_SITE in _types(result)


def test_a_link_already_followed_is_the_page_it_led_to_when_a_later_reply_lists_it_again() -> None:
    old = "https://www.reviews.example/old"
    new = "https://www.reviews.example/new"
    sixth = "laptop for coding comparison India"
    serper = FakeSerper(default=SerperResult())
    serper.answers["laptop buying guide India"] = SerperResult(sources=(web_source(old),))
    serper.answers[sixth] = SerperResult(sources=(web_source(old),))
    pages = FakePages(pages={old: review_source(new)})
    result = _run(_stage(serper=serper, pages=pages))
    assert pages.calls == [old]
    urls = [str(s.url) for s in result.sources if "reviews.example" in str(s.url)]
    assert urls == [new]


def test_every_different_reason_is_named_when_no_page_came_back() -> None:
    links = tuple(web_source(f"https://www.reviews.example/{name}") for name in ("a", "b", "c"))
    reasons = {str(link.url): reason for link, reason in zip(links, ("blocked", "blocked", "timeout"), strict=True)}
    result = _run(_stage(serper=FakeSerper(default=SerperResult(sources=links)), pages=FakePages(reasons=reasons)))
    note = _note(result)
    assert "Review sites could not be read." in note
    assert "Review sites timed out." in note


def test_reddit_failing_then_an_older_archive_finding_nothing_is_named_in_the_note() -> None:
    from app.research.reddit import RedditResult
    from app.research.reddit.base import failure

    class Empty(FakeReddit):
        def search_threads(self, query, subreddits, after=None, limit=25, budget=None):  # type: ignore[no-untyped-def]
            return RedditResult(partial=(failure("error", "made up"),))

    assert "Reddit had an error." in _note(_run(_stage(reddit=Empty())))


def test_a_review_page_goes_through_the_real_page_reader() -> None:
    """Everything real but the network: robots, the limiter, cleaning, and the page landing as a review_site source."""
    import httpx

    from app.research.reddit.settings import RedditSettings
    from tests.fakes.research import html_page, serper_reply
    from tests.fakes.research import result as serper_result

    page = "https://www.reviews.example/nova"

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.host == "google.serper.dev":
            return httpx.Response(200, json=serper_reply(serper_result(page, "A made-up review")))
        if str(request.url) == page:
            return html_page(title="Nova review")
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handle))
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("app.research.stage.build_research_client", lambda: client)
        patch.setattr("app.research.stage.load_dotenv", lambda: None)
        patch.setenv("SERPER_API_KEY", "not-a-real-key")
        patch.setattr("app.research.stage.load_reddit_settings", lambda: RedditSettings(mode="off"))
        stage = LiveResearchStage(
            settings=_settings(cache_on=False, page_interval_s=0.01),
            usage=_book(),
            pool=ThreadPoolExecutor(max_workers=6),
        )
        result = _run(stage)
    reviews = [s for s in result.sources if s.type is SourceType.REVIEW_SITE]
    assert [str(s.url) for s in reviews] == [page]
    assert reviews[0].title == "Nova review"


def test_a_page_found_only_by_the_held_query_is_read() -> None:
    sixth = "laptop for coding comparison India"
    serper = FakeSerper(default=SerperResult())
    serper.answers[sixth] = SerperResult(sources=(web_source(REVIEW),))
    pages = FakePages(pages={REVIEW: review_source(REVIEW)})
    result = _run(_stage(serper=serper, pages=pages))
    assert pages.calls == [REVIEW]
    assert SourceType.REVIEW_SITE in _types(result)


def test_a_reply_arriving_after_half_the_deadline_still_has_its_pages_read() -> None:
    release = threading.Event()
    stuck = "laptop buying guide India"
    late = "laptop for coding review India"

    class Mixed(FakeSerper):
        def search(self, query, region, usage, sites=()):  # type: ignore[no-untyped-def]
            if query == stuck:
                release.wait(5)
            if query == late:
                time.sleep(0.8)
            return super().search(query, region, usage, sites)

    serper = Mixed(default=SerperResult())
    serper.answers[late] = SerperResult(sources=(web_source(REVIEW),))
    pages = FakePages(pages={REVIEW: review_source(REVIEW)})
    result = _run(_stage(serper=serper, pages=pages, settings=_settings(deadline_s=1.2)))
    release.set()
    assert pages.calls == [REVIEW]
    assert SourceType.REVIEW_SITE in _types(result)


def test_review_sites_take_every_reason_web_search_failed_for() -> None:
    bad = SerperResult(failure=SourceFailure(source_type=SourceType.WEB_SEARCH, reason="error"))
    capped = SerperResult(failure=SourceFailure(source_type=SourceType.WEB_SEARCH, reason="quota_exhausted"))
    serper = FakeSerper(default=bad, answers={"laptop buying guide India": capped})
    note = _note(_run(_stage(serper=serper)))
    assert "Review sites had an error." in note
    assert "Review sites ran out of quota." in note
