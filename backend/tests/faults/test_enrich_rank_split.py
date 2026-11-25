"""D-101 (Om, 25 Nov 2026): the fact look-ups split by rank, in parallel. The top two products search Serper and their
links are read by the usual readers; products 3 to 5 search Firecrawl, which reads its results in the same call, so no
read step follows. Serper is used only while today's count leaves 25 for research and commerce. A path that fails, has
no room, or is slow starts the other one, inside the window. The one call starts at a cut-off that leaves it the
chosen model's measured 90th percentile; pages that arrive later are not read, but are kept for the next search.

Every search, page and answer is a fake, and every product and page is made up: no real API is called.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Sequence
from concurrent.futures import wait
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic
from types import SimpleNamespace
from typing import Any

import pytest

from app.contracts import (
    DataClass,
    Difficulty,
    EnrichTarget,
    LLMTask,
    Product,
    Source,
    SourceFailure,
    SourceType,
)
from app.enrich import facts as facts_module
from app.enrich.facts import FactAsk, FactLookup, KeepsReserve, UntilWindowEnds, Window
from app.enrich.parsing import RawFacts, RawPageFacts
from app.enrich.passages import GAP
from app.extract.parsing import RawClaim
from app.extract.settings import load_extract_settings
from app.research.cache import ResearchCache
from app.research.pages import PageResult
from app.research.readers import SearchedPage, SearchPages, search_credits
from app.research.serper import SerperResult
from app.research.settings import ProviderCap, load_research_settings
from app.research.usage import SearchUsage, UsageBook
from tests.fakes.research import review_source, web_source

NOW = datetime(2026, 11, 25, 9, 0, tzinfo=UTC)
NAMES = ["Vela Pro 14", "Nova Book 14", "Orbit Air 13", "Zen Slate 15", "Kite Pro 16"]
PRODUCTS = [Product(id=f"prd_{n.lower().replace(' ', '_')}", name=n, brand=n.split()[0]) for n in NAMES]
TARGETS = [(EnrichTarget(product_id=p.id, product_name=p.name, criterion_key="battery_life"), p) for p in PRODUCTS]
RANKED = FactAsk(category="laptop", region="IN", ranked=tuple(p.id for p in PRODUCTS))
REDDIT = "https://www.reddit.com/r/laptops/comments/abc123/made_up/"


def _query(product: Product) -> str:
    return f"{product.name} Battery life"


def _url(product: Product, site: str = "91mobiles.com") -> str:
    return f"https://www.{site}/{product.id.removeprefix('prd_').replace('_', '-')}-review"


def _sentence(name: str) -> str:
    return f"The {name} lasted 11 hours in our battery test."


def _page_text(product: Product) -> str:
    return f"{_sentence(product.name)} It weighs about 1.4 kg."


# --- fakes ------------------------------------------------------------------------------------------------------------


class Serper:
    """Serper's web search: each product's own review link, taking a Serper unit (with any reserve) as the real one."""

    def __init__(self, fail: set[str] | None = None, barrier: threading.Barrier | None = None) -> None:
        self.fail = fail or set()
        self.barrier = barrier
        self.asked: list[str] = []
        self._lock = threading.Lock()

    def search(self, query: str, region: str, usage: SearchUsage, sites: Sequence[str] = ()) -> SerperResult:
        with self._lock:
            self.asked.append(query)
        if self.barrier is not None:
            self.barrier.wait()
        if not usage.take("serper"):
            return SerperResult(failure=_failure("quota_exhausted", "web search: a Serper cap is reached"))
        if query in self.fail:
            return SerperResult(failure=_failure("error", "web search: HTTP 500"))
        product = next(p for p in PRODUCTS if _query(p) == query)
        return SerperResult(sources=(web_source(_url(product)),))


class Firecrawl:
    """Firecrawl's search that reads its results: each product's page, with its text, its credits taken first."""

    def __init__(
        self,
        fail: dict[str, tuple[str, str]] | None = None,
        hold: dict[str, threading.Event] | None = None,
        barrier: threading.Barrier | None = None,
        extra: Sequence[SearchedPage] = (),
    ) -> None:
        self.fail = fail or {}
        self.hold = hold or {}
        self.barrier = barrier
        self.extra = list(extra)
        self.asked: list[str] = []
        self.finished: list[str] = []
        self.left_out: list[tuple[str, ...]] = []
        self._lock = threading.Lock()

    def search_pages(
        self,
        query: str,
        region: str,
        usage: SearchUsage,
        *,
        results: int = 3,
        until: float,
        exclude: Sequence[str] = (),
    ) -> SearchPages:
        with self._lock:
            self.asked.append(query)
            self.left_out.append(tuple(exclude))
        if self.barrier is not None:
            self.barrier.wait()
        if query in self.fail:
            return SearchPages(failure=_failure(*self.fail[query]))
        credits = search_credits(results, read=True)
        if usage.left("firecrawl") < credits:  # as the real client: room for every credit first, then each counted
            return SearchPages(failure=_failure("quota_exhausted", "Firecrawl: search a cap is reached"))
        assert all(usage.take("firecrawl") for _ in range(credits))
        if query in self.hold:
            self.hold[query].wait(10)
        product = next(p for p in PRODUCTS if _query(p) == query)
        page = SearchedPage(url=_url(product, "notebookcheck.net"), title="A made-up review", text=_page_text(product))
        with self._lock:
            self.finished.append(query)
        return SearchPages((*self.extra, page))


class Pages:
    """Research's page readers: fetch reads a link (the Serper path); take_read takes a page a search brought."""

    def __init__(self, refuse: set[str] | None = None) -> None:
        self.refuse = refuse or set()  # pages robots.txt doesn't allow
        self.read: list[str] = []
        self.taken: list[str] = []

    def fetch(self, url: str, usage: SearchUsage) -> PageResult:
        self.read.append(url)
        product = next(p for p in PRODUCTS if url == _url(p))
        return PageResult(source=_fresh(review_source(url, text=_page_text(product))))

    def take_read(self, url: str, title: str, text: str, usage: SearchUsage) -> PageResult:
        self.taken.append(url)
        if url in self.refuse:
            return PageResult(reason="blocked")
        return PageResult(source=_fresh(review_source(url, title=title, text=text)))


