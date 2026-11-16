"""US-24: the real commerce stage. A fake shopping client: no real API is called."""

from __future__ import annotations

import threading
from datetime import UTC, datetime

import pytest

from app import contracts as C
from app.commerce import CommerceStage, ShoppingCommerceStage, default_commerce_stage
from app.commerce.stage import MAX_LOOKUPS, PricesUnavailable
from app.contracts import Budget, CommerceInput, CommerceResult, Money, Product, StageContext, Variant
from app.contracts import examples as ex
from app.pipeline import as_clause, build_coverage_note, plain_reason, run_stage
from app.research.settings import ProviderCap
from app.research.shopping import Listing, ShoppingResult
from app.research.usage import SearchUsage, UsageBook

FETCHED = datetime(2026, 11, 15, 9, 30, tzinfo=UTC)
CTX = StageContext(search_id="srch_commerce01")
VIVOBOOK = Product(
    id="prd_asus_vivobook_15_x1504va",
    name="ASUS Vivobook 15 X1504VA",
    brand="ASUS",
    variants=[
        Variant(id="var_vivobook_16_512", label="16 GB / 512 GB", attributes={"ram": "16GB", "storage": "512GB"})
    ],
)
VICTUS = Product(id="prd_hp_victus_15", name="HP Victus 15", brand="HP")
IDEAPAD = Product(id="prd_lenovo_ideapad_slim_3", name="IdeaPad Slim 3 15IAH8", brand="Lenovo")
FOURTH = Product(id="prd_acer_aspire_7", name="Aspire 7 A715", brand="Acer")
BUDGET = Budget(max=Money(amount=70000))


def _listing(title: str, price: str, store: str = "Made-up Store", link: str = "", delivery: str = "") -> Listing:
    slug = "-".join(title.lower().split())[:40]
    return Listing(title=title, link=link or f"https://{store.lower().replace(' ', '')}.example/{slug}", store=store,
                   price=price, delivery=delivery)  # fmt: skip


def _answer(*listings: Listing) -> ShoppingResult:
    return ShoppingResult(listings=listings, fetched_at=FETCHED)


def _failed(reason: str = "error") -> ShoppingResult:
    return ShoppingResult(failure=reason)  # type: ignore[arg-type]


class FakeShopping:
    """Answers by query; records what it was asked. A barrier shows the look-ups run at the same time."""

    def __init__(self, answers: dict[str, ShoppingResult], barrier: threading.Barrier | None = None) -> None:
        self.answers = answers
        self.barrier = barrier
        self.asked: list[str] = []
        self._lock = threading.Lock()

    def search(self, query: str, usage: SearchUsage) -> ShoppingResult:
        with self._lock:
            self.asked.append(query)
        if self.barrier is not None:
            self.barrier.wait()
        return self.answers.get(query, _answer())


def _usage() -> UsageBook:
    return UsageBook({"serper": ProviderCap(per_search=6, per_day=60)}, path=None)


def _run(shopping: FakeShopping, products: list[Product], budget: Budget | None = BUDGET) -> CommerceResult:
    stage = ShoppingCommerceStage(shopping, usage=_usage())
    return stage.run(CommerceInput(products=products, budget=budget), CTX)


VIVOBOOK_TITLE = "ASUS Vivobook 15 X1504VA Intel Core i5 (16GB RAM/512GB SSD) Laptop"


def test_the_top_3_are_looked_up_at_once_and_no_more() -> None:
    shopping = FakeShopping({}, barrier=threading.Barrier(3, timeout=5))
    result = _run(shopping, [VIVOBOOK, VICTUS, IDEAPAD, FOURTH])
    assert sorted(shopping.asked) == sorted(
        ["ASUS Vivobook 15 X1504VA", "HP Victus 15", "Lenovo IdeaPad Slim 3 15IAH8"]
    )
    assert len(shopping.asked) == MAX_LOOKUPS
    assert set(result.no_offer) == {VIVOBOOK.id, VICTUS.id, IDEAPAD.id, FOURTH.id}  # the 4th gets the store search


