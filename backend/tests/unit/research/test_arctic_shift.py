"""The Arctic Shift Reddit source (US-12), against httpx.MockTransport and hand-written fixtures."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from app.contracts import SourceTier, SourceType
from app.db.breaker import CircuitBreaker
from app.research.reddit import IntervalLimiter, RequestBudget
from app.research.reddit.archive import comment_depth
from app.research.reddit.arctic_shift import COVERAGE_NOTE, ArcticShiftSource
from app.research.reddit.cache import RedditCache
from tests.fakes.reddit import NOW, Archive, Clock, fixture, ok

POSTS = "/api/posts/search"
COMMENTS = "/api/comments/search"
IDS = "/api/posts/ids"
SALT = "test-salt"
USERNAMES = ("made_up_", "AutoModerator")


def _source(
    archive: Archive,
    *,
    clock: Clock | None = None,
    cache: RedditCache | None = None,
    breaker: CircuitBreaker | None = None,
    salt: str | None = SALT,
) -> ArcticShiftSource:
    clock = clock or Clock()
    return ArcticShiftSource(
        archive.client(),
        base_url="https://arctic.example.test",
        salt=salt,
        cache=cache or RedditCache(folder=None, now=lambda: NOW),
        limiter=IntervalLimiter(1.0, clock=clock, sleep=clock.sleep),
        breaker=breaker or CircuitBreaker(3, 600, clock=clock),
        now=lambda: NOW,
        sleep=clock.sleep,
    )


def _no_usernames(text: str) -> None:
    for name in USERNAMES:
        assert name not in text


# --- threads -------------------------------------------------------------------------------------


def test_threads_are_mapped_to_sources_with_canonical_permalinks() -> None:
    archive = Archive().on(POSTS, ok(fixture("arctic_shift_posts.json")))
    result = _source(archive).search_threads("laptop for coding", ["SuggestALaptop"])

    assert result.failure is None and result.origin == "arctic_shift"
    assert result.coverage_note == COVERAGE_NOTE
    assert [str(s.url) for s in result.sources] == [
        "https://www.reddit.com/r/SuggestALaptop/comments/1qx7aaa/",
        "https://www.reddit.com/r/SuggestALaptop/comments/1qx7bbb/",
    ]  # removed, deleted, over-18, AutoModerator and broken items are all skipped
    first, second = result.sources
    assert (first.type, first.tier, first.community, first.via) == (
        SourceType.REDDIT,
        SourceTier.COMMUNITY,
        "r/SuggestALaptop",
        "api",
    )
    assert first.title == "Laptop for coding under 70k?"
    assert first.text.startswith("Laptop for coding under 70k?\n\nBudget is 70k INR.")
    assert (
        second.text == "Nova Book 14 or Kestrel Slim 15: which laptop for a CS student?"
    )  # a post with no body: its title
    assert first.published_at == datetime(2026, 11, 1, tzinfo=UTC)
    assert second.published_at == datetime(2026, 10, 28, tzinfo=UTC)  # the epoch came as a string
    assert result.newest_at == datetime(2026, 11, 1, tzinfo=UTC)
    assert all(s.fetched_at == NOW and s.source_detail == "arctic_shift" for s in result.sources)
    assert first.author_hash is not None and first.author_hash != second.author_hash


def test_the_request_lists_a_subreddits_newest_posts_in_a_date_window_with_our_user_agent() -> None:
    archive = Archive().on(POSTS, ok({"data": []}))
    _source(archive).search_threads("  laptop   for coding ", ["SuggestALaptop"], limit=5)
    (request,) = archive.requests
    assert str(request.url).startswith("https://arctic.example.test/api/posts/search?")
    assert dict(request.url.params) == {  # no title or query search: it is refused on busy subreddits
        "subreddit": "SuggestALaptop",
        "after": (NOW - timedelta(days=180)).date().isoformat(),
        "limit": "100",
        "sort": "desc",
    }
    assert request.headers["User-Agent"].startswith("ShopSense/")
    assert "IT314 student project; +https://github.com/202401121-cmyk/Group13" in request.headers["User-Agent"]


def test_threads_with_more_of_the_query_words_come_first() -> None:
    archive = Archive().on(POSTS, ok(fixture("arctic_shift_posts.json")))
    source = _source(archive)
    coding = source.search_threads("best laptop for coding", ["SuggestALaptop"])
    assert [s.title for s in coding.sources] == [
        "Laptop for coding under 70k?",  # laptop and coding
        "Nova Book 14 or Kestrel Slim 15: which laptop for a CS student?",  # laptop only
    ]
    student = source.search_threads("student laptop", ["SuggestALaptop"])
    assert student.sources[0].title.startswith("Nova Book 14")
    assert len(archive.requests) == 1  # one listing, cached, serves every query


def test_no_matching_thread_is_an_empty_answer_not_a_failure() -> None:
    archive = Archive().on(POSTS, ok(fixture("arctic_shift_posts.json")))
    result = _source(archive).search_threads("washing machine", ["SuggestALaptop"])
    assert result.sources == () and result.failure is None and result.origin == "arctic_shift"


def test_an_after_date_is_sent_as_given() -> None:
    archive = Archive().on(POSTS, ok({"data": []}))
    _source(archive).search_threads("laptop", ["laptops"], after=datetime(2026, 8, 1, tzinfo=UTC))
    assert archive.requests[0].url.params["after"] == "2026-08-01"


def test_no_username_appears_in_any_output_or_log(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    archive = Archive().on(POSTS, ok(fixture("arctic_shift_posts.json")))
    archive.on(COMMENTS, ok(fixture("arctic_shift_comments.json")))
    source = _source(archive)
    found = source.search_threads("laptop", ["SuggestALaptop"])
    comments = source.get_comments("1qx7aaa")
    _no_usernames(json.dumps([s.model_dump(mode="json") for s in (*found.sources, *comments.sources)]))
    _no_usernames(caplog.text)


def test_without_a_salt_no_author_hash_is_kept() -> None:
    archive = Archive().on(POSTS, ok(fixture("arctic_shift_posts.json")))
    result = _source(archive, salt=None).search_threads("laptop", ["SuggestALaptop"])
    assert result.sources and all(s.author_hash is None for s in result.sources)


def test_several_subreddits_are_merged_newest_first_and_capped() -> None:
    def posts(request: httpx.Request) -> httpx.Response:
        sub = request.url.params["subreddit"]
        day = {"laptops": 1793318400, "SuggestALaptop": 1793145600}[sub]
        return ok(
            {
                "data": [
                    {
                        "id": f"{sub[:3].lower()}1",
                        "subreddit": sub,
                        "title": "laptop",
                        "author": "made_up_x",
                        "created_utc": day,
                    }
                ]
            }
        )

    archive = Archive().on(POSTS, posts)
    result = _source(archive).search_threads("laptop", ["SuggestALaptop", "laptops", "r/bad name"], limit=1)
    assert len(archive.requests) == 2  # the bad name is never sent
    assert [s.community for s in result.sources] == ["r/laptops"]  # the newest one


def test_one_subreddit_failing_does_not_lose_the_others() -> None:
    def posts(request: httpx.Request) -> httpx.Response:
        if request.url.params["subreddit"] == "laptops":
            return httpx.Response(503)
        return ok(fixture("arctic_shift_posts.json"))

    result = _source(Archive().on(POSTS, posts)).search_threads("laptop", ["laptops", "SuggestALaptop"])
    assert result.failure is None and len(result.sources) == 2


def test_every_subreddit_failing_reports_the_failure() -> None:
    result = _source(Archive().on(POSTS, httpx.Response(500))).search_threads("laptop", ["laptops", "Monitors"])
    assert result.sources == () and result.failure is not None
    assert (result.failure.source_type, result.failure.reason) == (SourceType.REDDIT, "error")
    assert result.failure.detail == "Arctic Shift: HTTP 500"


def test_nothing_to_search_is_reported_without_a_request() -> None:
    archive = Archive()
    for query, subs in (("laptop", []), ("   ", ["laptops"]), ("laptop", ["r/laptops"])):
        result = _source(archive).search_threads(query, subs)
        assert result.failure is not None and result.failure.reason == "not_found"
    assert archive.requests == []


@pytest.mark.parametrize(
    ("answer", "reason", "detail"),
    [
        (httpx.ReadTimeout("slow"), "timeout", "Arctic Shift: no reply within 10s"),
        (httpx.ConnectError("refused"), "error", "Arctic Shift: request failed (ConnectError)"),
        (httpx.Response(403), "blocked", "Arctic Shift: HTTP 403 (refused)"),
        (httpx.Response(404), "not_found", "Arctic Shift: HTTP 404"),
        (httpx.Response(200, text="<html>busy</html>"), "error", "Arctic Shift: the reply was not JSON"),
        (httpx.Response(200, json={"error": "bad field"}), "error", "Arctic Shift: the reply had no data list"),
    ],
    ids=["timeout", "network", "403", "404", "not-json", "no-data"],
)
def test_failures_are_reported_never_raised(answer: Exception | httpx.Response, reason: str, detail: str) -> None:
    result = _source(Archive().on(POSTS, answer)).search_threads("laptop", ["laptops"])
    assert result.failure is not None
    assert (result.failure.reason, result.failure.detail) == (reason, detail)


# --- rate limits, the limiter, the breaker and the budget ------------------------------------------


@pytest.mark.parametrize("status", [429, 422])
def test_a_rate_limit_waits_for_the_reset_and_retries_once(status: int) -> None:
    clock = Clock()
    limited = httpx.Response(
        status, headers={"X-RateLimit-Reset": "7"}, json={"error": "Timeout. Maybe slow down a bit"}
    )
    archive = Archive().on(POSTS, limited, ok(fixture("arctic_shift_posts.json")))
    budget = RequestBudget(10)
    result = _source(archive, clock=clock).search_threads("laptop", ["SuggestALaptop"], budget=budget)
    assert result.failure is None and len(result.sources) == 2
    assert len(archive.requests) == 2 and budget.used == 2  # the retry is a request too
    assert 7.0 in clock.slept


def test_a_second_rate_limit_gives_up() -> None:
    archive = Archive().on(POSTS, httpx.Response(429, headers={"X-RateLimit-Reset": "1"}))
    result = _source(archive).search_threads("laptop", ["laptops"])
    assert len(archive.requests) == 2
    assert result.failure is not None and result.failure.reason == "rate_limited"


def test_a_reset_longer_than_30_seconds_is_not_waited_for() -> None:
    clock = Clock()
    archive = Archive().on(POSTS, httpx.Response(429, headers={"X-RateLimit-Reset": "45"}))
    result = _source(archive, clock=clock).search_threads("laptop", ["laptops"])
    assert len(archive.requests) == 1 and 45.0 not in clock.slept
    assert result.failure is not None and result.failure.reason == "rate_limited"


def test_a_rate_limit_without_a_reset_header_waits_briefly() -> None:
    clock = Clock()
    archive = Archive().on(POSTS, httpx.Response(422), ok({"data": []}))
    _source(archive, clock=clock).search_threads("laptop", ["laptops"])
    assert 2.0 in clock.slept


def test_requests_are_at_least_a_second_apart() -> None:
    clock = Clock()
    archive = Archive().on(POSTS, ok({"data": []}))
    _source(archive, clock=clock).search_threads("laptop", ["a1", "b2", "c3"])
    assert clock.slept == [1.0, 1.0]  # the first request goes at once


def test_the_shared_limiter_is_one_per_process() -> None:
    from app.research.reddit import arctic_shift

    one = ArcticShiftSource(httpx.Client(), cache=RedditCache(folder=None))
    two = ArcticShiftSource(httpx.Client(), cache=RedditCache(folder=None))
    assert one.http.limiter is two.http.limiter is arctic_shift.SHARED_LIMITER
    assert arctic_shift.SHARED_LIMITER.interval_s == 1.0


def test_three_failures_pause_arctic_shift_then_one_trial_request() -> None:
    clock = Clock()
    archive = Archive().on(POSTS, httpx.Response(503), httpx.Response(503), httpx.Response(503), ok({"data": []}))
    source = _source(archive, clock=clock)
    for _ in range(3):
        source.search_threads("laptop", ["laptops"])
    paused = source.search_threads("laptop", ["laptops"])
    assert len(archive.requests) == 3  # the fourth search sent nothing
    assert paused.failure is not None and paused.failure.detail == "Arctic Shift is paused after repeated failures"
    clock.now += 601
    assert source.search_threads("laptop", ["laptops"]).failure is None  # the trial works: back to normal
    assert source.breaker.state == "closed"


def test_the_search_budget_caps_requests() -> None:
    archive = Archive().on(POSTS, ok(fixture("arctic_shift_posts.json")))
    budget = RequestBudget(1)
    result = _source(archive).search_threads("laptop", ["SuggestALaptop", "laptops"], budget=budget)
    assert len(archive.requests) == 1
    assert result.failure is None and len(result.sources) == 2  # the first subreddit's threads still count
    spent = _source(archive).search_threads("laptop", ["Monitors"], budget=budget)
    assert spent.failure is not None and spent.failure.reason == "quota_exhausted"
    assert len(archive.requests) == 1


# --- the 24-hour cache ---------------------------------------------------------------------------


def test_a_repeated_search_comes_from_the_cache_for_24_hours() -> None:
    now = [NOW]
    cache = RedditCache(folder=None, now=lambda: now[0])
    archive = Archive().on(POSTS, ok(fixture("arctic_shift_posts.json")))
    source = _source(archive, cache=cache)
    budget = RequestBudget(10)
    first = source.search_threads("laptop", ["SuggestALaptop"], budget=budget)
    again = source.search_threads("Laptop", ["suggestalaptop"], budget=budget)
    assert again.sources == first.sources and len(archive.requests) == 1 and budget.used == 1  # cached: free
    now[0] += timedelta(hours=24)
    source.search_threads("laptop", ["SuggestALaptop"])
    assert len(archive.requests) == 2


def test_the_disk_cache_keeps_cleaned_sources_and_no_usernames(tmp_path: Path) -> None:
    archive = Archive().on(POSTS, ok(fixture("arctic_shift_posts.json")))
    _source(archive, cache=RedditCache(folder=tmp_path, now=lambda: NOW)).search_threads("laptop", ["SuggestALaptop"])
    (saved,) = tmp_path.glob("*.json")
    _no_usernames(saved.read_text(encoding="utf-8"))

    fresh = _source(Archive(), cache=RedditCache(folder=tmp_path, now=lambda: NOW))  # a restart: memory is empty
    result = fresh.search_threads("laptop", ["SuggestALaptop"])
    assert len(result.sources) == 2 and result.failure is None


def test_a_broken_cache_file_is_ignored(tmp_path: Path) -> None:
    archive = Archive().on(POSTS, ok(fixture("arctic_shift_posts.json")))
    _source(archive, cache=RedditCache(folder=tmp_path, now=lambda: NOW)).search_threads("laptop", ["SuggestALaptop"])
    (saved,) = tmp_path.glob("*.json")
    saved.write_text("{not json", encoding="utf-8")
    again = Archive().on(POSTS, ok(fixture("arctic_shift_posts.json")))
    result = _source(again, cache=RedditCache(folder=tmp_path, now=lambda: NOW)).search_threads(
        "laptop", ["SuggestALaptop"]
    )
    assert len(again.requests) == 1 and len(result.sources) == 2


def test_failures_are_never_cached() -> None:
    archive = Archive().on(POSTS, httpx.Response(500), ok(fixture("arctic_shift_posts.json")))
    source = _source(archive)
    assert source.search_threads("laptop", ["SuggestALaptop"]).failure is not None
    assert source.search_threads("laptop", ["SuggestALaptop"]).failure is None


# --- comments ------------------------------------------------------------------------------------


def test_comments_are_read_three_deep_skipping_gone_and_bot_comments() -> None:
    archive = Archive().on(POSTS, ok(fixture("arctic_shift_posts.json")))
    archive.on(COMMENTS, ok(fixture("arctic_shift_comments.json")))
    source = _source(archive)
    source.search_threads("laptop", ["SuggestALaptop"])

    result = source.get_comments("t3_1qx7aaa")

    assert [str(s.url).split("/")[-2] for s in result.sources] == ["m1aaaa1", "m1aaaa2", "m1aaaa3", "m1aaab1"]
    first = result.sources[0]
    assert str(first.url) == "https://www.reddit.com/r/SuggestALaptop/comments/1qx7aaa/_/m1aaaa1/"
    assert first.title == "Laptop for coding under 70k?"  # the thread's title
    assert first.text.startswith("I bought the Nova Book 14 last month.")
    assert first.published_at == datetime(2026, 10, 30, tzinfo=UTC)
    assert result.sources[0].author_hash == result.sources[2].author_hash  # the same (made-up) person twice
    assert dict(archive.calls(COMMENTS)[0].url.params) == {"link_id": "1qx7aaa", "limit": "100", "sort": "desc"}
    assert archive.calls(IDS) == []  # the thread was known from the search
    assert result.coverage_note == COVERAGE_NOTE and result.origin == "arctic_shift"


def test_depth_one_reads_only_replies_to_the_thread() -> None:
    archive = Archive().on(IDS, ok({"data": [fixture("arctic_shift_posts.json")["data"][0]]}))
    archive.on(COMMENTS, ok(fixture("arctic_shift_comments.json")))
    result = _source(archive).get_comments("1qx7aaa", depth=1)
    assert [str(s.url).split("/")[-2] for s in result.sources] == ["m1aaaa1"]


def test_an_unknown_thread_is_looked_up_first() -> None:
    archive = Archive().on(IDS, ok({"data": [fixture("arctic_shift_posts.json")["data"][0]]}))
    archive.on(COMMENTS, ok(fixture("arctic_shift_comments.json")))
    budget = RequestBudget(10)
    result = _source(archive).get_comments("1qx7aaa", budget=budget)
    assert dict(archive.calls(IDS)[0].url.params) == {"ids": "1qx7aaa"}
    assert budget.used == 2 and len(result.sources) == 4


def test_an_over_18_thread_has_its_comments_skipped() -> None:
    archive = Archive().on(IDS, ok({"data": [fixture("arctic_shift_posts.json")["data"][4]]}))
    result = _source(archive).get_comments("1qx7eee")
    assert result.sources == () and result.failure is None
    assert archive.calls(COMMENTS) == []


@pytest.mark.parametrize(("ids_answer", "thread"), [(ok({"data": []}), "1qx7zzz"), (None, "../etc")])
def test_a_missing_or_bad_thread_is_not_found(ids_answer: httpx.Response | None, thread: str) -> None:
    archive = Archive()
    if ids_answer is not None:
        archive.on(IDS, ids_answer)
    result = _source(archive).get_comments(thread)
    assert result.failure is not None and result.failure.reason == "not_found"


def test_comment_depth() -> None:
    parents: dict[str | None, str | None] = {"a": "t3_x", "b": "t1_a", "c": "t1_b"}
    assert comment_depth("t3_x", parents) == 1
    assert comment_depth("t1_a", parents) == 2
    assert comment_depth("t1_c", parents) == 4
    assert comment_depth("t1_unknown", parents) == 2
    assert comment_depth(None, parents) == 1
    loop: dict[str | None, str | None] = {"a": "t1_b", "b": "t1_a"}
    assert comment_depth("t1_a", loop) == 3  # a loop in bad data ends, it never hangs
