"""US-41 (S4-36, P-34): enrich looks prices up in store listings. Fake shopping clients only: no real API is called.

Every product, store and price here is made up.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime

import httpx
import pytest

from app import contracts as C
from app.contracts import (
    Budget,
    ClaimType,
    EnrichInput,
    EnrichTarget,
    HardRule,
    Money,
    Product,
    ResolvedValue,
    SourceTier,
    SourceType,
    StageContext,
    ValueState,
)
from app.contracts import examples as ex
from app.decide.rules import PRICE_KEY
from app.enrich import EnrichStage, PriceLookupStage, price_targets, wants_prices
from app.enrich.stage import prices_note
from app.enrich.targets import MAX_TARGETS
from app.pipeline import run_stage
from app.research.cache import ResearchCache
from app.research.reddit.base import IntervalLimiter
from app.research.settings import ProviderCap
from app.research.shopping import Listing, SerperShopping, ShoppingResult
from app.research.usage import SearchUsage, UsageBook

FETCHED = datetime(2026, 11, 18, 9, 30, tzinfo=UTC)
NOW = datetime(2026, 11, 18, 9, 31, tzinfo=UTC)
SEARCH_ID = "srch_enrich01"

VELA = Product(id="prd_vela_pro_14", name="Vela Pro 14", brand="Vela")
NOVA = Product(id="prd_nova_book_14", name="Nova Book 14", brand="Nova")
VELA_TITLE = "Vela Pro 14 Laptop (16GB RAM, 512GB SSD)"


def ctx() -> StageContext:
    return StageContext(search_id=SEARCH_ID)


def _rubric(budget: bool = True, rules: list[HardRule] | None = None) -> C.Rubric:
    base = ex.rubric()
    return C.Rubric.model_validate(
        {
            **base.model_dump(),
            "budget": Budget(max=Money(amount=70000)).model_dump() if budget else None,
            "hard_rules": [r.model_dump() for r in rules or []],
        }
    )


def _listing(title: str, price: str, store: str = "Made-up Store", position: int = 1) -> Listing:
    slug = "-".join(title.lower().split())[:40]
    link = f"https://{store.lower().replace(' ', '')}.example/{slug}"
    return Listing(title=title, link=link, store=store, price=price, position=position)


def _answer(*listings: Listing) -> ShoppingResult:
    return ShoppingResult(listings=listings, fetched_at=FETCHED)


class FakeShopping:
    """Answers by query and records what it was asked. It takes a Serper unit for each query, as the real one does."""

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
        if not usage.take("serper"):
            return ShoppingResult(failure="quota_exhausted")
        return self.answers.get(query, _answer())


def _usage(per_search: int = 6, per_day: int = 60) -> UsageBook:
    return UsageBook({"serper": ProviderCap(per_search=per_search, per_day=per_day)}, path=None)


def _target(product: Product) -> EnrichTarget:
    return EnrichTarget(product_id=product.id, product_name=product.name, criterion_key=PRICE_KEY)


def _run(stage: PriceLookupStage, products: list[Product]) -> C.EnrichResult:
    stage.use_products(SEARCH_ID, products)
    return stage.run(EnrichInput(targets=[_target(p) for p in products]), ctx())


def _stage(shopping: object, usage: UsageBook | None = None) -> PriceLookupStage:
    return PriceLookupStage(shopping, usage=usage or _usage(), now=lambda: NOW)  # type: ignore[arg-type]


def _value(
    product: Product, key: str, state: ValueState, value: object = None, cites: list[str] | None = None
) -> ResolvedValue:
    """A resolved value. Known ones cite a made-up claim unless given theirs; an estimate has a score and no claims."""
    if cites is None and state is ValueState.KNOWN:
        cites = [f"clm_{product.id.encode().hex()[:24]}"]
    estimated = state is ValueState.ESTIMATED
    return ResolvedValue.model_validate(
        {
            "product_id": product.id,
            "criterion_key": key,
            "state": state,
            "value": None if estimated else value,
            "score": 6.0 if estimated else None,
            "confidence": 0.8,
            "supporting_claim_ids": cites or [],
        }
    )


def _product(n: int) -> Product:
    return Product(id=f"prd_made_up_{n}", name=f"Made Up {n}", brand="Made")


# --- the targets: picked in code -------------------------------------------------------------------------------------


def test_no_budget_and_no_price_rule_means_no_targets() -> None:
    rubric = _rubric(budget=False)
    assert not wants_prices(rubric)
    assert price_targets(rubric, [VELA], [], {VELA.name: VELA.id}, {}) == []


def test_a_hard_rule_on_the_price_asks_for_targets_without_a_budget() -> None:
    rule = HardRule(attribute=PRICE_KEY, operator="<=", value=70000, source_text="under 70k")
    rubric = _rubric(budget=False, rules=[rule])
    assert wants_prices(rubric)
    assert [t.product_id for t in price_targets(rubric, [VELA], [], {VELA.name: VELA.id}, {})] == [VELA.id]


def test_targets_are_kept_products_with_no_known_price() -> None:
    estimated, text, known = _product(1), _product(2), _product(3)
    values = [
        _value(VELA, PRICE_KEY, ValueState.UNKNOWN),
        _value(estimated, PRICE_KEY, ValueState.ESTIMATED),  # decide never reads an estimate as a price
        _value(text, PRICE_KEY, ValueState.KNOWN, "EMI from ₹2,999"),  # no price, so decide can't tell
        _value(known, PRICE_KEY, ValueState.KNOWN, 64990.0),
    ]
    products = [VELA, estimated, text, known, NOVA]  # Nova has no price value at all
    aliases = {p.name: p.id for p in products}
    targets = price_targets(_rubric(), products, values, aliases, {})
    assert [t.product_id for t in targets] == [VELA.id, estimated.id, text.id, NOVA.id]
    assert all(t.criterion_key == PRICE_KEY for t in targets)


def test_a_target_is_named_as_match_knows_it() -> None:
    """The claim's product_mention is the target's name, so it must be a name verify maps to the product."""
    aliases = {"vela pro 14": VELA.id, "Vela 14 Pro": VELA.id}
    (target,) = price_targets(_rubric(), [VELA], [], aliases, {})
    assert target.product_name == "vela pro 14"  # its own name, as match wrote it
    (other,) = price_targets(_rubric(), [VELA], [], {"Vela 14 Pro": VELA.id}, {})
    assert other.product_name == "Vela 14 Pro"
    assert price_targets(_rubric(), [VELA], [], {}, {}) == []  # a name verify can't map would be thrown away


def test_targets_are_ordered_by_distinct_sources_and_at_most_6() -> None:
    products = [_product(n) for n in range(8)]
    values: list[ResolvedValue] = []
    source_of: dict[str, str] = {}
    for n, product in enumerate(products):
        cites = []
        for k in range(n % 4):  # with the one below: 1, 1, 2, 3, 1, 1, 2, 3 distinct sources
            claim = f"clm_{n:02d}{k:02d}{'a' * 20}"
            cites.append(claim)
            source_of[claim] = f"src_{k:02d}{'b' * 14}"
        cites.append(f"clm_{n:02d}99{'c' * 20}")  # the same source again counts once
        source_of[cites[-1]] = f"src_00{'b' * 14}"
        values.append(_value(product, "battery_life", ValueState.KNOWN, 8.0, cites))
    aliases = {p.name: p.id for p in products}
    targets = price_targets(_rubric(), products, values, aliases, source_of)
    assert len(targets) == MAX_TARGETS == 6
    # 3 sources first, then 2, then 1; equal counts keep the products' order. Ordering only: nothing is scored.
    assert [t.product_id for t in targets] == [products[n].id for n in (3, 7, 2, 6, 0, 1)]


# --- the look-up ------------------------------------------------------------------------------------------------------


def test_the_lowest_accepted_price_becomes_a_store_claim_with_its_source() -> None:
    shopping = FakeShopping(
        {
            "Vela Pro 14": _answer(
                _listing(VELA_TITLE, "₹64,990.00", "Shop One", position=1),
                _listing(VELA_TITLE, "₹62,490.00", "Shop Two", position=2),
                _listing("Vela Pro 14 sleeve", "", "Shop Three", position=3),  # no price: not a candidate
            )
        }
    )
    stage = _stage(shopping)
    out = _run(stage, [VELA])
    (claim,) = out.new_claims
    (source,) = stage.sources_for(SEARCH_ID)
    assert (claim.criterion_key, claim.value, claim.product_mention) == (PRICE_KEY, 62490.0, VELA.name)
    assert claim.quote == f"{VELA_TITLE}, ₹62,490.00, at Shop Two"  # the listing's own words
    assert claim.claim_type is ClaimType.EXPERIENCE and claim.polarity == "neutral"
    assert claim.retrieved_at == FETCHED and claim.extracted_at == NOW
    assert claim.source_id == source.id and str(claim.source_url) == str(source.url)
    assert (source.type, source.tier, source.via) == (SourceType.RETAILER, SourceTier.RETAILER, "api")
    assert source.text == claim.quote and source.fetched_at == FETCHED and source.site == "shoptwo.example"
    assert (out.attempted, out.searches_used, out.llm_calls_used) == ([_target(VELA)], 1, 0)


def test_a_listing_for_another_model_is_refused() -> None:
    shopping = FakeShopping({"Vela Pro 14": _answer(_listing("Vela Pro 16 Laptop (32GB RAM)", "₹59,990"))})
    stage = _stage(shopping)
    out = _run(stage, [VELA])
    assert out.new_claims == [] and stage.sources_for(SEARCH_ID) == []


def test_an_emi_or_a_range_is_no_price() -> None:
    shopping = FakeShopping(
        {
            "Vela Pro 14": _answer(
                _listing(VELA_TITLE, "₹2,999/month EMI", "Shop One"),
                _listing(VELA_TITLE, "₹60,000 - ₹70,000", "Shop Two"),
            )
        }
    )
    assert _run(_stage(shopping), [VELA]).new_claims == []


def test_at_most_6_targets_are_looked_up_at_once() -> None:
    products = [_product(n) for n in range(8)]
    shopping = FakeShopping({}, barrier=threading.Barrier(6, timeout=5))
    stage = _stage(shopping, _usage(per_search=10))
    stage.use_products(SEARCH_ID, products)
    targets = [_target(p) for p in products]
    out = stage.run(EnrichInput(targets=targets), ctx())
    assert len(shopping.asked) == 6 and out.searches_used == 6
    assert out.attempted == targets[:6]


def _real_shopping(asked: list[httpx.Request], cache: ResearchCache, reply: httpx.Response) -> SerperShopping:
    def handle(request: httpx.Request) -> httpx.Response:
        asked.append(request)
        return reply

    return SerperShopping(
        httpx.Client(transport=httpx.MockTransport(handle)),
        "made-up-test-key",
        cache=cache,
        limiter=IntervalLimiter(0),
        now=lambda: FETCHED,
    )


def _serper_reply(title: str, price: str) -> httpx.Response:
    item = {"title": title, "source": "Shop One", "link": "https://shopone.example/vela-pro-14", "price": price}
    return httpx.Response(200, json={"shopping": [item]})


def test_the_cap_reached_sends_nothing() -> None:
    asked: list[httpx.Request] = []
    shopping = _real_shopping(asked, ResearchCache(folder=None), _serper_reply(VELA_TITLE, "₹62,490"))
    out = _run(_stage(shopping, _usage(per_day=0)), [VELA])
    assert asked == [] and out.new_claims == [] and out.searches_used == 0


def test_a_cached_answer_sends_nothing() -> None:
    asked: list[httpx.Request] = []
    cache = ResearchCache(folder=None)
    shopping = _real_shopping(asked, cache, _serper_reply(VELA_TITLE, "₹62,490"))
    first = _run(_stage(shopping), [VELA])
    assert len(asked) == 1 and first.searches_used == 1
    again = _run(_stage(shopping), [VELA])
    assert len(asked) == 1 and again.searches_used == 0  # the same query again: the cached answer, no request
    assert [c.value for c in again.new_claims] == [62490.0]
    assert again.new_claims[0].retrieved_at == FETCHED  # a cached price keeps its true age


def test_serper_down_gives_no_claims_and_no_llm_call() -> None:
    asked: list[httpx.Request] = []
    shopping = _real_shopping(asked, ResearchCache(folder=None), httpx.Response(503))
    stage = _stage(shopping)
    out = _run(stage, [VELA])
    assert asked  # it was tried
    assert out.new_claims == [] and out.llm_calls_used == 0
    assert stage.sources_for(SEARCH_ID) == []


def test_a_raising_client_falls_back_to_nothing() -> None:
    class Broken:
        def search(self, query: str, usage: SearchUsage) -> ShoppingResult:
            raise RuntimeError("made up")

    stage = _stage(Broken())
    stage.use_products(SEARCH_ID, [VELA])
    out, report = run_stage(stage, EnrichInput(targets=[_target(VELA)]), ctx(), 5)
    assert out == C.EnrichResult() and report.status is C.StageStatus.FALLBACK


def test_a_target_without_its_product_is_not_looked_up() -> None:
    shopping = FakeShopping({})
    out = _stage(shopping).run(EnrichInput(targets=[_target(VELA)]), ctx())  # use_products was never called
    assert shopping.asked == [] and out == C.EnrichResult()


def test_the_note_says_how_many_prices_came_from_store_listings() -> None:
    assert prices_note(2) == "2 prices came from store listings."
    assert prices_note(1) == "1 price came from a store listing."


def test_each_target_gets_its_own_claim_and_source() -> None:
    shopping = FakeShopping(
        {
            "Vela Pro 14": _answer(_listing(VELA_TITLE, "₹62,490")),
            "Nova Book 14": _answer(_listing("Nova Book 14 (16GB/512GB)", "₹64,990")),
        }
    )
    stage = _stage(shopping)
    out = _run(stage, [VELA, NOVA])
    assert sorted(c.value for c in out.new_claims) == [62490.0, 64990.0]
    assert {c.source_id for c in out.new_claims} == {s.id for s in stage.sources_for(SEARCH_ID)}


def test_the_sources_id_is_never_a_page_researchs() -> None:
    """Research names a page by its address alone. A listing's words are in its id, so a claim never cites a page
    whose text doesn't hold its quote (build rule 4)."""
    import hashlib

    listing = _listing(VELA_TITLE, "₹62,490")
    _run(stage := _stage(FakeShopping({"Vela Pro 14": _answer(listing)})), [VELA])
    (source,) = stage.sources_for(SEARCH_ID)
    assert source.id != f"src_{hashlib.sha256(listing.link.encode()).hexdigest()[:16]}"


