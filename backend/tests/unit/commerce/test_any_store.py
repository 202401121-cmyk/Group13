"""US-24 criterion 6 for any store (S5-16, D-99): a Google Shopping link becomes the store's own page only after code
has checked it. A made-up web (tests/fakes/store_web.py) answers every request; no live call is made. Every store,
page and product here is made up.
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from app.commerce import ShoppingCommerceStage
from app.commerce import stage as stage_module
from app.commerce import store_pages as checks_module
from app.commerce.links import VIA_GOOGLE
from app.commerce.matching import query_for
from app.commerce.safe_browsing import forget_the_note
from app.commerce.stage import MAX_STORE_SEARCHES
from app.contracts import CommerceInput, CommerceResult, Money, Offer, Product
from app.research.cache import ResearchCache
from app.research.settings import ProviderCap
from app.research.shopping import ShoppingResult
from app.research.usage import UsageBook
from tests.fakes.store_web import NOW, StoreWeb, book, found, product_html, scraped
from tests.unit.commerce.test_shopping_stage import (
    BUDGET,
    FETCHED,
    IDEAPAD,
    SHOPPING_OFFER,
    VICTUS,
    VIVOBOOK,
    VIVOBOOK_TITLE,
    FakeShopping,
    _answer,
    _listing,
    ctx,
)

GAMELOOT = "https://gameloot.in/asus-vivobook-15-x1504va-laptop"
GADGETS = "https://www.gadgetsclub.in/product/asus-vivobook-15-x1504va"


def _google(store: str, price: str = "₹55,000", title: str = VIVOBOOK_TITLE) -> ShoppingResult:
    return _answer(_listing(title, price, store=store, link=SHOPPING_OFFER))


def _run(
    web: StoreWeb,
    answers: dict[str, ShoppingResult],
    products: list[Product] | None = None,
    usage: UsageBook | None = None,
    **wiring: object,
) -> CommerceResult:
    search, pages = web.wiring(**wiring)  # type: ignore[arg-type]
    stage = ShoppingCommerceStage(FakeShopping(answers), web=search, pages=pages, usage=usage or book())
    return stage.run(CommerceInput(products=products or [VIVOBOOK], budget=BUDGET), ctx())


def _vivobook_at(web: StoreWeb, url: str, store: str, **wiring: object) -> Offer:
    """The Vivobook offered at one store through Google Shopping, with the search finding the store's own page."""
    web.firecrawl_found = [found(url, VIVOBOOK_TITLE)]
    (offer,) = _run(web, {query_for(VIVOBOOK): _google(store)}, **wiring).offers
    return offer


def _kept_google(offer: Offer, store: str) -> bool:
    return str(offer.url) == SHOPPING_OFFER and offer.store == f"{store}{VIA_GOOGLE}"


# --- any store, every check passed -------------------------------------------------------------------------------


def test_any_store_gets_its_own_page_once_every_check_passes() -> None:
    web = StoreWeb()
    web.scraped[GAMELOOT] = scraped(GAMELOOT, f"{VIVOBOOK_TITLE} | GameLoot", product_html(VIVOBOOK_TITLE))
    offer = _vivobook_at(web, GAMELOOT, "GameLoot")
    assert str(offer.url) == GAMELOOT and offer.store == "GameLoot"  # its own page, named as the store
    assert offer.price == Money(amount=55000) and offer.as_of == FETCHED  # the shopping result's, never the page's 99
    assert web.robots_asked() == ["gameloot.in"]
    assert len(web.sent_to("safebrowsing.googleapis.com")) == 1


def test_the_domain_an_offer_shows_is_its_store() -> None:
    web = StoreWeb()
    web.scraped[GADGETS] = scraped(GADGETS, VIVOBOOK_TITLE)
    offer = _vivobook_at(web, GADGETS, "gadgetsclub.in")
    assert str(offer.url) == GADGETS and offer.store == "gadgetsclub.in"


