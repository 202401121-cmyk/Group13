"""US-41 with D-100 item 2: when a product's fact search gives no maker's page, Firecrawl's map looks on the maker's
own site by the product's name, at most once a product, and the page it finds is read like any other. Every search,
map, page and answer is a fake, and every product and page is made up: no real API is called."""

from __future__ import annotations

from pathlib import Path
from time import monotonic
from typing import Any

from app.contracts import EnrichTarget, LLMTask, Product
from app.enrich.facts import FactAsk, FactLookup, Window, _Run
from app.enrich.parsing import RawFacts, RawPageFacts
from app.extract.parsing import RawClaim
from app.research.readers import MappedLink, SearchedPage, SearchPages
from tests.fakes.llm import FakeLLMClient
from tests.unit.enrich.test_fact_lookup import FakePages, FakeSearch, _lookup, _usage

ASUS = Product(id="prd_vivobook_15_x1504va", name="Vivobook 15 X1504VA", brand="Asus")
TARGET = EnrichTarget(product_id=ASUS.id, product_name=ASUS.name, criterion_key="battery_life")
MAKER = "https://www.asus.com/in/laptops/vivobook-15-x1504va/techspec/"
REVIEW = "https://www.91mobiles.com/vivobook-15-x1504va-review"
LINE = "The Vivobook 15 X1504VA lasted 9 hours in our battery test."
TEXT = f"{LINE} It weighs about 1.7 kg."
GOOD = RawClaim(
    product_mention=ASUS.name, criterion_key="battery_life", value=9, unit="hours", quote=LINE, polarity="positive"
)


class Maps:
    """Firecrawl, for the look-up: map answers as told, counted; a reading search answers as told."""

    def __init__(self, links: list[MappedLink] | tuple[str, str], pages: tuple[SearchedPage, ...] = ()) -> None:
        self.links, self.pages = links, pages
        self.asked: list[tuple[str, str]] = []
        self.searched: list[str] = []

    def map(self, site: str, search: str, usage: Any, *, until: float | None = None) -> Any:
        self.asked.append((site, search))
        return self.links

    def search_pages(self, query: str, region: str, usage: Any, **kwargs: Any) -> SearchPages:
        self.searched.append(query)
        return SearchPages(self.pages)


class TakesReads(FakePages):
    def take_read(self, url: str, title: str, text: str, usage: Any) -> Any:
        return self.fetch(url, usage)


def _llm() -> FakeLLMClient:
    return FakeLLMClient({LLMTask.ENRICH_EXTRACT: RawFacts(pages=[RawPageFacts(page=1, claims=[GOOD])])})


def _find(lookup: FactLookup, ask: FactAsk | None = None) -> list[str]:
    found = lookup.find([(TARGET, ASUS)], ask or FactAsk("laptop", "IN"), _usage(), monotonic() + 30, "srch_map01")
    return [str(s.url) for s in found.sources]


def test_a_makers_page_the_search_missed_is_found_by_map_and_read(tmp_path: Path) -> None:
    pages = FakePages({MAKER: TEXT, REVIEW: TEXT})
    lookup = _lookup(tmp_path, FakeSearch([REVIEW]), pages, _llm())
    lookup.firecrawl = maps = Maps([MappedLink(MAKER, "Vivobook 15 X1504VA tech specs")])  # type: ignore[assignment]
    assert _find(lookup) == [MAKER]
    assert maps.asked == [("https://www.asus.com", "Vivobook 15 X1504VA")]  # the product's name only
    assert pages.read == [MAKER]


def test_a_map_naming_another_model_or_another_site_is_not_used(tmp_path: Path) -> None:
    pages = FakePages({REVIEW: TEXT})
    lookup = _lookup(tmp_path, FakeSearch([REVIEW]), pages, _llm())
    lookup.firecrawl = Maps(  # type: ignore[assignment]
        [
            MappedLink("https://www.asus.com/in/laptops/vivobook-15-x1504vap/techspec/"),  # another model
            MappedLink("https://www.91mobiles.com/vivobook-15-x1504va"),  # not the maker's site
        ]
    )
    assert _find(lookup) == [REVIEW]


