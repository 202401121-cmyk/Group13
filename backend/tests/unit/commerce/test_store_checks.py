"""US-24 (S5-16, D-99): the parts of the store-page checks, one at a time. A made-up web (tests/fakes/store_web.py)
answers every request; no live call is made. Every store, page and product here is made up.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from time import monotonic

import httpx
import pytest

from app.commerce.links import (
    Store,
    address_words,
    is_search_page,
    is_store_host,
    load_stores,
    names_model,
    registrable,
    shown_domain,
    with_page,
)
from app.commerce.safe_browsing import SAFE_BROWSING_URL, SafeBrowsing
from app.commerce.store_pages import (
    MAX_ROBOTS_BYTES,
    FirecrawlFirst,
    StorePageChecker,
    product_names,
)
from app.contracts import Offer, SourceFailure, SourceType
from app.research.cache import ResearchCache
from app.research.serper import SerperResult
from app.research.usage import SearchUsage
from tests.fakes.research import web_source
from tests.fakes.store_web import NOW, StoreWeb, book, scraped
from tests.unit.commerce.test_shopping_stage import FETCHED, SHOPPING_OFFER, VIVOBOOK, VIVOBOOK_TITLE

PAGE = "https://gameloot.in/asus-vivobook-15-x1504va-laptop"
DISALLOW_ALL = "User-agent: *\nDisallow: /\n"


def _usage() -> SearchUsage:
    return book().search()


def _later(seconds: float = 30.0) -> float:
    return monotonic() + seconds


# --- links.py --------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("host", "site"),
    [
        ("www.gameloot.in", "gameloot.in"),
        ("gameloot.in", "gameloot.in"),
        ("shop.example.co.in", "example.co.in"),
        ("m.store.com.au", "store.com.au"),
        ("www.gadgetsclub.in.example.com", "example.com"),
        ("WWW.GameLoot.IN.", "gameloot.in"),
    ],
)
def test_the_registered_name_of_a_host(host: str, site: str) -> None:
    assert registrable(host) == site


@pytest.mark.parametrize(
    ("name", "domain"),
    [("gadgetsclub.in", "gadgetsclub.in"), ("Desertcart.ae", "desertcart.ae"), ("www.Aajjo.com", "aajjo.com"),
     ("GameLoot", None), ("Gadgets Now", None), ("v1.0", None)],
)  # fmt: skip
def test_a_store_named_by_its_address(name: str, domain: str | None) -> None:
    assert shown_domain(name) == domain


@pytest.mark.parametrize(
    ("name", "host", "is_it"),
    [
        ("GameLoot", "gameloot.in", True),
        ("GameLoot", "www.gameloot.in", True),
        ("GameLoot", "shop.gameloot.in", False),  # another of its sites
        ("GameLoot", "gamelootdeals.in", False),
        ("Gadgets Now", "gadgetsnow.com", True),
        ("Gadgets Club", "gadgets-club.in", False),  # a hyphen the name lacks: a look-alike
        ("gadgetsclub.in", "m.gadgetsclub.in", True),
        ("gadgetsclub.in", "gadgetsclub.in.example.com", False),
        ("Flipkart", "www.flipkart.com", True),
        ("Amazon", "www.amazon.com", False),  # "Amazon" is the known amazon.in
        ("Tata CLiQ", "www.tatacliq.com", True),
    ],
)
def test_whose_site_a_host_is(name: str, host: str, is_it: bool) -> None:
    assert is_store_host(name, host, load_stores()) is is_it


def test_a_model_code_must_be_a_whole_token() -> None:
    assert names_model(VIVOBOOK, VIVOBOOK_TITLE)
    assert names_model(VIVOBOOK, "ASUS Vivobook 15 (X1504VA-NJ521WS) Laptop")
    assert not names_model(VIVOBOOK, "ASUS Vivobook 15 X1504VAP Laptop")
    assert not names_model(VIVOBOOK, "ASUS Vivobook 15 AX1504VA Laptop")  # accepts allows it; a store page may not
    assert not names_model(VIVOBOOK, "HP Victus 15 Laptop")


def test_an_address_read_as_words() -> None:
    assert address_words("https://www.amazon.in/ASUS-Vivobook-15-X1504VA%20Laptop/dp/B0CTEST") == (
        "ASUS Vivobook 15 X1504VA Laptop dp B0CTEST"
    )
    assert address_words("http://[::1") == ""


@pytest.mark.parametrize(
    ("url", "search"),
    [
        ("https://gameloot.in/search?q=vivobook", True),
        ("https://www.amazon.in/s?k=vivobook", True),
        ("https://store.example/catalogsearch/result/?q=x1504va", True),
        ("https://store.example/?q=x1504va", True),
        ("https://store.example/?utm_source=ads", False),
        ("https://store.example/asus-vivobook-15-x1504va?keywords=asus", False),  # a product page's tracking
        ("https://store.example/searchlight-x1504va", False),
        ("http://[::1", True),  # an address that can't be read is never shown
    ],
)
def test_a_search_results_page(url: str, search: bool) -> None:
    assert is_search_page(url) is search


def test_a_broken_product_path_proves_nothing() -> None:
    store = Store(name="Made-up", domain="made-up.example", product_path="(")
    assert store.is_product_page("/p/123") is False


def test_a_page_the_contract_refuses_keeps_the_google_link() -> None:
    offer = Offer.model_validate(
        {"product_id": VIVOBOOK.id, "store": "GameLoot via Google Shopping", "url": SHOPPING_OFFER,
         "listing_title": VIVOBOOK_TITLE, "price": {"amount": 55000}, "as_of": FETCHED, "title_matched": True}
    )  # fmt: skip
    assert with_page(offer, "not an address", "GameLoot") is offer
    assert str(with_page(offer, PAGE, "GameLoot").url) == PAGE


# --- product data -----------------------------------------------------------------------------------------------------


def _ld(data: object) -> str:
    return f'<html><script type="application/ld+json">{json.dumps(data)}</script></html>'


@pytest.mark.parametrize(
    ("html", "names"),
    [
        (_ld({"@type": "Product", "name": "ASUS Vivobook 15 X1504VA"}), ["ASUS Vivobook 15 X1504VA"]),
        (_ld([{"@type": "WebPage"}, {"@type": ["Thing", "Product"], "name": "A"}]), ["A"]),  # a list of things
        (_ld({"@graph": [{"@type": "BreadcrumbList"}, {"@type": "schema:Product", "name": "B"}]}), ["B"]),
        (_ld({"@type": "ItemPage", "mainEntity": {"@type": "http://schema.org/ProductGroup", "name": "C"}}), ["C"]),
        (_ld({"@type": "Product", "name": {"@value": "D"}}), []),  # Product data, but no name in plain text
        (_ld(["a plain string", 7, {"@type": "Product", "name": "G"}]), ["G"]),  # values that are no objects: skipped
        (_ld({"@type": "Organization", "name": "GameLoot"}), None),
        ('<html><script type="application/ld+json">{not json</script></html>', None),
        ('<div itemscope itemtype="https://schema.org/Product"><span itemprop="name">E</span></div>', []),
        ("<html><body>No data here.</body></html>", None),
    ],
)
def test_schema_org_product_data_is_found_wherever_the_page_puts_it(html: str, names: list[str] | None) -> None:
    assert product_names(html) == names


def test_product_data_buried_too_deep_is_not_read() -> None:
    buried: object = {"@type": "Product", "name": "F"}
    for _ in range(12):
        buried = [buried]
    assert product_names(_ld(buried)) is None


# --- the search -------------------------------------------------------------------------------------------------------


class _Search:
    def __init__(self, answer: SerperResult) -> None:
        self.answer = answer
        self.asked = 0

    def search(self, query: str, region: str, usage: SearchUsage, sites: tuple[str, ...] = ()) -> SerperResult:
        self.asked += 1
        return self.answer


def test_the_search_is_serpers_when_firecrawl_has_no_key() -> None:
    serper = _Search(SerperResult(sources=(web_source(PAGE, title=VIVOBOOK_TITLE),)))
    search = FirecrawlFirst(None, serper)
    assert search.search("ASUS Vivobook 15 X1504VA", "IN", _usage()).sources[0].title == VIVOBOOK_TITLE
    assert repr(search).startswith("FirecrawlFirst(None, ")


def test_the_search_goes_to_serper_only_when_firecrawl_fails() -> None:
    serper = _Search(SerperResult())
    working = FirecrawlFirst(_Search(SerperResult()), serper)
    working.search("ASUS Vivobook 15 X1504VA", "IN", _usage())
    assert serper.asked == 0
    down = SerperResult(failure=SourceFailure(source_type=SourceType.WEB_SEARCH, reason="error"))
    FirecrawlFirst(_Search(down), serper).search("ASUS Vivobook 15 X1504VA", "IN", _usage())
    assert serper.asked == 1


# --- Safe Browsing ----------------------------------------------------------------------------------------------------


def test_safe_browsing_is_asked_for_the_address_alone_with_the_key_in_a_header() -> None:
    web = StoreWeb()
    safe = SafeBrowsing(web.client(), "made-up-key")
    assert safe.check(PAGE) == "safe"
    (request,) = web.requests
    assert str(request.url) == SAFE_BROWSING_URL  # no key in the address, so no log can hold it
    assert request.headers["x-goog-api-key"] == "made-up-key"
    body = json.loads(request.content)
    assert body["threatInfo"]["threatEntries"] == [{"url": PAGE}]
    assert body["threatInfo"]["threatEntryTypes"] == ["URL"]
    assert "made-up-key" not in repr(safe)


@pytest.mark.parametrize(("matches", "answer"), [([], "safe"), ([{"threatType": "MALWARE"}], "threat"), (None, "safe")])
def test_safe_browsing_answers(matches: object, answer: str) -> None:
    web = StoreWeb()
    web.safe = lambda _: httpx.Response(200, json={} if matches is None else {"matches": matches})
    assert SafeBrowsing(web.client(), "made-up-key").check(PAGE) == answer


# --- the checker ------------------------------------------------------------------------------------------------------


def _checker(web: StoreWeb, cache: ResearchCache | None = None) -> StorePageChecker:
    return web.wiring(cache)[1]


def test_the_checker_keeps_its_verdicts_on_the_real_clock_by_default() -> None:
    web = StoreWeb()
    checker = StorePageChecker(web.client(), cache=ResearchCache(folder=None), safe_browsing=SafeBrowsing(web.client(),
                               None), firecrawl=None, jina=None)  # fmt: skip
    assert abs(checker._now() - datetime.now(UTC)) < timedelta(seconds=5)


def test_a_reader_that_does_not_say_the_status_says_nothing_about_the_page() -> None:
    web = StoreWeb()
    web.scraped[PAGE] = scraped(PAGE, VIVOBOOK_TITLE, status=None)
    verdict = _checker(web).verify(VIVOBOOK, PAGE, _usage(), _later())
    assert (verdict.url, verdict.why, verdict.keep) == (None, "unread", None)


def test_a_page_that_does_not_open_with_200_fails_for_a_day() -> None:
    web = StoreWeb()
    web.scraped[PAGE] = scraped(PAGE, VIVOBOOK_TITLE, status=404)
    verdict = _checker(web).verify(VIVOBOOK, PAGE, _usage(), _later())
    assert (verdict.url, verdict.why, verdict.keep) == (None, "status", timedelta(hours=24))


def test_a_known_stores_product_address_is_proof_after_a_redirect_too() -> None:
    web = StoreWeb()
    flipkart = "https://www.flipkart.com/asus-vivobook-15-x1504va/p/itm0123456789"
    web.scraped["https://www.flipkart.com/asus-vivobook-15-x1504va"] = scraped(
        "https://www.flipkart.com/asus-vivobook-15-x1504va", VIVOBOOK_TITLE, "<html>No data.</html>", final=flipkart
    )
    verdict = _checker(web).verify(VIVOBOOK, "https://www.flipkart.com/asus-vivobook-15-x1504va", _usage(), _later())
    assert verdict.url == flipkart


def test_no_time_left_before_the_read_starts_none() -> None:
    web = StoreWeb()
    cache = ResearchCache(folder=None, now=lambda: NOW)
    cache.put_text(("robots", "gameloot.in"), "")  # robots.txt known already: no clock reading for it
    checker = _checker(web, cache)
    times: Iterator[float] = iter([0.0, 0.6])  # time enough for Safe Browsing, then none for the read
    checker._clock = lambda: next(times, 0.6)  # type: ignore[method-assign]
    verdict = checker.verify(VIVOBOOK, PAGE, _usage(), ends=0.7)
    assert (verdict.url, verdict.why, verdict.keep) == (None, "late", None)
    assert len(web.sent_to("safebrowsing.googleapis.com")) == 1 and web.sent_to("api.firecrawl.dev") == []


def test_a_robots_address_httpx_can_not_send_disallows_for_now() -> None:
    checker = _checker(StoreWeb())

    def refuses(url: str, until: float) -> httpx.Response:
        raise ValueError("an address httpx can't send")

    checker._send_get = refuses  # type: ignore[method-assign]
    assert checker._read_robots(PAGE, _usage(), _later()) == (DISALLOW_ALL, False)


def test_a_kept_verdict_that_cannot_be_read_is_none() -> None:
    web = StoreWeb()
    cache = ResearchCache(folder=None, now=lambda: NOW)
    checker = _checker(web, cache)
    key = ("store_page", "asus vivobook 15 x1504va", "gameloot.in")
    for text in ("not json", json.dumps({"url": PAGE}), json.dumps({"url": 7, "until": NOW.isoformat()}),
                 json.dumps({"url": PAGE, "until": "2026-11-26T06:00:00"})):  # fmt: skip
        cache.put_text(key, text)
        assert checker._kept(key) == (False, None)
    cache.put_text(key, json.dumps({"url": None, "until": (NOW + timedelta(hours=1)).isoformat()}))
    assert checker._kept(key) == (True, None)


# --- robots.txt -------------------------------------------------------------------------------------------------------


def _robots(web: StoreWeb, cache: ResearchCache | None = None) -> tuple[str, bool, str | None]:
    """(the verdict's reason, whether it is kept, the robots text kept for the host)."""
    cache = cache if cache is not None else ResearchCache(folder=None, now=lambda: NOW)
    verdict = _checker(web, cache)._robots(PAGE, _usage(), _later())
    kept = cache.get_text(("robots", "gameloot.in"))
    return ("allowed" if verdict is None else verdict.why), (verdict is not None and verdict.keep is not None), kept


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (200, "User-agent: *\nDisallow: /asus-\n", ("robots", True, "User-agent: *\nDisallow: /asus-\n")),
        (200, "User-agent: *\nAllow: /\n", ("allowed", False, "User-agent: *\nAllow: /\n")),
        (404, "", ("allowed", False, "")),  # no file: everything is allowed, and that is kept
        (401, "", ("robots", True, "User-agent: *\nDisallow: /\n")),
        (403, "", ("robots", True, "User-agent: *\nDisallow: /\n")),
        (429, "", ("robots", False, None)),  # busy: no for now, asked again next time
        (503, "", ("robots", False, None)),
    ],
)
def test_robots_answers_are_read_as_research_reads_them(status: int, body: str, expected: tuple[object, ...]) -> None:
    web = StoreWeb()
    web.robots["gameloot.in"] = (status, body)
    assert _robots(web) == expected


