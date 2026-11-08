"""The archive chain and the factory (US-12): Arctic Shift first, PullPush only when it failed or found nothing."""

from __future__ import annotations

import httpx
import pytest

from app.contracts import SourceType
from app.db.breaker import CircuitBreaker
from app.research.reddit import (
    ArchiveRedditSource,
    ArcticShiftSource,
    IntervalLimiter,
    PullPushSource,
    RequestBudget,
    build_reddit_source,
    load_reddit_settings,
)
from app.research.reddit.cache import RedditCache
from tests.fakes.reddit import NOW, Archive, Clock, fixture, ok

AS_POSTS, AS_COMMENTS = "/api/posts/search", "/api/comments/search"
PP_POSTS, PP_COMMENTS = "/reddit/search/submission/", "/reddit/search/comment/"


def _chain(arctic: Archive, pullpush: Archive) -> ArchiveRedditSource:
    clock = Clock()
    common = {
        "salt": "s",
        "cache": RedditCache(folder=None, now=lambda: NOW),
        "now": lambda: NOW,
        "sleep": clock.sleep,
    }
    return ArchiveRedditSource(
        [
            ArcticShiftSource(
                arctic.client(),
                limiter=IntervalLimiter(1.0, clock=clock, sleep=clock.sleep),
                breaker=CircuitBreaker(3, 600, clock=clock),
                **common,  # type: ignore[arg-type]
            ),
            PullPushSource(
                pullpush.client(),
                limiter=IntervalLimiter(2.0, clock=clock, sleep=clock.sleep),
                breaker=CircuitBreaker(3, 30, clock=clock),
                **common,  # type: ignore[arg-type]
            ),
        ]
    )


def test_fresh_arctic_shift_data_is_used_and_pullpush_is_never_asked() -> None:
    arctic = Archive().on(AS_POSTS, ok(fixture("arctic_shift_posts.json")))
    pullpush = Archive().on(PP_POSTS, ok(fixture("pullpush_submissions.json")))
    result = _chain(arctic, pullpush).search_threads("laptop for coding", ["SuggestALaptop"])
    assert result.origin == "arctic_shift" and len(result.sources) == 2
    assert pullpush.requests == []


@pytest.mark.parametrize(
    "arctic_answer", [httpx.Response(503), ok({"data": []})], ids=["arctic-shift-failed", "arctic-shift-found-nothing"]
)
def test_pullpush_is_asked_when_arctic_shift_failed_or_found_nothing(arctic_answer: httpx.Response) -> None:
    arctic = Archive().on(AS_POSTS, arctic_answer)
    pullpush = Archive().on(PP_POSTS, ok(fixture("pullpush_submissions.json")))
    result = _chain(arctic, pullpush).search_threads("laptop for coding", ["SuggestALaptop"])
    assert result.origin == "pullpush" and result.failure is None
    assert result.coverage_note == "Reddit via older archive (to May 2025)"
    assert {s.source_detail for s in result.sources} == {"pullpush"}


def test_nothing_anywhere_but_one_answer_is_an_empty_result_not_a_failure() -> None:
    arctic = Archive().on(AS_POSTS, ok({"data": []}))
    pullpush = Archive().on(PP_POSTS, httpx.Response(522))
    result = _chain(arctic, pullpush).search_threads("laptop", ["laptops"])
    assert result.sources == () and result.failure is None and result.origin == "arctic_shift"


def test_both_archives_failing_reports_both_and_the_search_goes_on() -> None:
    arctic = Archive().on(AS_POSTS, httpx.Response(503))
    pullpush = Archive().on(PP_POSTS, httpx.ReadTimeout("slow"))
    result = _chain(arctic, pullpush).search_threads("laptop", ["laptops"])
    assert result.sources == ()
    assert result.failure is not None and result.failure.source_type is SourceType.REDDIT
    assert result.failure.reason == "error"
    assert result.failure.detail == "Arctic Shift: HTTP 503; PullPush: no reply within 10s"


def test_comments_follow_the_same_order() -> None:
    arctic = Archive().on(AS_POSTS, ok(fixture("arctic_shift_posts.json"))).on(AS_COMMENTS, httpx.Response(500))
    pullpush = Archive().on(PP_POSTS, ok({"data": [fixture("arctic_shift_posts.json")["data"][0]]}))
    pullpush.on(PP_COMMENTS, ok(fixture("arctic_shift_comments.json")))
    chain = _chain(arctic, pullpush)
    chain.search_threads("laptop", ["SuggestALaptop"])
    result = chain.get_comments("1qx7aaa")
    assert result.origin == "pullpush" and len(result.sources) == 4


def test_one_budget_covers_every_archive() -> None:
    arctic = Archive().on(AS_POSTS, httpx.Response(503))
    pullpush = Archive().on(PP_POSTS, ok(fixture("pullpush_submissions.json")))
    budget = RequestBudget(3)
    _chain(arctic, pullpush).search_threads("laptop", ["laptops", "SuggestALaptop"], budget=budget)
    assert budget.used == 3 and len(arctic.requests) + len(pullpush.requests) == 3