class ReadsEachPage:
    """The one call: for each page sent, the page's own sentence about its product. Records when it was called."""

    def __init__(self) -> None:
        self.calls: list[tuple[float, LLMTask, str]] = []

    def complete(self, task: LLMTask, system: str, user: str, schema: type[Any], **kwargs: Any) -> Any:
        self.calls.append((monotonic(), task, user))
        found = re.findall(r"^Page \d+, about (.+), for ([a-z_, ]*):$", user, flags=re.MULTILINE)
        claims = [
            RawPageFacts(
                page=number,
                claims=[
                    RawClaim(
                        product_mention=name,
                        criterion_key=keys.split(", ")[0],
                        value=11,
                        unit="hours",
                        quote=_sentence(name),
                        polarity="positive",
                    )
                ],
            )
            for number, (name, keys) in enumerate(found, start=1)
        ]
        return RawFacts(pages=claims)

    def pages_sent(self) -> list[str]:
        (_, _, user) = self.calls[-1]
        return re.findall(r"^Page \d+, about (.+), for", user, flags=re.MULTILINE)


class Timer:
    """The LLM client's measured answer times: the time to leave the call, or None when no model has room."""

    def __init__(self, seconds: float | None) -> None:
        self.seconds = seconds
        self.asked: list[tuple[Any, ...]] = []

    def answer_time_s(self, *args: Any) -> float | None:
        self.asked.append(args)
        return self.seconds


def _failure(reason: Any, detail: str) -> SourceFailure:
    return SourceFailure(source_type=SourceType.WEB_SEARCH, reason=reason, detail=detail)


def _fresh(page: Source) -> Source:
    return Source.model_validate({**page.model_dump(), "fetched_at": datetime.now(UTC)})  # the cache keeps 7 days


def _book(serper_today: int = 0, firecrawl_per_day: int = 300) -> UsageBook:
    caps = {
        "serper": ProviderCap(per_search=6, per_day=80),
        "firecrawl": ProviderCap(per_search=30, per_day=firecrawl_per_day),
        "jina": ProviderCap(per_search=10, per_day=200),
    }
    book = UsageBook(caps, path=None)
    earlier = book.search(per_search={"serper": 80})
    for _ in range(serper_today):
        assert earlier.take("serper")  # today's searches before this one: research's and commerce's
    return book


