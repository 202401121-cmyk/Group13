"""Reddit threads with their comments (US-12): upvotes, a tree, and authors only ever as salted hashes (D-53)."""

from __future__ import annotations

import hashlib

import httpx
import pytest

from app.contracts import Source
from app.contracts.research import MAX_COMMENT_CHARS
from app.db.breaker import CircuitBreaker
from app.research.reddit import IntervalLimiter, RedditResult, RequestBudget
from app.research.reddit.arctic_shift import ArcticShiftSource
from app.research.reddit.base import failure
from app.research.reddit.cache import RedditCache
from app.research.settings import ProviderCap, ResearchSettings
from app.research.threads import CountingBudget, ThreadProgress, find_threads, read_comments
from app.research.usage import UsageBook
from tests.fakes.reddit import NOW, Archive, Clock, fixture, ok
from tests.fakes.research import FakeReddit, thread

POSTS = "/api/posts/search"
COMMENTS = "/api/comments/search"
SALT = "test-salt"
USERNAMES = ("made_up_", "AutoModerator")


def _source(archive: Archive, *, salt: str | None = SALT, cache: RedditCache | None = None) -> ArcticShiftSource:
    clock = Clock()
    return ArcticShiftSource(
        archive.client(),
        base_url="https://arctic.example.test",
        salt=salt,
        cache=cache or RedditCache(folder=None, now=lambda: NOW),
        limiter=IntervalLimiter(1.0, clock=clock, sleep=clock.sleep),
        breaker=CircuitBreaker(3, 600, clock=clock),
        now=lambda: NOW,
        sleep=clock.sleep,
    )


def _archive() -> Archive:
    return (
        Archive()
        .on(POSTS, ok(fixture("arctic_shift_posts.json")))
        .on(COMMENTS, ok(fixture("arctic_shift_comments.json")))
    )


def _budget(per_search: int = 10) -> CountingBudget:
    usage = UsageBook({"reddit_archive": ProviderCap(per_search=per_search, per_day=500)}, path=None).search()
    return CountingBudget(usage)


def _first_thread(archive: Archive, **kwargs: object) -> object:
    source = _source(archive, **kwargs)  # type: ignore[arg-type]
    return source, source.search_threads("laptop for coding", ["SuggestALaptop"]).sources[0]


def test_threads_carry_url_and_fetch_time() -> None:
    _, first = _first_thread(_archive())
    assert str(first.url).startswith("https://www.reddit.com/r/")  # type: ignore[attr-defined]
    assert first.fetched_at == NOW  # type: ignore[attr-defined]
    assert first.community  # type: ignore[attr-defined]
    assert first.site is None  # type: ignore[attr-defined]


def test_upvotes_and_comment_count_are_read() -> None:
    archive = Archive().on(
        POSTS,
        ok(
            {
                "data": [
                    {
                        "id": "1aaaaaa",
                        "subreddit": "laptops",
                        "title": "Laptop for coding",
                        "selftext": "Which one?",
                        "author": "made_up_asker",
                        "created_utc": 1793318400,
                        "score": 42,
                        "num_comments": 7,
                    }
                ]
            }
        ),
    )
    item = _source(archive).search_threads("laptop", ["laptops"]).sources[0]
    assert item.upvotes == 42
    assert item.comment_count == 7


@pytest.mark.parametrize("score", ["many", None, {"a": 1}])
def test_an_odd_score_never_loses_the_thread(score: object) -> None:
    post = {
        "id": "1aaaaaa",
        "subreddit": "laptops",
        "title": "Laptop for coding",
        "author": "made_up_asker",
        "created_utc": 1793318400,
        "score": score,
        "num_comments": score,
    }
    item = _source(Archive().on(POSTS, ok({"data": [post]}))).search_threads("laptop", ["laptops"]).sources[0]
    assert item.upvotes is None
    assert item.comment_count is None


def test_comment_authors_are_only_ever_salted_hashes() -> None:
    source, first = _first_thread(_archive())
    result = source.with_comments(first)  # type: ignore[attr-defined]
    updated = result.sources[0]
    assert updated.comments
    expected = hashlib.sha256(f"{SALT}:made_up_owner_one".encode()).hexdigest()
    assert expected in {c.author_hash for c in updated.comments}
    dumped = updated.model_dump_json()
    for name in USERNAMES:
        assert name not in dumped