def test_the_one_search_names_the_model_only_with_no_site_and_no_store_list() -> None:
    """D-99 #1 and #8: one search a priced product, for its brand and name; never a site: list, never shopper text."""
    web = StoreWeb()
    _vivobook_at(web, GAMELOOT, "GameLoot")
    (search,) = [r for r in web.sent_to("api.firecrawl.dev") if r.url.path == "/v2/search"]
    assert json.loads(search.content) == {"query": "ASUS Vivobook 15 X1504VA", "limit": 10, "country": "IN"}


def test_at_most_three_store_searches_a_search_and_only_for_priced_products() -> None:
    web = StoreWeb()
    answers = {
        query_for(VIVOBOOK): _google("GameLoot"),
        query_for(VICTUS): _google("Desertcart.ae", title="HP Victus 15 Laptop"),
        query_for(IDEAPAD): _answer(_listing("Lenovo IdeaPad Slim 3 15IAH8", "", store="Aajjo", link=SHOPPING_OFFER)),
    }
    _run(web, answers, [VIVOBOOK, VICTUS, IDEAPAD])
    searches = [r for r in web.sent_to("api.firecrawl.dev") if r.url.path.endswith("/search")]  # maps aside (D-100)
    queries = [json.loads(r.content)["query"] for r in searches]
    assert sorted(queries) == ["ASUS Vivobook 15 X1504VA", "HP Victus 15"]  # the IdeaPad has no price
    assert MAX_STORE_SEARCHES == 3


def test_an_offer_on_the_stores_own_page_already_needs_no_search() -> None:
    web = StoreWeb()
    answers = {query_for(VIVOBOOK): _answer(_listing(VIVOBOOK_TITLE, "₹55,000", store="GameLoot", link=GAMELOOT))}
    (offer,) = _run(web, answers).offers
    assert str(offer.url) == GAMELOOT and web.requests == []


# --- the edge cases (D-99) ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://gadgetsclub.in.example.com/asus-vivobook-15-x1504va",  # example.com's site, not the store's
        "https://gadgets-club.in/asus-vivobook-15-x1504va",  # a look-alike name
        "https://shop.gadgetsclub.in/asus-vivobook-15-x1504va",  # another of its sites: never taken for the store's
    ],
)
def test_a_look_alike_host_is_never_the_store(url: str) -> None:
    web = StoreWeb()
    web.scraped[url] = scraped(url, VIVOBOOK_TITLE)
    assert _kept_google(_vivobook_at(web, url, "gadgetsclub.in"), "gadgetsclub.in")
    assert web.robots_asked() == [] and web.sent_to("safebrowsing.googleapis.com") == []  # never even checked


def test_a_redirect_to_another_domain_keeps_the_google_link() -> None:
    web = StoreWeb()
    web.scraped[GAMELOOT] = scraped(GAMELOOT, VIVOBOOK_TITLE, final="https://gameloot-deals.example/vivobook-x1504va")
    assert _kept_google(_vivobook_at(web, GAMELOOT, "GameLoot"), "GameLoot")


def test_a_redirect_inside_the_store_shows_where_the_page_ended() -> None:
    web = StoreWeb()
    moved = "https://www.gameloot.in/p/asus-vivobook-15-x1504va"
    web.scraped[GAMELOOT] = scraped(GAMELOOT, VIVOBOOK_TITLE, final=moved)
    offer = _vivobook_at(web, GAMELOOT, "GameLoot")
    assert str(offer.url) == moved
    assert web.robots_asked() == ["gameloot.in", "www.gameloot.in"]  # robots.txt asked where it ended too


def test_an_http_link_is_never_shown_or_checked() -> None:
    web = StoreWeb()
    http = "http://gameloot.in/asus-vivobook-15-x1504va-laptop"
    assert _kept_google(_vivobook_at(web, http, "GameLoot"), "GameLoot")
    assert web.robots_asked() == [] and web.sent_to("safebrowsing.googleapis.com") == []


