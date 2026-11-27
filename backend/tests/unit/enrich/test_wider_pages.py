"""US-41 (S5-23, D-102 item 2): a fact may come from any checked public page, makers' and review sites' first, and a
hard rule's spec that held a product back is looked up by its own key's words. Every search, page and answer here is a
fake, and every product, site and page is made up: no real API is called."""

from __future__ import annotations

import threading
from collections.abc import Sequence
from pathlib import Path
from time import monotonic
from typing import Any

import httpx
import pytest

from app.contracts import EnrichTarget, LLMTask, Product, Source, SourceTier, SourceType
from app.enrich.facts import (
    FactAsk,
    _path_of,
    other_page,
    other_site,
    preference,
    readable,
    search_label,
    spec_label,
)
from app.enrich.stage import facts_note
from app.extract.parsing import RawClaim
from app.extract.settings import load_extract_settings
from app.research.cache import ResearchCache
from app.research.pages import OTHER_TIER, ReviewPages
from app.research.readers import SearchedPage, SearchPages
from app.research.settings import ResearchSettings, load_research_settings
from app.research.usage import SearchUsage
from tests.fakes.llm import FakeLLMClient
from tests.fakes.reddit import NOW, Clock
from tests.fakes.research import Web, html_page, web_source
from tests.unit.enrich.test_fact_lookup import (
    ASK,
    GOOD,
    MAKER,
    REVIEW,
    TARGET,
    TEXT,
    VELA,
    FakePages,
    FakeSearch,
    _answer,
    _find,
    _lookup,
    _usage,
)

SETTINGS = load_research_settings()
OTHER = "https://www.laptop-notes.example/vela-pro-14-battery-test"
ASUS = Product(id="prd_vivobook_15_x1504va", name="Vivobook 15 X1504VA", brand="Asus")
RAM = EnrichTarget(product_id=VELA.id, product_name="Vela Pro 14", criterion_key="ram_gb")
RAM_LINE = "The Vela Pro 14 has 16 GB of RAM."
RAM_CLAIM = RawClaim(
    product_mention="Vela Pro 14", criterion_key="ram_gb", value=16, unit="GB", quote=RAM_LINE, polarity="neutral"
)


# --- which pages ------------------------------------------------------------------------------------------------------


def test_a_page_on_a_site_no_list_names_gives_a_fact_when_it_names_the_model_and_holds_the_quote(
    tmp_path: Path,
) -> None:
    llm = FakeLLMClient({LLMTask.ENRICH_EXTRACT: _answer(GOOD)})
    pages = FakePages()
    claims, sources = _find(_lookup(tmp_path, FakeSearch([OTHER]), pages, llm))
    assert claims == ["Vela Pro 14|battery_life|11.0"] and [str(s.url) for s in sources] == [OTHER]
    assert pages.read == [OTHER]


def test_a_page_naming_a_longer_model_is_refused(tmp_path: Path) -> None:
    """X1504VAP is another model: the page names the X1504VA nowhere as a whole word, so it is never sent."""
    target = EnrichTarget(product_id=ASUS.id, product_name=ASUS.name, criterion_key="battery_life")
    pages = FakePages({OTHER: "The Vivobook 15 X1504VAP lasted 8 hours in our battery test."})
    llm = FakeLLMClient({LLMTask.ENRICH_EXTRACT: _answer(GOOD)})
    found = _lookup(tmp_path, FakeSearch([OTHER]), pages, llm).find(
        [(target, ASUS)], ASK, _usage(), monotonic() + 30, "srch_wider01"
    )
    assert found.claims == [] and llm.calls == []


def test_a_quote_not_on_another_page_is_refused(tmp_path: Path) -> None:
    unsaid = RawClaim(**{**GOOD.model_dump(), "quote": "The Vela Pro 14 lasted 20 hours on one charge."})
    llm = FakeLLMClient({LLMTask.ENRICH_EXTRACT: _answer(unsaid)})
    assert _find(_lookup(tmp_path, FakeSearch([OTHER]), FakePages(), llm)) == ([], [])