def test_an_accepted_listing_becomes_an_offer_with_its_price_time_and_variant() -> None:
    shopping = FakeShopping({"ASUS Vivobook 15 X1504VA": _answer(_listing(VIVOBOOK_TITLE, "₹52,990.00"))})
    (offer,) = _run(shopping, [VIVOBOOK]).offers
    assert (offer.product_id, offer.store, offer.listing_title) == (VIVOBOOK.id, "Made-up Store", VIVOBOOK_TITLE)
    assert offer.price == Money(amount=52990)
    assert offer.as_of == FETCHED  # the time the result was fetched (DR-04)
    assert offer.title_matched and offer.variant_matched and offer.variant_id == "var_vivobook_16_512"
    assert not offer.over_budget and offer.availability == "unknown"


def test_25k_is_read_as_25000() -> None:
    shopping = FakeShopping({"HP Victus 15": _answer(_listing("HP Victus 15 Gaming Laptop", "₹25k"))})
    (offer,) = _run(shopping, [VICTUS]).offers
    assert offer.price == Money(amount=25000)


def test_a_price_above_the_budget_is_flagged() -> None:
    shopping = FakeShopping({"HP Victus 15": _answer(_listing("HP Victus 15 Gaming Laptop", "₹74,990"))})
    (offer,) = _run(shopping, [VICTUS]).offers
    assert offer.over_budget  # DR-01: shown, and flagged
    (unbounded,) = _run(shopping, [VICTUS], budget=None).offers
    assert not unbounded.over_budget


def test_an_unreadable_price_is_no_price() -> None:
    shopping = FakeShopping({"HP Victus 15": _answer(_listing("HP Victus 15 Gaming Laptop", "$899.00"))})
    (offer,) = _run(shopping, [VICTUS]).offers
    assert offer.price is None and not offer.over_budget


@pytest.mark.parametrize(
    ("delivery", "title", "availability"),
    [
        ("In stock", "HP Victus 15 Laptop", "in_stock"),
        ("Free delivery", "HP Victus 15 Laptop (Out of stock)", "out_of_stock"),
    ],
)  # fmt: skip
def test_availability_is_what_the_result_says(delivery: str, title: str, availability: str) -> None:
    shopping = FakeShopping({"HP Victus 15": _answer(_listing(title, "₹60,000", delivery=delivery))})
    (offer,) = _run(shopping, [VICTUS]).offers
    assert offer.availability == availability


SHOPPING_OFFER = (
    "https://www.google.com/search?ibp=oshop&q=HP+Victus+15&prds=catalogid:1,gpcid:2,pvo:2,pvt:hg&gl=in&udm=28"
)


def test_a_google_shopping_link_for_the_offer_is_kept_and_said_so() -> None:
    """All Serper gives for India (15 Nov 2026): the store's offer on Google Shopping, never passed off as the store."""
    listing = _listing("HP Victus 15 Laptop", "₹60,000", store="Flipkart", link=SHOPPING_OFFER)
    (offer,) = _run(FakeShopping({"HP Victus 15": _answer(listing)}), [VICTUS]).offers
    assert offer.store == "Flipkart via Google Shopping"
    assert str(offer.url) == SHOPPING_OFFER


def test_a_google_redirect_is_unwrapped_to_the_stores_own_page() -> None:
    redirect = "https://www.google.com/url?url=https://www.flipkart.com/hp-victus-15/p/itm123&sa=U"
    listing = _listing("HP Victus 15 Laptop", "₹60,000", store="", link=redirect)
    (offer,) = _run(FakeShopping({"HP Victus 15": _answer(listing)}), [VICTUS]).offers
    assert str(offer.url) == "https://www.flipkart.com/hp-victus-15/p/itm123"
    assert offer.store == "www.flipkart.com"


@pytest.mark.parametrize(
    "link",
    [
        "https://www.google.com/shopping/product/123",  # not one offer
        "https://www.google.co.in/search?q=HP+Victus+15",  # a plain search
        "https://www.google.com/url?url=https://www.google.com/maps",  # a redirect to Google again
    ],
)
def test_any_other_google_link_is_refused(link: str) -> None:
    listing = _listing("HP Victus 15 Laptop", "₹60,000", link=link)
    result = _run(FakeShopping({"HP Victus 15": _answer(listing)}), [VICTUS])
    assert result.offers == [] and VICTUS.id in result.no_offer