def test_a_safe_browsing_threat_keeps_the_google_link_for_30_minutes_only(tmp_path: Path) -> None:
    web = StoreWeb()
    web.safe = lambda _: httpx.Response(200, json={"matches": [{"threatType": "SOCIAL_ENGINEERING"}]})
    web.scraped[GAMELOOT] = scraped(GAMELOOT, VIVOBOOK_TITLE)
    first = _vivobook_at(web, GAMELOOT, "GameLoot", cache=ResearchCache(folder=tmp_path, now=lambda: NOW))
    assert _kept_google(first, "GameLoot") and web.sent_to("api.firecrawl.dev")[1:] == []  # never read
    web.safe = lambda _: httpx.Response(200, json={})
    soon = NOW + timedelta(minutes=29)
    again = _vivobook_at(
        web, GAMELOOT, "GameLoot", cache=ResearchCache(folder=tmp_path, now=lambda: soon), now=lambda: soon
    )
    assert _kept_google(again, "GameLoot") and len(web.sent_to("safebrowsing.googleapis.com")) == 1  # not asked again
    later = NOW + timedelta(minutes=31)
    cleared = _vivobook_at(
        web, GAMELOOT, "GameLoot", cache=ResearchCache(folder=tmp_path, now=lambda: later), now=lambda: later
    )
    assert str(cleared.url) == GAMELOOT  # Google's answer is asked again once 30 minutes have passed


@pytest.mark.parametrize(
    "answer",
    [
        lambda _: httpx.Response(500),
        lambda _: httpx.Response(200, text="not JSON"),
        lambda _: httpx.Response(200, json=["not", "an", "object"]),
    ],
    ids=["server-error", "not-json", "not-an-object"],
)
def test_a_safe_browsing_error_keeps_the_google_link_and_is_not_kept(answer: object, tmp_path: Path) -> None:
    web = StoreWeb()
    web.safe = answer  # type: ignore[assignment]
    web.scraped[GAMELOOT] = scraped(GAMELOOT, VIVOBOOK_TITLE)
    cache = ResearchCache(folder=tmp_path, now=lambda: NOW)
    assert _kept_google(_vivobook_at(web, GAMELOOT, "GameLoot", cache=cache), "GameLoot")
    web.safe = lambda _: httpx.Response(200, json={})
    assert str(_vivobook_at(web, GAMELOOT, "GameLoot", cache=cache).url) == GAMELOOT  # tried again, not kept


def test_a_safe_browsing_timeout_keeps_the_google_link() -> None:
    web = StoreWeb()

    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("made-up timeout", request=request)

    web.safe = slow
    web.scraped[GAMELOOT] = scraped(GAMELOOT, VIVOBOOK_TITLE)
    assert _kept_google(_vivobook_at(web, GAMELOOT, "GameLoot"), "GameLoot")


def test_with_no_safe_browsing_key_the_check_is_skipped_and_said_once(caplog: pytest.LogCaptureFixture) -> None:
    forget_the_note()
    web = StoreWeb()
    web.scraped[GAMELOOT] = scraped(GAMELOOT, VIVOBOOK_TITLE)
    with caplog.at_level(logging.WARNING, logger="app.commerce.safe_browsing"):
        first = _vivobook_at(web, GAMELOOT, "GameLoot", safe_key=None)
        second = _vivobook_at(web, GAMELOOT, "GameLoot", safe_key=None)
    assert str(first.url) == str(second.url) == GAMELOOT  # the other checks still decide
    assert web.sent_to("safebrowsing.googleapis.com") == []
    said = [r.getMessage() for r in caplog.records if "SAFE_BROWSING_API_KEY" in r.getMessage()]
    assert said == ["commerce: SAFE_BROWSING_API_KEY is not set; the Safe Browsing check is skipped"]