def test_without_a_salt_no_author_hash_is_kept() -> None:
    source, first = _first_thread(_archive(), salt=None)
    updated = source.with_comments(first).sources[0]  # type: ignore[attr-defined]
    assert updated.comments
    assert all(c.author_hash is None for c in updated.comments)
    for name in USERNAMES:
        assert name not in updated.model_dump_json()


def test_comment_depth_and_parent_are_kept() -> None:
    source, first = _first_thread(_archive())
    comments = {c.id: c for c in source.with_comments(first).sources[0].comments}  # type: ignore[attr-defined]
    assert comments["m1aaaa1"].depth == 1
    assert comments["m1aaaa2"].depth == 2
    assert comments["m1aaaa2"].parent_id == "m1aaaa1"
    assert comments["m1aaaa3"].depth == 3
    assert comments["m1aaaa1"].upvotes == 12
    assert comments["m1aaaa2"].upvotes == 0  # no score sent


def test_comment_text_is_capped() -> None:
    long = {
        "id": "m1long1",
        "link_id": "t3_1qx7aaa",
        "parent_id": "t3_1qx7aaa",
        "subreddit": "SuggestALaptop",
        "body": "word " * 2000,
        "author": "made_up_owner_one",
        "created_utc": 1793318400,
    }
    archive = _archive().on(COMMENTS, ok({"data": [long]}))
    source, first = _first_thread(archive)
    comment = source.with_comments(first).sources[0].comments[0]  # type: ignore[attr-defined]
    assert len(comment.text) <= MAX_COMMENT_CHARS


def test_gone_and_bot_comments_are_skipped_and_too_deep_replies_too() -> None:
    def comment(cid: str, parent: str, **extra: object) -> dict[str, object]:
        return {
            "id": cid,
            "link_id": "t3_1qx7aaa",
            "parent_id": parent,
            "subreddit": "SuggestALaptop",
            "body": "Made-up text.",
            "author": "made_up_owner",
            "created_utc": 1793318400,
            **extra,
        }

    data = [
        comment("m1", "t3_1qx7aaa"),
        comment("m2", "t3_1qx7aaa", body="[deleted]"),
        comment("m3", "t3_1qx7aaa", author="[deleted]"),
        comment("m4", "t3_1qx7aaa", author="AutoModerator"),
        comment("m5", "t1_m1"),
        comment("m6", "t1_m5"),
        comment("m7", "t1_m6"),
    ]
    source, first = _first_thread(_archive().on(COMMENTS, ok({"data": data})))
    kept = {c.id for c in source.with_comments(first).sources[0].comments}  # type: ignore[attr-defined]
    assert kept == {"m1", "m5", "m6"}


def test_comments_are_cached_with_no_second_request() -> None:
    archive = _archive()
    source, first = _first_thread(archive)
    source.with_comments(first)  # type: ignore[attr-defined]
    source.with_comments(first)  # type: ignore[attr-defined]
    assert len(archive.calls(COMMENTS)) == 1


def test_a_comment_failure_is_reported_and_the_thread_is_not_lost() -> None:
    archive = _archive().on(COMMENTS, httpx.Response(500))
    source, first = _first_thread(archive)
    result = source.with_comments(first)  # type: ignore[attr-defined]
    assert result.failure is not None


def test_the_official_path_reads_the_same_comments() -> None:
    """It feeds the same RawComment, so a fake PRAW client gets the same tree and hashes."""
    from types import SimpleNamespace

    from app.research.reddit import OfficialRedditSource

    def praw_comment(cid: str, parent: str, author: str, score: int) -> SimpleNamespace:
        return SimpleNamespace(
            id=cid,
            parent_id=parent,
            body="Made-up text.",
            author=SimpleNamespace(name=author),
            created_utc=1793318400,
            score=score,
        )

    comments = [praw_comment("m1", "t3_abc", "made_up_one", 5), praw_comment("m2", "t1_m1", "made_up_two", 1)]
    forest = SimpleNamespace(replace_more=lambda limit: None, list=lambda: comments)
    submission = SimpleNamespace(
        subreddit=SimpleNamespace(display_name="laptops"), title="A title", over_18=False, comments=forest
    )
    reddit = SimpleNamespace(submission=lambda id: submission)
    source = OfficialRedditSource(reddit, salt=SALT, cache=RedditCache(folder=None, now=lambda: NOW))
    updated = source.with_comments(thread("abc")).sources[0]
    assert [c.depth for c in updated.comments] == [1, 2]
    assert "made_up" not in updated.model_dump_json()


