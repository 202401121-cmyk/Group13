"""Fault tests (US-12): a Reddit archive that is down never crashes or stalls a search."""

from __future__ import annotations

from time import monotonic

import httpx

from app.db.breaker import CircuitBreaker
from app.research.reddit import ArchiveRedditSource, IntervalLimiter, PullPushSource, RequestBudget
from app.research.reddit.arctic_shift import ArcticShiftSource
from app.research.reddit.cache import RedditCache
from tests.fakes.reddit import NOW, Archive, Clock


def _arctic(archive: Archive, clock: Clock) -> ArcticShiftSource:
    return ArcticShiftSource(
        archive.client(),
        cache=RedditCache(folder=None, now=lambda: NOW),
        limiter=IntervalLimiter(1.0, clock=clock, sleep=clock.sleep),
        breaker=CircuitBreaker(3, 600, clock=clock),
        now=lambda: NOW,
        sleep=clock.sleep,
    )


def test_arctic_shift_down_gives_a_clean_failure_then_stops_asking() -> None:
    clock = Clock()
    archive = Archive().on("/api/posts/search", httpx.ConnectError("refused"))
    archive.on("/api/comments/search", httpx.Response(502))
    source = _arctic(archive, clock)
    budget = RequestBudget(10)

    started = monotonic()
    threads = source.search_threads("laptop for coding", ["SuggestALaptop", "laptops", "IndiaTech"], budget=budget)
    comments = source.get_comments("1qx7aaa", budget=budget)
    later = source.search_threads("laptop", ["Monitors"], budget=budget)

    assert monotonic() - started < 1  # fake time: no real waiting
    assert threads.sources == () and threads.failure is not None and threads.failure.reason == "error"
    assert comments.failure is not None  # paused, so not even the thread lookup is sent
    assert later.failure is not None and "paused" in later.failure.detail
    assert len(archive.requests) == 3  # three failures opened the breaker; nothing more was sent
    assert budget.used == 3


def test_both_archives_down_the_search_still_finishes_with_no_reddit_evidence() -> None:
    clock = Clock()
    arctic = Archive().on("/api/posts/search", httpx.Response(503))
    pullpush = Archive().on("/reddit/search/submission/", httpx.Response(522))
    chain = ArchiveRedditSource(
        [
            _arctic(arctic, clock),
            PullPushSource(
                pullpush.client(),
                cache=RedditCache(folder=None, now=lambda: NOW),
                limiter=IntervalLimiter(2.0, clock=clock, sleep=clock.sleep),
                breaker=CircuitBreaker(3, 30, clock=clock),
                now=lambda: NOW,
                sleep=clock.sleep,
            ),
        ]
    )
    started = monotonic()
    result = chain.search_threads("laptop for coding", ["SuggestALaptop"], budget=RequestBudget(10))
    assert monotonic() - started < 1
    assert result.sources == () and result.failure is not None
    assert result.failure.detail == "Arctic Shift: HTTP 503; PullPush: HTTP 522"
