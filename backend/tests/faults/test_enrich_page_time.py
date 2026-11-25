"""US-41 (S5-6): a fact page's own time. Found live on 25 Nov 2026: one slow site used up the look-up's whole read
window, and the client was closed under the reads still running, so even a page that came late was never kept.

The slow-site, late-page, open-client and counting tests fail on the code before that fix. The others guard the new
code (the window's end on Windows' coarse clock, a read the shut pool cancelled, a read after the window spending no
paid reader). Every search, page and answer is a fake, and every product and page is made up.
"""

from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any

import pytest

from app.contracts import EnrichInput, LLMTask, Source, SourceFailure, SourceType, StageContext
from app.enrich import PriceLookupStage
from app.enrich import facts as facts_module
from app.enrich.stage import LOOKUP_DEADLINE_S
from app.research.pages import PageResult
from app.research.serper import SerperResult
from app.research.settings import ProviderCap
from app.research.usage import SearchUsage, UsageBook
from tests.fakes.llm import FakeLLMClient
from tests.fakes.research import review_source
from tests.unit.enrich.test_fact_lookup import (
    ASK,
    GOOD,
    NOW,
    REVIEW,
    TARGET,
    TEXT,
    VELA,
    FakeSearch,
    _answer,
    _lookup,
    _usage,
    short_window,
)
from tests.unit.enrich.test_price_lookup import FakeShopping

SLOW = "https://www.notebookcheck.net/vela-pro-14-review"
SLOWER = "https://www.rtings.com/laptop/reviews/vela/pro-14"
THIRD = "https://www.digit.in/reviews/laptops/vela-pro-14-review.html"


class SlowPages:
    """Reads the slow addresses only once `release` is set; any other address at once. Records every read asked."""

    def __init__(self, slow: set[str]) -> None:
        self.slow = slow
        self.release = threading.Event()
        self.read: list[str] = []
        self.finished: list[str] = []

    def fetch(self, url: str, usage: SearchUsage) -> PageResult:
        self.read.append(url)
        if url in self.slow:
            self.release.wait(10)
        self.finished.append(url)
        page = review_source(url, title="A made-up review", text=TEXT)
        fresh = Source.model_validate({**page.model_dump(), "fetched_at": datetime.now(UTC)})  # the cache keeps 7 days
        return PageResult(source=fresh)


def test_a_slow_page_never_takes_the_whole_window_the_next_link_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(facts_module, "PAGE_S", 0.2)
    pages = SlowPages({SLOW})
    lookup = _lookup(
        tmp_path, FakeSearch([SLOW, REVIEW]), pages, FakeLLMClient({LLMTask.ENRICH_EXTRACT: _answer(GOOD)})
    )  # type: ignore[arg-type]
    started = monotonic()
    try:
        found = lookup.find([(TARGET, VELA)], ASK, _usage(), monotonic() + 30, "srch_pagetime1")
    finally:
        pages.release.set()
    assert monotonic() - started < 5  # not the read window's 15 s
    assert [str(s.url) for s in found.sources] == [REVIEW]  # the next link was read in time
    assert [c.criterion_key for c in found.claims] == ["battery_life"]


