"""US-24 with D-100 item 9: when the search finds no page on an offer's store, Firecrawl's map looks on that store's own
site by the model's name (1 credit, at most 3 a search), and the address it finds passes S5-16's same checks. A made-up
web answers every request (tests/fakes/store_web.py); no live call is made. Every store, page and product is made up."""

from __future__ import annotations

import json
from time import monotonic

from app.commerce.matching import query_for
from app.commerce.store_pages import MAX_STORE_MAPS
from app.contracts import Product
from tests.fakes.store_web import StoreWeb, book, product_html, scraped
from tests.unit.commerce.test_any_store import _google, _kept_google, _run
from tests.unit.commerce.test_shopping_stage import IDEAPAD, VICTUS, VIVOBOOK, VIVOBOOK_TITLE

CROMA_PAGE = "https://www.croma.com/asus-vivobook-15-x1504va-laptop/p/300123"
GAMELOOT_SITE = "https://www.gameloot.in"
GAMELOOT_PAGE = "https://gameloot.in/asus-vivobook-15-x1504va-laptop"


def _maps(web: StoreWeb) -> list[dict[str, object]]:
    return [json.loads(r.content) for r in web.sent_to("api.firecrawl.dev") if r.url.path == "/v2/map"]


def test_a_store_the_search_missed_is_mapped_and_its_page_checked() -> None:
    web = StoreWeb()
    web.mapped["https://www.gameloot.in"] = [{"url": GAMELOOT_PAGE, "title": f"{VIVOBOOK_TITLE} | GameLoot"}]
    web.scraped[GAMELOOT_PAGE] = scraped(GAMELOOT_PAGE, f"{VIVOBOOK_TITLE} | GameLoot", product_html(VIVOBOOK_TITLE))
    (offer,) = _run(web, {query_for(VIVOBOOK): _google("gameloot.in")}).offers
    assert str(offer.url) == GAMELOOT_PAGE  # found by the map, then every check passed
    (sent,) = _maps(web)
    assert sent["url"] == GAMELOOT_SITE and sent["search"] == "ASUS Vivobook 15 X1504VA"  # the model's name only
    assert web.robots_asked() == ["gameloot.in"]


def test_a_known_store_is_mapped_by_its_listed_domain_and_its_address_proves_the_page() -> None:
    web = StoreWeb()
    web.mapped["https://www.croma.com"] = ["https://www.croma.com/asus-vivobook-15-x1504va-laptop/p/300123"]
    (offer,) = _run(web, {query_for(VIVOBOOK): _google("Croma")}).offers
    assert str(offer.url) == CROMA_PAGE  # stores.yaml's product path proves it: no read


def test_a_map_finding_nothing_keeps_the_google_link() -> None:
    web = StoreWeb()
    web.mapped["https://www.gameloot.in"] = [{"url": "https://gameloot.in/another-laptop", "title": "Another laptop"}]
    (offer,) = _run(web, {query_for(VIVOBOOK): _google("gameloot.in")}).offers
    assert _kept_google(offer, "gameloot.in")
    assert len(_maps(web)) == 1


def test_a_store_whose_site_isnt_known_is_never_mapped() -> None:
    web = StoreWeb()
    (offer,) = _run(web, {query_for(VIVOBOOK): _google("Some Shop")}).offers
    assert _kept_google(offer, "Some Shop") and _maps(web) == []


def test_without_firecrawl_no_store_is_mapped() -> None:
    web = StoreWeb()
    _run(web, {query_for(VIVOBOOK): _google("gameloot.in")}, firecrawl_key=None)
    assert _maps(web) == []


def test_a_map_that_fails_keeps_the_google_link() -> None:
    web = StoreWeb()
    web.firecrawl_status = 402  # out of credits: the search and the map both fail
    (offer,) = _run(web, {query_for(VIVOBOOK): _google("gameloot.in")}).offers
    assert _kept_google(offer, "gameloot.in")


def test_at_most_three_maps_a_search() -> None:
    from app.commerce.store_pages import StorePageChecker

    web = StoreWeb()
    _, pages = web.wiring()
    assert isinstance(pages, StorePageChecker)
    usage = book().search()
    made_up = Product(id="prd_made_up_9", name="Made-up Laptop 9", brand="Made")
    for product in (VIVOBOOK, VICTUS, IDEAPAD, made_up):  # four offers on a store the search missed
        assert pages._mapped(product, "gameloot.in", usage, monotonic() + 30) is None  # the map finds nothing
    sent = [m["search"] for m in _maps(web)]
    assert MAX_STORE_MAPS == 3 and sent == [query_for(p) for p in (VIVOBOOK, VICTUS, IDEAPAD)]  # never the fourth


def test_no_map_with_too_little_of_commerces_time_left() -> None:
    from app.commerce.store_pages import StorePageChecker

    web = StoreWeb()
    _, pages = web.wiring()
    assert isinstance(pages, StorePageChecker)
    assert pages._mapped(VIVOBOOK, "gameloot.in", book().search(), monotonic()) is None
    assert _maps(web) == []


def test_a_fourth_map_is_never_sent() -> None:
    from app.commerce.store_pages import StorePageChecker

    web = StoreWeb()
    _, pages = web.wiring()
    assert isinstance(pages, StorePageChecker)
    pages.maps = MAX_STORE_MAPS
    assert pages._mapped(VIVOBOOK, "gameloot.in", book().search(), monotonic() + 30) is None
    assert _maps(web) == []