def test_a_robots_redirect_inside_the_store_is_followed() -> None:
    web = StoreWeb()
    web.robots_moves["gameloot.in"] = "https://www.gameloot.in/robots.txt"
    web.robots["www.gameloot.in"] = (200, "User-agent: *\nDisallow: /\n")
    assert _robots(web)[:2] == ("robots", True)
    assert web.robots_asked() == ["gameloot.in", "www.gameloot.in"]


@pytest.mark.parametrize(
    "move",
    ["https://robots.elsewhere.example/robots.txt", "https://gameloot.in/robots.txt"],  # off the site; a loop
)
def test_a_robots_redirect_off_the_store_or_without_end_disallows_for_now(move: str) -> None:
    web = StoreWeb()
    web.robots_moves["gameloot.in"] = move
    assert _robots(web) == ("robots", False, None)
    assert all(host == "gameloot.in" for host in web.robots_asked()) and len(web.robots_asked()) <= 4


def test_a_robots_redirect_with_nowhere_to_go_disallows_for_now() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(301, headers={"location": ""})

    web = StoreWeb()
    web.client = lambda: httpx.Client(transport=httpx.MockTransport(handle))  # type: ignore[method-assign]
    assert _robots(web) == ("robots", False, None)


def test_a_robots_file_that_cannot_be_reached_disallows_for_now() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("made-up refusal", request=request)

    web = StoreWeb()
    web.client = lambda: httpx.Client(transport=httpx.MockTransport(handle))  # type: ignore[method-assign]
    assert _robots(web) == ("robots", False, None)