def test_the_counting_budget_counts_each_sent_request() -> None:
    book = UsageBook({"reddit_archive": ProviderCap(per_search=10, per_day=500)}, path=None)
    usage = book.search()
    budget = CountingBudget(usage)
    archive = _archive()
    source, first = _first_thread(archive)
    source.search_threads("laptop", ["SuggestALaptop"], budget=budget)  # type: ignore[attr-defined]
    source.with_comments(first, budget=budget)  # type: ignore[attr-defined]
    assert len(archive.calls(COMMENTS)) == 1  # the listing was already cached, so the comment read is the one request
    assert budget.used == 1
    assert usage.used["reddit_archive"] == 1
    assert book.today()["reddit_archive"] == 1


def test_the_counting_budget_stops_at_the_daily_cap() -> None:
    usage = UsageBook({"reddit_archive": ProviderCap(per_search=10, per_day=1)}, path=None).search()
    budget = CountingBudget(usage)
    assert isinstance(budget, RequestBudget)
    assert budget.left == 1
    assert budget.take()
    assert budget.left == 0
    assert not budget.take()


# --- reading threads for the stage -----------------------------------------------------------------


def _settings(**overrides: object) -> ResearchSettings:
    return ResearchSettings(**overrides)  # type: ignore[arg-type]


def read_threads(
    reddit: object,
    query: str,
    category: str,
    budget: RequestBudget,
    settings: ResearchSettings,
    progress: ThreadProgress,
    time_left: object = lambda: float("inf"),
) -> None:
    """The stage's two steps, one after the other."""
    find_threads(reddit, query, category, budget, settings, progress)  # type: ignore[arg-type]
    if not progress.finished:
        read_comments(reddit, budget, settings, progress, time_left)  # type: ignore[arg-type]


def test_read_threads_merges_duplicates_and_reads_comments_for_the_top_threads() -> None:
    threads = [thread(f"t{i}", f"Topic number{i} words{i}", upvotes=i) for i in range(10)]
    threads.append(thread("dup", "Topic number9 words9", upvotes=1))
    reddit = FakeReddit(threads=threads)
    progress = ThreadProgress()
    read_threads(reddit, "laptop recommendations", "laptop", _budget(), _settings(threads_with_comments=3), progress)
    assert progress.finished
    assert progress.failure is None
    assert len(progress.threads) == 10
    assert len(reddit.comment_calls) == 3
    assert reddit.comment_calls[0] == str(thread("t9").url)
    assert reddit.queries == ["laptop recommendations"]
    assert reddit.subreddits[0]


def test_read_threads_keeps_the_cap() -> None:
    threads = [thread(f"t{i}", f"Topic number{i} words{i}") for i in range(30)]
    progress = ThreadProgress()
    read_threads(FakeReddit(threads=threads), "q", "laptop", _budget(), _settings(max_reddit_threads=5), progress)
    assert len(progress.threads) == 5


def test_threads_are_visible_before_their_comments_finish() -> None:
    progress = ThreadProgress()
    seen: list[int] = []

    class Watching(FakeReddit):
        def with_comments(self, thread, depth=3, limit=100, budget=None):  # type: ignore[no-untyped-def]
            seen.append(len(progress.snapshot()))
            return super().with_comments(thread, depth, limit, budget)

    read_threads(Watching(threads=[thread("aaa")]), "q", "laptop", _budget(), _settings(), progress)
    assert seen == [1]


def test_running_out_of_time_stops_the_comment_reads_and_keeps_the_threads() -> None:
    reddit = FakeReddit(threads=[thread("aaa", "One topic"), thread("bbb", "Another subject")])
    progress = ThreadProgress()
    read_threads(reddit, "q", "laptop", _budget(), _settings(), progress, time_left=lambda: 0.0)
    assert reddit.comment_calls == []
    assert len(progress.threads) == 2
    assert not progress.finished