def _lookup(
    tmp_path: Path,
    serper: Serper,
    firecrawl: Firecrawl | None,
    pages: Pages,
    llm: ReadsEachPage,
    timer: Timer | None = None,
) -> FactLookup:
    cache = ResearchCache(folder=tmp_path / "enrich", ttl=timedelta(days=7), reddit_ttl=timedelta(days=7))
    return FactLookup(
        llm,  # type: ignore[arg-type]
        serper,
        pages,
        cache,
        research=load_research_settings(),
        extract=load_extract_settings(env={}),
        now=lambda: NOW,
        firecrawl=firecrawl,
        timer=timer,
    )


def _find(
    lookup: FactLookup,
    targets: Sequence[tuple[EnrichTarget, Product]] = TARGETS,
    book: UsageBook | None = None,
    *,
    ends_in: float = 30.0,
    searches: int = 6,
) -> Any:
    usage = (book or _book()).search(per_search={"serper": 6})
    found = lookup.find(list(targets), RANKED, usage, monotonic() + ends_in, "srch_d101", searches=searches)
    return SimpleNamespace(**vars(found), usage=usage)  # with the search's usage, for its counts


def _named(found: Any) -> list[str]:
    return sorted(c.product_mention for c in found.claims)


def _window(monkeypatch: pytest.MonkeyPatch, seconds: float, ends_in: float = 30.0) -> None:
    """A reading window `seconds` long for a look-up ending `ends_in` from now: the call is left the rest."""
    monkeypatch.setattr(facts_module, "CALL_S", ends_in - facts_module.ANSWER_MARGIN_S - seconds)
    monkeypatch.setattr(facts_module, "MIN_READ_S", 0.0)


# --- the split by rank ----------------------------------------------------------------------------------------------


def test_the_top_two_search_serper_and_the_rest_firecrawl_all_at_once(tmp_path: Path) -> None:
    barrier = threading.Barrier(5, timeout=5)  # every search must be going at once, or none passes
    serper, firecrawl, pages, llm = Serper(barrier=barrier), Firecrawl(barrier=barrier), Pages(), ReadsEachPage()
    found = _find(_lookup(tmp_path, serper, firecrawl, pages, llm), targets=list(reversed(TARGETS)))
    assert sorted(serper.asked) == sorted(_query(p) for p in PRODUCTS[:2])  # by rank, not by the targets' order
    assert sorted(firecrawl.asked) == sorted(_query(p) for p in PRODUCTS[2:])
    assert sorted(pages.read) == sorted(_url(p) for p in PRODUCTS[:2])  # only Serper's links are read
    assert sorted(pages.taken) == sorted(_url(p, "notebookcheck.net") for p in PRODUCTS[2:])  # no read follows
    assert _named(found) == sorted(NAMES)
    assert llm.pages_sent() == NAMES  # one call, the pages in rank order
    assert found.searches == 5 and found.usage.used["serper"] == 2
    assert found.usage.used["firecrawl"] == 3 * search_credits(3, read=True)  # 5 credits each, counted before sending
    assert set(firecrawl.left_out) == {facts_module.NEVER_SEARCHED}  # Firecrawl never reads Reddit or YouTube (D-53)
    assert {"reddit.com", "youtube.com", "www.reddit.com"} <= set(facts_module.NEVER_SEARCHED)


def test_without_firecrawl_every_product_searches_serper(tmp_path: Path) -> None:
    serper, pages = Serper(), Pages()
    found = _find(_lookup(tmp_path, serper, None, pages, ReadsEachPage()))
    assert sorted(serper.asked) == sorted(_query(p) for p in PRODUCTS) and _named(found) == sorted(NAMES)


def test_a_product_the_first_ranking_doesnt_place_comes_after_those_it_does(tmp_path: Path) -> None:
    serper, firecrawl = Serper(), Firecrawl()
    lookup = _lookup(tmp_path, serper, firecrawl, Pages(), ReadsEachPage())
    usage = _book().search(per_search={"serper": 6})
    ask = FactAsk(category="laptop", region="IN", ranked=(PRODUCTS[2].id,))
    lookup.find(TARGETS[:3], ask, usage, monotonic() + 30, "srch_d101b")
    assert sorted(serper.asked) == sorted([_query(PRODUCTS[2]), _query(PRODUCTS[0])])  # placed, then the first target
    assert firecrawl.asked == [_query(PRODUCTS[1])]


# --- the Serper reserve -----------------------------------------------------------------------------------------------