def test_the_page_cap_stops_a_robots_read_and_nothing_is_kept() -> None:
    web = StoreWeb()
    checker = _checker(web)
    spent = book()
    usage = spent.search({"pages": 0})
    verdict = checker._robots(PAGE, usage, _later())
    assert verdict is not None and (verdict.why, verdict.keep) == ("late", None)
    assert web.robots_asked() == []


class _Broken(httpx.SyncByteStream):
    def __iter__(self) -> Iterator[bytes]:
        raise httpx.ReadError("made-up break")


class _Endless(httpx.SyncByteStream):
    def __iter__(self) -> Iterator[bytes]:
        while True:
            yield b"#" * 65536


def test_a_robots_body_that_breaks_disallows_for_now() -> None:
    checker = _checker(StoreWeb())
    answer = checker._robots_answer(httpx.Response(200, stream=_Broken()), _later())
    assert answer == ("User-agent: *\nDisallow: /\n", False)


def test_a_robots_body_is_read_up_to_its_limit() -> None:
    text, keep = _checker(StoreWeb())._robots_answer(httpx.Response(200, stream=_Endless()), _later())
    assert len(text) == MAX_ROBOTS_BYTES and keep


def test_a_robots_body_still_arriving_at_its_time_disallows_for_now() -> None:
    """httpx's timeout is per read: a server sending a little at a time never trips it, so the body has its own."""
    checker = _checker(StoreWeb())
    ticks = iter([0.0, 6.0])
    checker._clock = lambda: next(ticks, 6.0)  # type: ignore[method-assign]
    answer = checker._robots_answer(httpx.Response(200, stream=_Endless()), until=5.0)
    assert answer == ("User-agent: *\nDisallow: /\n", False)