def test_a_google_shopping_listing_is_named_by_its_store() -> None:
    """For India Serper gives Google Shopping links (S4-14): the drawer names the source by the store, never Google."""
    link = "https://www.google.com/search?ibp=oshop&q=vela&prds=pid:123"
    listing = Listing(title=VELA_TITLE, link=link, store="Made-up Store", price="₹62,490", position=1)
    stage = _stage(FakeShopping({"Vela Pro 14": _answer(listing)}))
    (claim,) = _run(stage, [VELA]).new_claims
    (source,) = stage.sources_for(SEARCH_ID)
    assert source.site == "Made-up Store via Google Shopping"
    assert str(source.url) == link and claim.quote.endswith("at Made-up Store via Google Shopping")


def test_a_slow_look_up_is_late_and_the_others_are_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.enrich import stage as stage_module

    monkeypatch.setattr(stage_module, "LOOKUP_DEADLINE_S", 3.0)
    release = threading.Event()

    class Slow(FakeShopping):
        def search(self, query: str, usage: SearchUsage) -> ShoppingResult:
            if query == "Nova Book 14":
                release.wait(10)
            return super().search(query, usage)

    stage = _stage(Slow({"Vela Pro 14": _answer(_listing(VELA_TITLE, "₹62,490"))}))
    try:
        out = _run(stage, [VELA, NOVA])
    finally:
        release.set()
    assert [c.value for c in out.new_claims] == [62490.0]
    assert out.attempted == [_target(VELA), _target(NOVA)]