@pytest.mark.parametrize(
    "url",
    [
        "https://forum.laptop-notes.example/vela-pro-14",
        "https://www.laptop-notes.example/forums/vela-pro-14",
        "https://www.laptop-notes.example/questions/123/is-the-vela-pro-14-good",
        "https://community.vela.example/battery",
        "https://www.quora.com/Is-the-Vela-Pro-14-good",
        "https://laptops.stackexchange.com/q/1/vela",
    ],
)
def test_a_forum_or_a_q_and_a_host_is_refused(tmp_path: Path, url: str) -> None:
    assert not readable(url, SETTINGS)
    pages = FakePages()
    llm = FakeLLMClient({LLMTask.ENRICH_EXTRACT: _answer(GOOD)})
    assert _find(_lookup(tmp_path, FakeSearch([url]), pages, llm)) == ([], [])
    assert pages.read == []


@pytest.mark.parametrize(
    "url",
    [
        "https://www.snapdeal.com/vela-pro-14",  # a store no list names
        "https://shop.laptop-notes.example/vela-pro-14",
        "https://www.laptop-notes.example/product/vela-pro-14",
        "https://www.amazon.in/vela-pro-14/dp/B0MADEUP",  # a listed store: never read for a fact
        "https://www.facebook.com/vela-laptops",
        "https://www.laptop-notes.example/account/login",
        "https://www.reddit.com/r/laptops/comments/abc123/vela_pro_14/",
        "https://www.youtube.com/watch?v=madeup00001",
        "not a link",
    ],
)
def test_stores_social_sites_reddit_youtube_and_login_pages_are_never_read(url: str) -> None:
    assert not readable(url, SETTINGS)
    assert not other_page(url, SETTINGS)


def test_a_listed_maker_or_review_page_keeps_its_own_kind() -> None:
    assert readable(MAKER, SETTINGS) and readable(REVIEW, SETTINGS)
    assert not other_page(MAKER, SETTINGS) and not other_page(REVIEW, SETTINGS)
    assert (preference(MAKER, SETTINGS), preference(REVIEW, SETTINGS), preference(OTHER, SETTINGS)) == (0, 1, 2)
    assert preference("https://www.amazon.in/x", SETTINGS) == 2  # a store sorts last; readable() refuses it anyway


def test_a_path_that_is_no_url_reads_as_none() -> None:
    assert _path_of("http://[::1") == ""


def test_makers_then_review_sites_then_other_pages(tmp_path: Path) -> None:
    llm = FakeLLMClient({LLMTask.ENRICH_EXTRACT: _answer(GOOD)})
    pages = FakePages()
    _find(_lookup(tmp_path, FakeSearch([OTHER, REVIEW, MAKER]), pages, llm))
    assert pages.read == [MAKER]
    later = FakePages()
    _find(_lookup(tmp_path / "again", FakeSearch([OTHER, REVIEW]), later, llm))
    assert later.read == [REVIEW]


class ReadingSearch:
    """Firecrawl's search that reads its results: the pages it was told, in that order."""

    def __init__(self, urls: Sequence[str]) -> None:
        self.urls = list(urls)

    def search_pages(
        self, query: str, region: str, usage: SearchUsage, *, results: int = 3, until: float, exclude: object = ()
    ) -> SearchPages:
        return SearchPages(tuple(SearchedPage(url, "A made-up page", TEXT) for url in self.urls))


class TakesReads(FakePages):
    def take_read(self, url: str, title: str, text: str, usage: SearchUsage) -> object:
        return self.fetch(url, usage)