def test_a_map_that_fails_or_a_product_with_no_makers_site_falls_back_to_the_search(tmp_path: Path) -> None:
    lookup = _lookup(tmp_path, FakeSearch([REVIEW]), FakePages({REVIEW: TEXT}), _llm())
    lookup.firecrawl = maps = Maps(("quota_exhausted", "a cap is reached"))  # type: ignore[assignment]
    assert _find(lookup) == [REVIEW]
    nameless = Product(id="prd_made_up_1", name="Vivobook 15 X1504VA", brand=None)
    target = EnrichTarget(product_id=nameless.id, product_name=nameless.name, criterion_key="battery_life")
    lookup.find([(target, nameless)], FactAsk("laptop", "IN"), _usage(), monotonic() + 30, "srch_map02")
    assert len(maps.asked) == 1  # no maker's site known for it: no map


def test_a_search_that_gave_a_makers_page_maps_nothing(tmp_path: Path) -> None:
    lookup = _lookup(tmp_path, FakeSearch([MAKER]), FakePages({MAKER: TEXT}), _llm())
    lookup.firecrawl = maps = Maps([MappedLink(MAKER)])  # type: ignore[assignment]
    assert _find(lookup) == [MAKER] and maps.asked == []


def test_a_kept_map_sends_nothing_the_next_time(tmp_path: Path) -> None:
    first = _lookup(tmp_path, FakeSearch([REVIEW]), FakePages({MAKER: TEXT, REVIEW: TEXT}), _llm())
    first.firecrawl = Maps([MappedLink(MAKER, "Vivobook 15 X1504VA")])  # type: ignore[assignment]
    assert _find(first) == [MAKER]
    again = _lookup(tmp_path, FakeSearch([REVIEW]), FakePages({MAKER: TEXT, REVIEW: TEXT}), _llm())
    again.firecrawl = maps = Maps([])  # type: ignore[assignment]
    assert _find(again) == [MAKER] and maps.asked == []  # kept 7 days in enrich's cache


def test_firecrawls_path_maps_the_maker_when_its_pages_hold_none(tmp_path: Path) -> None:
    pages = TakesReads({MAKER: TEXT, REVIEW: TEXT})
    lookup = _lookup(tmp_path, FakeSearch([]), pages, _llm())
    lookup.firecrawl = Maps(  # type: ignore[assignment]
        [MappedLink(MAKER, "Vivobook 15 X1504VA")], pages=(SearchedPage(REVIEW, "A review", TEXT),)
    )
    ranked = FactAsk("laptop", "IN", ranked=("prd_someone_else", "prd_another_one", ASUS.id))  # Firecrawl first
    assert _find(lookup, ranked) == [MAKER]


def test_a_product_is_mapped_once_and_never_after_the_window(tmp_path: Path) -> None:
    lookup = _lookup(tmp_path, FakeSearch([]), FakePages({MAKER: "A page about another laptop."}), _llm())
    lookup.firecrawl = maps = Maps([MappedLink(MAKER, "Vivobook 15 X1504VA")])  # type: ignore[assignment]
    run = _Run(FactAsk("laptop", "IN"), _usage(), Window(monotonic() + 30), 6)
    assert lookup._maker_by_map(ASUS, run) is None  # its page doesn't name the product
    assert lookup._maker_by_map(ASUS, run) is None and len(maps.asked) == 1  # never twice
    over = _Run(FactAsk("laptop", "IN"), _usage(), Window(monotonic() - 1), 6)
    other = Product(id="prd_vivobook_16", name="Vivobook 16", brand="Asus")
    assert lookup._maker_by_map(other, over) is None and len(maps.asked) == 1
    lookup.firecrawl = None
    assert lookup._maker_by_map(other, run) is None


def test_firecrawls_path_keeps_its_page_when_the_map_finds_no_makers_page(tmp_path: Path) -> None:
    pages = TakesReads({REVIEW: TEXT})
    lookup = _lookup(tmp_path, FakeSearch([]), pages, _llm())
    lookup.firecrawl = Maps([], pages=(SearchedPage(REVIEW, "A review", TEXT),))  # type: ignore[assignment]
    ranked = FactAsk("laptop", "IN", ranked=("prd_someone_else", "prd_another_one", ASUS.id))
    assert _find(lookup, ranked) == [REVIEW]