def test_a_page_with_no_product_data_keeps_the_google_link() -> None:
    web = StoreWeb()
    web.scraped[GAMELOOT] = scraped(GAMELOOT, VIVOBOOK_TITLE, product_html(VIVOBOOK_TITLE, ld=False))
    assert _kept_google(_vivobook_at(web, GAMELOOT, "GameLoot"), "GameLoot")


@pytest.mark.parametrize("where", ["page", "search result"])
def test_a_page_for_the_x1504vap_never_stands_for_the_x1504va(where: str) -> None:
    web = StoreWeb()
    other = "ASUS Vivobook 15 X1504VAP Intel Core i5 Laptop"
    if where == "page":  # the search result names the X1504VA, but the page itself is the X1504VAP
        web.scraped[GAMELOOT] = scraped(GAMELOOT, other, product_html(other))
        offer = _vivobook_at(web, GAMELOOT, "GameLoot")
    else:
        web.firecrawl_found = [found("https://gameloot.in/asus-vivobook-15-x1504vap", other)]
        (offer,) = _run(web, {query_for(VIVOBOOK): _google("GameLoot")}).offers
    assert _kept_google(offer, "GameLoot")


@pytest.mark.parametrize(
    "url",
    [
        "https://gameloot.in/login?next=/asus-vivobook-15-x1504va",
        "https://gameloot.in/cart/add/asus-vivobook-15-x1504va",
        "https://gameloot.in/checkout/asus-vivobook-15-x1504va",
        "https://gameloot.in/search/asus-vivobook-15-x1504va",
        "https://gameloot.in/s/asus-vivobook-15-x1504va",
    ],
)
def test_a_login_cart_checkout_or_search_page_is_never_shown(url: str) -> None:
    web = StoreWeb()
    assert _kept_google(_vivobook_at(web, url, "GameLoot"), "GameLoot")
    assert web.robots_asked() == []


@pytest.mark.parametrize("final", ["https://gameloot.in/account/login", "https://gameloot.in/search?q=x1504va"])
def test_a_page_that_ends_on_a_login_or_search_page_keeps_the_google_link(final: str) -> None:
    web = StoreWeb()
    web.scraped[GAMELOOT] = scraped(GAMELOOT, VIVOBOOK_TITLE, final=final)
    assert _kept_google(_vivobook_at(web, GAMELOOT, "GameLoot"), "GameLoot")


def test_robots_txt_saying_no_keeps_the_google_link_and_reads_nothing() -> None:
    web = StoreWeb()
    web.robots["gameloot.in"] = (200, "User-agent: *\nDisallow: /asus-\n")
    web.scraped[GAMELOOT] = scraped(GAMELOOT, VIVOBOOK_TITLE)
    assert _kept_google(_vivobook_at(web, GAMELOOT, "GameLoot"), "GameLoot")
    assert web.sent_to("safebrowsing.googleapis.com") == [] and len(web.sent_to("api.firecrawl.dev")) == 1  # search


def test_robots_txt_saying_no_where_the_page_ended_keeps_the_google_link() -> None:
    web = StoreWeb()
    moved = "https://www.gameloot.in/private/asus-vivobook-15-x1504va"
    web.robots["www.gameloot.in"] = (200, "User-agent: ShopSense\nDisallow: /private/\n")
    web.scraped[GAMELOOT] = scraped(GAMELOOT, VIVOBOOK_TITLE, final=moved)
    assert _kept_google(_vivobook_at(web, GAMELOOT, "GameLoot"), "GameLoot")


def test_firecrawl_out_of_room_searches_with_serper_and_reads_with_jina() -> None:
    web = StoreWeb()
    web.serper_found = [found(GAMELOOT, VIVOBOOK_TITLE)]
    web.jina[GAMELOOT] = {"url": GAMELOOT, "title": VIVOBOOK_TITLE, "content": product_html(VIVOBOOK_TITLE)}
    full = book(firecrawl=ProviderCap(per_search=30, per_day=300, total=0))  # the plan's total is used up
    (offer,) = _run(web, {query_for(VIVOBOOK): _google("GameLoot")}, usage=full).offers
    assert str(offer.url) == GAMELOOT and offer.store == "GameLoot"
    assert web.sent_to("api.firecrawl.dev") == []  # the total stops Firecrawl for good
    assert len(web.sent_to("google.serper.dev")) == 1 and len(web.sent_to("r.jina.ai")) == 1