def test_titles_of_threads_beyond_the_cap_still_count_for_candidates() -> None:
    threads = [thread(f"t{i}", f"Topic number{i} words{i}") for i in range(30)]
    progress = ThreadProgress()
    find_threads(FakeReddit(threads=threads), "q", "laptop", _budget(), _settings(max_reddit_threads=5), progress)
    assert len(progress.threads) == 5
    assert len(progress.titles) == 30


def test_a_failed_search_is_reported() -> None:
    progress = ThreadProgress()
    read_threads(FakeReddit(failure_reason="timeout"), "q", "laptop", _budget(), _settings(), progress)
    assert progress.failure == "timeout"
    assert progress.threads == []
    assert progress.finished


def test_a_comment_read_that_raises_keeps_the_thread_and_goes_on() -> None:
    reddit = FakeReddit(
        threads=[thread("aaa", "One topic"), thread("bbb", "Another subject")], comment_error=RuntimeError("x")
    )
    progress = ThreadProgress()
    read_threads(reddit, "q", "laptop", _budget(), _settings(), progress)
    assert len(progress.threads) == 2
    assert len(reddit.comment_calls) == 2
    assert progress.failure == "error"
    assert progress.finished


def test_a_capped_reddit_is_reported_and_sends_nothing() -> None:
    archive = _archive()
    progress = ThreadProgress()
    read_threads(_source(archive), "laptop", "laptop", _budget(per_search=0), _settings(), progress)
    assert archive.requests == []
    assert progress.failure == "quota_exhausted"
    assert progress.threads == []
    assert progress.finished


def test_a_capped_reddit_still_answers_from_its_cache() -> None:
    """A cache hit is free. A repeat search at the cap must get what it has, not "ran out of quota"."""
    cache = RedditCache(folder=None, now=lambda: NOW)
    archive = _archive()
    first = ThreadProgress()
    read_threads(_source(archive, cache=cache), "laptop", "laptop", _budget(), _settings(), first)
    assert any(t.comments for t in first.threads)
    sent = len(archive.requests)
    again = ThreadProgress()
    read_threads(_source(archive, cache=cache), "laptop", "laptop", _budget(per_search=0), _settings(), again)
    assert len(archive.requests) == sent
    assert again.failure is None
    assert [t.url for t in again.threads] == [t.url for t in first.threads]
    assert any(t.comments for t in again.threads)


def test_a_cap_reached_during_the_listings_is_reported_and_keeps_what_was_found() -> None:
    archive = _archive()
    progress = ThreadProgress()
    budget = _budget(per_search=1)
    find_threads(_source(archive), "laptop", "laptop", budget, _settings(), progress)
    assert len(archive.calls(POSTS)) == 1  # the other subreddits got no request
    assert progress.threads
    assert progress.failure == "quota_exhausted"


@pytest.mark.parametrize("reason", ["rate_limited", "error", "timeout"])
def test_a_comment_read_that_fails_is_reported_and_the_threads_stay(reason: str) -> None:
    reddit = FakeReddit(
        threads=[thread("aaa", "One topic"), thread("bbb", "Another subject")], comment_failure_reason=reason
    )
    progress = ThreadProgress()
    read_threads(reddit, "q", "laptop", _budget(), _settings(), progress)
    assert len(progress.threads) == 2
    assert len(reddit.comment_calls) == 2
    assert progress.failure == reason
    assert progress.finished


def test_a_cap_named_alongside_a_comment_read_is_reported() -> None:
    capped = failure("quota_exhausted", "made up")

    class Named(FakeReddit):
        def with_comments(self, thread, depth=3, limit=100, budget=None):  # type: ignore[no-untyped-def]
            return RedditResult((thread,), partial=(capped,))

    progress = ThreadProgress()
    read_threads(Named(threads=[thread("aaa")]), "q", "laptop", _budget(), _settings(), progress)
    assert progress.failure == "quota_exhausted"


def test_a_thread_with_no_comments_found_is_not_a_failure() -> None:
    reddit = FakeReddit(threads=[thread("aaa")], comment_failure_reason="not_found")
    progress = ThreadProgress()
    read_threads(reddit, "q", "laptop", _budget(), _settings(), progress)
    assert progress.failure is None


