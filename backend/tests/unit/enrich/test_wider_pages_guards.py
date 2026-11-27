"""US-41 (S5-23, D-102): the guards around another site's page. Never an internal address, a copy of Reddit or YouTube,
a forum (even on a listed site), a Q&A site, a social site or a store; research's own reader and cache never see such a
page; and a listed page still coming is waited for. Every search, page and answer is a fake, and every site is made up:
no real API is called and no name is looked up."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from time import monotonic
from typing import Any

import httpx
import pytest

from app.contracts import LLMTask, Source, SourceTier, SourceType
from app.enrich import facts as facts_module
from app.enrich.facts import FactAsk, other_page, readable
from app.research import pages as pages_module
from app.research.cache import ResearchCache
from app.research.pages import PageResult, ReviewPages, on_open_internet
from app.research.settings import ProviderCap, ResearchSettings, load_research_settings
from app.research.usage import SearchUsage, UsageBook
from tests.fakes.llm import FakeLLMClient
from tests.fakes.reddit import NOW, Clock
from tests.fakes.research import Web, html_page, review_source, web_source
from tests.unit.enrich.test_fact_lookup import GOOD, MAKER, REVIEW, TARGET, TEXT, VELA, _answer, _lookup, _usage

SETTINGS = load_research_settings()
OTHER = "https://www.laptop-notes.example/vela-pro-14"
PUBLIC = ["93.184.216.34"]


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/x",
        "http://[::1]/x",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/vela",
        "http://localhost/vela",
        "http://intranet/vela",  # a name with no dot
        "http://printer.local/vela",
        "https://wiki.corp.internal/vela",
        "https://www.laptop-notes.example:8443/vela",  # another port
        "https://www.laptop-notes.example:notaport/vela",
    ],
)
def test_an_internal_address_or_another_port_is_never_another_page(url: str) -> None:
    assert not other_page(url, SETTINGS) and not readable(url, SETTINGS)


@pytest.mark.parametrize(
    "url",
    [
        "https://web.archive.org/web/2024/https://www.reddit.com/r/laptops/comments/abc123/x/",
        "https://archive.ph/AbCdE",
        "https://www.laptop-notes.example/copy?from=youtube.com/watch",
        "https://www.threads.com/@vela/post/abc",
        "https://bsky.app/profile/vela",
        "https://vela.tumblr.com/post/1",
        "https://www.head-fi.org/showcase/vela-pro-14.25981/reviews",  # trust.yaml's community site
        "https://xdaforums.com/t/vela-pro-14.4612345/",  # a forum by its name, on a bare domain
        "https://www.laptop-notes.example/showthread.php?t=12",
        "https://www.poorvika.com/vela-pro-14-16gb",  # a store no list names but ours
        "https://www.laptop-notes.example/buy-online/vela-pro-14",  # a store's word inside a folder's name
        "https://www.laptop-notes.example/basket",
    ],
)
def test_copies_archives_social_sites_forums_and_stores_are_never_other_pages(url: str) -> None:
    assert not other_page(url, SETTINGS) and not readable(url, SETTINGS)


def test_a_forum_on_a_listed_review_site_is_never_read_for_a_fact() -> None:
    assert readable("https://www.91mobiles.com/vela-pro-14-review", SETTINGS)
    assert not readable("https://www.91mobiles.com/forum/vela-pro-14", SETTINGS)
    assert not readable("https://zentalk.asus.com/t5/laptops/vela/td-p/1", SETTINGS)  # trust.yaml: community


def test_a_public_page_on_a_named_site_still_passes() -> None:
    assert other_page(OTHER, SETTINGS) and readable(OTHER, SETTINGS)


def test_the_lists_from_config_are_read_once_and_a_broken_trust_file_leaves_the_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facts_module._community_sites.cache_clear()

    def broken() -> object:
        raise OSError("trust.yaml unreadable")

    monkeypatch.setattr("app.verify.trust.default_trust_table", broken)
    try:
        assert facts_module._community_sites() == ()
        assert not readable("https://www.quora.com/Is-the-Vela-Pro-14-good", SETTINGS)  # the code's own list holds
    finally:
        facts_module._community_sites.cache_clear()


# --- the reader checks the name's addresses ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("answer", "public"),
    [
        (PUBLIC, True),
        (["10.0.0.5"], False),
        (["127.0.0.1"], False),
        (["169.254.169.254"], False),
        (["::1"], False),
        (["fe80::1%eth0"], False),
        (["93.184.216.34", "192.168.1.9"], False),  # one private address is enough to refuse
        ([], False),
        (["not an address"], False),
    ],
)
def test_only_a_name_on_the_open_internet_alone_is_reachable(answer: list[str], public: bool) -> None:
    assert on_open_internet("www.laptop-notes.example", lambda host: answer) is public


def test_a_name_that_wont_resolve_is_not_reachable() -> None:
    def nothing(host: str) -> list[str]:
        raise OSError("no such name")

    assert not on_open_internet("www.laptop-notes.example", nothing)


def _reader(web: Web, resolve: Any = lambda host: PUBLIC, cache: ResearchCache | None = None) -> ReviewPages:
    clock = Clock()
    return ReviewPages(
        web.client(),
        ResearchSettings(review_domains=("reviews.example",)),
        cache=cache or ResearchCache(folder=None, now=lambda: NOW),
        now=lambda: NOW,
        clock=clock,
        sleep=clock.sleep,
        other_pages=lambda url: other_page(url, ResearchSettings()),
        resolve=resolve,
    )


def _pages_usage() -> SearchUsage:
    return UsageBook({"pages": ProviderCap(per_search=16, per_day=200)}, path=None).search()


def test_a_name_that_resolves_inside_sends_nothing_not_even_for_robots() -> None:
    web = Web().on(OTHER, html_page())
    found = _reader(web, resolve=lambda host: ["10.0.0.5"]).fetch(OTHER, _pages_usage())
    assert found.reason == "blocked"
    assert web.requests == []
    taken = _reader(web, resolve=lambda host: ["10.0.0.5"]).take_read(OTHER, "t", " ".join(["w"] * 200), _pages_usage())
    assert taken.reason == "blocked" and web.requests == []


def test_a_redirect_to_an_internal_address_is_never_followed() -> None:
    inside = "http://169.254.169.254/latest/meta-data/"
    web = Web().on(OTHER, httpx.Response(302, headers={"location": inside})).on(inside, html_page())
    assert _reader(web).fetch(OTHER, _pages_usage()).source is None
    assert web.asked(inside) == 0


def test_another_sites_page_is_never_kept_in_researchs_cache() -> None:
    """A listed page redirected to another site is the caller's to keep: research's own reader, sharing the cache,
    is never handed a page from a site it doesn't read."""
    listed = "https://www.reviews.example/vela-pro-14"
    web = Web().on(listed, httpx.Response(302, headers={"location": OTHER})).on(OTHER, html_page(title="Vela notes"))
    cache = ResearchCache(folder=None, now=lambda: NOW)
    found = _reader(web, cache=cache).fetch(listed, _pages_usage())
    assert found.source is not None and found.source.type is SourceType.WEB_SEARCH
    assert cache.get_sources(("page", listed)) in (None, [], ())
    clock = Clock()
    research = ReviewPages(
        web.client(),
        ResearchSettings(review_domains=("reviews.example",)),
        cache=cache,
        now=lambda: NOW,
        clock=clock,
        sleep=clock.sleep,
    )
    assert research.fetch(listed, _pages_usage()).source is None  # its own rules: off the listed sites, blocked