def test_with_serper_down_to_its_reserve_every_product_searches_firecrawl(tmp_path: Path) -> None:
    serper, firecrawl = Serper(), Firecrawl()
    found = _find(_lookup(tmp_path, serper, firecrawl, Pages(), ReadsEachPage()), book=_book(serper_today=80 - 25))
    assert serper.asked == [] and sorted(firecrawl.asked) == sorted(_query(p) for p in PRODUCTS)
    assert _named(found) == sorted(NAMES)


def test_serper_goes_only_while_one_more_query_still_leaves_the_reserve(tmp_path: Path) -> None:
    """26 left today: the first top product's query leaves 25 and goes; the second's would cut into the 25, so it is
    refused before it leaves (nothing counted), and that product goes to Firecrawl instead."""
    book = _book(serper_today=80 - 26)
    serper, firecrawl = Serper(), Firecrawl()
    found = _find(_lookup(tmp_path, serper, firecrawl, Pages(), ReadsEachPage()), targets=TARGETS[:2], book=book)
    assert book.today()["serper"] == 80 - 25  # one query sent, and the reserve untouched
    assert len(serper.asked) == 2 and len(firecrawl.asked) == 1  # one refused at our end, then Firecrawl
    assert _named(found) == sorted(NAMES[:2]) and found.searches == 2


def test_the_reserve_holds_for_serper_alone() -> None:
    usage = _book(serper_today=80 - 26).search(per_search={"serper": 6, "jina": 10})
    keeping = KeepsReserve(usage)
    assert keeping.left("serper") == 1 and keeping.take("serper") and not keeping.take("serper")
    assert keeping.left("jina") == 10 and keeping.take("jina")  # another provider keeps its own room
    assert usage.used["serper"] == 1 and usage.left("serper") == 5  # the search's own room, without the reserve


# --- each path fails over to the other --------------------------------------------------------------------------------


def test_a_serper_search_that_fails_goes_to_firecrawl(tmp_path: Path) -> None:
    serper, firecrawl = Serper(fail={_query(PRODUCTS[0])}), Firecrawl()
    found = _find(_lookup(tmp_path, serper, firecrawl, Pages(), ReadsEachPage()), targets=TARGETS[:1])
    assert serper.asked == [_query(PRODUCTS[0])] and firecrawl.asked == [_query(PRODUCTS[0])]
    assert _named(found) == [NAMES[0]] and found.searches == 2  # the failed one was sent: it counts


@pytest.mark.parametrize(
    ("failure", "counted"),
    [
        (("error", "Firecrawl: search HTTP 500"), 2),  # it left the machine, then failed
        (("quota_exhausted", "Firecrawl: search HTTP 402"), 2),  # Firecrawl's own refusal: sent
        (("rate_limited", "Firecrawl: search paced"), 1),  # our gate held it: never sent
        (("blocked", "Firecrawl: search FIRECRAWL_API_KEY is not set"), 1),
    ],
)
def test_a_firecrawl_search_that_fails_goes_to_serper(tmp_path: Path, failure: tuple[str, str], counted: int) -> None:
    query = _query(PRODUCTS[2])
    serper, firecrawl = Serper(), Firecrawl(fail={query: failure})
    found = _find(_lookup(tmp_path, serper, firecrawl, Pages(), ReadsEachPage()), targets=TARGETS[2:3])
    assert firecrawl.asked == [query] and serper.asked == [query]
    assert _named(found) == [NAMES[2]] and found.searches == counted


def test_a_firecrawl_search_with_no_room_sends_nothing_and_goes_to_serper(tmp_path: Path) -> None:
    serper, firecrawl = Serper(), Firecrawl()
    lookup = _lookup(tmp_path, serper, firecrawl, Pages(), ReadsEachPage())
    found = _find(lookup, targets=TARGETS[2:3], book=_book(firecrawl_per_day=4))  # one search needs 5 credits
    assert found.usage.used["firecrawl"] == 0 and serper.asked == [_query(PRODUCTS[2])]
    assert _named(found) == [NAMES[2]] and found.searches == 1