def test_a_page_firecrawl_can_not_read_goes_to_jina() -> None:
    web = StoreWeb()
    web.jina[GAMELOOT] = {"url": GAMELOOT, "title": VIVOBOOK_TITLE, "content": product_html(VIVOBOOK_TITLE)}
    assert str(_vivobook_at(web, GAMELOOT, "GameLoot").url) == GAMELOOT  # Firecrawl had no copy of it


@pytest.mark.parametrize("firecrawl_status", [500, 402])
def test_every_reader_down_keeps_the_google_link_and_is_not_kept(firecrawl_status: int, tmp_path: Path) -> None:
    web = StoreWeb()
    web.serper_found = [found(GAMELOOT, VIVOBOOK_TITLE)]  # Firecrawl's search is down too
    web.firecrawl_status, web.jina_status = firecrawl_status, 503
    cache = ResearchCache(folder=tmp_path, now=lambda: NOW)
    assert _kept_google(_vivobook_at(web, GAMELOOT, "GameLoot", cache=cache), "GameLoot")
    web.firecrawl_status, web.jina_status = 200, 200
    web.scraped[GAMELOOT] = scraped(GAMELOOT, VIVOBOOK_TITLE)
    assert str(_vivobook_at(web, GAMELOOT, "GameLoot", cache=cache).url) == GAMELOOT  # tried again next time


def test_a_cache_hit_spends_nothing(tmp_path: Path) -> None:
    web = StoreWeb()
    web.scraped[GAMELOOT] = scraped(GAMELOOT, VIVOBOOK_TITLE)
    spent = book()
    first = _vivobook_at(web, GAMELOOT, "GameLoot", cache=ResearchCache(folder=tmp_path, now=lambda: NOW))
    asked = len(web.requests)
    web.firecrawl_found = [found(GAMELOOT, VIVOBOOK_TITLE)]
    (again,) = _run(
        web,
        {query_for(VIVOBOOK): _google("GameLoot")},
        usage=spent,
        cache=ResearchCache(folder=tmp_path, now=lambda: NOW),
    ).offers
    assert str(first.url) == str(again.url) == GAMELOOT
    assert len(web.requests) == asked  # nothing sent: the search, robots.txt and the verdict all came from the cache
    assert spent.today() == dict.fromkeys(spent.caps, 0)


def test_a_cached_failure_is_not_tried_again(tmp_path: Path) -> None:
    web = StoreWeb()
    web.scraped[GAMELOOT] = scraped(GAMELOOT, VIVOBOOK_TITLE, product_html(VIVOBOOK_TITLE, ld=False))
    cache = ResearchCache(folder=tmp_path, now=lambda: NOW)
    assert _kept_google(_vivobook_at(web, GAMELOOT, "GameLoot", cache=cache), "GameLoot")
    web.scraped[GAMELOOT] = scraped(GAMELOOT, VIVOBOOK_TITLE)  # the page is fixed, but not today
    assert _kept_google(_vivobook_at(web, GAMELOOT, "GameLoot", cache=cache), "GameLoot")
    scrapes = [r for r in web.sent_to("api.firecrawl.dev") if r.url.path.endswith("/scrape")]
    assert len(scrapes) == 1 and len(web.sent_to("safebrowsing.googleapis.com")) == 1
    tomorrow = NOW + timedelta(hours=24, minutes=1)
    later = _vivobook_at(web, GAMELOOT, "GameLoot", cache=ResearchCache(folder=tmp_path, now=lambda: tomorrow),
                         now=lambda: tomorrow)  # fmt: skip
    assert str(later.url) == GAMELOOT  # a day on, it is tried again