def test_a_product_keeps_3_offers_one_a_store_cheapest_first() -> None:
    title = "HP Victus 15 Gaming Laptop"
    listings = (
        _listing(title, "₹64,000", store="Store A"),
        _listing(title, "₹61,000", store="Store B"),
        _listing(title, "₹59,000", store="Store A", link="https://storea.example/again"),
        _listing(title, "no price", store="Store C"),
        _listing(title, "₹66,000", store="Store D"),
        _listing("HP Omen 15 Gaming Laptop", "₹10,000", store="Store E"),  # another model, however cheap
    )
    result = _run(FakeShopping({"HP Victus 15": _answer(*listings)}), [VICTUS])
    assert [(o.store, o.price.amount if o.price else None) for o in result.offers] == [
        ("Store A", 59000),
        ("Store B", 61000),
        ("Store D", 66000),
    ]


def test_no_accepted_offer_gives_the_store_search() -> None:
    shopping = FakeShopping({"HP Victus 15": _answer(_listing("HP Omen 15 Gaming Laptop", "₹60,000"))})
    result = _run(shopping, [VICTUS])
    assert result.offers == []
    assert str(result.no_offer[VICTUS.id]) == "https://www.google.com/search?tbm=shop&q=HP+Victus+15"


def test_one_failed_look_up_leaves_the_others_priced() -> None:
    shopping = FakeShopping(
        {
            "ASUS Vivobook 15 X1504VA": _failed("timeout"),
            "HP Victus 15": _answer(_listing("HP Victus 15 Laptop", "₹60,000")),
        }
    )
    result = _run(shopping, [VIVOBOOK, VICTUS])
    assert [o.product_id for o in result.offers] == [VICTUS.id]
    assert VIVOBOOK.id in result.no_offer


@pytest.mark.parametrize(
    ("reason", "words"),
    [
        ("error", "the service could not be reached"),
        ("timeout", "took too long"),
        ("rate_limited", "the service was busy"),
        ("blocked", "the service refused the request"),
        ("quota_exhausted", "the service refused the request"),
    ],
)
def test_serper_down_gives_store_searches_for_all_and_a_coverage_note(reason: str, words: str) -> None:
    top3 = ("ASUS Vivobook 15 X1504VA", "HP Victus 15", "Lenovo IdeaPad Slim 3 15IAH8")
    shopping = FakeShopping({q: _failed(reason) for q in top3})
    stage = ShoppingCommerceStage(shopping, usage=_usage())
    inp = CommerceInput(products=[VIVOBOOK, VICTUS, IDEAPAD, FOURTH], budget=BUDGET)  # the 4th is never looked up
    with pytest.raises(PricesUnavailable) as raised:
        stage.run(inp, CTX)
    assert plain_reason(raised.value) == words
    fresh = StageContext(search_id="srch_commerce03")  # its own 150 s: a module-level one runs out in a long run
    out, report = run_stage(stage, inp, fresh, 15)
    assert report.status is C.StageStatus.FALLBACK and out is not None
    assert out.offers == [] and set(out.no_offer) == {VIVOBOOK.id, VICTUS.id, IDEAPAD.id, FOURTH.id}
    note = build_coverage_note([report], [], False)
    assert note == f"Commerce used a simpler fallback: {as_clause(words)}."


def test_example_offers_never_appear_in_a_live_result() -> None:
    examples = ex.commerce()
    named = {o.store for o in examples.offers} | {str(o.url) for o in examples.offers}
    shopping = FakeShopping({"HP Victus 15": _answer(_listing("HP Victus 15 Laptop", "₹60,000"))})
    result = _run(shopping, [VICTUS, VIVOBOOK])
    shown = {o.store for o in result.offers} | {str(o.url) for o in result.offers}
    assert not (named & shown)
    assert {o.product_id for o in result.offers} <= {VICTUS.id, VIVOBOOK.id}


def test_the_live_stage_is_the_default_and_is_real() -> None:
    stage = default_commerce_stage()
    assert isinstance(stage, ShoppingCommerceStage) and stage.is_placeholder is False
    assert not hasattr(stage, "baseline")  # its fallback is honest, so a fallback is never labelled a sample
    assert CommerceStage.is_placeholder is True


def test_the_stubs_fallback_gives_store_searches_and_no_offers() -> None:
    inp = CommerceInput(products=[VIVOBOOK, VICTUS], budget=BUDGET)
    out = CommerceStage().fallback(inp, CTX, RuntimeError("a made-up failure"))
    assert out.offers == [] and set(out.no_offer) == {VIVOBOOK.id, VICTUS.id}