def test_too_little_time_left_sends_nothing() -> None:
    """The look-ups keep time for verify and decide: a short search ranks what it knows instead (build rule 9)."""
    shopping = FakeShopping({"Vela Pro 14": _answer(_listing(VELA_TITLE, "₹62,490"))})
    stage = _stage(shopping)
    stage.use_products(SEARCH_ID, [VELA])
    short = StageContext(search_id=SEARCH_ID, time_limit_s=7.0)
    assert stage.run(EnrichInput(targets=[_target(VELA)]), short) == C.EnrichResult()
    assert shopping.asked == []


def test_the_real_client_is_built_as_commerces(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no client given, the stage builds Serper shopping with the shared limiter and the research cache. With no
    key nothing is sent: the look-up reads as blocked."""
    from app.enrich import stage as stage_module

    monkeypatch.setenv("SERPER_API_KEY", "")
    built: list[httpx.Client] = []

    def client() -> httpx.Client:
        made = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(599)))
        built.append(made)
        return made

    monkeypatch.setattr(stage_module, "build_research_client", client)
    stage = PriceLookupStage(usage=_usage())
    out = _run(stage, [VELA])
    assert out.new_claims == [] and out.searches_used == 0 and len(built) == 1


@pytest.mark.parametrize("status", [401, 403])
def test_a_key_serper_refused_sent_its_request_and_is_counted(status: int) -> None:
    """A refused key went out (D-78): it counts, unlike a missing key, which sends nothing."""
    refused = ShoppingResult(failure="blocked", detail=f"shopping: HTTP {status} (refused)")
    out = _run(_stage(FakeShopping({"Vela Pro 14": refused})), [VELA])
    assert out.searches_used == 1 and out.new_claims == []


def test_the_stub_stays_as_the_fallback() -> None:
    stage = PriceLookupStage(FakeShopping({}))
    inp = EnrichInput(targets=[_target(VELA)])
    assert stage.fallback(inp, ctx(), RuntimeError("made up")) == EnrichStage().fallback(inp, ctx(), RuntimeError())
    assert stage.is_placeholder is False


def test_enrich_has_its_own_6_whatever_researchs_per_search() -> None:
    """D-78: research's per-search room can't use up enrich's. Here research's is 2 and enrich still sends 6."""
    products = [_product(n) for n in range(6)]
    shopping = FakeShopping({})
    out = _run(_stage(shopping, _usage(per_search=2)), products)
    assert len(shopping.asked) == 6 and out.searches_used == 6


def test_a_look_up_whose_time_is_up_takes_no_unit() -> None:
    from time import monotonic

    from app.enrich.stage import look_up

    shopping = FakeShopping({"Vela Pro 14": _answer(_listing(VELA_TITLE, "₹62,490"))})
    usage = _usage().search()
    late = look_up(shopping, "Vela Pro 14", usage, monotonic() - 1)
    assert late.failure == "timeout" and shopping.asked == [] and usage.used["serper"] == 0
    assert look_up(shopping, "Vela Pro 14", usage, monotonic() + 5).listings  # in time it asks
