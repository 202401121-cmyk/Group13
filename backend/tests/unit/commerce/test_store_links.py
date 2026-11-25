"""US-24 (S4-26; D-99 since S5-16): a Google Shopping link becomes the store's own page when the product's one search
finds this exact model on the store's own site and the page passes the checks (store_pages.py). Fake shopping and web
search, and a made-up web for the checks (tests/fakes/store_web.py); made-up products, pages and stores.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from pathlib import Path

import httpx
import pytest

from app.commerce import ShoppingCommerceStage
from app.commerce import stage as stage_module
from app.commerce.links import is_product_page, load_stores, store_for
from app.commerce.stage import MAX_STORE_SEARCHES
from app.contracts import CommerceInput, CommerceResult, Money, Product, Source, SourceFailure, SourceType
from app.research.cache import ResearchCache
from app.research.reddit import IntervalLimiter
from app.research.serper import SerperResult, SerperSource
from app.research.settings import ProviderCap
from app.research.shopping import Listing, ShoppingResult
from app.research.usage import SearchUsage, UsageBook
from tests.fakes.research import web_source
from tests.fakes.store_web import StoreWeb, book
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

VICTUS_PAGE = "https://www.flipkart.com/hp-victus-15-gaming-laptop/p/itm0123456789"


class FakeWeb:
    """Answers every web search with the same results; records what it was asked."""

    def __init__(self, *found: Source) -> None:
        self.found = found
        self.asked: list[tuple[str, str, tuple[str, ...]]] = []

    def search(self, query: str, region: str, usage: SearchUsage, sites: Sequence[str] = ()) -> SerperResult:
        self.asked.append((query, region, tuple(sites)))
        return SerperResult(sources=self.found)


def _google(title: str, store: str, price: str = "₹60,000") -> Listing:
    return _listing(title, price, store=store, link=SHOPPING_OFFER)


def _priced(
    web: FakeWeb | SerperSource,
    answers: dict[str, ShoppingResult],
    products: list[Product],
    usage: UsageBook | None = None,
) -> CommerceResult:
    _, pages = StoreWeb().wiring()  # robots.txt allows, Safe Browsing finds nothing; known stores need no read
    stage = ShoppingCommerceStage(FakeShopping(answers), web=web, pages=pages, usage=usage or book())
    return stage.run(CommerceInput(products=products, budget=BUDGET), ctx())


def test_a_store_page_found_is_swapped_in_with_the_shopping_price_and_time() -> None:
    web = FakeWeb(web_source(VICTUS_PAGE, title="HP Victus 15 Gaming Laptop (16GB RAM, 512GB SSD)"))
    result = _priced(web, {"HP Victus 15": _answer(_google("HP Victus 15 Laptop", "Flipkart"))}, [VICTUS])
    (offer,) = result.offers
    assert str(offer.url) == VICTUS_PAGE  # the store's own page now
    assert offer.store == "Flipkart"  # no longer "via Google Shopping"
    assert offer.price == Money(amount=60000) and offer.as_of == FETCHED  # the shopping result's price and time
    assert offer.listing_title == "HP Victus 15 Laptop"


def test_a_store_page_for_another_model_is_refused() -> None:
    web = FakeWeb(web_source("https://www.flipkart.com/hp-omen-15/p/itm999", title="HP Omen 15 Gaming Laptop"))
    result = _priced(web, {"HP Victus 15": _answer(_google("HP Victus 15 Laptop", "Flipkart"))}, [VICTUS])
    (offer,) = result.offers
    assert str(offer.url) == SHOPPING_OFFER and offer.store == "Flipkart via Google Shopping"


def test_a_search_page_on_the_store_is_not_a_product_page() -> None:
    web = FakeWeb(web_source("https://www.flipkart.com/search?q=hp+victus+15", title="HP Victus 15 - Buy online"))
    result = _priced(web, {"HP Victus 15": _answer(_google("HP Victus 15 Laptop", "Flipkart"))}, [VICTUS])
    assert str(result.offers[0].url) == SHOPPING_OFFER


def test_no_match_keeps_the_google_link_and_its_label() -> None:
    elsewhere = web_source("https://www.amazon.in/hp-victus-15/dp/B0TEST", title="HP Victus 15 Laptop")  # not Flipkart
    result = _priced(FakeWeb(elsewhere), {"HP Victus 15": _answer(_google("HP Victus 15", "Flipkart"))}, [VICTUS])
    (offer,) = result.offers
    assert str(offer.url) == SHOPPING_OFFER and offer.store == "Flipkart via Google Shopping"


def test_a_store_not_in_the_list_is_searched_too() -> None:
    """D-99: no store list decides which stores are tried. Flipkart's page is no page of Gadgets Now's."""
    web = FakeWeb(web_source(VICTUS_PAGE, title="HP Victus 15 Gaming Laptop"))
    result = _priced(web, {"HP Victus 15": _answer(_google("HP Victus 15 Laptop", "Gadgets Now"))}, [VICTUS])
    assert web.asked == [("HP Victus 15", "IN", ())]
    assert result.offers[0].store == "Gadgets Now via Google Shopping"


def test_one_search_a_product_for_the_model_alone() -> None:
    web = FakeWeb()
    answers = {
        "HP Victus 15": _answer(
            _google("HP Victus 15 Laptop", "Flipkart"),
            _google("HP Victus 15 Laptop", "Amazon.in", price="₹61,000"),
            _listing("HP Victus 15 Laptop", "₹62,000", store="Croma", link="https://www.croma.com/hp-victus-15/p/1"),
        )
    }
    _priced(web, answers, [VICTUS])
    assert web.asked == [("HP Victus 15", "IN", ())]  # one search, no site: list (D-99)


def test_at_most_three_extra_queries_a_search() -> None:
    web = FakeWeb()
    answers = {
        "ASUS Vivobook 15 X1504VA": _answer(_google(VIVOBOOK_TITLE, "Flipkart")),
        "HP Victus 15": _answer(_google("HP Victus 15 Laptop", "Flipkart")),
        "Lenovo IdeaPad Slim 3 15IAH8": _answer(_google("Lenovo IdeaPad Slim 3 15IAH8 Laptop", "Croma")),
    }
    _priced(web, answers, [VIVOBOOK, VICTUS, IDEAPAD])
    assert len(web.asked) == 3 == MAX_STORE_SEARCHES


def _real_web(cache: ResearchCache, asked: list[httpx.Request]) -> SerperSource:
    def handle(request: httpx.Request) -> httpx.Response:
        asked.append(request)
        organic = [{"title": "HP Victus 15 Gaming Laptop", "link": VICTUS_PAGE, "snippet": "Buy the HP Victus 15."}]
        return httpx.Response(200, json={"organic": organic})

    return SerperSource(httpx.Client(transport=httpx.MockTransport(handle)), "made-up-key", cache=cache,
                        limiter=IntervalLimiter(0), now=lambda: FETCHED)  # fmt: skip


def test_the_cap_reached_sends_nothing_and_keeps_the_google_link() -> None:
    asked: list[httpx.Request] = []
    web = _real_web(ResearchCache(folder=None, now=lambda: FETCHED), asked)
    spent = UsageBook({"serper": ProviderCap(per_search=0, per_day=60)}, path=None)
    result = _priced(web, {"HP Victus 15": _answer(_google("HP Victus 15 Laptop", "Flipkart"))}, [VICTUS], spent)
    assert asked == []
    assert str(result.offers[0].url) == SHOPPING_OFFER


def test_a_cached_answer_sends_nothing(tmp_path: Path) -> None:
    asked: list[httpx.Request] = []
    answers = {"HP Victus 15": _answer(_google("HP Victus 15 Laptop", "Flipkart"))}
    first = _priced(_real_web(ResearchCache(folder=tmp_path, now=lambda: FETCHED), asked), answers, [VICTUS])
    again = _priced(_real_web(ResearchCache(folder=tmp_path, now=lambda: FETCHED), asked), answers, [VICTUS])
    assert len(asked) == 1  # the second search was served from the cache
    assert str(first.offers[0].url) == str(again.offers[0].url) == VICTUS_PAGE


def test_the_store_map_loads_and_matches_store_names(tmp_path: Path) -> None:
    stores = load_stores()
    assert [s.domain for s in stores] == [
        "flipkart.com",
        "amazon.in",
        "croma.com",
        "reliancedigital.in",
        "vijaysales.com",
    ]

    def domain(name: str) -> str | None:
        store = store_for(name, stores)
        return None if store is None else store.domain

    assert domain("Amazon.in") == domain("amazon") == "amazon.in"
    assert domain("Reliance Digital") == "reliancedigital.in"
    assert domain("Vijay Sales") == "vijaysales.com"
    assert store_for("Gadgets Now", stores) is None
    broken = tmp_path / "stores.yaml"
    broken.write_text("stores: [{name: Flipkart}]", encoding="utf-8")
    assert load_stores(broken) == ()  # a bad map: no store is searched, every Google link stays


class BrokenWeb(FakeWeb):
    """Fails or raises for the queries named; answers the rest like FakeWeb."""

    def __init__(self, *found: Source, failing: str = "", raising: str = "") -> None:
        super().__init__(*found)
        self.failing, self.raising = failing, raising

    def search(self, query: str, region: str, usage: SearchUsage, sites: Sequence[str] = ()) -> SerperResult:
        answer = super().search(query, region, usage, sites)
        if query == self.raising:
            raise RuntimeError("a made-up bug")
        if query == self.failing:
            return SerperResult(failure=SourceFailure(source_type=SourceType.WEB_SEARCH, reason="timeout"))
        return answer


VIVOBOOK_PAGE = "https://www.amazon.in/ASUS-Vivobook-15-X1504VA-Laptop/dp/B0CTESTVB1"
BOTH = {
    "ASUS Vivobook 15 X1504VA": _answer(_google(VIVOBOOK_TITLE, "Amazon.in")),
    "HP Victus 15": _answer(_google("HP Victus 15 Laptop", "Flipkart")),
}
PAGES = (web_source(VICTUS_PAGE, title="HP Victus 15 Gaming Laptop"), web_source(VIVOBOOK_PAGE, title=VIVOBOOK_TITLE))


def _links(result: CommerceResult) -> dict[str, str]:
    return {offer.product_id: str(offer.url) for offer in result.offers}


@pytest.mark.parametrize("web", [BrokenWeb(*PAGES, failing="HP Victus 15"), BrokenWeb(*PAGES, raising="HP Victus 15")])
def test_a_failed_store_page_search_keeps_its_google_link_and_the_others_pages(web: BrokenWeb) -> None:
    links = _links(_priced(web, BOTH, [VIVOBOOK, VICTUS]))
    assert links == {VIVOBOOK.id: VIVOBOOK_PAGE, VICTUS.id: SHOPPING_OFFER}  # the price stays either way


def test_a_late_store_page_search_keeps_the_google_link(monkeypatch: pytest.MonkeyPatch) -> None:
    release = threading.Event()

    class SlowWeb(FakeWeb):
        def search(self, query: str, region: str, usage: SearchUsage, sites: Sequence[str] = ()) -> SerperResult:
            release.wait(5)
            return super().search(query, region, usage, sites)

    monkeypatch.setattr(stage_module, "STAGE_LIMIT_S", 1.5)  # the stage hands back at 0.5 s
    monkeypatch.setattr(stage_module, "WEB_MIN_S", 0.1)
    try:
        result = _priced(SlowWeb(*PAGES), BOTH, [VIVOBOOK, VICTUS])
    finally:
        release.set()
    assert _links(result) == {VIVOBOOK.id: SHOPPING_OFFER, VICTUS.id: SHOPPING_OFFER}


def test_no_store_page_search_when_the_stage_has_no_time_for_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(stage_module, "WEB_MIN_S", 60.0)  # more than the stage's 15 s
    web = FakeWeb(*PAGES)
    result = _priced(web, BOTH, [VIVOBOOK, VICTUS])
    assert web.asked == []
    assert set(_links(result).values()) == {SHOPPING_OFFER}


@pytest.mark.parametrize(
    ("link", "store", "is_page"),
    [
        (VICTUS_PAGE, "Flipkart", True),
        ("https://m.flipkart.com/hp-victus-15/p/itm0123456789", "Flipkart", True),
        ("https://flipkart.com/hp-victus-15/p/itm0123456789?pid=X", "Flipkart", True),
        ("https://www.flipkart.com/laptops/hp~brand/pr?sid=6bo", "Flipkart", False),  # a list of products
        ("https://stories.flipkart.com/hp-victus-15/p/itm0123456789", "Flipkart", False),  # not the store's own site
        ("https://www.flipkart.com.example/hp-victus-15/p/itm0123", "Flipkart", False),
        (VIVOBOOK_PAGE, "Amazon", True),
        ("https://www.amazon.in/gp/product/B0CTESTVB1/", "Amazon", True),
        ("https://www.amazon.in/s?k=asus+vivobook+15", "Amazon", False),  # a search
        ("https://www.amazon.in/stores/ASUS/page/1234", "Amazon", False),  # a brand page
        ("https://www.croma.com/asus-vivobook-15/p/301234", "Croma", True),
        ("https://www.croma.com/laptops/c/20", "Croma", False),
        ("https://www.reliancedigital.in/asus-vivobook-15/p/493838", "Reliance Digital", True),
        ("https://www.reliancedigital.in/search?q=vivobook", "Reliance Digital", False),
    ],
)
def test_only_a_stores_own_product_pages_count(link: str, store: str, is_page: bool) -> None:
    mapped = store_for(store, load_stores())
    assert mapped is not None
    assert is_product_page(link, mapped) is is_page


def test_a_stores_own_page_and_its_google_link_are_one_offer() -> None:
    answers = {
        "HP Victus 15": _answer(
            _listing("HP Victus 15 Laptop", "₹59,000", store="Flipkart", link=VICTUS_PAGE),
            _google("HP Victus 15 Laptop", "Flipkart", price="₹60,000"),
            _google("HP Victus 15 Laptop", "Amazon.in", price="₹61,000"),
        )
    }
    result = _priced(FakeWeb(), answers, [VICTUS])
    assert [(o.store, o.price) for o in result.offers] == [
        ("Flipkart", Money(amount=59000)),  # the cheaper one; Flipkart's Google link is the same store
        ("Amazon.in via Google Shopping", Money(amount=61000)),
    ]