def test_an_empty_chain_is_refused() -> None:
    with pytest.raises(ValueError, match="at least one archive"):
        ArchiveRedditSource([])


# --- the factory ---------------------------------------------------------------------------------


def test_the_default_is_arctic_shift_then_pullpush() -> None:
    source = build_reddit_source(load_reddit_settings({}), httpx.Client(), RedditCache(folder=None))
    assert isinstance(source, ArchiveRedditSource)
    assert [a.name for a in source.archives] == ["arctic_shift", "pullpush"]


def test_reddit_archives_sets_the_order_and_the_urls() -> None:
    env = {"REDDIT_ARCHIVES": "pullpush", "PULLPUSH_BASE_URL": "https://pp.example.test", "REDDIT_USER_SALT": "s"}
    source = build_reddit_source(load_reddit_settings(env), httpx.Client(), RedditCache(folder=None))
    assert isinstance(source, ArchiveRedditSource)
    (only,) = source.archives
    assert isinstance(only, PullPushSource)
    assert only.http.base_url == "https://pp.example.test" and only.salt == "s"


def test_off_means_no_reddit_source() -> None:
    assert build_reddit_source(load_reddit_settings({"REDDIT_SOURCE": "off"})) is None


def test_a_fresh_archive_failing_stays_named_if_the_older_one_then_finds_nothing() -> None:
    arctic = Archive().on(AS_POSTS, httpx.Response(503))
    pullpush = Archive().on(PP_POSTS, ok({"data": []}))
    result = _chain(arctic, pullpush).search_threads("laptop", ["laptops"])
    assert result.sources == () and result.failure is None
    assert [f.reason for f in result.partial] == ["error"]


def test_an_older_archive_failing_after_a_fresh_one_found_nothing_is_not_named() -> None:
    arctic = Archive().on(AS_POSTS, ok({"data": []}))
    pullpush = Archive().on(PP_POSTS, httpx.Response(522))
    assert _chain(arctic, pullpush).search_threads("laptop", ["laptops"]).partial == ()


def test_threads_from_the_older_archive_name_only_a_cap() -> None:
    failed = Archive().on(AS_POSTS, httpx.Response(503))
    older = Archive().on(PP_POSTS, ok(fixture("pullpush_submissions.json")))
    assert _chain(failed, older).search_threads("laptop for coding", ["SuggestALaptop"]).partial == ()


def test_a_cap_is_named_even_when_another_failure_came_first() -> None:
    """One request left, and it fails. Every other subreddit and archive then says the cap, which must not be lost."""
    arctic = Archive().on(AS_POSTS, httpx.Response(503))
    pullpush = Archive().on(PP_POSTS, ok({"data": []}))
    result = _chain(arctic, pullpush).search_threads("laptop", ["laptops", "Monitors"], budget=RequestBudget(1))
    assert result.failure is not None and result.failure.reason == "error"
    assert "quota_exhausted" in {f.reason for f in result.partial}


def test_a_cap_reached_by_a_later_archive_after_a_fresh_one_found_nothing_is_named() -> None:
    arctic = Archive().on(AS_POSTS, ok({"data": []}))
    pullpush = Archive().on(PP_POSTS, ok({"data": []}))
    result = _chain(arctic, pullpush).search_threads("laptop", ["laptops"], budget=RequestBudget(1))
    assert [f.reason for f in result.partial] == ["quota_exhausted"]


def test_a_bad_archive_address_is_a_failure_not_a_crash() -> None:
    source = ArcticShiftSource(
        httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200))),
        base_url="https://a:b",
        cache=RedditCache(folder=None, now=lambda: NOW),
        limiter=IntervalLimiter(0),
        breaker=CircuitBreaker(3, 600),
        now=lambda: NOW,
    )
    result = source.search_threads("laptop", ["laptops"])
    assert result.failure is not None and result.failure.reason == "error"


def test_one_archive_alone_names_every_failure_when_all_its_subreddits_fail() -> None:
    arctic = Archive().on(AS_POSTS, httpx.Response(503))
    alone = _chain(arctic, Archive()).archives[0]
    result = alone.search_threads("laptop", ["laptops", "Monitors"], budget=RequestBudget(1))
    assert result.failure is not None and result.failure.reason == "error"
    assert [f.reason for f in result.partial] == ["quota_exhausted"]


def test_a_cap_is_named_even_when_the_older_archive_answers_from_its_cache() -> None:
    arctic = Archive().on(AS_POSTS, httpx.Response(503))
    older = Archive().on(PP_POSTS, ok(fixture("pullpush_submissions.json")))
    chain = _chain(arctic, older)
    assert chain.search_threads("laptop for coding", ["SuggestALaptop"]).sources
    again = chain.search_threads("laptop for coding", ["SuggestALaptop"], budget=RequestBudget(0))
    assert again.sources
    assert [f.reason for f in again.partial] == ["quota_exhausted"]