def test_a_slow_path_starts_the_other_past_its_share_and_the_first_page_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(facts_module, "PATH_SHARE", 0.01)  # about 0.24 s of a 24 s window
    query = _query(PRODUCTS[2])
    release = threading.Event()
    serper, firecrawl = Serper(), Firecrawl(hold={query: release})
    lookup = _lookup(tmp_path, serper, firecrawl, Pages(), ReadsEachPage())
    started = monotonic()
    found = _find(lookup, targets=TARGETS[2:3])
    assert monotonic() - started < 5  # it didn't wait for Firecrawl
    release.set()
    for job in list(lookup.in_flight):
        job.result(timeout=5)
    assert serper.asked == [query] and _named(found) == [NAMES[2]]
    assert firecrawl.finished == [query]  # it ended on its own after the window, and its pages were kept
    assert lookup._kept_page(_url(PRODUCTS[2], "notebookcheck.net")) is not None


def test_both_paths_failing_find_nothing_and_count_what_was_sent(tmp_path: Path) -> None:
    query = _query(PRODUCTS[0])
    serper, firecrawl = Serper(fail={query}), Firecrawl(fail={query: ("error", "Firecrawl: search HTTP 500")})
    llm = ReadsEachPage()
    found = _find(_lookup(tmp_path, serper, firecrawl, Pages(), llm), targets=TARGETS[:1])
    assert (found.claims, found.searches, llm.calls) == ([], 2, [])


def test_enrichs_searches_are_never_passed_with_failovers(tmp_path: Path) -> None:
    serper = Serper(fail={_query(p) for p in PRODUCTS[:3]})
    firecrawl = Firecrawl(fail={_query(p): ("error", "Firecrawl: search HTTP 500") for p in PRODUCTS[:3]})
    found = _find(_lookup(tmp_path, serper, firecrawl, Pages(), ReadsEachPage()), targets=TARGETS[:3], searches=4)
    assert len(serper.asked) + len(firecrawl.asked) == 4 and found.searches == 4  # 3 first paths, one failover


# --- Firecrawl's pages come with its search ---------------------------------------------------------------------------


def test_firecrawls_pages_are_held_to_the_page_rules_and_never_read_again(tmp_path: Path) -> None:
    product = PRODUCTS[2]
    robots_says_no = SearchedPage(url=_url(product, "rtings.com"), title="t", text=_page_text(product))
    to_reddit = SearchedPage(url=REDDIT, title="t", text=_page_text(product))
    firecrawl, pages = Firecrawl(extra=[robots_says_no, to_reddit]), Pages(refuse={robots_says_no.url})
    found = _find(_lookup(tmp_path, Serper(), firecrawl, pages, ReadsEachPage()), targets=TARGETS[2:3])
    assert pages.read == []  # no read step: the text came with the search
    assert pages.taken == [robots_says_no.url, _url(product, "notebookcheck.net")]  # Reddit is never even taken
    assert [str(s.url) for s in found.sources] == [_url(product, "notebookcheck.net")]


def test_a_kept_firecrawl_search_sends_nothing_the_next_time(tmp_path: Path) -> None:
    firecrawl = Firecrawl()
    _find(_lookup(tmp_path, Serper(), firecrawl, Pages(), ReadsEachPage()), targets=TARGETS[2:3])
    again = Firecrawl()
    found = _find(_lookup(tmp_path, Serper(), again, Pages(), ReadsEachPage()), targets=TARGETS[2:3])
    assert again.asked == [] and found.searches == 0 and _named(found) == [NAMES[2]]


def test_a_kept_firecrawl_search_that_cant_be_read_is_searched_again(tmp_path: Path) -> None:
    lookup = _lookup(tmp_path, Serper(), Firecrawl(), Pages(), ReadsEachPage())
    lookup.cache.put_text(("fact_pages", _query(PRODUCTS[2]), "IN"), "not json")
    assert _named(_find(lookup, targets=TARGETS[2:3])) == [NAMES[2]]
    assert lookup.firecrawl.asked == [_query(PRODUCTS[2])]  # type: ignore[union-attr]


def test_a_firecrawl_path_that_raises_finds_nothing_and_the_rest_goes_on(tmp_path: Path) -> None:
    class Broken(Firecrawl):
        def search_pages(self, *args: Any, **kwargs: Any) -> SearchPages:
            raise RuntimeError("made-up bug")

    serper = Serper()
    found = _find(_lookup(tmp_path, serper, Broken(), Pages(), ReadsEachPage()), targets=TARGETS[2:3])
    assert serper.asked == [_query(PRODUCTS[2])] and _named(found) == [NAMES[2]]  # the other path still went


# --- the window and the cut-off ---------------------------------------------------------------------------------------