# --- the review's fixes -----------------------------------------------------------------------------------------------


def test_the_address_shown_after_a_redirect_passes_safe_browsing_too() -> None:
    """The page ended somewhere else on the store's site: that address is what the offer shows, so it is checked."""
    web = StoreWeb()
    moved = "https://gameloot.in/promo/asus-vivobook-15-x1504va"

    def listed(request: httpx.Request) -> httpx.Response:
        asked = json.loads(request.content)["threatInfo"]["threatEntries"][0]["url"]
        return httpx.Response(200, json={"matches": [{"threatType": "MALWARE"}]} if asked == moved else {})

    web.safe = listed
    web.scraped[PAGE] = scraped(PAGE, VIVOBOOK_TITLE, final=moved)
    verdict = _checker(web).verify(VIVOBOOK, PAGE, _usage(), _later())
    assert (verdict.url, verdict.why, verdict.keep) == (None, "threat", timedelta(minutes=30))
    assert len(web.sent_to("safebrowsing.googleapis.com")) == 2


def test_a_crawl_delay_longer_than_research_allows_is_a_no_as_in_research() -> None:
    web = StoreWeb()
    web.robots["gameloot.in"] = (200, "User-agent: *\nCrawl-delay: 30\nAllow: /\n")
    assert _robots(web)[:2] == ("robots", True)
    web = StoreWeb()
    web.robots["gameloot.in"] = (200, "User-agent: *\nCrawl-delay: 5\nAllow: /\n")
    assert _robots(web)[0] == "allowed"