def test_firecrawls_pages_go_makers_then_reviews_then_other_pages(tmp_path: Path) -> None:
    llm = FakeLLMClient({LLMTask.ENRICH_EXTRACT: _answer(GOOD)})
    lookup = _lookup(tmp_path, FakeSearch([]), TakesReads(), llm)
    lookup.firecrawl = ReadingSearch([OTHER, REVIEW])
    ranked = FactAsk(category="laptop", region="IN", ranked=("prd_someone_else", "prd_another_one", VELA.id))
    found = lookup.find([(TARGET, VELA)], ranked, _usage(), monotonic() + 30, "srch_wider02")
    assert [str(s.url) for s in found.sources] == [REVIEW]


def test_another_pages_source_is_a_web_page_and_counts_least() -> None:
    page = Source.model_validate({**web_source(OTHER).model_dump(), "via": "direct", "tier": SourceTier.RETAILER})
    assert other_site(page)
    assert not other_site(web_source(OTHER).model_copy(update={"via": "search_snippet"}))
    from tests.fakes.research import review_source

    assert not other_site(review_source(REVIEW))


# --- a rule's spec as a target ---------------------------------------------------------------------------------------


def test_a_rules_spec_is_searched_by_its_own_words_and_asked_as_a_spec(tmp_path: Path) -> None:
    search = FakeSearch([OTHER])
    pages = FakePages({OTHER: f"{RAM_LINE} {TEXT}"})
    carried = RawClaim(**{**GOOD.model_dump(), "criterion_key": "cpu_performance", "score": 8})
    llm = FakeLLMClient({LLMTask.ENRICH_EXTRACT: _answer(RAM_CLAIM, carried)})
    found = _lookup(tmp_path, search, pages, llm).find([(RAM, VELA)], ASK, _usage(), monotonic() + 30, "srch_wider03")
    assert search.asked == ["Vela Pro 14 ram"]
    assert [(c.criterion_key, c.value) for c in found.claims] == [("ram_gb", 16.0)]  # the carrier's claim is left out
    (call,) = llm.calls
    assert "- ram_gb:" in call.user and "memory_and_storage" not in call.user  # never renamed to a criterion


def test_a_spec_and_a_criterion_go_in_one_call(tmp_path: Path) -> None:
    pages = FakePages({OTHER: f"{RAM_LINE} {TEXT}"})
    llm = FakeLLMClient({LLMTask.ENRICH_EXTRACT: _answer(RAM_CLAIM, GOOD)})
    found = _lookup(tmp_path, FakeSearch([OTHER]), pages, llm).find(
        [(RAM, VELA), (TARGET, VELA)], ASK, _usage(), monotonic() + 30, "srch_wider04"
    )
    assert sorted(c.criterion_key for c in found.claims) == ["battery_life", "ram_gb"]
    assert len(llm.calls) == 1


@pytest.mark.parametrize(
    ("key", "words"),
    [
        ("ram_gb", "ram"),
        ("battery_life_h", "battery life"),
        ("has_backlit_keyboard", "backlit keyboard"),
        ("refresh_rate_hz", "refresh rate"),
        ("has_5g", "5g"),
        ("warranty_years", None),
    ],
)
def test_a_known_spec_key_has_its_own_words_and_any_other_none(key: str, words: str | None) -> None:
    assert spec_label(key, load_extract_settings(env={})) == words


def test_a_criterion_is_searched_by_its_fixed_label_and_a_spec_by_its_words() -> None:
    settings = load_extract_settings(env={})
    assert search_label("laptop", "battery_life", settings) == "Battery life"
    assert search_label("laptop", "ram_gb", settings) == "ram"
    assert search_label("laptop", "warranty_years", settings) is None


# --- research's reader, for enrich ------------------------------------------------------------------------------------

SITE = "https://www.laptop-notes.example"
PAGE = f"{SITE}/vela-pro-14"


PUBLIC_IP = ["93.184.216.34"]  # a made-up answer: the open internet