def test_the_window_ends_with_the_pages_that_came_in_time_and_late_ones_are_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _window(monkeypatch, 0.5)
    late = _query(PRODUCTS[4])
    release = threading.Event()
    firecrawl, llm = Firecrawl(hold={late: release}), ReadsEachPage()
    lookup = _lookup(tmp_path, Serper(), firecrawl, Pages(), llm)
    monkeypatch.setattr(facts_module, "PATH_SHARE", 2.0)  # no failover inside the window: this checks the window alone
    found = _find(lookup)
    assert llm.pages_sent() == NAMES[:4] and _named(found) == sorted(NAMES[:4])  # read in time, and called
    release.set()
    wait([job for job in lookup.in_flight if not job.cancelled()], timeout=5)
    assert lookup._kept_page(_url(PRODUCTS[4], "notebookcheck.net")) is not None  # late, kept for the next search


def test_the_call_starts_at_the_cut_off_that_leaves_it_its_measured_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(facts_module, "MIN_READ_S", 0.1)
    monkeypatch.setattr(facts_module, "PATH_SHARE", 0.99)
    late = _query(PRODUCTS[2])
    release = threading.Event()
    llm, timer = ReadsEachPage(), Timer(2.5)
    lookup = _lookup(tmp_path, Serper(), Firecrawl(hold={late: release}), Pages(), llm, timer)
    started = monotonic()
    found = _find(lookup, targets=TARGETS[:3], ends_in=4.0)  # the cut-off: 4.0 - 1.0 margin - 2.5 = 0.5 s in
    release.set()
    ((at, task, _),) = llm.calls
    assert 0.4 <= at - started < 1.5 and task is LLMTask.ENRICH_EXTRACT  # at the cut-off, not before, not at the end
    assert llm.pages_sent() == NAMES[:2] and _named(found) == sorted(NAMES[:2])
    (asked,) = timer.asked
    assert asked[:5] == (LLMTask.ENRICH_EXTRACT, DataClass.PUBLIC, Difficulty.LONG, facts_module.PLANNED_TOKENS, 3000)


def test_with_every_page_in_early_the_call_goes_at_once(tmp_path: Path) -> None:
    llm = ReadsEachPage()
    started = monotonic()
    _find(_lookup(tmp_path, Serper(), Firecrawl(), Pages(), llm, Timer(2.0)))
    assert llm.calls[0][0] - started < 5  # the 26 s window isn't waited out


@pytest.mark.parametrize("seconds", [None, 28.6])  # no model has room; or its time leaves under 1.5 s to read
def test_with_no_time_for_pages_and_the_call_nothing_is_searched(tmp_path: Path, seconds: float | None) -> None:
    serper, firecrawl, llm = Serper(), Firecrawl(), ReadsEachPage()
    found = _find(_lookup(tmp_path, serper, firecrawl, Pages(), llm, Timer(seconds)))
    assert (found.claims, found.searches, serper.asked, firecrawl.asked, llm.calls) == ([], 0, [], [], [])


def test_no_searches_left_to_enrich_looks_nothing_up(tmp_path: Path) -> None:
    serper = Serper()
    found = _find(_lookup(tmp_path, serper, Firecrawl(), Pages(), ReadsEachPage()), searches=0)
    assert found.claims == [] and serper.asked == []


def test_a_closed_window_stops_the_paid_readers_at_once_whatever_the_clock_says() -> None:
    caps = {"pages": ProviderCap(per_search=24, per_day=200), "jina": ProviderCap(per_search=10, per_day=200)}
    usage = UsageBook(caps, path=None).search()
    window = Window(monotonic() + 60)
    reading = UntilWindowEnds(usage, window)
    assert reading.take("jina") and window.left() > 50
    window.close()
    assert (reading.take("jina"), reading.left("jina"), window.left(), window.over()) == (False, 0, 0.0, True)
    assert reading.take("pages") and reading.left("pages") == 23  # a plain fetch still goes, as the search's own
    assert Window(monotonic() - 1).over() and Window(monotonic() - 1).left() == 0.0


# --- passages, not whole pages ----------------------------------------------------------------------------------------