def test_the_sites_paced_at_once_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pages_module, "MAX_LIMITERS", 2)
    monkeypatch.setattr(pages_module, "_page_limiters", type(pages_module._page_limiters)())
    clock = Clock()
    for site in ("a.example", "b.example", "c.example"):
        pages_module._shared_page_limiter(site, 1.0, clock, clock.sleep)
    assert [key[0] for key in pages_module._page_limiters] == ["b.example", "c.example"]
    pages_module._shared_page_limiter("b.example", 2.0, clock, clock.sleep)  # used again: kept, and slower
    assert [key[0] for key in pages_module._page_limiters] == ["c.example", "b.example"]


# --- a listed page still coming is waited for ------------------------------------------------------------------------


class KindPages:
    """Reads a listed page as its kind and another site's as research reads one; a maker's page comes `slow` late."""

    def __init__(self, slow: float = 0.0, maker_text: str = TEXT) -> None:
        self.slow, self.maker_text = slow, maker_text
        self.read: list[str] = []
        self._lock = threading.Lock()

    def fetch(self, url: str, usage: SearchUsage) -> PageResult:
        with self._lock:
            self.read.append(url)
        if url == MAKER:
            time.sleep(self.slow)
            page = review_source(url, text=self.maker_text)
            page = Source.model_validate({**page.model_dump(), "type": SourceType.MANUFACTURER, "tier": 1})
        elif url == REVIEW:
            page = review_source(url, text=TEXT)
        else:
            page = web_source(url, text=TEXT)
            page = Source.model_validate({**page.model_dump(), "via": "direct", "tier": SourceTier.RETAILER})
        return PageResult(source=Source.model_validate({**page.model_dump(), "fetched_at": NOW}))

    def take_read(self, url: str, title: str, text: str, usage: SearchUsage) -> PageResult:
        return self.fetch(url, usage)


class LinksInOrder:
    def __init__(self, links: list[str]) -> None:
        self.links = links

    def search(self, query: str, region: str, usage: SearchUsage, sites: object = ()) -> Any:
        from app.research.serper import SerperResult

        usage.take("serper")
        return SerperResult(sources=tuple(web_source(link) for link in self.links))


