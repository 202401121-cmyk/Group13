"""D-101: Firecrawl's search with its top results read in the same call, so no read step follows. Every credit is
counted before it is sent, the answer is due inside the look-up's window, and a page that came back is held to the
rules a page read here is (take_read). Also the usage book's reserve, which keeps Serper's last 25 for research and
commerce. Recordings only: no network.
"""

from __future__ import annotations

import json
import threading
from time import monotonic
from typing import Any

import httpx
import pytest

from app.llm.retry import RetryClock
from app.research import usage as usage_module
from app.research.cache import ResearchCache
from app.research.readers import (
    FIRECRAWL_SEARCH_URL,
    FirecrawlClient,
    FirecrawlGate,
    SearchedPage,
    plain_text,
    search_credits,
)
from app.research.settings import ProviderCap
from app.research.usage import UsageBook
from tests.fakes.reddit import NOW
from tests.fakes.research import Web
from tests.unit.research.test_page_readers import PAGE, SITE, WORDS, _pages, _usage

LANDED = f"{SITE}/nova-book-14-review"
ELSEWHERE = f"{SITE}/nova-book-14-specs"


def _item(url: str, **extra: Any) -> dict[str, Any]:
    meta = {"statusCode": 200, "url": url, "sourceURL": url, "title": "Nova Book 14 review", **extra.pop("meta", {})}
    return {
        "url": url,
        "title": "a result",
        "description": "x",
        "markdown": f"# Nova Book 14\n\n{WORDS}",
        "metadata": meta,
        **extra,
    }


def _reply(*items: Any, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json={"success": True, "data": {"web": list(items)}, "creditsUsed": 5})


def _client(web: Web, key: str | None = "fc-made-up", retry: RetryClock | None = None) -> FirecrawlClient:
    cache = ResearchCache(folder=None, now=lambda: NOW)
    return FirecrawlClient(web.client(), key, cache=cache, gate=FirecrawlGate(), now=lambda: NOW, retry=retry)


def _bodies(web: Web) -> list[dict[str, Any]]:
    return [json.loads(r.content) for r in web.requests if str(r.url) == FIRECRAWL_SEARCH_URL]


def test_a_search_reads_its_results_in_the_same_call_and_every_credit_is_counted_first() -> None:
    items = [
        _item(PAGE, meta={"url": LANDED}),  # landed elsewhere on the site after a redirect
        _item(f"{SITE}/gone", meta={"statusCode": 404}),
        _item(f"{SITE}/broken", meta={"error": "timed out"}),
        {**_item(f"{SITE}/no-text"), "markdown": None},
        "not a result",
        {"url": ELSEWHERE, "markdown": WORDS},  # no metadata at all: its own address and no title
        {"markdown": WORDS, "metadata": {}},  # no address anywhere
    ]
    web = Web().on(FIRECRAWL_SEARCH_URL, _reply(*items))
    usage = _usage()
    found = _client(web).search_pages(
        "Nova Book 14 Battery life", "in", usage, until=monotonic() + 6.5, exclude=("reddit.com", "youtube.com")
    )
    assert found.failure is None
    assert [(p.url, p.title) for p in found.pages] == [(LANDED, "Nova Book 14 review"), (ELSEWHERE, "")]
    assert found.pages[0].text == plain_text(_item(PAGE)["markdown"])  # made plain, as every reader's text
    (body,) = _bodies(web)
    assert body["limit"] == 3 and body["country"] == "IN" and 6000 <= body["timeout"] <= 6500  # the time then left
    assert body["excludeDomains"] == ["reddit.com", "youtube.com"]  # Firecrawl never reads them (D-53)
    assert body["scrapeOptions"] == {
        "formats": ["markdown"],
        "onlyMainContent": True,
        "maxAge": 86_400_000,
        "parsers": [],  # no PDF is parsed: that costs a credit a page, and one a result is counted
        "timeout": body["timeout"] - 2000,  # each read leaves the search step its 2 s: a slow page comes back unread
    }
    assert found.unread == 4  # the 404, the error, the one with no text and the one with no address
    assert usage.used["firecrawl"] == search_credits(3, read=True) == 5  # 2 for the search, 1 a result read
    assert usage.used["firecrawl_pages"] == 0  # the search's credits, not page reads of research's