def test_a_merged_threads_other_url_survives_a_cached_comment_read() -> None:
    """Comments cached by one search must not bring back another search's copy of the thread's text."""
    cache = RedditCache(folder=None, now=lambda: NOW)
    source, first = _first_thread(_archive(), cache=cache)
    source.with_comments(first)  # type: ignore[attr-defined]
    merged = Source.model_validate({**first.model_dump(), "text": f"{first.text}\n\nSame topic: https://a.example/"})  # type: ignore[attr-defined]
    updated = source.with_comments(merged).sources[0]  # type: ignore[attr-defined]
    assert updated.comments
    assert "Same topic" in updated.text


def test_a_shallower_depth_leaves_deeper_replies_out() -> None:
    source, first = _first_thread(_archive())
    comments = source.with_comments(first, depth=1).sources[0].comments  # type: ignore[attr-defined]
    assert comments
    assert {c.depth for c in comments} == {1}


def test_reddit_text_is_cleaned_of_entities_and_invisible_characters() -> None:
    zero_width = chr(0x200B)
    post = {
        "id": "1aaaaaa",
        "subreddit": "laptops",
        "title": f"Laptop{zero_width} &amp; mouse",
        "selftext": f"Good{zero_width} &amp; cheap\n\n\nsecond   line",
        "author": "made_up_asker",
        "created_utc": 1793318400,
    }
    item = _source(Archive().on(POSTS, ok({"data": [post]}))).search_threads("laptop", ["laptops"]).sources[0]
    assert item.title == "Laptop & mouse"
    assert item.text == "Laptop & mouse\n\nGood & cheap\n\nsecond line"


def test_comment_text_is_cleaned_too() -> None:
    zero_width = chr(0x200B)
    raw = {
        "id": "m1a",
        "link_id": "t3_1qx7aaa",
        "parent_id": "t3_1qx7aaa",
        "subreddit": "SuggestALaptop",
        "body": f"Fine{zero_width} &amp; fast",
        "author": "made_up_owner_one",
        "created_utc": 1793318400,
    }
    source, first = _first_thread(_archive().on(COMMENTS, ok({"data": [raw]})))
    assert source.with_comments(first).sources[0].comments[0].text == "Fine & fast"  # type: ignore[attr-defined]


def test_the_breaker_is_shared_between_searches() -> None:
    from app.research.reddit.factory import build_archives, shared_breakers
    from app.research.reddit.settings import RedditSettings

    http = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(500)))
    first = build_archives(RedditSettings(), http, RedditCache(folder=None))
    second = build_archives(RedditSettings(), http, RedditCache(folder=None))
    assert first.archives[0].breaker is second.archives[0].breaker  # type: ignore[attr-defined]
    assert first.archives[1].breaker is second.archives[1].breaker  # type: ignore[attr-defined]
    assert first.archives[0].breaker is not first.archives[1].breaker  # type: ignore[attr-defined]
    assert shared_breakers("arctic_shift").max_failures == 3


def test_a_subreddit_the_archive_does_not_have_is_not_a_failure() -> None:
    def posts(request: httpx.Request) -> httpx.Response:
        if request.url.params["subreddit"] == "laptops":
            return httpx.Response(404)
        return ok(fixture("arctic_shift_posts.json"))

    found = _source(Archive().on(POSTS, posts)).search_threads("laptop", ["laptops", "SuggestALaptop"])
    assert found.sources
    assert found.partial == ()


def test_a_subreddit_that_fails_is_named_and_the_rest_are_kept() -> None:
    def posts(request: httpx.Request) -> httpx.Response:
        if request.url.params["subreddit"] == "laptops":
            return httpx.Response(503)
        return ok(fixture("arctic_shift_posts.json"))

    found = _source(Archive().on(POSTS, posts)).search_threads("laptop", ["laptops", "SuggestALaptop"])
    assert found.sources
    assert [f.reason for f in found.partial] == ["error"]


def test_the_official_source_shares_its_breaker_too() -> None:
    from app.research.reddit.factory import build_reddit_source
    from app.research.reddit.settings import RedditSettings

    settings = RedditSettings(mode="official", client_id="a", client_secret="b", user_agent="c")
    first = build_reddit_source(settings, cache=RedditCache(folder=None))
    second = build_reddit_source(settings, cache=RedditCache(folder=None))
    assert first.breaker is second.breaker  # type: ignore[union-attr]