def test_robots_rules_read_the_path_as_sent() -> None:
    """As research does: an encoded slash is not a slash, so a rule written for /a%2Fb stops /a%2Fb."""
    web = StoreWeb()
    web.robots["gameloot.in"] = (200, "User-agent: *\nDisallow: /a%2Fb\n")
    checker = _checker(web)
    assert checker._robots("https://gameloot.in/a%2Fb-x1504va", _usage(), _later()) is not None
    assert checker._robots("https://gameloot.in/a/b-x1504va", _usage(), _later()) is None


def test_a_robots_read_that_fails_once_is_tried_once_more() -> None:
    web = StoreWeb()
    answers = iter([httpx.Response(503), httpx.Response(200, text="User-agent: *\nDisallow: /\n")])

    def flaky(request: httpx.Request) -> httpx.Response:
        return next(answers)

    web.client = lambda: httpx.Client(transport=httpx.MockTransport(flaky))  # type: ignore[method-assign]
    usage = _usage()
    assert _checker(web)._robots(PAGE, usage, _later()) is not None  # the retry's file said no
    assert usage.used["pages"] == 2  # the retry took a page too


def test_no_robots_retry_without_time_for_it() -> None:
    checker = _checker(StoreWeb())
    usage = _usage()
    assert checker._may_retry(usage, until=monotonic() + 0.1) is False
    assert usage.used["pages"] == 0