def test_with_no_key_or_no_room_nothing_is_sent() -> None:
    web = Web().on(FIRECRAWL_SEARCH_URL, _reply(_item(PAGE)))
    no_key = _client(web, key=None).search_pages("q", "IN", _usage(), until=monotonic() + 5)
    assert no_key.failure is not None and no_key.failure.reason == "blocked"
    capped = _usage(firecrawl=ProviderCap(per_search=30, per_day=4))  # one search needs 5
    no_room = _client(web).search_pages("q", "IN", capped, until=monotonic() + 5)
    assert no_room.failure is not None and no_room.failure.reason == "quota_exhausted"
    assert web.requests == [] and capped.used["firecrawl"] == 0


@pytest.mark.parametrize(
    ("reply", "reason"),
    [
        (httpx.Response(402, json={"success": False}), "quota_exhausted"),
        (httpx.Response(200, json={"success": True, "data": {}}), "error"),
        (httpx.Response(200, json={"success": True, "data": "not a dict"}), "error"),
    ],
)
def test_a_refused_or_unreadable_reply_is_a_failure(reply: httpx.Response, reason: str) -> None:
    found = _client(Web().on(FIRECRAWL_SEARCH_URL, reply)).search_pages("q", "IN", _usage(), until=monotonic() + 5)
    assert found.failure is not None and found.failure.reason == reason and found.pages == ()


def test_a_search_that_reads_its_results_is_never_retried() -> None:
    web = Web().on(FIRECRAWL_SEARCH_URL, httpx.Response(503, json={"success": False}))
    retry = RetryClock(time_left=lambda: 60.0, sleep=lambda _: None)  # a retry would have time
    found = _client(web, retry=retry).search_pages("q", "XYZ", _usage(), until=monotonic() + 5)
    assert found.failure is not None and len(web.requests) == 1  # its answer is of use only inside the window
    (body,) = _bodies(web)
    assert body["country"] == "IN"  # a region that isn't a country: our own
    assert "excludeDomains" not in body  # none asked for


def test_with_less_than_firecrawls_second_left_nothing_is_sent_or_counted() -> None:
    web = Web().on(FIRECRAWL_SEARCH_URL, _reply(_item(PAGE)))
    usage = _usage()
    found = _client(web).search_pages("q", "IN", usage, until=monotonic() + 0.5)
    assert found.failure is not None and found.failure.reason == "timeout"
    assert found.failure.detail is not None and "no time left to ask" in found.failure.detail  # never sent (D-78)
    assert web.requests == [] and usage.used["firecrawl"] == 0


def test_a_slot_is_waited_for_no_longer_than_the_window_leaves() -> None:
    gate = FirecrawlGate(wait_s=30.0)
    for _ in range(2):  # both of Firecrawl's slots are taken by other requests
        assert gate.try_start() is None
    client = FirecrawlClient(Web().client(), "fc-made-up", cache=ResearchCache(folder=None), gate=gate)
    started = monotonic()
    found = client.search_pages("q", "IN", _usage(), until=monotonic() + 3.5)
    assert found.failure is not None and found.failure.reason == "rate_limited"  # paced: never sent
    assert monotonic() - started < 3  # not the gate's own 30 s: the 0.5 s the window left past the 3 s it needs
    for _ in range(2):
        gate.finish(200)


class _Racing(FirecrawlGate):
    """Another request takes the credits while this one waits for its slot: the room it was told of is gone."""

    def __init__(self, take: Any) -> None:
        super().__init__()
        self.take = take

    def try_start(self, wait_s: float | None = None) -> Any:
        self.take()
        return super().try_start(wait_s)


def test_credits_are_taken_all_at_once_or_none_when_another_request_took_the_room() -> None:
    book = UsageBook({"firecrawl": ProviderCap(per_search=30, per_day=8)}, path=None)
    usage, other = book.search(), book.search()
    web = Web().on(FIRECRAWL_SEARCH_URL, _reply(_item(PAGE)))
    gate = _Racing(lambda: other.take("firecrawl", count=5))  # 3 left of 8: room for 3 of this search's 5
    client = FirecrawlClient(web.client(), "fc-made-up", cache=ResearchCache(folder=None), gate=gate)
    found = client.search_pages("q", "IN", usage, until=monotonic() + 5)
    assert found.failure is not None and found.failure.reason == "quota_exhausted" and web.requests == []
    assert usage.used["firecrawl"] == 0 and book.today()["firecrawl"] == 5  # none of the 3 it could have had


