"""US-24: the Serper shopping client, with S4-12's one-retry rule (S4-26). A fake transport and a fake clock: no
real API is called and nothing waits for real time."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.llm.retry import RetryClock
from app.research.cache import ResearchCache
from app.research.reddit import IntervalLimiter
from app.research.settings import ProviderCap
from app.research.shopping import SHOPPING_URL, Listing, SerperShopping, read_listings
from app.research.usage import SearchUsage, UsageBook
from tests.fakes.retry import NoJitter

KEY = "made-up-serper-key"
FETCHED = datetime(2026, 11, 15, 9, 30, tzinfo=UTC)
ITEM = {
    "title": "ASUS Vivobook 15 X1504VA (16GB RAM/512GB SSD) Laptop",
    "source": "Made-up Store",
    "link": "https://store.example/asus-vivobook-15-x1504va",
    "price": "₹52,990.00",
    "delivery": "Free delivery",
    "position": 1,
}


class Now:
    def __init__(self) -> None:
        self.at = FETCHED

    def __call__(self) -> datetime:
        return self.at


def _usage(per_search: int = 6) -> SearchUsage:
    return UsageBook({"serper": ProviderCap(per_search=per_search, per_day=60)}, path=None).search()


class Waits:
    """A fake sleep, and the time the commerce stage still has (S4-26: a retry goes only within it)."""

    def __init__(self, left: float = 15.0) -> None:
        self.left = left
        self.slept: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.left -= seconds

    def clock(self) -> RetryClock:
        return RetryClock(time_left=lambda: self.left, sleep=self.sleep, rng=NoJitter())


def _shopping(
    answer: httpx.Response | Exception | list[httpx.Response | Exception],
    *,
    key: str | None = KEY,
    cache: ResearchCache | None = None,
    now: Now | None = None,
    waits: Waits | None = None,
) -> tuple[SerperShopping, list[httpx.Request]]:
    """A client whose requests get `answer`, or each answer of a list in turn (the last one repeats)."""
    asked: list[httpx.Request] = []
    answers = list(answer) if isinstance(answer, list) else [answer]

    def handle(request: httpx.Request) -> httpx.Response:
        asked.append(request)
        reply = answers.pop(0) if len(answers) > 1 else answers[0]
        if isinstance(reply, Exception):
            raise reply
        return reply

    clock = now or Now()
    client = SerperShopping(
        httpx.Client(transport=httpx.MockTransport(handle)),
        key,
        cache=cache or ResearchCache(folder=None, now=clock),
        limiter=IntervalLimiter(0),
        now=clock,
        retry=(waits or Waits()).clock(),
    )
    return client, asked


def _ok(*items: object) -> httpx.Response:
    return httpx.Response(200, json={"searchParameters": {"q": "x"}, "shopping": list(items)})


def test_the_request_is_a_post_to_shopping_with_the_key_in_its_header() -> None:
    client, asked = _shopping(_ok(ITEM))
    found = client.search("ASUS Vivobook 15 X1504VA", _usage())
    (request,) = asked
    assert (request.method, str(request.url)) == ("POST", SHOPPING_URL)
    assert request.headers["X-API-KEY"] == KEY
    assert json.loads(request.content) == {"q": "ASUS Vivobook 15 X1504VA", "gl": "in", "hl": "en"}
    assert found.failure is None and found.fetched_at == FETCHED and not found.from_cache
    assert found.listings == (
        Listing(
            title=ITEM["title"],
            link=ITEM["link"],
            store="Made-up Store",
            price="₹52,990.00",
            delivery="Free delivery",
            position=1,
        ),
    )


def test_the_key_is_never_shown(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    client, _ = _shopping(httpx.Response(500))
    found = client.search("a laptop", _usage())
    assert KEY not in repr(client) and KEY not in caplog.text and KEY not in found.detail


def test_a_result_needs_a_title_and_a_web_link_and_its_text_is_trimmed() -> None:
    found = read_listings(
        [
            ITEM,
            {"title": "", "link": "https://store.example/a"},
            {"title": "No link"},
            {"title": "Not a web link", "link": "javascript:alert(1)"},
            "not an object",
            {"title": "  Spaced   out  ", "link": "https://store.example/b", "price": 52990, "source": None},
        ]
    )
    assert [x.title for x in found] == [ITEM["title"], "Spaced out"]
    assert found[1].price == "52990" and found[1].store == "" and found[1].position == 2


@pytest.mark.parametrize(
    ("answer", "reason"),
    [
        (httpx.Response(401), "blocked"),
        (httpx.Response(403), "blocked"),
        (httpx.Response(402), "quota_exhausted"),
        (httpx.Response(429), "rate_limited"),
        (httpx.Response(503), "error"),
        (httpx.Response(200, text="not json"), "error"),
        (httpx.Response(200, json={"organic": []}), "error"),
        (httpx.ReadTimeout("slow"), "timeout"),
        (httpx.ConnectError("refused"), "error"),
    ],
)
def test_each_failure_is_a_reason_never_an_exception(answer: httpx.Response | Exception, reason: str) -> None:
    client, _ = _shopping(answer)
    found = client.search("a laptop", _usage())
    assert found.failure == reason and found.listings == ()


def test_with_no_key_nothing_is_sent() -> None:
    client, asked = _shopping(_ok(ITEM), key=None)
    assert client.search("a laptop", _usage()).failure == "blocked"
    assert asked == []


def test_a_used_up_cap_sends_nothing() -> None:
    client, asked = _shopping(_ok(ITEM))
    assert client.search("a laptop", _usage(per_search=0)).failure == "quota_exhausted"
    assert asked == []


def test_each_request_sent_counts_one_serper_unit() -> None:
    client, asked = _shopping(httpx.Response(503))
    usage = _usage()
    client.search("a laptop", usage)
    assert len(asked) == 2 and usage.used["serper"] == 2  # the 503 and its one retry: both were sent


def test_a_timeout_is_retried_once_and_counts_two_units() -> None:
    waits = Waits()
    client, asked = _shopping([httpx.ReadTimeout("slow"), _ok(ITEM)], waits=waits)
    usage = _usage()
    found = client.search("ASUS Vivobook 15 X1504VA", usage)
    assert found.failure is None and found.listings[0].title == ITEM["title"]
    assert len(asked) == 2 and usage.used["serper"] == 2
    assert len(waits.slept) == 1  # one short wait before the retry


@pytest.mark.parametrize("first", [httpx.Response(500), httpx.Response(503), httpx.ConnectError("refused")])
def test_a_5xx_or_broken_connection_is_retried_once(first: httpx.Response | Exception) -> None:
    client, asked = _shopping([first, first, _ok(ITEM)])
    assert client.search("a laptop", _usage()).failure is not None  # failed twice: no third try
    assert len(asked) == 2


@pytest.mark.parametrize("status", [400, 401, 402, 403, 404])
def test_a_403_is_never_retried(status: int) -> None:
    client, asked = _shopping([httpx.Response(status), _ok(ITEM)])
    usage = _usage()
    assert client.search("a laptop", usage).failure is not None
    assert len(asked) == 1 and usage.used["serper"] == 1


def test_a_429_waits_its_stated_time_once() -> None:
    waits = Waits()
    client, asked = _shopping([httpx.Response(429, headers={"Retry-After": "2"}), _ok(ITEM)], waits=waits)
    assert client.search("a laptop", _usage()).failure is None
    assert len(asked) == 2 and waits.slept == [pytest.approx(2.0)]

    unsaid, asked_once = _shopping([httpx.Response(429), _ok(ITEM)])  # no stated wait: never retried
    assert unsaid.search("a laptop", _usage()).failure == "rate_limited"
    assert len(asked_once) == 1


def test_no_retry_past_the_stages_time() -> None:
    client, asked = _shopping([httpx.ReadTimeout("slow"), _ok(ITEM)], waits=Waits(left=5.0))
    assert client.search("a laptop", _usage()).failure == "timeout"  # a retry wouldn't finish in time
    assert len(asked) == 1


def test_a_retry_needs_a_unit_of_the_cap() -> None:
    client, asked = _shopping([httpx.Response(503), _ok(ITEM)])
    usage = _usage(per_search=1)
    assert client.search("a laptop", usage).failure == "error"
    assert len(asked) == 1  # the cap's last unit went to the first request


def test_a_cache_hit_makes_no_call_and_keeps_its_first_time(tmp_path: object) -> None:
    now = Now()
    cache = ResearchCache(folder=tmp_path, now=now)  # type: ignore[arg-type]
    first, asked = _shopping(_ok(ITEM), cache=cache, now=now)
    usage = _usage()
    first.search("ASUS Vivobook 15 X1504VA", usage)
    now.at = FETCHED + timedelta(hours=3)
    again, asked_again = _shopping(_ok(ITEM), cache=ResearchCache(folder=tmp_path, now=now), now=now)  # type: ignore[arg-type]
    found = again.search("ASUS Vivobook 15 X1504VA", usage)
    assert asked_again == [] and len(asked) == 1
    assert found.from_cache and found.fetched_at == FETCHED  # a cached price keeps its true age (DR-04)
    assert found.listings[0].title == ITEM["title"]
    assert usage.used["serper"] == 1  # a cache hit is free


def test_a_failure_is_never_cached(tmp_path: object) -> None:
    now = Now()
    answers = [httpx.Response(503), httpx.Response(503), _ok(ITEM)]  # the first search fails twice
    cache = ResearchCache(folder=tmp_path, now=now)  # type: ignore[arg-type]
    client = SerperShopping(
        httpx.Client(transport=httpx.MockTransport(lambda _: answers.pop(0))),
        KEY,
        cache=cache,
        limiter=IntervalLimiter(0),
        now=now,
        retry=Waits().clock(),
    )
    usage = _usage()
    assert client.search("a laptop", usage).failure == "error"
    assert client.search("a laptop", usage).failure is None


def test_a_damaged_cache_entry_is_asked_again(tmp_path: object) -> None:
    now = Now()
    cache = ResearchCache(folder=tmp_path, now=now)  # type: ignore[arg-type]
    cache.put_text(("serper_shopping", "a laptop", "in"), "{not json")
    client, asked = _shopping(_ok(ITEM), cache=cache, now=now)
    assert client.search("a laptop", _usage()).failure is None
    assert len(asked) == 1