def test_a_store_named_only_by_its_name_is_never_under_an_odd_ending() -> None:
    stores = load_stores()
    assert is_store_host("GameLoot", "gameloot.in", stores)
    assert is_store_host("GameLoot", "gameloot.co.in", stores)
    assert not is_store_host("GameLoot", "gameloot.shop", stores)
    assert not is_store_host("GameLoot", "gameloot.xyz", stores)


def test_each_shop_on_shared_hosting_is_its_own_site() -> None:
    assert registrable("www.mystore.myshopify.com") == "mystore.myshopify.com"
    web = StoreWeb()
    shop = "https://mystore.myshopify.com/products/asus-vivobook-15-x1504va"
    web.scraped[shop] = scraped(shop, VIVOBOOK_TITLE, final="https://other.myshopify.com/products/x1504va")
    verdict = _checker(web).verify(VIVOBOOK, shop, _usage(), _later())
    assert (verdict.url, verdict.why) == (None, "redirect")


def test_a_site_research_never_reads_is_never_a_store_page() -> None:
    web = StoreWeb()
    for link in ("https://www.facebook.com/asus-vivobook-15-x1504va", "https://www.youtube.com/x1504va"):
        verdict = _checker(web).verify(VIVOBOOK, link, _usage(), _later())
        assert (verdict.url, verdict.why) == (None, "never_read")
    assert web.requests == []


@pytest.mark.parametrize("matches", [{"x": 1}, "x", 7])
def test_a_safe_browsing_reply_that_cannot_be_read_is_never_safe(matches: object) -> None:
    web = StoreWeb()
    web.safe = lambda _: httpx.Response(200, json={"matches": matches})
    assert SafeBrowsing(web.client(), "made-up-key").check(PAGE) == "error"


def test_the_missing_key_is_said_once_however_many_checks_reach_it_at_once(caplog: pytest.LogCaptureFixture) -> None:
    import threading

    from app.commerce.safe_browsing import forget_the_note

    forget_the_note()
    safe = SafeBrowsing(StoreWeb().client(), None)
    start = threading.Barrier(8)

    def check() -> None:
        start.wait()
        safe.check(PAGE)

    with caplog.at_level("WARNING", logger="app.commerce.safe_browsing"):
        threads = [threading.Thread(target=check) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    assert sum("SAFE_BROWSING_API_KEY" in r.getMessage() for r in caplog.records) == 1


def test_product_data_is_found_by_plain_searching_even_on_a_hostile_page() -> None:
    hostile = '<script type="application/ld+json">' * 20_000  # thousands of openings, never closed
    assert product_names(hostile) is None
    assert product_names("<script type=application/ld+json") is None  # a tag never finished
    page = '<script src="a.js"></script>' * 500 + _ld({"@type": "Product", "name": "H"})
    assert product_names(page) == ["H"]


def test_a_page_for_the_x1504va_never_stands_for_the_x1504vap() -> None:
    """D-99 #2's reverse: the product is the X1504VAP, and the page says X1504VA."""
    from app.contracts import Product

    x1504vap = Product(id="prd_asus_vivobook_15_x1504vap", name="ASUS Vivobook 15 X1504VAP", brand="ASUS")
    assert names_model(x1504vap, "ASUS Vivobook 15 X1504VAP Laptop")
    assert not names_model(x1504vap, VIVOBOOK_TITLE)
    assert not names_model(x1504vap, address_words(PAGE))