def test_a_scrape_whose_credits_ran_out_gives_its_page_back() -> None:
    caps = {
        "firecrawl_pages": ProviderCap(per_search=4, per_day=30),
        "firecrawl": ProviderCap(per_search=30, per_day=1),
    }
    book = UsageBook(caps, path=None)
    usage, other = book.search(), book.search()
    web = Web()
    gate = _Racing(lambda: other.take("firecrawl"))  # the last credit goes elsewhere while this waits
    client = FirecrawlClient(web.client(), "fc-made-up", cache=ResearchCache(folder=None), gate=gate)
    usage_module.sent("firecrawl_pages")  # this thread's earlier requests (other tests') are settled
    assert client.scrape(PAGE, usage) == ("quota_exhausted", "a cap is reached")
    assert usage.used["firecrawl_pages"] == 0 and book.today()["firecrawl_pages"] == 0 and web.requests == []
    assert usage_module.not_sent("firecrawl_pages") == 0  # nothing left waiting to be given back twice


def test_a_request_with_no_scrape_options_gets_firecrawls_timeout_alone() -> None:
    from app.research.readers import _with_timeout

    assert _with_timeout({"query": "q"}, 2500) == {"query": "q", "timeout": 2500}
    timed = _with_timeout({"query": "q", "scrapeOptions": {"formats": ["markdown"]}}, 2500)
    assert timed["scrapeOptions"] == {"formats": ["markdown"], "timeout": 500}  # 2 s left for the search step


def test_what_a_firecrawl_search_costs() -> None:
    assert search_credits(3, read=True) == 5
    assert (search_credits(10, read=False), search_credits(11, read=False)) == (2, 4)


def test_a_searched_page_is_plain_data() -> None:
    assert SearchedPage(url=PAGE, title="t", text="x").url == PAGE


# --- a page a search brought, held to the page rules -----------------------------------------------------------------


def test_a_page_a_search_brought_is_kept_as_a_page_read_through_firecrawl() -> None:
    web = Web()
    result = _pages(web).take_read(PAGE, "Nova Book 14 review", WORDS, _usage())
    assert result.source is not None and result.source.via == "firecrawl"
    assert result.source.site == "reviews.example" and str(result.source.url) == PAGE
    assert web.asked(PAGE) == 0  # no request to the page itself


def test_robots_txt_still_decides_and_no_page_request_is_sent() -> None:
    web = Web().on(f"{SITE}/robots.txt", httpx.Response(200, text="User-agent: *\nDisallow: /"))
    result = _pages(web).take_read(PAGE, "t", WORDS, _usage())
    assert result.source is None and result.reason == "blocked" and web.asked(PAGE) == 0


@pytest.mark.parametrize(
    ("url", "text", "reason"),
    [
        ("https://www.unknown.example/nova-book-14", WORDS, "not_found"),  # no configured site
        (f"{SITE}/account/login?next=/nova-book-14", WORDS, "not_found"),  # a logged-in page
        (PAGE, "too thin", "not_found"),
    ],
)
def test_a_page_off_the_rules_is_never_kept(url: str, text: str, reason: str) -> None:
    result = _pages(Web()).take_read(url, "t", text, _usage())
    assert result.source is None and result.reason == reason


def test_robots_out_of_room_and_a_page_the_contract_refuses_are_not_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    no_room = _usage(pages=ProviderCap(per_search=0, per_day=0))  # robots.txt couldn't be asked
    assert _pages(Web()).take_read(PAGE, "t", WORDS, no_room).reason == "quota_exhausted"
    pages = _pages(Web())
    monkeypatch.setattr(pages, "_source", lambda *args: None)
    assert pages.take_read(PAGE, "t", WORDS, _usage()).reason == "error"


# --- the usage book's reserve ----------------------------------------------------------------------------------------


def test_a_sender_keeping_a_reserve_stops_short_of_the_days_cap() -> None:
    book = UsageBook({"serper": ProviderCap(per_search=80, per_day=10)}, path=None)
    earlier = book.search()
    for _ in range(6):
        assert earlier.take("serper")
    usage = book.search()
    assert usage.left("serper", keep=3) == 1 and usage.take("serper", keep=3)
    assert not usage.take("serper", keep=3) and usage.left("serper", keep=3) == 0  # 3 stay for others
    assert usage.take("serper") and book.today()["serper"] == 8  # a sender keeping none goes on


def test_a_search_reading_three_results_gives_all_five_credits_back_when_refused_before_it_left() -> None:
    book = UsageBook({"firecrawl": ProviderCap(per_search=30, per_day=300)}, path=None)
    usage = book.search()
    usage_module.sent("firecrawl")  # this thread's earlier requests (other tests') are settled
    assert all(usage.take("firecrawl") for _ in range(5))
    assert usage_module.not_sent("firecrawl") == 5  # a run budget refused it: every unit goes back (D-78)
    assert usage.used["firecrawl"] == 0 and book.today()["firecrawl"] == 0