def test_the_call_gets_the_passages_around_the_product_and_its_criterion_not_the_whole_page(tmp_path: Path) -> None:
    product = PRODUCTS[0]
    filler = " ".join(f"Unrelated sentence number {n} about the keyboard and the ports." for n in range(80))
    long_text = f"{filler} {_sentence(product.name)} {filler}"

    class LongPages(Pages):
        def fetch(self, url: str, usage: SearchUsage) -> PageResult:
            self.read.append(url)
            return PageResult(source=_fresh(review_source(url, text=long_text)))

    llm = ReadsEachPage()
    found = _find(_lookup(tmp_path, Serper(), None, LongPages(), llm), targets=TARGETS[:1])
    (_, _, user) = llm.calls[0]
    assert _sentence(product.name) in user and GAP.strip() not in _sentence(product.name)
    assert len(user) < len(long_text) / 2  # passages, not the page
    assert "Page 1, about Vela Pro 14, for battery_life:" in user
    assert _named(found) == [product.name]  # the quote is checked against the whole page, and is on it


def test_a_model_quicker_than_the_clients_last_second_still_gets_its_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real client starts no call in its last second (CALL_MARGIN_S). A model measured at 0.4 s still gets its
    call at the cut-off, as the cut-off always leaves it 2 s (CALL_FLOOR_S)."""
    from app.contracts import Provider
    from app.llm import InMemoryCallLog, WrapperLLMClient, load_llm_config
    from tests.fakes.llm import FakeAdapter

    monkeypatch.setattr(facts_module, "MIN_READ_S", 0.1)
    monkeypatch.setattr(facts_module, "PATH_SHARE", 0.99)

    def reads(user: str) -> str:
        reader = ReadsEachPage()
        return str(reader.complete(LLMTask.ENRICH_EXTRACT, "", user, RawFacts).model_dump_json())

    adapters = {Provider.GEMINI: FakeAdapter(Provider.GEMINI, replies=(reads,))}
    client = WrapperLLMClient(load_llm_config(), adapters, InMemoryCallLog(), limiters={})  # type: ignore[arg-type]
    gemini = next(m for m in client.config.models if m.provider is Provider.GEMINI)
    for _ in range(3):
        client.times.note((gemini.provider, gemini.model), LLMTask.ENRICH_EXTRACT, 0.4)
    late = _query(PRODUCTS[2])
    release = threading.Event()
    cache = ResearchCache(folder=tmp_path / "enrich", ttl=timedelta(days=7), reddit_ttl=timedelta(days=7))
    lookup = FactLookup(
        client,
        Serper(),
        Pages(),
        cache,
        research=load_research_settings(),
        extract=load_extract_settings(env={}),
        now=lambda: NOW,
        firecrawl=Firecrawl(hold={late: release}),
        timer=client,
    )
    usage = _book().search(per_search={"serper": 6})
    try:
        found = lookup.find(TARGETS[:3], RANKED, usage, monotonic() + 4.0, "srch_d101c")
    finally:
        release.set()
    assert sorted(c.product_mention for c in found.claims) == sorted(NAMES[:2])  # read at the cut-off, and answered
    assert [r.outcome for r in client.call_log.records if r.task is LLMTask.ENRICH_EXTRACT] == ["ok"]  # type: ignore[attr-defined]


# --- guards: a failure loses only its own page (build rule 9) ---------------------------------------------------------


def _bare_run() -> Any:
    return facts_module._Run(RANKED, _book().search(per_search={"serper": 6}), Window(monotonic() + 5), 6)


def test_a_product_whose_paths_cant_start_finds_nothing(tmp_path: Path) -> None:
    lookup = _lookup(tmp_path, Serper(), Firecrawl(), Pages(), ReadsEachPage())
    run = _bare_run()
    run.paths.shutdown()  # the window is over: neither path may start
    assert lookup._one_product(_query(PRODUCTS[0]), PRODUCTS[0], "serper", run) is None
    assert lookup.in_flight == []


def test_a_look_up_or_a_serper_path_that_raises_finds_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*args: Any) -> Any:
        raise RuntimeError("made-up bug")

    lookup = _lookup(tmp_path, Serper(), Firecrawl(), Pages(), ReadsEachPage())
    monkeypatch.setattr(lookup, "_start", broken)
    assert lookup._one_product(_query(PRODUCTS[0]), PRODUCTS[0], "serper", _bare_run()) is None
    other = _lookup(tmp_path / "b", Serper(), Firecrawl(), Pages(), ReadsEachPage())
    monkeypatch.setattr(other, "_links", broken)
    assert other._by_serper(_query(PRODUCTS[0]), PRODUCTS[0], _bare_run()) is None


def test_with_under_a_second_left_at_the_call_none_is_sent(tmp_path: Path) -> None:
    llm = ReadsEachPage()
    lookup = _lookup(tmp_path, Serper(), Firecrawl(), Pages(), llm)
    assert lookup._answer("Page 1, about Vela Pro 14, for battery_life:", monotonic() + 1.5, "srch_d101d") == (None, 0)
    assert llm.calls == []  # 0.5 s left after the margin: no model answers that soon


# --- review 2 ---------------------------------------------------------------------------------------------------------


def test_a_page_two_products_share_goes_to_the_call_for_each(tmp_path: Path) -> None:
    """Found in review: one product's page was dropped when another had it too, and its look-up was lost."""
    both = [PRODUCTS[2], PRODUCTS[3]]
    shared = SearchedPage(
        url="https://www.91mobiles.com/orbit-air-13-vs-zen-slate-15",
        title="t",
        text=" ".join(_sentence(p.name) for p in both),
    )
    llm = ReadsEachPage()
    found = _find(_lookup(tmp_path, Serper(), Firecrawl(extra=[shared]), Pages(), llm), targets=TARGETS[2:4])
    assert sorted(llm.pages_sent()) == sorted(p.name for p in both)  # the shared page, once for each product
    assert _named(found) == sorted(p.name for p in both)