def _reader(web: Web, other: bool = True, resolve: Any = lambda host: PUBLIC_IP) -> ReviewPages:
    clock = Clock()
    return ReviewPages(
        web.client(),
        ResearchSettings(review_domains=("reviews.example",)),
        cache=ResearchCache(folder=None, now=lambda: NOW),
        now=lambda: NOW,
        clock=clock,
        sleep=clock.sleep,
        other_pages=(lambda url: other_page(url, ResearchSettings())) if other else None,
        resolve=resolve,
    )


def _pages_usage() -> SearchUsage:
    from app.research.settings import ProviderCap
    from app.research.usage import UsageBook

    return UsageBook({"pages": ProviderCap(per_search=16, per_day=200)}, path=None).search()


def test_another_page_is_read_as_a_web_page_of_the_lowest_tier() -> None:
    web = Web().on(PAGE, html_page(title="Vela Pro 14 notes"))
    found = _reader(web).fetch(PAGE, _pages_usage())
    source = found.source
    assert source is not None
    assert (source.type, source.tier, source.site, source.via) == (
        SourceType.WEB_SEARCH,
        OTHER_TIER,
        "laptop-notes.example",
        "direct",
    )
    assert OTHER_TIER is SourceTier.RETAILER  # the lowest there is (DR-03)


def test_another_pages_robots_file_is_obeyed() -> None:
    web = Web().on(f"{SITE}/robots.txt", httpx.Response(200, text="User-agent: *\nDisallow: /")).on(PAGE, html_page())
    assert _reader(web).fetch(PAGE, _pages_usage()).reason == "blocked"
    assert web.asked(PAGE) == 0


def test_a_page_the_test_refuses_or_research_own_reader_is_never_read() -> None:
    web = Web().on(PAGE, html_page())
    forum = "https://forum.laptop-notes.example/vela"
    assert _reader(web).fetch(forum, _pages_usage()).source is None
    assert (
        _reader(web, other=False).fetch(PAGE, _pages_usage()).reason == "not_found"
    )  # research reads listed sites only
    assert web.asked(PAGE) == 0


def test_a_page_firecrawl_brought_is_taken_as_another_page_of_the_lowest_tier() -> None:
    web = Web()
    found = _reader(web).take_read(PAGE, "Vela Pro 14 notes", " ".join(["word"] * 200), _pages_usage())
    assert found.source is not None and found.source.tier is OTHER_TIER and found.source.via == "firecrawl"


def test_the_real_look_up_reads_other_pages_through_research_reader(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.enrich import PriceLookupStage
    from app.llm.retry import RetryClock

    for key in ("FIRECRAWL_API_KEY", "SERPER_API_KEY", "JINA_API_KEY"):
        monkeypatch.setenv(key, "made-up")
    lookup, client = PriceLookupStage(now=lambda: NOW)._wire_facts(RetryClock(time_left=lambda: 10.0))
    client.close()
    pages = lookup.pages
    assert isinstance(pages, ReviewPages) and pages.other_pages is not None
    assert pages.other_pages(OTHER) and not pages.other_pages("https://forum.laptop-notes.example/x")


# --- the note ---------------------------------------------------------------------------------------------------------


def test_the_note_says_apart_how_many_facts_came_from_other_websites() -> None:
    assert facts_note(2) == "2 missing facts were found on maker and review pages."
    assert facts_note(1, 1) == "1 missing fact was found on another website, so it counts for less."
    assert facts_note(3, 3) == "3 missing facts were found on other websites, so they count for less."
    assert facts_note(3, 1) == (
        "3 missing facts were found: 2 on maker and review pages, and 1 on other websites, which count for less."
    )


def test_two_look_ups_at_once_share_nothing(tmp_path: Path) -> None:
    """Two readers built for enrich read the same other page at once, each on its own."""
    web = Web().on(PAGE, html_page())
    results: list[object] = []
    threads = [threading.Thread(target=lambda: results.append(_reader(web).fetch(PAGE, _pages_usage()))) for _ in "ab"]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    assert len(results) == 2