def test_a_scrape_whose_page_went_elsewhere_takes_no_credit() -> None:
    caps = {
        "firecrawl_pages": ProviderCap(per_search=4, per_day=1),
        "firecrawl": ProviderCap(per_search=30, per_day=30),
    }
    book = UsageBook(caps, path=None)
    usage, other = book.search(), book.search()
    web = Web()
    gate = _Racing(lambda: other.take("firecrawl_pages"))  # the day's last page goes elsewhere while this waits
    client = FirecrawlClient(web.client(), "fc-made-up", cache=ResearchCache(folder=None), gate=gate)
    assert client.scrape(PAGE, usage) == ("quota_exhausted", "a cap is reached")
    assert usage.used["firecrawl"] == 0 and web.requests == []


def test_a_unit_given_back_after_its_request_was_settled_still_goes_back() -> None:
    book = UsageBook({"jina": ProviderCap(per_search=10, per_day=200)}, path=None)
    usage = book.search()
    assert usage.take("jina")
    usage_module.sent("jina")  # settled: no longer waiting on this thread
    usage.untake("jina")
    assert usage.used["jina"] == 0 and book.today()["jina"] == 0


def test_with_too_little_time_for_the_search_step_and_a_read_nothing_reaches_the_gate() -> None:
    gate = FirecrawlGate()
    client = FirecrawlClient(Web().client(), "fc-made-up", cache=ResearchCache(folder=None), gate=gate)
    found = client.search_pages("q", "IN", _usage(), until=monotonic() + 2.5)  # under 1 s for reads after the 2 s
    assert found.failure is not None and found.failure.reason == "timeout"
    assert len(gate._started) == 0  # no place in the minute taken


def test_a_request_refused_after_the_gate_gives_its_place_in_the_minute_back() -> None:
    book = UsageBook({"firecrawl": ProviderCap(per_search=30, per_day=8)}, path=None)
    usage, other = book.search(), book.search()
    gate = _Racing(lambda: other.take("firecrawl", count=5))  # the room is gone once it reaches the gate
    client = FirecrawlClient(Web().client(), "fc-made-up", cache=ResearchCache(folder=None), gate=gate)
    assert client.search_pages("q", "IN", usage, until=monotonic() + 5).failure is not None
    assert len(gate._started) == 0  # 10 a minute counts only requests sent
    assert gate.try_start() is None and gate.try_start() is None  # both slots are free again
    gate.finish(200)
    gate.finish(200)


def test_credits_firecrawl_says_it_used_beyond_those_counted_are_counted_too(caplog: pytest.LogCaptureFixture) -> None:
    reply = httpx.Response(200, json={"success": True, "data": {"web": [_item(PAGE)]}, "creditsUsed": 8})
    usage = _usage()
    _client(Web().on(FIRECRAWL_SEARCH_URL, reply)).search_pages("q", "IN", usage, until=monotonic() + 5)
    assert usage.used["firecrawl"] == 8  # 5 before it went, and the 3 it says it spent
    tight = _usage(firecrawl=ProviderCap(per_search=30, per_day=6))
    _client(Web().on(FIRECRAWL_SEARCH_URL, reply)).search_pages("q", "IN", tight, until=monotonic() + 5)
    assert tight.used["firecrawl"] == 6 and "credits more than its caps had room for" in caplog.text


class _LateSlot(FirecrawlGate):
    """The slot comes, but only once the time a read needed has gone."""

    def try_start(self, wait_s: float | None = None) -> Any:
        threading.Event().wait(0.6)
        return super().try_start(wait_s)


def test_a_slot_that_comes_too_late_sends_nothing_and_gives_its_place_back() -> None:
    gate = _LateSlot()
    web = Web().on(FIRECRAWL_SEARCH_URL, _reply(_item(PAGE)))
    client = FirecrawlClient(web.client(), "fc-made-up", cache=ResearchCache(folder=None), gate=gate)
    usage = _usage()
    found = client.search_pages("q", "IN", usage, until=monotonic() + 3.3)  # 2.7 s left once the slot came
    assert found.failure is not None and found.failure.reason == "timeout"
    assert web.requests == [] and usage.used["firecrawl"] == 0 and len(gate._started) == 0
