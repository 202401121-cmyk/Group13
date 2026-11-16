"""US-24: the edge cases the review found: whole links, a slow look-up, prices the card can't show, stock words, the
shared daily cap, the cache's limit, and Serper's answer for India as recorded on 15 Nov 2026."""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from app.commerce import ShoppingCommerceStage
from app.commerce import stage as commerce_stage
from app.commerce.matching import accepts
from app.contracts import Budget, CommerceInput, CommerceResult, Money, Product, StageContext
from app.research.cache import ResearchCache
from app.research.reddit import IntervalLimiter
from app.research.reddit.cache import serve_limit
from app.research.settings import ProviderCap
from app.research.shopping import Listing, SerperShopping, ShoppingResult, read_listings
from app.research.usage import SearchUsage, UsageBook

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "commerce" / "serper_shopping_in.json"
FETCHED = datetime(2026, 11, 15, 9, 30, tzinfo=UTC)
VICTUS = Product(id="prd_hp_victus_15", name="HP Victus 15", brand="HP")
SWIFT = Product(id="prd_acer_swift_go_14", name="Acer Swift Go 14", brand="Acer")
PIXEL = Product(id="prd_google_pixel_8a", name="Pixel 8a", brand="Google")


def _ctx() -> StageContext:
    return StageContext(search_id="srch_commerce09")


class Answers:
    def __init__(self, answers: dict[str, ShoppingResult], slow: dict[str, threading.Event] | None = None) -> None:
        self.answers, self.slow = answers, slow or {}

    def search(self, query: str, usage: SearchUsage) -> ShoppingResult:
        if query in self.slow:
            self.slow[query].wait(5)
        return self.answers.get(query, ShoppingResult(listings=(), fetched_at=FETCHED))


def _usage(path: Path | None = None, per_day: int = 60) -> UsageBook:
    return UsageBook({"serper": ProviderCap(per_search=6, per_day=per_day)}, path=path)


def _run(answers: Answers, products: list[Product]) -> CommerceResult:
    stage = ShoppingCommerceStage(answers, usage=_usage())
    return stage.run(CommerceInput(products=products, budget=Budget(max=Money(amount=70000))), _ctx())


def _one(title: str, price: str, *, store: str = "Made-up Store", link: str = "", delivery: str = "") -> ShoppingResult:
    listing = Listing(
        title=title, link=link or "https://store.example/item", store=store, price=price, delivery=delivery
    )
    return ShoppingResult(listings=(listing,), fetched_at=FETCHED)


def test_a_long_link_is_kept_whole_and_an_absurd_one_refused() -> None:
    long_link = "https://www.google.com/search?ibp=oshop&prds=" + "x" * 400
    found = read_listings(
        [
            {"title": "HP Victus 15", "link": long_link},
            {"title": "HP Victus 15", "link": "https://a.example/" + "y" * 2100},
        ]
    )
    assert [x.link for x in found] == [long_link]


def test_a_slow_look_up_is_no_answer_and_the_others_are_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(commerce_stage, "LOOKUP_DEADLINE_S", 0.3)
    never = threading.Event()
    answers = Answers({"HP Victus 15": _one("HP Victus 15 Gaming Laptop", "₹60,000")}, slow={"Acer Swift Go 14": never})
    try:
        result = _run(answers, [VICTUS, SWIFT])
    finally:
        never.set()
    assert [o.product_id for o in result.offers] == [VICTUS.id]
    assert SWIFT.id in result.no_offer


def test_a_product_whose_offers_have_no_readable_price_also_gets_the_store_search() -> None:
    result = _run(Answers({"HP Victus 15": _one("HP Victus 15 Gaming Laptop", "Call for price")}), [VICTUS])
    (offer,) = result.offers
    assert offer.price is None
    assert VICTUS.id in result.no_offer  # "no price found" and a store search, with the link still offered


@pytest.mark.parametrize("said", ["Not in stock", "Currently unavailable", "Sold out"])
def test_not_in_stock_is_never_read_as_in_stock(said: str) -> None:
    result = _run(Answers({"HP Victus 15": _one("HP Victus 15 Gaming Laptop", "₹60,000", delivery=said)}), [VICTUS])
    assert result.offers[0].availability == "out_of_stock"


def test_googles_own_store_is_a_store() -> None:
    listing = _one(
        "Google Pixel 8a 128GB", "₹39,999", store="Google Store", link="https://store.google.com/in/product/pixel_8a"
    )
    (offer,) = _run(Answers({"Google Pixel 8a": listing}), [PIXEL]).offers
    assert offer.store == "Google Store" and str(offer.url) == "https://store.google.com/in/product/pixel_8a"


def test_the_note_gives_the_likeliest_cause_when_the_failures_differ() -> None:
    answers = Answers(
        {
            "HP Victus 15": ShoppingResult(failure="quota_exhausted"),
            "Acer Swift Go 14": ShoppingResult(failure="timeout"),
        }
    )
    with pytest.raises(commerce_stage.PricesUnavailable) as raised:
        _run(answers, [VICTUS, SWIFT])
    assert isinstance(raised.value.__cause__, TimeoutError)


def test_shopping_and_web_search_share_one_daily_cap(tmp_path: Path) -> None:
    """Two books, as research and commerce each keep, on the one usage.json: the 60 a day is for both."""
    path = tmp_path / "usage.json"
    research, commerce = _usage(path, per_day=3), _usage(path, per_day=3)
    web = research.search()
    assert web.take("serper") and web.take("serper")
    prices = commerce.search()
    assert prices.take("serper")
    assert not prices.take("serper") and not research.search().take("serper")


def test_a_cached_answer_is_served_until_the_caches_limit_then_asked_again(tmp_path: Path) -> None:
    now = [FETCHED]
    answers = [
        httpx.Response(200, json={"shopping": [{"title": "HP Victus 15", "link": "https://store.example/v"}]})
    ] * 2
    asked: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        asked.append(request)
        return answers[len(asked) - 1]

    def client() -> SerperShopping:
        return SerperShopping(
            httpx.Client(transport=httpx.MockTransport(handle)),
            "made-up-key",
            cache=ResearchCache(folder=tmp_path, now=lambda: now[0]),
            limiter=IntervalLimiter(0),
            now=lambda: now[0],
        )

    usage = _usage().search()
    client().search("HP Victus 15", usage)
    now[0] = FETCHED + serve_limit() - timedelta(minutes=1)
    assert client().search("HP Victus 15", usage).from_cache and len(asked) == 1
    now[0] = FETCHED + serve_limit()
    assert not client().search("HP Victus 15", usage).from_cache and len(asked) == 2
    assert serve_limit() <= timedelta(hours=24)


def test_serpers_answer_for_india_as_recorded() -> None:
    """15 Nov 2026: every link Serper gave for gl=in was a Google Shopping page for one store's offer."""
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    listings = read_listings(data["shopping"])
    assert len(listings) == 6
    assert all(httpx.URL(x.link).host == "www.google.com" and "ibp=oshop" in x.link for x in listings)
    shopping = Answers({"Acer Swift Go 14": ShoppingResult(listings=listings, fetched_at=FETCHED)})
    result = _run(shopping, [SWIFT])
    assert result.offers and all(o.store.endswith(" via Google Shopping") for o in result.offers)
    assert all(accepts(SWIFT, o.listing_title) for o in result.offers)
    assert not any("Aspire" in o.listing_title for o in result.offers)  # the Aspire Go 14 is another laptop