def _sources(found: Any) -> list[str]:
    return [str(s.url) for s in found.sources]


def test_another_sites_page_that_comes_first_waits_for_a_makers_page_still_being_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(facts_module, "PAGE_S", 0.05)  # the second link starts while the maker's is still read
    llm = FakeLLMClient({LLMTask.ENRICH_EXTRACT: _answer(GOOD)})
    pages = KindPages(slow=0.4)
    lookup = _lookup(tmp_path, LinksInOrder([MAKER, OTHER]), pages, llm)  # type: ignore[arg-type]
    found = lookup.find([(TARGET, VELA)], FactAsk("laptop", "IN"), _usage(), monotonic() + 30, "srch_guard01")
    assert pages.read == [MAKER, OTHER] and _sources(found) == [MAKER]


def test_another_sites_page_is_used_when_the_makers_page_doesnt_name_the_product(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(facts_module, "PAGE_S", 0.05)
    llm = FakeLLMClient({LLMTask.ENRICH_EXTRACT: _answer(GOOD)})
    pages = KindPages(slow=0.3, maker_text="A made-up page about another laptop altogether.")
    lookup = _lookup(tmp_path, LinksInOrder([MAKER, OTHER]), pages, llm)  # type: ignore[arg-type]
    found = lookup.find([(TARGET, VELA)], FactAsk("laptop", "IN"), _usage(), monotonic() + 30, "srch_guard02")
    assert _sources(found) == [OTHER]


def test_with_only_another_sites_page_it_is_used(tmp_path: Path) -> None:
    llm = FakeLLMClient({LLMTask.ENRICH_EXTRACT: _answer(GOOD)})
    lookup = _lookup(tmp_path, LinksInOrder([OTHER]), KindPages(), llm)  # type: ignore[arg-type]
    found = lookup.find([(TARGET, VELA)], FactAsk("laptop", "IN"), _usage(), monotonic() + 30, "srch_guard03")
    assert _sources(found) == [OTHER]


class ReadsAt:
    """Firecrawl's reading search: another site's page, at once."""

    def search_pages(self, query: str, region: str, usage: SearchUsage, **kwargs: Any) -> Any:
        from app.research.readers import SearchedPage, SearchPages

        usage.take("firecrawl", count=5)
        return SearchPages((SearchedPage(OTHER, "A made-up page", TEXT),))


def test_a_path_with_another_sites_page_waits_for_the_other_path_to_bring_a_listed_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-101's two paths: Firecrawl gives another site's page first; Serper's maker page, slower, wins (D-102)."""
    llm = FakeLLMClient({LLMTask.ENRICH_EXTRACT: _answer(GOOD)})
    lookup = _lookup(tmp_path, LinksInOrder([MAKER]), KindPages(slow=0.3), llm)  # type: ignore[arg-type]
    lookup.firecrawl = ReadsAt()  # type: ignore[assignment]
    ranked = FactAsk("laptop", "IN", ranked=("prd_someone_else", "prd_another_one", VELA.id))  # Firecrawl first
    usage = UsageBook(
        {"serper": ProviderCap(per_search=6, per_day=80), "firecrawl": ProviderCap(per_search=30, per_day=300)},
        path=None,
    ).search(per_search={"serper": 6})
    found = lookup.find([(TARGET, VELA)], ranked, usage, monotonic() + 30, "srch_guard04")
    assert _sources(found) == [MAKER]


def test_a_product_with_no_page_but_another_sites_from_either_path_gets_that_page(tmp_path: Path) -> None:
    llm = FakeLLMClient({LLMTask.ENRICH_EXTRACT: _answer(GOOD)})
    lookup = _lookup(tmp_path, LinksInOrder([]), KindPages(), llm)  # type: ignore[arg-type]
    lookup.firecrawl = ReadsAt()  # type: ignore[assignment]
    ranked = FactAsk("laptop", "IN", ranked=("prd_someone_else", "prd_another_one", VELA.id))
    usage = UsageBook(
        {"serper": ProviderCap(per_search=6, per_day=80), "firecrawl": ProviderCap(per_search=30, per_day=300)},
        path=None,
    ).search(per_search={"serper": 6})
    assert _sources(lookup.find([(TARGET, VELA)], ranked, usage, monotonic() + 30, "srch_guard05")) == [OTHER]


def test_a_port_that_is_no_port_is_never_a_named_host() -> None:
    assert not facts_module._named_host("https://www.laptop-notes.example:99999/x", "www.laptop-notes.example")


def test_the_resolver_returns_the_addresses_the_client_would_use(monkeypatch: pytest.MonkeyPatch) -> None:
    import socket

    def answer(host: str, port: object, type: int = 0) -> list[tuple[object, ...]]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(pages_module.socket, "getaddrinfo", answer)
    assert pages_module.addresses_of("www.laptop-notes.example") == ["93.184.216.34"]
