"""US-41 (S5-6): the fact look-up. A web search for the product and the criterion's fixed stand-in, one maker or review
page for it, one batched LLM call, and only what the page supports word for word, cited to that page. Every search,
page and answer here is a fake, and every product and page is made up: no real API is called.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic

import pytest

from app.contracts import EnrichInput, EnrichTarget, LLMTask, Product, Source, StageContext
from app.contracts.enrich import MAX_ENRICH_SEARCHES
from app.decide.rules import PRICE_KEY
from app.enrich import PriceLookupStage
from app.enrich.facts import FactAsk, FactLookup, names_product, readable, stand_in_label
from app.enrich.parsing import RawFacts, RawPageFacts
from app.extract.parsing import RawClaim
from app.extract.settings import load_extract_settings
from app.research.cache import ResearchCache
from app.research.pages import PageResult
from app.research.serper import SerperResult
from app.research.settings import ProviderCap, load_research_settings
from app.research.usage import SearchUsage, UsageBook
from tests.fakes.llm import FakeLLMClient
from tests.fakes.research import review_source, web_source
from tests.unit.enrich.test_price_lookup import FakeShopping

NOW = datetime(2026, 11, 25, 9, 0, tzinfo=UTC)
VELA = Product(id="prd_vela_pro_14", name="Vela Pro 14", brand="Vela")
TARGET = EnrichTarget(product_id=VELA.id, product_name="Vela Pro 14", criterion_key="battery_life")
QUERY = "Vela Pro 14 Battery life"
MAKER = "https://www.dell.com/en-in/vela-pro-14"
REVIEW = "https://www.91mobiles.com/vela-pro-14-review"
STORE = "https://www.amazon.in/vela-pro-14/dp/B0MADEUP"
REDDIT = "https://www.reddit.com/r/laptops/comments/abc123/vela_pro_14/"
VIDEO = "https://www.youtube.com/watch?v=madeup00001"
TEXT = "The Vela Pro 14 lasted 11 hours in our battery test. The Nova Book 14 lasted 9 hours."
GOOD = RawClaim(
    product_mention="Vela Pro 14",
    criterion_key="battery_life",
    value=11,
    unit="hours",
    quote="The Vela Pro 14 lasted 11 hours in our battery test.",
    polarity="positive",
)
ASK = FactAsk(category="laptop", region="IN")


class FakeSearch:
    """Serper web search: the links it was told to give, taking a Serper unit for each search, as the real one does."""

    def __init__(self, links: list[str], fail: bool = False) -> None:
        self.links, self.fail = links, fail
        self.asked: list[str] = []
        self._lock = threading.Lock()

    def search(self, query: str, region: str, usage: SearchUsage, sites: object = ()) -> SerperResult:
        with self._lock:
            self.asked.append(query)
        if self.fail:
            raise AssertionError("a cached search must send nothing")
        if not usage.take("serper"):
            return _out_of_room()
        return SerperResult(sources=tuple(web_source(link) for link in self.links))


def _out_of_room() -> SerperResult:
    from app.contracts import SourceFailure, SourceType

    return SerperResult(failure=SourceFailure(source_type=SourceType.WEB_SEARCH, reason="quota_exhausted", detail="x"))


class FakePages:
    """Reads a page as a review page with the given text, and records every address it was asked to read."""

    def __init__(self, texts: dict[str, str] | None = None, fail: bool = False) -> None:
        self.texts = texts or {}
        self.fail = fail
        self.read: list[str] = []

    def fetch(self, url: str, usage: SearchUsage) -> PageResult:
        if self.fail:
            raise AssertionError("a cached page must be read from the cache")
        self.read.append(url)
        page = review_source(url, title="A made-up review", text=self.texts.get(url, TEXT))
        fresh = Source.model_validate({**page.model_dump(), "fetched_at": datetime.now(UTC)})  # the cache keeps 7 days
        return PageResult(source=fresh)


def _answer(*claims: RawClaim, page: int = 1) -> RawFacts:
    return RawFacts(pages=[RawPageFacts(page=page, claims=list(claims))])


def _usage(per_day: int = 80) -> SearchUsage:
    return UsageBook({"serper": ProviderCap(per_search=MAX_ENRICH_SEARCHES, per_day=per_day)}, path=None).search(
        per_search={"serper": MAX_ENRICH_SEARCHES}
    )


def _lookup(tmp_path: Path, search: FakeSearch, pages: FakePages, llm: FakeLLMClient) -> FactLookup:
    cache = ResearchCache(folder=tmp_path / "enrich", ttl=timedelta(days=7), reddit_ttl=timedelta(days=7))
    return FactLookup(
        llm,  # type: ignore[arg-type]
        search,
        pages,
        cache,
        research=load_research_settings(),
        extract=load_extract_settings(env={}),
        now=lambda: NOW,
    )


def short_window(monkeypatch: pytest.MonkeyPatch, seconds: float, ends_in: float = 30.0) -> None:
    """A reading window `seconds` long for a look-up ending `ends_in` from now: the call is left the rest (D-101)."""
    from app.enrich import facts as facts_module

    monkeypatch.setattr(facts_module, "CALL_S", ends_in - facts_module.ANSWER_MARGIN_S - seconds)
    monkeypatch.setattr(facts_module, "MIN_READ_S", 0.0)


def _find(lookup: FactLookup, ask: FactAsk = ASK, usage: SearchUsage | None = None) -> tuple[list[str], list[Source]]:
    found = lookup.find([(TARGET, VELA)], ask, usage or _usage(), monotonic() + 30, "srch_facts01")
    return [f"{c.product_mention}|{c.criterion_key}|{c.value}" for c in found.claims], found.sources


def test_a_supported_fact_is_kept_and_cites_its_page(tmp_path: Path) -> None:
    llm = FakeLLMClient({LLMTask.ENRICH_EXTRACT: _answer(GOOD)})
    claims, sources = _find(_lookup(tmp_path, FakeSearch([REVIEW]), FakePages(), llm))
    assert claims == ["Vela Pro 14|battery_life|11.0"]
    assert [str(s.url) for s in sources] == [REVIEW]
    (call,) = llm.calls
    assert call.data_class.value == "public"  # build rule 8: public text only, never the shopper's words
    assert "Vela Pro 14" in call.user and "battery_life" in call.user


def test_the_search_asks_for_the_product_and_the_criterions_fixed_label(tmp_path: Path) -> None:
    search = FakeSearch([REVIEW])
    _find(_lookup(tmp_path, search, FakePages(), FakeLLMClient({LLMTask.ENRICH_EXTRACT: _answer(GOOD)})))
    assert search.asked == [QUERY]
    assert stand_in_label("laptop", "battery_life", load_extract_settings(env={})) == "Battery life"
    assert stand_in_label("laptop", "battery_for_long_flights_with_my_kids", load_extract_settings(env={})) in {
        None,
        "Battery life",
    }  # a generated key goes out only as its fixed stand-in's label, never in its own words


def test_a_quote_not_on_the_page_is_dropped(tmp_path: Path) -> None:
    made_up = RawClaim(**{**GOOD.model_dump(), "quote": "The Vela Pro 14 lasted 20 hours in every test."})
    llm = FakeLLMClient({LLMTask.ENRICH_EXTRACT: _answer(made_up)})
    assert _find(_lookup(tmp_path, FakeSearch([REVIEW]), FakePages(), llm)) == ([], [])


def test_a_value_for_a_product_the_page_doesnt_name_is_dropped(tmp_path: Path) -> None:
    other = RawClaim(**{**GOOD.model_dump(), "product_mention": "Orbit Air 13"})
    llm = FakeLLMClient({LLMTask.ENRICH_EXTRACT: _answer(other)})
    assert _find(_lookup(tmp_path, FakeSearch([REVIEW]), FakePages(), llm))[0] == []


def test_a_value_for_another_product_the_page_does_name_is_dropped_too(tmp_path: Path) -> None:
    nova = RawClaim(
        **{**GOOD.model_dump(), "product_mention": "Nova Book 14", "quote": "The Nova Book 14 lasted 9 hours."}
    )
    llm = FakeLLMClient({LLMTask.ENRICH_EXTRACT: _answer(nova)})
    assert _find(_lookup(tmp_path, FakeSearch([REVIEW]), FakePages(), llm))[0] == []


@pytest.mark.parametrize("variant", ["Vela Pro 14 Ultra", "Vela Pro"])
def test_a_value_for_a_variant_or_a_range_is_never_credited_to_the_product(tmp_path: Path, variant: str) -> None:
    """A longer name is another model, and a shorter one could be any of a range: neither is the Vela Pro 14."""
    quote = f"The {variant} lasted 14 hours in our battery test."
    pages = FakePages({REVIEW: f"{TEXT} {quote}"})
    other = RawClaim(**{**GOOD.model_dump(), "product_mention": variant, "value": 14, "quote": quote})
    llm = FakeLLMClient({LLMTask.ENRICH_EXTRACT: _answer(other)})
    assert _find(_lookup(tmp_path, FakeSearch([REVIEW]), pages, llm))[0] == []


def test_a_criterion_the_call_cant_carry_apart_is_never_searched_for(tmp_path: Path) -> None:
    """cpu_performance stands in as processor_speed, which another product's target asks for already: the call can
    ask for processor_speed once, so a search for cpu_performance would be spent in vain."""
    nova = Product(id="prd_nova_book_14", name="Nova Book 14", brand="Nova")
    targets = [
        (EnrichTarget(product_id=VELA.id, product_name=VELA.name, criterion_key="cpu_performance"), VELA),
        (EnrichTarget(product_id=nova.id, product_name=nova.name, criterion_key="processor_speed"), nova),
    ]
    search = FakeSearch([REVIEW])
    lookup = _lookup(tmp_path, search, FakePages(), FakeLLMClient())
    lookup.find(targets, ASK, _usage(), monotonic() + 30, "srch_facts05")
    assert search.asked == ["Nova Book 14 Processor speed"]


def test_a_page_that_doesnt_name_the_product_is_never_sent(tmp_path: Path) -> None:
    llm = FakeLLMClient({LLMTask.ENRICH_EXTRACT: _answer(GOOD)})
    pages = FakePages({REVIEW: "A made-up review of another laptop altogether."})
    assert _find(_lookup(tmp_path, FakeSearch([REVIEW]), pages, llm)) == ([], [])
    assert llm.calls == []


def test_reddit_youtube_and_store_addresses_are_never_read_and_makers_come_first(tmp_path: Path) -> None:
    pages = FakePages()
    llm = FakeLLMClient({LLMTask.ENRICH_EXTRACT: _answer(GOOD)})
    _find(_lookup(tmp_path, FakeSearch([REDDIT, VIDEO, STORE, REVIEW, MAKER]), pages, llm))
    assert pages.read == [MAKER]
    assert not any(readable(link, load_research_settings()) for link in (REDDIT, VIDEO, STORE))


def test_a_page_research_already_read_is_skipped(tmp_path: Path) -> None:
    pages = FakePages()
    llm = FakeLLMClient({LLMTask.ENRICH_EXTRACT: _answer(GOOD)})
    _find(_lookup(tmp_path, FakeSearch([MAKER, REVIEW]), pages, llm), FactAsk("laptop", "IN", frozenset({MAKER})))
    assert pages.read == [REVIEW]


def test_a_cached_search_page_and_answer_send_nothing(tmp_path: Path) -> None:
    first = _find(
        _lookup(tmp_path, FakeSearch([REVIEW]), FakePages(), FakeLLMClient({LLMTask.ENRICH_EXTRACT: _answer(GOOD)}))
    )
    silent = FakeLLMClient()  # no canned answer: a call would fail
    again = _lookup(tmp_path, FakeSearch([REVIEW], fail=True), FakePages(fail=True), silent)
    assert _find(again) == first
    assert silent.calls == []


def test_the_llm_failing_adds_nothing(tmp_path: Path) -> None:
    llm = FakeLLMClient()  # LLMUnavailable
    lookup = _lookup(tmp_path, FakeSearch([REVIEW]), FakePages(), llm)
    found = lookup.find([(TARGET, VELA)], ASK, _usage(), monotonic() + 30, "srch_facts01")
    assert (found.claims, found.sources, found.llm_calls) == ([], [], 1)


def test_no_call_is_sent_with_too_little_time_left(tmp_path: Path) -> None:
    llm = FakeLLMClient({LLMTask.ENRICH_EXTRACT: _answer(GOOD)})
    lookup = _lookup(tmp_path, FakeSearch([REVIEW]), FakePages(), llm)
    assert lookup.find([(TARGET, VELA)], ASK, _usage(), monotonic() + 1, "srch_facts01").claims == []
    assert llm.calls == []


def test_names_product_wants_the_whole_name_in_order() -> None:
    assert names_product("I tested the vela  pro 14 today", "Vela Pro 14")
    assert not names_product("the Vela Pro 140 is new", "Vela Pro 14")
    assert not names_product("Pro 14 Vela", "Vela Pro 14")


# --- in the stage: shared searches, prices first ----------------------------------------------------------------


def _stage(
    tmp_path: Path, per_day: int, llm: FakeLLMClient, search: FakeSearch
) -> tuple[PriceLookupStage, FakeShopping]:
    book = UsageBook({"serper": ProviderCap(per_search=MAX_ENRICH_SEARCHES, per_day=per_day)}, path=None)
    shopping = FakeShopping({})
    facts = _lookup(tmp_path, search, FakePages(), llm)
    return PriceLookupStage(shopping, usage=book, facts=facts, now=lambda: NOW), shopping


def _price_target(n: int) -> tuple[EnrichTarget, Product]:
    product = Product(id=f"prd_made_up_{n}", name=f"Made-up Laptop {n}", brand="Madeup")
    return EnrichTarget(product_id=product.id, product_name=product.name, criterion_key=PRICE_KEY), product


def test_serper_out_of_room_after_prices_gives_no_fact_lookups(tmp_path: Path) -> None:
    llm = FakeLLMClient({LLMTask.ENRICH_EXTRACT: _answer(GOOD)})
    search = FakeSearch([REVIEW])
    stage, shopping = _stage(tmp_path, per_day=3, llm=llm, search=search)
    prices = [_price_target(n) for n in range(3)]
    stage.use_products("srch_facts02", [p for _, p in prices] + [VELA])
    stage.use_search("srch_facts02", ASK)
    out = stage.run(EnrichInput(targets=[t for t, _ in prices] + [TARGET]), StageContext(search_id="srch_facts02"))
    assert len(shopping.asked) == 3  # every price look-up went first
    assert out.searches_used == 3 and out.llm_calls_used == 0
    assert llm.calls == []  # the fact search found no room, so no page and no call


def test_the_stage_adds_the_facts_and_their_pages_beside_the_prices(tmp_path: Path) -> None:
    llm = FakeLLMClient({LLMTask.ENRICH_EXTRACT: _answer(GOOD)})
    stage, _ = _stage(tmp_path, per_day=80, llm=llm, search=FakeSearch([REVIEW]))
    stage.use_products("srch_facts03", [VELA])
    stage.use_search("srch_facts03", ASK)
    out = stage.run(EnrichInput(targets=[TARGET]), StageContext(search_id="srch_facts03"))
    assert [c.criterion_key for c in out.new_claims] == ["battery_life"]
    assert (out.searches_used, out.llm_calls_used) == (1, 1)
    assert [str(s.url) for s in stage.sources_for("srch_facts03")] == [REVIEW]


def test_with_enrichs_six_searches_prices_go_first_and_facts_get_the_rest(tmp_path: Path) -> None:
    llm = FakeLLMClient({LLMTask.ENRICH_EXTRACT: _answer(GOOD)})
    search = FakeSearch([REVIEW])
    stage, shopping = _stage(tmp_path, per_day=80, llm=llm, search=search)
    prices = [_price_target(n) for n in range(3)]
    facts = [
        (EnrichTarget(product_id=p.id, product_name=p.name, criterion_key="battery_life"), p)
        for p in [VELA, *(_price_target(n)[1] for n in range(3, 5))]
    ]
    stage.use_products("srch_facts06", [p for _, p in prices + facts])
    stage.use_search("srch_facts06", ASK)
    targets = [t for t, _ in prices + facts]
    out = stage.run(EnrichInput(targets=targets), StageContext(search_id="srch_facts06"))
    assert (len(shopping.asked), len(search.asked)) == (3, 3)
    assert out.searches_used == MAX_ENRICH_SEARCHES


def test_a_fact_look_up_that_misses_its_time_adds_nothing() -> None:
    from concurrent.futures import Future

    from app.enrich.facts import NOTHING

    assert PriceLookupStage._facts_by(Future(), monotonic() + 0.05) is NOTHING


def test_enrichs_cache_keeps_a_page_seven_days_and_no_longer(tmp_path: Path) -> None:
    import os
    import time

    from app.research.cache import ENRICH_TTL, PAGES, enrich_cache_dir, sweep_expired_files

    research = tmp_path / ".cache" / "research"
    folder = enrich_cache_dir(research) / PAGES
    folder.mkdir(parents=True)
    for name, days in (("six.json", 6), ("eight.json", 8)):
        (folder / name).write_text("{}", encoding="utf-8")
        then = time.time() - days * 86_400
        os.utime(folder / name, (then, then))
    sweep_expired_files(research, tmp_path / "reddit")
    assert timedelta(days=7) == ENRICH_TTL
    assert sorted(p.name for p in folder.iterdir()) == ["six.json"]


def test_the_llm_failing_in_the_stage_keeps_the_search_going(tmp_path: Path) -> None:
    stage, _ = _stage(tmp_path, per_day=80, llm=FakeLLMClient(), search=FakeSearch([REVIEW]))
    stage.use_products("srch_facts04", [VELA])
    stage.use_search("srch_facts04", ASK)
    out = stage.run(EnrichInput(targets=[TARGET]), StageContext(search_id="srch_facts04"))
    assert out.new_claims == [] and out.llm_calls_used == 1


@pytest.mark.parametrize("key", ["battery_life", "display_quality"])
def test_a_fixed_criterion_has_its_own_label(key: str) -> None:
    assert stand_in_label("laptop", key, load_extract_settings(env={}))
