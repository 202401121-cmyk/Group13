"""The PullPush fallback archive (US-12), against httpx.MockTransport and hand-written Pushshift-style fixtures."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx

from app.db.breaker import CircuitBreaker
from app.research.reddit import IntervalLimiter, PullPushSource
from app.research.reddit.cache import RedditCache
from tests.fakes.reddit import NOW, Archive, Clock, fixture, ok

SUBMISSIONS = "/reddit/search/submission/"
COMMENTS = "/reddit/search/comment/"


def _source(archive: Archive, clock: Clock | None = None) -> PullPushSource:
    clock = clock or Clock()
    return PullPushSource(
        archive.client(),
        base_url="https://pullpush.example.test",
        salt="test-salt",
        cache=RedditCache(folder=None, now=lambda: NOW),
        limiter=IntervalLimiter(2.0, clock=clock, sleep=clock.sleep),
        breaker=CircuitBreaker(3, 30, clock=clock),
        now=lambda: NOW,
        sleep=clock.sleep,
    )


def test_threads_are_mapped_and_labelled_with_how_old_the_archive_is() -> None:
    archive = Archive().on(SUBMISSIONS, ok(fixture("pullpush_submissions.json")))
    result = _source(archive).search_threads("best laptop for coding", ["SuggestALaptop"])

    assert result.failure is None and result.origin == "pullpush"
    assert [str(s.url) for s in result.sources] == [
        "https://www.reddit.com/r/SuggestALaptop/comments/1kq2aaa/",
        "https://www.reddit.com/r/SuggestALaptop/comments/1kq2ccc/",
    ]  # the removed thread is skipped
    assert all(s.source_detail == "pullpush" and s.fetched_at == NOW for s in result.sources)
    assert result.newest_at == datetime(2025, 5, 10, tzinfo=UTC)
    assert result.coverage_note == "Reddit via older archive (to May 2025)"
    assert "made_up_" not in json.dumps([s.model_dump(mode="json") for s in result.sources])


def test_the_request_sends_the_query_words_and_no_date_window_by_default() -> None:
    archive = Archive().on(SUBMISSIONS, ok({"data": []}))
    source = _source(archive)
    source.search_threads("best laptop for coding", ["SuggestALaptop"])
    assert str(archive.requests[0].url).startswith("https://pullpush.example.test/reddit/search/submission/?")
    assert dict(archive.requests[0].url.params) == {
        "subreddit": "SuggestALaptop",
        "q": "laptop coding",
        "size": "100",
        "sort": "desc",
        "sort_type": "created_utc",
    }
    source.search_threads("laptop", ["laptops"], after=datetime(2025, 1, 1, tzinfo=UTC))
    assert archive.requests[1].url.params["after"] == "1735689600"
    assert archive.requests[1].headers["User-Agent"].startswith("ShopSense/")


def test_nothing_found_has_no_coverage_note() -> None:
    result = _source(Archive().on(SUBMISSIONS, ok({"data": []}))).search_threads("laptop", ["laptops"])
    assert result.sources == () and result.failure is None and result.coverage_note is None


def test_comments_come_from_the_comment_search() -> None:
    archive = Archive().on(SUBMISSIONS, ok(fixture("pullpush_submissions.json")))
    archive.on(COMMENTS, ok(fixture("pullpush_comments.json")))
    source = _source(archive)
    source.search_threads("laptop", ["SuggestALaptop"])
    result = source.get_comments("1kq2aaa")
    assert [str(s.url) for s in result.sources] == [
        "https://www.reddit.com/r/SuggestALaptop/comments/1kq2aaa/_/mq1aaa1/"
    ]
    assert dict(archive.calls(COMMENTS)[0].url.params) == {
        "link_id": "1kq2aaa",
        "size": "100",
        "sort": "desc",
        "sort_type": "created_utc",
    }
    assert result.coverage_note == "Reddit via older archive (to May 2025)"


def test_an_unknown_thread_is_looked_up_by_id() -> None:
    first = fixture("pullpush_submissions.json")["data"][0]
    archive = Archive().on(SUBMISSIONS, ok({"data": [first]})).on(COMMENTS, ok(fixture("pullpush_comments.json")))
    result = _source(archive).get_comments("1kq2aaa")
    assert archive.calls(SUBMISSIONS)[0].url.params["ids"] == "1kq2aaa"
    assert len(result.sources) == 1


def test_requests_are_two_seconds_apart() -> None:
    clock = Clock()
    archive = Archive().on(SUBMISSIONS, ok({"data": []}))
    _source(archive, clock).search_threads("laptop", ["a1", "b2"])
    assert clock.slept == [2.0]


def test_a_rate_limit_is_retried_once_after_retry_after() -> None:
    clock = Clock()
    archive = Archive().on(SUBMISSIONS, httpx.Response(429, headers={"Retry-After": "5"}), ok({"data": []}))
    assert _source(archive, clock).search_threads("laptop", ["laptops"]).failure is None
    assert 5.0 in clock.slept and len(archive.requests) == 2


def test_a_422_is_not_a_rate_limit_for_pullpush() -> None:
    archive = Archive().on(SUBMISSIONS, httpx.Response(422))
    result = _source(archive).search_threads("laptop", ["laptops"])
    assert len(archive.requests) == 1
    assert result.failure is not None and result.failure.detail == "PullPush: HTTP 422"


def test_the_short_breaker_pauses_for_30_seconds() -> None:
    clock = Clock()
    archive = Archive().on(SUBMISSIONS, httpx.Response(522))
    source = _source(archive, clock)
    for _ in range(4):
        source.search_threads("laptop", ["laptops"])
    assert len(archive.requests) == 3
    clock.now += 31
    source.search_threads("laptop", ["laptops"])
    assert len(archive.requests) == 4  # one trial after the pause
