"""The official Reddit source (US-12) with a fake PRAW client. Reddit's API is never called."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from app.db.breaker import CircuitBreaker
from app.research.reddit import OfficialRedditSource, RequestBudget, build_reddit_source, load_reddit_settings
from app.research.reddit.cache import RedditCache
from app.research.reddit.official import failure_for
from tests.fakes.reddit import NOW, Clock

T1 = 1793491200.0  # 1 Nov 2026
T0 = 1793145600.0  # 28 Oct 2026


def _who(name: str | None) -> Any:
    return None if name is None else SimpleNamespace(name=name)


def submission(sid: str, title: str, *, author: str | None = "made_up_api_one", **extra: Any) -> Any:
    fields = {
        "id": sid,
        "subreddit": SimpleNamespace(display_name="SuggestALaptop"),
        "title": title,
        "selftext": "",
        "author": _who(author),
        "created_utc": T1,
        "over_18": False,
        "removed_by_category": None,
    }
    return SimpleNamespace(**{**fields, **extra})


def comment(cid: str, parent: str, body: str, author: str | None = "made_up_api_two") -> Any:
    return SimpleNamespace(id=cid, parent_id=parent, body=body, author=_who(author), created_utc=T0)


@dataclass
class FakeComments:
    items: list[Any]
    replaced: list[int | None] = field(default_factory=list)

    def replace_more(self, limit: int | None = 32) -> list[Any]:
        self.replaced.append(limit)
        return []

    def list(self) -> list[Any]:
        return self.items


@dataclass
class FakeReddit:
    """The few PRAW calls the source makes: subreddit(name).search(...) and submission(id=...)."""

    posts: list[Any] = field(default_factory=list)
    thread: Any = None
    error: Exception | None = None
    searches: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)
    read_only: bool = True

    def subreddit(self, name: str) -> Any:
        def search(query: str, **kwargs: Any) -> list[Any]:
            self.searches.append((name, query, kwargs))
            if self.error is not None:
                raise self.error
            return self.posts

        return SimpleNamespace(search=search)

    def submission(self, id: str) -> Any:  # PRAW's own keyword
        if self.error is not None:
            raise self.error
        return self.thread


def _source(reddit: FakeReddit, clock: Clock | None = None) -> OfficialRedditSource:
    clock = clock or Clock()
    return OfficialRedditSource(
        reddit,
        salt="test-salt",
        cache=RedditCache(folder=None, now=lambda: NOW),
        breaker=CircuitBreaker(3, 600, clock=clock),
        now=lambda: NOW,
    )


def test_search_maps_submissions_and_skips_what_we_never_read() -> None:
    reddit = FakeReddit(
        posts=[
            submission("1abcaaa", "Laptop for coding?", selftext="Python and Docker."),
            submission("1abcbbb", "Gone", author=None),
            submission("1abcccc", "Over 18", over_18=True),
            submission("1abcddd", "Removed", removed_by_category="moderator"),
            submission("1abceee", "Rules", author="AutoModerator"),
            SimpleNamespace(id=None),  # a broken object is skipped
        ]
    )
    result = _source(reddit).search_threads("best laptop for coding", ["SuggestALaptop"], limit=10)

    assert result.failure is None and result.origin == "official" and result.coverage_note is None
    (only,) = result.sources
    assert str(only.url) == "https://www.reddit.com/r/SuggestALaptop/comments/1abcaaa/"
    assert only.text == "Laptop for coding?\n\nPython and Docker."
    assert (only.source_detail, only.community, only.fetched_at) == ("official", "r/SuggestALaptop", NOW)
    assert only.published_at == datetime(2026, 11, 1, tzinfo=UTC)
    assert only.author_hash is not None
    assert "made_up_" not in json.dumps(only.model_dump(mode="json"))
    assert reddit.searches == [("SuggestALaptop", "laptop coding", {"sort": "new", "time_filter": "year", "limit": 10})]


def test_after_drops_older_threads() -> None:
    reddit = FakeReddit(posts=[submission("1abcaaa", "laptop", created_utc=T0), submission("1abcbbb", "laptop")])
    result = _source(reddit).search_threads("laptop", ["laptops"], after=datetime(2026, 10, 30, tzinfo=UTC))
    assert [str(s.url).split("/")[-2] for s in result.sources] == ["1abcbbb"]


def test_comments_are_read_to_a_depth_without_extra_requests() -> None:
    comments = FakeComments(
        [
            comment("c1", "t3_1abcaaa", "Battery is great."),
            comment("c2", "t1_c1", "Agreed."),
            comment("c3", "t1_c2", "Mine too."),
            comment("c4", "t1_c3", "Too deep."),
            comment("c5", "t3_1abcaaa", "[removed]"),
            comment("c6", "t3_1abcaaa", "Account gone", author=None),
        ]
    )
    reddit = FakeReddit(thread=submission("1abcaaa", "Laptop for coding?", comments=comments))
    result = _source(reddit).get_comments("t3_1abcaaa", depth=3)
    assert [str(s.url).split("/")[-2] for s in result.sources] == ["c1", "c2", "c3"]
    assert {s.title for s in result.sources} == {"Laptop for coding?"}
    assert comments.replaced == [0]


def test_an_over_18_thread_is_not_read() -> None:
    comments = FakeComments([comment("c1", "t3_1abcaaa", "text")])
    reddit = FakeReddit(thread=submission("1abcaaa", "x", over_18=True, comments=comments))
    result = _source(reddit).get_comments("1abcaaa")
    assert result.sources == () and result.failure is None and comments.replaced == []


class TooManyRequests(Exception):  # named like prawcore's, which is all failure_for looks at
    pass


class NotFound(Exception):
    pass


class Forbidden(Exception):
    pass


class RequestException(Exception):
    pass


class SpecialForbidden(Forbidden):
    pass


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (TooManyRequests("secret detail"), "rate_limited"),
        (NotFound(), "not_found"),
        (Forbidden(), "blocked"),
        (SpecialForbidden(), "blocked"),
        (RequestException(), "error"),
        (TimeoutError(), "timeout"),
        (RuntimeError("boom"), "error"),
    ],
)
def test_api_errors_become_failures_without_their_message(error: Exception, reason: str) -> None:
    result = _source(FakeReddit(error=error)).search_threads("laptop", ["laptops"])
    assert result.failure is not None and result.failure.reason == reason
    assert "secret" not in result.failure.detail and "boom" not in result.failure.detail
    assert failure_for(error).reason == reason


def test_the_breaker_and_the_budget_apply_too() -> None:
    clock = Clock()
    reddit = FakeReddit(error=RequestException())
    source = _source(reddit, clock)
    for _ in range(4):
        source.search_threads("laptop", ["laptops"])
    assert len(reddit.searches) == 3  # paused after three failures

    budget = RequestBudget(1)
    healthy = _source(FakeReddit(posts=[submission("1abcaaa", "laptop")]))
    assert healthy.search_threads("laptop", ["a1", "b2"], budget=budget).failure is None
    assert budget.used == 1
    assert healthy.get_comments("1abcaaa", budget=budget).failure is not None


def test_answers_are_cached() -> None:
    reddit = FakeReddit(posts=[submission("1abcaaa", "laptop")])
    source = _source(reddit)
    source.search_threads("laptop", ["laptops"])
    source.search_threads("laptop", ["laptops"])
    assert len(reddit.searches) == 1


# --- choosing it ---------------------------------------------------------------------------------

OFFICIAL_ENV = {
    "REDDIT_SOURCE": "official",
    "REDDIT_CLIENT_ID": "fake-client-id",
    "REDDIT_CLIENT_SECRET": "fake-client-secret",
    "REDDIT_USER_AGENT": "script:shopsense:0.1 (by /u/someone)",
}


def test_official_with_all_three_settings_builds_a_read_only_praw_client() -> None:
    source = build_reddit_source(load_reddit_settings(OFFICIAL_ENV), cache=RedditCache(folder=None))
    assert isinstance(source, OfficialRedditSource)
    assert source.reddit.read_only is True
    assert "fake-client-secret" not in repr(source.reddit)


def test_official_without_its_settings_uses_the_archives() -> None:
    env = {k: v for k, v in OFFICIAL_ENV.items() if k != "REDDIT_CLIENT_SECRET"}
    source = build_reddit_source(load_reddit_settings(env), cache=RedditCache(folder=None))
    assert source is not None and source.name == "archive"