def test_no_page_is_asked_for_once_the_window_is_over(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Each link's share is at most half of the time the search left: the second link starts halfway, and the third,
    which would start as the window ends, is never asked for."""
    monkeypatch.setattr(facts_module, "PAGE_S", 5.0)
    short_window(monkeypatch, 0.3)
    pages = SlowPages({SLOW, SLOWER, THIRD})
    llm = FakeLLMClient({LLMTask.ENRICH_EXTRACT: _answer(GOOD)})
    lookup = _lookup(tmp_path, FakeSearch([SLOW, SLOWER, THIRD]), pages, llm)  # type: ignore[arg-type]
    try:
        found = lookup.find([(TARGET, VELA)], ASK, _usage(), monotonic() + 30, "srch_pagetime2")
    finally:
        pages.release.set()
    assert (found.claims, found.sources, llm.calls) == ([], [], [])
    assert pages.read == [SLOW, SLOWER]  # the third link was never asked for: the window was over


def _run(reads: Any = None) -> Any:
    run = facts_module._Run(ASK, _usage(), facts_module.Window(monotonic() + 5), 6)
    if reads is not None:
        run.reads = reads
    return run


def test_a_read_asked_for_after_the_reads_were_shut_finds_nothing(tmp_path: Path) -> None:
    pages = SlowPages(set())
    lookup = _lookup(tmp_path, FakeSearch([REVIEW]), pages, FakeLLMClient({}))  # type: ignore[arg-type]
    reads = ThreadPoolExecutor(max_workers=1)
    reads.shutdown()
    assert lookup._first_naming([REVIEW], VELA, _run(reads)) is None and pages.read == []


class RecordingClient:
    """Stands in for the look-up's HTTP client: records when it is closed."""

    def __init__(self) -> None:
        self.closed = threading.Event()

    def close(self) -> None:
        self.closed.set()


def test_the_client_stays_open_until_a_late_page_arrives_and_that_page_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The window ends with a page still arriving: the search goes on without it, but the client is closed only once it
    has arrived, so it is kept in enrich's cache and the next search reads it from there."""
    monkeypatch.setattr(facts_module, "PAGE_S", 0.2)
    short_window(monkeypatch, 0.3, ends_in=LOOKUP_DEADLINE_S)  # the stage's look-ups end this long after they start
    pages = SlowPages({SLOW})
    llm = FakeLLMClient({LLMTask.ENRICH_EXTRACT: _answer(GOOD)})
    lookup = _lookup(tmp_path, FakeSearch([SLOW]), pages, llm)  # type: ignore[arg-type]
    client = RecordingClient()
    book = UsageBook({"serper": ProviderCap(per_search=6, per_day=80)}, path=None)
    stage = PriceLookupStage(FakeShopping({}), usage=book, now=lambda: NOW)
    monkeypatch.setattr(stage, "_wire_facts", lambda retry: (lookup, client))
    stage.use_products("srch_pagetime3", [VELA])
    stage.use_search("srch_pagetime3", ASK)
    try:
        out = stage.run(EnrichInput(targets=[TARGET]), StageContext(search_id="srch_pagetime3"))
        assert out.new_claims == []  # the page came too late for this search
        assert not client.closed.is_set()  # its read is still going: the client is not closed under it
    finally:
        pages.release.set()
    assert client.closed.wait(5)  # closed once the late read ended
    assert pages.finished == [SLOW]
    kept = lookup.cache.get_sources(("page", SLOW))
    assert kept is not None and [str(s.url) for s in kept] == [SLOW]  # kept for the next search
    again = lookup.find([(TARGET, VELA)], ASK, _usage(), monotonic() + 30, "srch_pagetime4")
    assert [str(s.url) for s in again.sources] == [SLOW] and pages.read == [SLOW]  # read from the cache this time


def test_a_finished_look_up_closes_its_client_at_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lookup = _lookup(
        tmp_path, FakeSearch([REVIEW]), SlowPages(set()), FakeLLMClient({LLMTask.ENRICH_EXTRACT: _answer(GOOD)})
    )  # type: ignore[arg-type]
    client = RecordingClient()
    book = UsageBook({"serper": ProviderCap(per_search=6, per_day=80)}, path=None)
    stage = PriceLookupStage(FakeShopping({}), usage=book, now=lambda: NOW)
    monkeypatch.setattr(stage, "_wire_facts", lambda retry: (lookup, client))
    stage.use_products("srch_pagetime5", [VELA])
    stage.use_search("srch_pagetime5", ASK)
    out = stage.run(EnrichInput(targets=[TARGET]), StageContext(search_id="srch_pagetime5"))
    assert [c.criterion_key for c in out.new_claims] == ["battery_life"]
    assert client.closed.wait(2)


# --- the review's findings --------------------------------------------------------------------------------------------


class TimedPages(SlowPages):
    """Each slow address arrives on its own after the given seconds; any other at once."""

    def __init__(self, after: dict[str, float]) -> None:
        super().__init__(set(after))
        self.after = after

    def fetch(self, url: str, usage: SearchUsage) -> PageResult:
        if url in self.after:
            self.read.append(url)
            threading.Event().wait(self.after[url])
            self.finished.append(url)
            page = review_source(url, title="A made-up review", text=TEXT)
            return PageResult(source=Source.model_validate({**page.model_dump(), "fetched_at": datetime.now(UTC)}))
        return super().fetch(url, usage)


def test_a_page_arriving_after_its_own_time_but_inside_the_window_is_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One readable link, slower than PAGE_S: it is still waited for while the window lasts."""
    monkeypatch.setattr(facts_module, "PAGE_S", 0.2)
    pages = TimedPages({SLOW: 0.6})
    lookup = _lookup(tmp_path, FakeSearch([SLOW]), pages, FakeLLMClient({LLMTask.ENRICH_EXTRACT: _answer(GOOD)}))  # type: ignore[arg-type]
    found = lookup.find([(TARGET, VELA)], ASK, _usage(), monotonic() + 30, "srch_pagetime6")
    assert [str(s.url) for s in found.sources] == [SLOW]
    assert [c.criterion_key for c in found.claims] == ["battery_life"]


def test_a_slow_first_link_naming_the_product_wins_while_the_second_is_still_coming(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(facts_module, "PAGE_S", 0.2)
    pages = TimedPages({SLOW: 0.5, SLOWER: 5.0})
    lookup = _lookup(
        tmp_path, FakeSearch([SLOW, SLOWER]), pages, FakeLLMClient({LLMTask.ENRICH_EXTRACT: _answer(GOOD)})
    )  # type: ignore[arg-type]
    started = monotonic()
    found = lookup.find([(TARGET, VELA)], ASK, _usage(), monotonic() + 30, "srch_pagetime7")
    assert [str(s.url) for s in found.sources] == [SLOW]  # both were read side by side; the first to arrive won
    assert pages.read == [SLOW, SLOWER] and monotonic() - started < 4


class CancellingReads:
    """A shut pool's answer: every read it is given is cancelled before it starts."""

    def __init__(self) -> None:
        self.given = 0

    def submit(self, fn: object, *args: object) -> Future[Source | None]:
        self.given += 1
        job: Future[Source | None] = Future()
        job.cancel()
        job.set_running_or_notify_cancel()  # as a pool's worker leaves a cancelled item: waiters see it done
        return job


def test_a_read_cancelled_before_it_started_is_skipped(tmp_path: Path) -> None:
    lookup = _lookup(tmp_path, FakeSearch([SLOW, REVIEW]), SlowPages(set()), FakeLLMClient({}))  # type: ignore[arg-type]
    reads = CancellingReads()
    assert lookup._first_naming([SLOW, REVIEW], VELA, _run(reads)) is None
    assert reads.given == 2  # each link was asked once, and nothing raised


class BlockingSearch(FakeSearch):
    """A search that answers only once released."""

    def __init__(self, links: list[str]) -> None:
        super().__init__(links)
        self.release = threading.Event()

    def search(self, query: str, region: str, usage: SearchUsage, sites: object = ()) -> SerperResult:
        self.release.wait(10)
        return super().search(query, region, usage, sites)


def test_a_search_still_going_at_the_windows_end_keeps_the_client_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    short_window(monkeypatch, 0.3, ends_in=LOOKUP_DEADLINE_S)
    search = BlockingSearch([REVIEW])
    lookup = _lookup(tmp_path, search, SlowPages(set()), FakeLLMClient({LLMTask.ENRICH_EXTRACT: _answer(GOOD)}))  # type: ignore[arg-type]
    client = RecordingClient()
    book = UsageBook({"serper": ProviderCap(per_search=6, per_day=80)}, path=None)
    stage = PriceLookupStage(FakeShopping({}), usage=book, now=lambda: NOW)
    monkeypatch.setattr(stage, "_wire_facts", lambda retry: (lookup, client))
    stage.use_products("srch_pagetime8", [VELA])
    stage.use_search("srch_pagetime8", ASK)
    try:
        out = stage.run(EnrichInput(targets=[TARGET]), StageContext(search_id="srch_pagetime8"))
        assert out.new_claims == [] and not client.closed.is_set()  # the search is still going
    finally:
        search.release.set()
    assert client.closed.wait(5)


@pytest.mark.parametrize(
    ("reason", "detail", "sent"),
    [
        ("quota_exhausted", "a Serper cap is reached", False),
        ("quota_exhausted", "web search: a Serper cap is reached", False),
        ("quota_exhausted", "Firecrawl: search a cap is reached", False),
        ("quota_exhausted", "HTTP 402 (no credits left)", True),  # Serper's own refusal: the request went out
        ("blocked", "SERPER_API_KEY is not set", False),
        ("blocked", "Firecrawl: search FIRECRAWL_API_KEY is not set", False),
        ("blocked", "HTTP 401 (refused)", True),
        ("timeout", "shopping: no time left to ask", False),
        ("timeout", "shopping: no answer in time", True),
        ("error", None, True),
        (None, None, True),
        ("rate_limited", "Firecrawl: search paced", False),  # Firecrawl's own gate held it back
        ("blocked", "Firecrawl: search switched off for this run", False),
        ("rate_limited", "web search: HTTP 429 (rate limited)", True),
        ("error", "Firecrawl: search request failed (OverBudget)", False),  # a live run's guard refused it first
    ],
)
def test_what_counts_as_sent(reason: str | None, detail: str | None, sent: bool) -> None:
    assert facts_module.nothing_sent(reason, detail) is not sent


def test_a_fact_search_with_no_key_counts_as_none(tmp_path: Path) -> None:
    class NoKey(FakeSearch):
        def search(self, query: str, region: str, usage: SearchUsage, sites: object = ()) -> SerperResult:
            failure = SourceFailure(
                source_type=SourceType.WEB_SEARCH, reason="blocked", detail="SERPER_API_KEY is not set"
            )
            return SerperResult(failure=failure)

    lookup = _lookup(tmp_path, NoKey([REVIEW]), SlowPages(set()), FakeLLMClient({}))  # type: ignore[arg-type]
    found = lookup.find([(TARGET, VELA)], ASK, _usage(), monotonic() + 30, "srch_pagetime9")
    assert found.searches == 0


class PaidReaderPages(SlowPages):
    """Fetches like a page reader whose plain fetch failed: it then asks for a Jina read, and records the answer."""

    def __init__(self, slow: set[str]) -> None:
        super().__init__(slow)
        self.jina_room: list[bool] = []

    def fetch(self, url: str, usage: SearchUsage) -> PageResult:
        self.read.append(url)
        if url in self.slow:
            self.release.wait(10)
        self.jina_room.append(usage.take("jina"))  # the plain fetch failed: Jina is next
        self.finished.append(url)
        return PageResult(source=None)


def test_a_read_left_going_after_the_window_spends_no_paid_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(facts_module, "PAGE_S", 0.1)
    short_window(monkeypatch, 0.3)
    pages = PaidReaderPages({SLOW})
    book = UsageBook(
        {"serper": ProviderCap(per_search=6, per_day=80), "jina": ProviderCap(per_search=10, per_day=200)}, path=None
    )
    usage = book.search(per_search={"serper": 6})
    lookup = _lookup(tmp_path, FakeSearch([REVIEW, SLOW]), pages, FakeLLMClient({}))  # type: ignore[arg-type]
    lookup.find([(TARGET, VELA)], ASK, usage, monotonic() + 30, "srch_pagetime11")
    pages.release.set()
    for job in list(lookup.in_flight):
        job.result(timeout=5)
    assert pages.jina_room == [True, False]  # in the window, Jina had room; after it, none
    assert usage.used["jina"] == 1  # the late read's ask was refused, so nothing more was counted


def test_the_window_only_stops_the_paid_readers() -> None:
    caps = {"pages": ProviderCap(per_search=24, per_day=200), "jina": ProviderCap(per_search=10, per_day=200)}
    usage = UsageBook(caps, path=None).search()
    over = facts_module.UntilWindowEnds(usage, facts_module.Window(monotonic() - 1))
    assert (over.take("jina"), over.left("jina")) == (False, 0)
    assert (over.take("pages"), over.left("pages")) == (True, 23)  # a plain fetch still goes, as the search's own
    assert usage.used["pages"] == 1
    ahead = facts_module.UntilWindowEnds(usage, facts_module.Window(monotonic() + 60))
    assert ahead.take("jina") and usage.used["jina"] == 1