# --- the known stores: a speed-up, not a gate (D-99 #5) -----------------------------------------------------------


def test_a_known_stores_product_address_needs_no_read() -> None:
    web = StoreWeb()
    flipkart = "https://www.flipkart.com/asus-vivobook-15-x1504va-laptop/p/itm0123456789"
    offer = _vivobook_at(web, flipkart, "Flipkart")
    assert str(offer.url) == flipkart and offer.store == "Flipkart"
    assert [r.url.path for r in web.sent_to("api.firecrawl.dev")] == ["/v2/search"]  # no scrape
    assert web.robots_asked() == ["www.flipkart.com"] and len(web.sent_to("safebrowsing.googleapis.com")) == 1


def test_a_known_store_page_not_in_its_product_form_is_read_like_any_other() -> None:
    web = StoreWeb()
    croma = "https://www.croma.com/asus-vivobook-15-x1504va-laptop/buy"
    web.scraped[croma] = scraped(croma, VIVOBOOK_TITLE)
    assert str(_vivobook_at(web, croma, "Croma").url) == croma
    assert [r.url.path for r in web.sent_to("api.firecrawl.dev")] == ["/v2/search", "/v2/scrape"]


def test_amazon_is_its_known_domain_never_another_amazon() -> None:
    web = StoreWeb()
    us = "https://www.amazon.com/ASUS-Vivobook-15-X1504VA-Laptop/dp/B0CTESTVB1"
    assert _kept_google(_vivobook_at(web, us, "Amazon"), "Amazon")


# --- time and failures --------------------------------------------------------------------------------------------


def test_a_check_that_raises_keeps_its_google_link(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*_: object, **__: object) -> str | None:
        raise RuntimeError("a made-up bug")

    monkeypatch.setattr(checks_module.StorePageChecker, "store_page", broken)
    web = StoreWeb()
    assert _kept_google(_vivobook_at(web, GAMELOOT, "GameLoot"), "GameLoot")


def test_a_late_check_keeps_the_google_link(monkeypatch: pytest.MonkeyPatch) -> None:
    """The stage hands back at its time; a check still running then changes nothing."""
    import threading

    release = threading.Event()
    real = checks_module.StorePageChecker.store_page

    def slow(self: checks_module.StorePageChecker, *args: object) -> str | None:
        release.wait(5)
        return real(self, *args)  # type: ignore[arg-type]

    monkeypatch.setattr(checks_module.StorePageChecker, "store_page", slow)
    monkeypatch.setattr(stage_module, "STAGE_LIMIT_S", 1.5)  # the stage hands back at 0.5 s
    monkeypatch.setattr(stage_module, "WEB_MIN_S", 0.1)
    web = StoreWeb()
    web.scraped[GAMELOOT] = scraped(GAMELOOT, VIVOBOOK_TITLE)
    try:
        offer = _vivobook_at(web, GAMELOOT, "GameLoot")
    finally:
        release.set()
    assert _kept_google(offer, "GameLoot")


@pytest.mark.parametrize("left", [0.2, 0.7])
def test_no_check_is_started_without_time_for_it(monkeypatch: pytest.MonkeyPatch, left: float) -> None:
    """With under half a second left, no request is started, and nothing is kept: the next search tries again."""
    web = StoreWeb()
    web.robots["gameloot.in"] = (200, "User-agent: *\nAllow: /\n")
    _, pages = web.wiring()
    if left > 0.5:  # robots.txt is read (and kept), then the time runs out before Safe Browsing
        times = iter([0.0, 0.6])
        monkeypatch.setattr(pages, "_clock", lambda: next(times, 0.6))
    else:
        monkeypatch.setattr(pages, "_clock", lambda: 0.0)
    verdict = pages.verify(VIVOBOOK, GAMELOOT, book().search(), ends=left)
    assert (verdict.url, verdict.why, verdict.keep) == (None, "late", None)
    assert web.sent_to("safebrowsing.googleapis.com") == []