class Unread(Firecrawl):
    """Its search says one result came back unread: that read may work next time."""

    def search_pages(self, *args: Any, **kwargs: Any) -> SearchPages:
        found = super().search_pages(*args, **kwargs)
        return SearchPages(found.pages, found.failure, unread=1)


def test_a_reading_search_with_a_failed_read_is_not_kept_and_is_asked_again(tmp_path: Path) -> None:
    _find(_lookup(tmp_path, Serper(), Unread(), Pages(), ReadsEachPage()), targets=TARGETS[2:3])
    again = Firecrawl()
    _find(_lookup(tmp_path, Serper(), again, Pages(), ReadsEachPage()), targets=TARGETS[2:3])
    assert again.asked == [_query(PRODUCTS[2])]  # its pages were kept one by one, but not the list


def test_a_reading_search_whose_page_robots_refused_is_not_kept(tmp_path: Path) -> None:
    product = PRODUCTS[2]
    pages = Pages(refuse={_url(product, "notebookcheck.net")})  # robots.txt couldn't be read, or said no
    _find(_lookup(tmp_path, Serper(), Firecrawl(), pages, ReadsEachPage()), targets=TARGETS[2:3])
    again = Firecrawl()
    _find(_lookup(tmp_path, Serper(), again, Pages(), ReadsEachPage()), targets=TARGETS[2:3])
    assert again.asked == [_query(product)]


def test_a_reading_search_still_queued_when_the_window_closes_is_never_sent(tmp_path: Path) -> None:
    firecrawl = Firecrawl()
    lookup = _lookup(tmp_path, Serper(), firecrawl, Pages(), ReadsEachPage())
    run = _bare_run()
    run.window.close()
    assert lookup._searched(("fact_pages", "q", "IN"), _query(PRODUCTS[2]), run) == []
    assert run.sent == 0 and run.usage.used.get("firecrawl", 0) == 0  # refused before it went: given back


def test_a_generated_criterions_own_words_never_choose_the_passages(tmp_path: Path) -> None:
    """D-51, found in review: a generated key can hold the shopper's words. Only the fixed key the call is sent and
    its label pick the passages."""
    from app.contracts import ExtractInput
    from app.extract.prompts import allowed_criteria

    product = PRODUCTS[0]
    generated = "battery_for_long_flights_with_my_kids"
    target = EnrichTarget(product_id=product.id, product_name=product.name, criterion_key=generated)
    filler = " ".join(["The hinge feels solid and the ports are where you expect them."] * 40)
    text = f"Kids love long flights. {filler} {_sentence(product.name)} {filler}"
    page = _fresh(review_source(_url(product), text=text))
    criteria = allowed_criteria(
        ExtractInput(source=page, criterion_keys=[generated], category_label="laptop"),
        DataClass.PUBLIC,
        settings=load_extract_settings(env={}),
    )
    ask = FactLookup._page_ask(page, product, [(target, product, "Battery life")], criteria)
    assert ask.keys and generated not in ask.keys  # the call is sent the fixed stand-in's key
    assert "flights" not in ask.text and _sentence(product.name) in ask.text