def test_the_reader_ladder_gets_the_reads_cut_off_as_its_deadline(tmp_path: Path) -> None:
    from tests.fakes.research import Web
    from tests.unit.research.test_page_readers import _pages

    pages = _pages(Web())
    lookup = _lookup(tmp_path, FakeSearch([]), pages, _llm())  # type: ignore[arg-type]
    ends = monotonic() + 30
    lookup.find([(TARGET, ASUS)], FactAsk("laptop", "IN"), _usage(), ends, "srch_map03")
    assert pages.ends is not None and monotonic() < pages.ends < ends  # the rendered step stops before the call's time


def test_firecrawls_path_maps_nothing_when_its_pages_hold_the_makers(tmp_path: Path) -> None:
    pages = TakesReads({MAKER: TEXT})
    lookup = _lookup(tmp_path, FakeSearch([]), pages, _llm())
    lookup.firecrawl = maps = Maps(  # type: ignore[assignment]
        [MappedLink(MAKER, "Vivobook 15 X1504VA")], pages=(SearchedPage(MAKER, "Tech specs", TEXT),)
    )
    ranked = FactAsk("laptop", "IN", ranked=("prd_someone_else", "prd_another_one", ASUS.id))  # Firecrawl first
    assert _find(lookup, ranked) == [MAKER] and maps.asked == []


def test_a_sent_map_is_one_of_enrichs_six_searches(tmp_path: Path) -> None:
    lookup = _lookup(tmp_path, FakeSearch([]), FakePages({}), _llm())
    lookup.firecrawl = maps = Maps([])  # type: ignore[assignment]
    run = _Run(FactAsk("laptop", "IN"), _usage(), Window(monotonic() + 30), 1)
    assert lookup._maker_by_map(ASUS, run) is None and run.sent == 1  # sent: counted
    other = Product(id="prd_vivobook_16", name="Vivobook 16", brand="Asus")
    assert lookup._maker_by_map(other, run) is None and len(maps.asked) == 1  # none left: never sent


def test_a_map_refused_before_it_left_gives_its_search_back(tmp_path: Path) -> None:
    lookup = _lookup(tmp_path, FakeSearch([]), FakePages({}), _llm())
    lookup.firecrawl = Maps(("quota_exhausted", "a cap is reached"))  # type: ignore[assignment]
    run = _Run(FactAsk("laptop", "IN"), _usage(), Window(monotonic() + 30), 6)
    assert lookup._maker_by_map(ASUS, run) is None and run.sent == 0
    lookup.firecrawl = Maps(("quota_exhausted", "HTTP 402"))  # type: ignore[assignment]
    other = Product(id="prd_vivobook_16", name="Vivobook 16", brand="Asus")
    assert lookup._maker_by_map(other, run) is None and run.sent == 1  # Firecrawl refused it: it left, so it counts


def test_searches_and_maps_together_stay_at_most_six(tmp_path: Path) -> None:
    from app.enrich.facts import MAX_ENRICH_SEARCHES

    products = [Product(id=f"prd_vivobook_{n}", name=f"Vivobook {n}", brand="Asus") for n in range(10, 14)]
    targets = [(EnrichTarget(product_id=p.id, product_name=p.name, criterion_key="battery_life"), p) for p in products]
    search = FakeSearch([REVIEW])  # no maker's page: every product would map too
    lookup = _lookup(tmp_path, search, FakePages({REVIEW: "A page about another laptop."}), _llm())
    lookup.firecrawl = maps = Maps([])  # type: ignore[assignment]
    lookup.find(targets, FactAsk("laptop", "IN"), _usage(), monotonic() + 30, "srch_map04")
    # Four products want a search each (two by Serper, two by Firecrawl, D-101) and a map each: eight, for six. The
    # paths run at once, so which come first varies; together they are exactly six, and at least two are maps.
    searched, mapped = len(search.asked) + len(maps.searched), len(maps.asked)
    assert MAX_ENRICH_SEARCHES == 6 and searched + mapped == 6 and searched <= 4 and mapped >= 2
