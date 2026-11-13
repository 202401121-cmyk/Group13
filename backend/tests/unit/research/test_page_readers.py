"""Hard-to-read pages (US-38, the page half; D-62, D-64): Jina Reader, then Firecrawl, when a plain fetch fails.

Maker pages are tier 1 and store pages tier 5. robots.txt is asked before any reader, exactly as for a plain fetch, and
YouTube, social sites and logged-in pages never reach a reader. A Reddit thread the archive lacks may be read through
Firecrawl, as Reddit data. Firecrawl search stands in when Serper can't answer. Recordings only: no network.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx
import pytest

from app.contracts import DataClass, ResearchInput, Source, SourceFailure, SourceTier, SourceType, StageContext
from app.contracts.research import ResearchResult
from app.pipeline import build_coverage_note
from app.research.cache import ResearchCache
from app.research.pages import PageReaders, PageResult, ReviewPages
from app.research.readers import (
    FIRECRAWL_SCRAPE_URL,
    FIRECRAWL_SEARCH_URL,
    JINA_URL,
    FirecrawlClient,
    FirecrawlGate,
    JinaReader,
    SearchWithBackup,
)
from app.research.serper import SerperResult
from app.research.settings import ProviderCap, ResearchSettings
from app.research.stage import LiveResearchStage
from app.research.usage import SearchUsage, UsageBook
from tests.fakes.reddit import NOW, Clock
from tests.fakes.research import (
    FakePages,
    FakeReddit,
    FakeSerper,
    Web,
    html_page,
    review_source,
    rubric,
    thread,
    web_source,
)

SITE = "https://www.reviews.example"
PAGE = f"{SITE}/nova-book-14"
MAKER = "https://www.maker.example/laptops/nova-book-14"
STORE = "https://www.store.example/nova-book-14/p/123"
THREAD = "https://www.reddit.com/r/laptops/comments/abc123/nova_book_14_for_video_editing/"
WORDS = " ".join(f"word{i}" for i in range(200))
THIN = html_page(body="Loading...")
CAPS = {
    "pages": ProviderCap(per_search=24, per_day=200),
    "jina": ProviderCap(per_search=10, per_day=200),
    "firecrawl_pages": ProviderCap(per_search=4, per_day=30),
    "firecrawl": ProviderCap(per_search=30, per_day=30),
    "serper": ProviderCap(per_search=6, per_day=60, total=2400),
}


def _usage(**caps: ProviderCap) -> SearchUsage:
    return UsageBook({**CAPS, **caps}, path=None).search()


def _settings() -> ResearchSettings:
    return ResearchSettings(
        review_domains=("reviews.example",),
        maker_domains={"nova": ("maker.example",)},
        retailer_domains=("store.example",),
    )


def _jina(url: str, text: str = WORDS, title: str = "Nova Book 14 review") -> tuple[str, httpx.Response]:
    body = f"Title: {title}\n\nURL Source: {url}\n\nMarkdown Content:\n{text}"
    return f"{JINA_URL}{url}", httpx.Response(200, text=body)


def _scrape(markdown: str = WORDS, title: str = "Nova Book 14", status: int = 200) -> httpx.Response:
    if status != 200:
        return httpx.Response(status, json={"success": False})
    return httpx.Response(200, json={"success": True, "data": {"markdown": markdown, "metadata": {"title": title}}})


def _pages(
    web: Web,
    *,
    jina: bool = True,
    firecrawl: bool = True,
    gate: FirecrawlGate | None = None,
    cache: ResearchCache | None = None,
) -> ReviewPages:
    clock = Clock()
    http = web.client()
    cache = cache or ResearchCache(folder=None, now=lambda: NOW)
    readers = PageReaders(
        jina=JinaReader(http, None) if jina else None,
        firecrawl=FirecrawlClient(http, "fc-made-up", cache=cache, gate=gate or FirecrawlGate(), now=lambda: NOW)
        if firecrawl
        else None,
    )
    return ReviewPages(http, _settings(), cache=cache, readers=readers, now=lambda: NOW, clock=clock, sleep=clock.sleep)


def _scrapes(web: Web) -> list[dict[str, object]]:
    return [json.loads(r.content) for r in web.requests if str(r.url) == FIRECRAWL_SCRAPE_URL]


# --- the chain -------------------------------------------------------------------------------------------------------


def test_a_thin_page_goes_to_jina_and_is_read() -> None:
    web = Web().on(PAGE, THIN).on(*_jina(PAGE))
    found = _pages(web).fetch(PAGE, _usage())
    assert found.source is not None
    assert found.source.via == "jina"
    assert found.source.type is SourceType.REVIEW_SITE and found.source.tier is SourceTier.EDITORIAL
    assert "word199" in found.source.text and "Markdown Content" not in found.source.text
    assert found.source.title == "Nova Book 14 review"
    assert _scrapes(web) == []


def test_a_page_the_site_refuses_goes_to_a_reader_too() -> None:
    web = Web().on(PAGE, httpx.Response(403)).on(*_jina(PAGE))
    found = _pages(web).fetch(PAGE, _usage())
    assert found.source is not None and found.source.via == "jina"


def test_jina_failing_goes_to_firecrawl() -> None:
    web = Web().on(PAGE, THIN).on(f"{JINA_URL}{PAGE}", httpx.Response(429)).on(FIRECRAWL_SCRAPE_URL, _scrape())
    found = _pages(web).fetch(PAGE, _usage())
    assert found.source is not None
    assert found.source.via == "firecrawl"
    [sent] = _scrapes(web)
    assert sent["url"] == PAGE
    assert sent["formats"] == ["markdown"] and sent["onlyMainContent"] is True
    assert sent["maxAge"] == 86_400_000 and sent["location"] == {"country": "IN"}
    assert isinstance(sent["timeout"], int) and 1000 <= sent["timeout"] <= 15_000
    assert sent.get("storeInCache", True) is True  # a public page may stay in their cache


def test_jina_thin_goes_to_firecrawl() -> None:
    web = Web().on(PAGE, THIN).on(*_jina(PAGE, text="Loading...")).on(FIRECRAWL_SCRAPE_URL, _scrape())
    found = _pages(web).fetch(PAGE, _usage())
    assert found.source is not None and found.source.via == "firecrawl"


def test_both_readers_failing_skip_the_page_with_a_note() -> None:
    web = (
        Web().on(PAGE, THIN).on(f"{JINA_URL}{PAGE}", httpx.Response(500)).on(FIRECRAWL_SCRAPE_URL, _scrape(status=500))
    )
    found = _pages(web).fetch(PAGE, _usage())
    assert found.source is None
    assert [f.detail.split(":")[0] for f in found.lost_to] == ["Jina Reader", "Firecrawl"]
    assert all(f.source_type is SourceType.REVIEW_SITE for f in found.lost_to)


def test_a_page_read_plainly_never_reaches_a_reader() -> None:
    web = Web().on(PAGE, html_page())
    found = _pages(web).fetch(PAGE, _usage())
    assert found.source is not None and found.source.via == "direct"
    assert web.asked(f"{JINA_URL}{PAGE}") == 0 and _scrapes(web) == []


# --- what never reaches a reader -------------------------------------------------------------------------------------


def test_robots_disallowing_means_no_reader_call() -> None:
    web = Web().on(f"{SITE}/robots.txt", httpx.Response(200, text="User-agent: *\nDisallow: /nova"))
    web.on(*_jina(PAGE)).on(FIRECRAWL_SCRAPE_URL, _scrape())
    found = _pages(web).fetch(PAGE, _usage())
    assert found.source is None and found.reason == "blocked"
    assert web.asked(PAGE) == 0 and web.asked(f"{JINA_URL}{PAGE}") == 0 and _scrapes(web) == []


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=abc",
        "https://youtu.be/abc",
        "https://www.instagram.com/p/abc/",
        f"{SITE}/account/orders",
        f"{SITE}/login?next=/nova-book-14",
        "https://www.store.example/checkout/cart",
    ],
)
def test_a_youtube_social_or_logged_in_url_never_reaches_a_reader(url: str) -> None:
    web = Web().on(*_jina(url)).on(FIRECRAWL_SCRAPE_URL, _scrape())
    found = _pages(web).fetch(url, _usage())
    assert found.source is None
    assert web.asked(f"{JINA_URL}{url}") == 0 and _scrapes(web) == []


def test_a_page_that_needs_a_login_never_reaches_a_reader() -> None:
    web = Web().on(PAGE, httpx.Response(401)).on(*_jina(PAGE)).on(FIRECRAWL_SCRAPE_URL, _scrape())
    found = _pages(web).fetch(PAGE, _usage())
    assert found.source is None and found.reason == "blocked"
    assert web.asked(f"{JINA_URL}{PAGE}") == 0 and _scrapes(web) == []


# --- page types ------------------------------------------------------------------------------------------------------


def test_a_store_page_is_retailer_tier_5() -> None:
    web = Web().on(STORE, html_page(title="Nova Book 14 at the store"))
    found = _pages(web).fetch(STORE, _usage())
    assert found.source is not None
    assert (found.source.type, found.source.tier) == (SourceType.RETAILER, SourceTier.RETAILER)
    assert found.source.site == "store.example"


def test_a_maker_page_is_manufacturer_tier_1_even_through_a_reader() -> None:
    web = Web().on(MAKER, THIN).on(*_jina(MAKER))
    found = _pages(web).fetch(MAKER, _usage())
    assert found.source is not None
    assert (found.source.type, found.source.tier) == (SourceType.MANUFACTURER, SourceTier.MANUFACTURER)
    assert found.source.via == "jina"


def test_a_page_on_no_configured_site_is_never_fetched() -> None:
    web = Web()
    found = _pages(web).fetch("https://www.elsewhere.example/nova", _usage())
    assert found.source is None and web.requests == []


# --- Reddit through Firecrawl ----------------------------------------------------------------------------------------


def test_a_firecrawl_reddit_thread_is_reddit_data_without_usernames() -> None:
    markdown = (
        "Nova Book 14 for video editing?\n\nPosted by [u/made_up_editor](https://www.reddit.com/user/made_up_editor/)\n\n"
        f"{WORDS}\n\nu/another_made_up_name says the Nova Book 14 renders fast. /u/third_name agrees."
    )
    web = Web().on(FIRECRAWL_SCRAPE_URL, _scrape(markdown=markdown, title="Nova Book 14 for video editing?"))
    found = _pages(web).read_reddit(THREAD, _usage())
    source = found.source
    assert source is not None
    assert (source.type, source.tier, source.data_class) == (SourceType.REDDIT, SourceTier.COMMUNITY, DataClass.REDDIT)
    assert source.community == "r/laptops" and source.via == "firecrawl"
    assert source.author_hash is None
    for name in ("made_up_editor", "another_made_up_name", "third_name"):
        assert name not in source.text
    [sent] = _scrapes(web)
    assert sent["storeInCache"] is False  # Reddit text never stays in their cache (D-53)


def test_reddit_robots_that_disallow_mean_no_firecrawl_call() -> None:
    web = Web().on("https://www.reddit.com/robots.txt", httpx.Response(200, text="User-agent: *\nDisallow: /"))
    web.on(FIRECRAWL_SCRAPE_URL, _scrape())
    found = _pages(web).read_reddit(THREAD, _usage())
    assert found.source is None and found.reason == "blocked" and _scrapes(web) == []


def test_a_non_thread_reddit_url_is_never_read() -> None:
    web = Web().on(FIRECRAWL_SCRAPE_URL, _scrape())
    assert _pages(web).read_reddit("https://www.reddit.com/user/someone/", _usage()).source is None
    assert _scrapes(web) == []


# --- caps, pacing and health -----------------------------------------------------------------------------------------


def test_jina_stops_at_its_cap_a_search() -> None:
    usage = _usage(jina=ProviderCap(per_search=1, per_day=200))
    other = f"{SITE}/kestrel-slim-15"
    web = Web().on(PAGE, THIN).on(other, THIN).on(*_jina(PAGE)).on(*_jina(other))
    pages = _pages(web, firecrawl=False)
    assert pages.fetch(PAGE, usage).source is not None
    lost = pages.fetch(other, usage)
    assert lost.source is None and web.asked(f"{JINA_URL}{other}") == 0
    assert [(f.reason, f.detail.split(":")[0]) for f in lost.lost_to] == [("quota_exhausted", "Jina Reader")]


def test_firecrawl_stops_at_4_pages_a_search() -> None:
    urls = [f"{SITE}/page-{i}" for i in range(5)]
    web = Web().on(FIRECRAWL_SCRAPE_URL, _scrape())
    for url in urls:
        web.on(url, THIN)
    pages = _pages(web, jina=False)
    usage = _usage()
    read = [pages.fetch(url, usage).source is not None for url in urls]
    assert read == [True, True, True, True, False]
    assert len(_scrapes(web)) == 4


def test_firecrawl_stops_at_30_credits_a_day() -> None:
    usage = _usage(firecrawl=ProviderCap(per_search=30, per_day=1))
    other = f"{SITE}/kestrel-slim-15"
    web = Web().on(PAGE, THIN).on(other, THIN).on(FIRECRAWL_SCRAPE_URL, _scrape())
    pages = _pages(web, jina=False)
    assert pages.fetch(PAGE, usage).source is not None
    assert pages.fetch(other, usage).source is None
    assert len(_scrapes(web)) == 1


def test_the_caps_in_config_are_the_decided_ones() -> None:
    from app.research.settings import load_research_settings

    settings = load_research_settings(env={})
    assert settings.caps["jina"].per_search == 10
    assert settings.caps["firecrawl_pages"].per_search == 4
    assert settings.caps["firecrawl"].per_day == 30
    assert (settings.max_maker_pages, settings.max_store_pages) == (3, 3)


def test_firecrawl_is_paced_at_10_a_minute() -> None:
    clock = Clock()
    gate = FirecrawlGate(clock=clock)
    assert all(gate.try_start() is None and gate.finish(200) is None for _ in range(10))
    assert gate.try_start() == "rate_limited"
    clock.now += 61
    assert gate.try_start() is None


def test_firecrawl_is_paced_at_2_at_a_time() -> None:
    gate = FirecrawlGate(clock=Clock(), wait_s=0.01)
    assert gate.try_start() is None and gate.try_start() is None
    assert gate.try_start() == "rate_limited"  # a third waits briefly for a slot, then gives up
    gate.finish(200)
    assert gate.try_start() is None


def test_firecrawl_402_marks_it_dead_for_the_run() -> None:
    gate = FirecrawlGate()
    other = f"{SITE}/kestrel-slim-15"
    web = Web().on(PAGE, THIN).on(other, THIN).on(FIRECRAWL_SCRAPE_URL, _scrape(status=402))
    pages = _pages(web, jina=False, gate=gate)
    first = pages.fetch(PAGE, _usage())
    assert first.source is None and [f.reason for f in first.lost_to] == ["quota_exhausted"]
    assert pages.fetch(other, _usage()).source is None
    assert len(_scrapes(web)) == 1  # never asked again this run


def test_firecrawl_429_cools_it() -> None:
    clock = Clock()
    gate = FirecrawlGate(clock=clock)
    assert gate.try_start() is None
    gate.finish(429)
    assert gate.try_start() == "rate_limited"
    clock.now += 61
    assert gate.try_start() is None


# --- cache -----------------------------------------------------------------------------------------------------------


def test_a_cached_page_makes_no_call() -> None:
    cache = ResearchCache(folder=None, now=lambda: NOW)
    web = Web().on(PAGE, THIN).on(*_jina(PAGE))
    assert _pages(web, cache=cache).fetch(PAGE, _usage()).source is not None
    asked = len(web.requests)
    again = _pages(web, cache=cache).fetch(PAGE, _usage())
    assert again.from_cache and again.source is not None and again.source.via == "jina"
    assert len(web.requests) == asked


def test_a_cached_firecrawl_search_makes_no_call() -> None:
    cache = ResearchCache(folder=None, now=lambda: NOW)
    web = Web().on(FIRECRAWL_SEARCH_URL, _search_reply(f"{SITE}/a"))
    firecrawl = FirecrawlClient(web.client(), "fc-made-up", cache=cache, gate=FirecrawlGate(), now=lambda: NOW)
    assert firecrawl.search("best laptop", "IN", _usage()).sources
    again = firecrawl.search("best laptop", "IN", _usage())
    assert again.from_cache and again.sources
    assert sum(str(r.url) == FIRECRAWL_SEARCH_URL for r in web.requests) == 1


# --- the search backup -----------------------------------------------------------------------------------------------


def _search_reply(*urls: str) -> httpx.Response:
    web = [{"url": url, "title": "A made-up result", "description": "A made-up snippet."} for url in urls]
    return httpx.Response(200, json={"success": True, "data": {"web": web}})


def _no_credits() -> FakeSerper:
    failure = SourceFailure(source_type=SourceType.WEB_SEARCH, reason="quota_exhausted", detail="web search: HTTP 402")
    return FakeSerper(default=SerperResult(failure=failure), charge=False)


def test_serper_out_of_credits_sends_the_queries_to_firecrawl_search() -> None:
    web = Web().on(FIRECRAWL_SEARCH_URL, _search_reply(f"{SITE}/a", THREAD))
    cache = ResearchCache(folder=None, now=lambda: NOW)
    firecrawl = FirecrawlClient(web.client(), "fc-made-up", cache=cache, gate=FirecrawlGate(), now=lambda: NOW)
    usage = _usage()
    found = SearchWithBackup(_no_credits(), firecrawl).search("best laptop for video editing", "IN", usage)
    assert found.failure is None
    assert [str(s.url) for s in found.sources] == [f"{SITE}/a"]
    assert all(s.via == "search_snippet" and s.type is SourceType.WEB_SEARCH for s in found.sources)
    assert found.reddit_links == (THREAD,)  # links and titles only, as from Serper
    [sent] = [json.loads(r.content) for r in web.requests]
    assert sent["query"] == "best laptop for video editing"
    assert (sent["country"], sent["limit"]) == ("IN", 10)
    assert usage.used["firecrawl"] == 2  # a search is 2 credits


def test_serper_answering_never_reaches_firecrawl_search() -> None:
    web = Web().on(FIRECRAWL_SEARCH_URL, _search_reply(f"{SITE}/a"))
    firecrawl = FirecrawlClient(
        web.client(), "fc-made-up", cache=ResearchCache(folder=None, now=lambda: NOW), gate=FirecrawlGate()
    )
    serper = FakeSerper(default=SerperResult(sources=(web_source(f"{SITE}/b"),)))
    found = SearchWithBackup(serper, firecrawl).search("best laptop", "IN", _usage())
    assert found.failure is None and web.requests == []


def test_serpers_own_per_search_cap_is_not_a_reason_for_the_backup() -> None:
    """Six queries a search is our own limit, not Serper failing: the backup must not add more."""
    web = Web().on(FIRECRAWL_SEARCH_URL, _search_reply(f"{SITE}/a"))
    firecrawl = FirecrawlClient(
        web.client(), "fc-made-up", cache=ResearchCache(folder=None, now=lambda: NOW), gate=FirecrawlGate()
    )
    usage = _usage(serper=ProviderCap(per_search=0, per_day=60))

    found = SearchWithBackup(FakeSerper(), firecrawl).search("best laptop", "IN", usage)
    assert found.failure is not None and web.requests == []


# --- the coverage note -----------------------------------------------------------------------------------------------


def test_the_coverage_note_names_the_reader() -> None:
    failures = [
        SourceFailure(source_type=SourceType.REVIEW_SITE, reason="rate_limited", detail="Jina Reader: HTTP 429"),
        SourceFailure(
            source_type=SourceType.REVIEW_SITE, reason="quota_exhausted", detail="Firecrawl: a cap is reached"
        ),
    ]
    note = build_coverage_note([], failures, partial=False)
    assert note is not None
    assert "Jina Reader hit a rate limit" in note
    assert "Firecrawl ran out of quota" in note
    assert "hard-to-read pages" in note


Answer = Callable[[httpx.Request], httpx.Response]


# --- the stage -------------------------------------------------------------------------------------------------------


class _Pages(FakePages):
    """FakePages, plus Reddit threads read through Firecrawl, and a page lost to its readers."""

    def __init__(self, pages: dict[str, Source], lost: dict[str, PageResult] | None = None) -> None:
        super().__init__(pages=pages)
        self.lost = lost or {}
        self.reddit_calls: list[str] = []

    def fetch(self, url: str, usage: SearchUsage) -> Any:
        if url in self.lost:
            with self._lock:
                self.calls.append(url)
            return self.lost[url]
        return super().fetch(url, usage)

    def read_reddit(self, url: str, usage: SearchUsage) -> PageResult:
        with self._lock:
            self.reddit_calls.append(url)
        return PageResult(source=thread("zzz999", "A thread the archive lacks"))


def _stage(serper: FakeSerper, pages: _Pages, reddit: FakeReddit | None) -> LiveResearchStage:
    return LiveResearchStage(
        settings=_settings().model_copy(update={"deadline_s": 5.0}),
        serper=serper,  # type: ignore[arg-type]
        pages=pages,  # type: ignore[arg-type]
        reddit=reddit,  # type: ignore[arg-type]
        usage=UsageBook(CAPS | {"reddit_archive": ProviderCap(per_search=10, per_day=500)}, path=None),
        pool=ThreadPoolExecutor(max_workers=6),
    )


def _run(stage: LiveResearchStage) -> ResearchResult:
    return stage.run(ResearchInput(rubric=rubric()), StageContext(search_id="srch_readers"))


def test_maker_and_store_pages_stop_at_3_each() -> None:
    makers = [f"https://www.maker.example/nova-{i}" for i in range(5)]
    stores = [f"https://www.store.example/nova-{i}" for i in range(5)]
    found = SerperResult(sources=tuple(web_source(url) for url in [*makers, *stores]))
    pages = _Pages({url: review_source(url) for url in [*makers, *stores]})
    _run(_stage(FakeSerper(default=found), pages, FakeReddit(threads=[thread("aaa")])))
    assert sum(url in makers for url in pages.calls) == 3
    assert sum(url in stores for url in pages.calls) == 3


def test_a_reddit_thread_goes_to_firecrawl_only_when_the_archive_lacks_it() -> None:
    archived = thread("aaa")
    missing = "https://www.reddit.com/r/laptops/comments/zzz999/a_thread_the_archive_lacks/"
    found = SerperResult(reddit_links=(str(archived.url), missing))
    pages = _Pages({})
    result = _run(_stage(FakeSerper(default=found), pages, FakeReddit(threads=[archived])))
    assert pages.reddit_calls == [missing]
    reddit = [s for s in result.sources if s.type is SourceType.REDDIT]
    assert len(reddit) == 2 and all(s.data_class is DataClass.REDDIT for s in reddit)


def test_with_reddit_off_no_thread_is_read_through_firecrawl() -> None:
    missing = "https://www.reddit.com/r/laptops/comments/zzz999/a_thread_the_archive_lacks/"
    pages = _Pages({})
    _run(_stage(FakeSerper(default=SerperResult(reddit_links=(missing,))), pages, None))
    assert pages.reddit_calls == []


def test_a_page_lost_to_its_readers_names_them_in_the_result() -> None:
    lost_url = f"{SITE}/nova"
    lost = PageResult(
        reason="not_found",
        lost_to=(
            SourceFailure(source_type=SourceType.REVIEW_SITE, reason="rate_limited", detail="Jina Reader: HTTP 429"),
            SourceFailure(source_type=SourceType.REVIEW_SITE, reason="error", detail="Firecrawl: HTTP 500"),
        ),
    )
    pages = _Pages({}, lost={lost_url: lost})
    result = _run(_stage(FakeSerper(default=SerperResult(sources=(web_source(lost_url),))), pages, None))
    named = [f.detail.split(":")[0] for f in result.failures if f.detail]
    assert "Jina Reader" in named and "Firecrawl" in named
    note = build_coverage_note([], result.failures, partial=False) or ""
    assert "Jina Reader hit a rate limit" in note and "Firecrawl had an error" in note


# --- from the review -------------------------------------------------------------------------------------------------


def test_a_reader_page_after_a_redirect_takes_the_landing_sites_kind() -> None:
    web = Web().on(PAGE, httpx.Response(302, headers={"location": STORE})).on(STORE, httpx.ReadTimeout("slow"))
    web.on(*_jina(STORE))
    found = _pages(web).fetch(PAGE, _usage())
    source = found.source
    assert source is not None and source.via == "jina"
    assert str(source.url) == STORE
    assert (source.type, source.tier, source.site) == (SourceType.RETAILER, SourceTier.RETAILER, "store.example")


def test_a_reddit_username_written_as_plain_link_words_is_removed() -> None:
    markdown = f"[made_up_editor](https://www.reddit.com/user/made_up_editor/) • 2y ago\n\n{WORDS}"
    web = Web().on(FIRECRAWL_SCRAPE_URL, _scrape(markdown=markdown))
    source = _pages(web).read_reddit(THREAD, _usage()).source
    assert source is not None and "made_up_editor" not in source.text


def test_jinas_failure_is_named_even_when_firecrawl_reads_the_page() -> None:
    web = Web().on(PAGE, THIN).on(f"{JINA_URL}{PAGE}", httpx.Response(429)).on(FIRECRAWL_SCRAPE_URL, _scrape())
    found = _pages(web).fetch(PAGE, _usage())
    assert found.source is not None and found.source.via == "firecrawl"
    assert [(f.detail.split(":")[0], f.reason) for f in found.lost_to] == [("Jina Reader", "rate_limited")]


def test_a_failed_backup_search_is_named_too() -> None:
    web = Web().on(FIRECRAWL_SEARCH_URL, httpx.Response(402, json={"success": False}))
    firecrawl = FirecrawlClient(
        web.client(), "fc-made-up", cache=ResearchCache(folder=None, now=lambda: NOW), gate=FirecrawlGate()
    )
    found = SearchWithBackup(_no_credits(), firecrawl).search("best laptop", "IN", _usage())
    assert found.failure is not None and found.failure.reason == "quota_exhausted"
    assert [f.detail.split(":")[0] for f in found.also_failed] == ["Firecrawl"]


def test_a_cached_firecrawl_search_keeps_its_reddit_titles() -> None:
    cache = ResearchCache(folder=None, now=lambda: NOW)
    reply = httpx.Response(
        200,
        json={"success": True, "data": {"web": [{"url": THREAD, "title": "Nova Book 14 for editing? : r/laptops"}]}},
    )
    web = Web().on(FIRECRAWL_SEARCH_URL, reply)
    firecrawl = FirecrawlClient(web.client(), "fc-made-up", cache=cache, gate=FirecrawlGate(), now=lambda: NOW)
    first = firecrawl.search("nova", "IN", _usage())
    again = firecrawl.search("nova", "IN", _usage())
    assert again.from_cache and again.reddit_titles == first.reddit_titles == ("Nova Book 14 for editing?",)


def test_requests_waiting_for_a_slot_cannot_pass_10_a_minute() -> None:
    gate = FirecrawlGate(clock=Clock(), wait_s=0.01)
    started = [gate.try_start() for _ in range(12)]  # two run, the rest find no slot in time
    assert started.count(None) == 2
    for _ in range(2):
        gate.finish(200)
    more = [gate.try_start() for _ in range(10)]
    assert more.count(None) <= 8  # the minute holds 10 at most, counting the two that ran
    for _ in range(more.count(None)):
        gate.finish(200)


def test_reddit_links_never_use_the_review_pages_share() -> None:
    links = tuple(f"https://www.reddit.com/r/laptops/comments/zz{i:04d}/made_up/" for i in range(12))
    reviews = [f"{SITE}/review-{i}" for i in range(3)]
    found = SerperResult(sources=tuple(web_source(url) for url in reviews), reddit_links=links)
    pages = _Pages({url: review_source(url) for url in reviews})
    _run(_stage(FakeSerper(default=found), pages, FakeReddit(threads=[thread("aaa")])))
    assert sorted(url for url in pages.calls if url in reviews) == sorted(reviews)
    assert len(pages.reddit_calls) == 4  # no more than Firecrawl could read in a search


def test_reader_text_goes_through_the_same_extraction_and_quote_rules() -> None:
    """Criterion 4: a page read through Jina is extracted like any other, and a claim must quote its text."""
    from datetime import UTC, datetime

    from app.contracts import ExtractInput, LLMTask
    from app.extract import GuardedExtractStage
    from app.extract.parsing import RawClaim, RawExtraction
    from tests.fakes.llm import FakeLLMClient

    quote = "word10 word11 word12 word13"
    web = Web().on(PAGE, THIN).on(*_jina(PAGE, text=f"The Nova Book 14 battery_life note. {WORDS}"))
    source = _pages(web).fetch(PAGE, _usage()).source
    assert source is not None and source.via == "jina"
    raw = RawExtraction(
        mentions=[],
        claims=[
            RawClaim(
                product_mention="Nova Book 14",
                criterion_key="battery_life",
                score=8.0,
                quote=quote,
                polarity="positive",
            ),
            RawClaim(
                product_mention="Nova Book 14",
                criterion_key="battery_life",
                score=2.0,
                quote="not on the page at all",
                polarity="negative",
            ),
        ],
    )
    stage = GuardedExtractStage(FakeLLMClient({LLMTask.EXTRACT_CLAIMS: raw}), now=lambda: datetime.now(UTC))
    out = stage.run(
        ExtractInput(source=source, criterion_keys=["battery_life"], category_label="laptop"),
        StageContext(search_id="srch_jina"),
    )
    assert [c.quote for c in out.claims] == [quote]  # the made-up quote is dropped, as for every source
