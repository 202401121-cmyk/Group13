"""Scoring runs inside the Reddit task (US-13): low scorers go, the best get their comments read, comments are scored.

Every thread here is synthetic.
"""

from __future__ import annotations

import dataclasses
import logging
import random
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Any

import pytest

from app.contracts import ResearchInput, SearchRequest, Source, SourceComment, SourceType, StageContext
from app.db.breaker import CircuitBreaker
from app.pipeline import OLDER_ARCHIVE_NOTE, InMemoryResultStore, PipelineRunner, default_search_stages
from app.research import threads as threads_module
from app.research.reddit import IntervalLimiter, RedditResult, RequestBudget
from app.research.reddit.arctic_shift import ArcticShiftSource
from app.research.reddit.cache import RedditCache
from app.research.reddit.quality import load_quality_settings
from app.research.serper import SerperResult
from app.research.settings import ProviderCap, ResearchSettings
from app.research.stage import LiveResearchStage
from app.research.threads import ThreadProgress, find_threads, read_comments
from app.research.usage import UsageBook
from tests.fakes.reddit import NOW, Archive, Clock, fixture, ok
from tests.fakes.research import FakePages, FakeReddit, FakeSerper, rubric, thread


def _thread(name: str, upvotes: int = 5, days: float = 0) -> Source:
    # Titles with no words in common, so near-duplicate merging leaves each thread alone.
    title = f"{name}one {name}two {name}three {name}four"
    source = thread(
        name, title=title, text=f"A made-up post {name}.", upvotes=upvotes, comment_count=1, subreddit="laptops"
    )
    return Source.model_validate({**source.model_dump(), "published_at": source.fetched_at - timedelta(days=days)})


def _find(*threads: Source, settings: ResearchSettings | None = None) -> ThreadProgress:
    progress = ThreadProgress()
    find_threads(
        FakeReddit(threads=threads), "laptop", "laptop", RequestBudget(10), settings or ResearchSettings(), progress
    )
    return progress


def test_low_scorers_are_dropped_and_remembered() -> None:
    good, poor = _thread("aaa"), _thread("bbb", days=2000)
    progress = _find(good, poor)
    assert [t.id for t in progress.snapshot()] == [good.id]
    (gone,) = progress.dropped
    assert gone.url == str(poor.url)


def test_kept_threads_carry_their_scores_best_first() -> None:
    low, high = _thread("aaa", upvotes=1), _thread("bbb", upvotes=190)
    kept = _find(low, high).snapshot()
    assert [t.id for t in kept] == [high.id, low.id]
    assert kept[0].quality > kept[1].quality


def test_the_thread_limit_keeps_the_best_scorers() -> None:
    threads = [_thread(f"t{i:02d}", upvotes=i) for i in range(10)]
    kept = _find(*threads, settings=ResearchSettings(max_reddit_threads=3)).snapshot()
    assert [t.upvotes for t in kept] == [9, 8, 7]


def test_the_same_threads_in_any_order_give_the_same_result() -> None:
    """Near-duplicates with the same counts: which copy is kept must not depend on the order they were listed in."""
    twins = [thread(f"t{i}", title="Same made-up title here", text=f"Made-up post {i}.", upvotes=5) for i in range(4)]
    others = [_thread(f"o{i}", upvotes=i) for i in range(4)]
    first = _find(*twins, *others).snapshot()
    assert len(first) == 5
    for seed in range(4):
        shuffled = [*twins, *others]
        random.Random(seed).shuffle(shuffled)
        assert _find(*shuffled).snapshot() == first


def test_a_thread_past_the_limit_is_not_called_dropped() -> None:
    threads = [_thread(f"t{i:02d}", upvotes=i) for i in range(5)]
    assert _find(*threads, settings=ResearchSettings(max_reddit_threads=3)).dropped == []


def test_the_top_scorers_get_their_comments_read() -> None:
    low, high = _thread("aaa", upvotes=1), _thread("bbb", upvotes=190)
    reddit = FakeReddit(threads=(low, high), with_comment_counts={str(low.url): 2, str(high.url): 2})
    settings = ResearchSettings(threads_with_comments=1)
    progress = ThreadProgress()
    find_threads(reddit, "laptop", "laptop", RequestBudget(10), settings, progress)
    read_comments(reddit, RequestBudget(10), settings, progress)
    assert reddit.comment_calls == [str(high.url)]


class _WithComments(FakeReddit):
    def with_comments(
        self, thread: Source, depth: int = 3, limit: int = 100, budget: RequestBudget | None = None
    ) -> RedditResult:
        rows = (
            SourceComment(id="m1", depth=1, upvotes=30, text="The XM5 is good.", author_hash="a" * 64),
            SourceComment(id="m2", parent_id="m1", depth=2, upvotes=0, text="Ok."),
        )
        return RedditResult((Source.model_validate({**thread.model_dump(), "comments": rows}),))


def test_each_comment_read_is_scored() -> None:
    reddit = _WithComments(threads=(_thread("aaa"),))
    progress = ThreadProgress()
    find_threads(reddit, "laptop", "laptop", RequestBudget(10), ResearchSettings(), progress)
    before = progress.snapshot()[0].quality
    read_comments(reddit, RequestBudget(10), ResearchSettings(), progress)
    (read,) = progress.snapshot()
    assert read.quality == before  # the thread keeps its own score
    assert read.comments[0].quality > read.comments[1].quality
    assert read.comments[0].quality != 0.5


def test_a_scoring_bug_does_not_lose_the_other_threads(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    real = threads_module.score_comments
    calls: list[str] = []

    def broken_once(source: Source, *args: Any) -> Source:
        calls.append(source.id)
        if len(calls) == 1:
            raise RuntimeError("a made-up secret bug")
        return real(source, *args)

    monkeypatch.setattr("app.research.threads.score_comments", broken_once)
    reddit = _WithComments(threads=(_thread("aaa", upvotes=190), _thread("bbb", upvotes=1)))
    progress = ThreadProgress()
    find_threads(reddit, "laptop", "laptop", RequestBudget(10), ResearchSettings(), progress)
    with caplog.at_level(logging.WARNING, logger="app.research.threads"):
        read_comments(reddit, RequestBudget(10), ResearchSettings(), progress)
    first, second = progress.snapshot()
    assert first.comments == ()  # kept, with no comments
    assert len(second.comments) == 2
    assert progress.finished is True
    assert progress.failures == ["error"]
    assert "RuntimeError" in caplog.text
    assert "secret" not in caplog.text


def test_the_stage_names_the_dropped_and_the_estimated_threads_for_the_cli() -> None:
    good, poor = _thread("aaa"), _thread("bbb", days=2000)
    guessed = Source.model_validate({**_thread("ccc").model_dump(), "upvotes": None})
    usage = UsageBook(
        {
            "serper": ProviderCap(per_search=6, per_day=60),
            "reddit_archive": ProviderCap(per_search=10, per_day=500),
            "pages": ProviderCap(per_search=16, per_day=200),
        },
        path=None,
    )
    stage = LiveResearchStage(
        settings=ResearchSettings(review_domains=("reviews.example",), deadline_s=5.0),
        serper=FakeSerper(default=SerperResult()),  # type: ignore[arg-type]
        pages=FakePages(pages={}),  # type: ignore[arg-type]
        reddit=FakeReddit(threads=[good, poor, guessed]),  # type: ignore[arg-type]
        usage=usage,
        pool=ThreadPoolExecutor(max_workers=6),
    )
    result = stage.run(ResearchInput(rubric=rubric()), StageContext(search_id="srch_a"))
    assert {s.id for s in result.sources if s.type is SourceType.REDDIT} == {good.id, guessed.id}
    assert [row.url for row in stage.last_dropped_threads] == [str(poor.url)]
    assert stage.last_estimated_threads == frozenset({str(guessed.url)})
    assert "https://" not in repr(stage.last_report)  # the report, which the log reads, holds no URL


def test_scoring_uses_the_shipped_threshold() -> None:
    assert load_quality_settings().thread.min_score == 0.25


def test_comments_are_read_three_levels_deep() -> None:
    depths: list[int] = []

    class Depths(_WithComments):
        def with_comments(
            self, thread: Source, depth: int = 3, limit: int = 100, budget: RequestBudget | None = None
        ) -> RedditResult:
            depths.append(depth)
            return super().with_comments(thread, depth, limit, budget)

    reddit = Depths(threads=(_thread("aaa"),))
    progress = ThreadProgress()
    find_threads(reddit, "laptop", "laptop", RequestBudget(10), ResearchSettings(), progress)
    read_comments(reddit, RequestBudget(10), ResearchSettings(), progress)
    assert depths == [3]


def test_a_threads_score_survives_a_real_comment_read_and_a_cached_one() -> None:
    clock = Clock()
    archive = (
        Archive()
        .on("/api/posts/search", ok(fixture("arctic_shift_posts.json")))
        .on("/api/comments/search", ok(fixture("arctic_shift_comments.json")))
    )
    source = ArcticShiftSource(
        archive.client(),
        base_url="https://arctic.example.test",
        salt="test-salt",
        cache=RedditCache(folder=None, now=lambda: NOW),
        limiter=IntervalLimiter(1.0, clock=clock, sleep=clock.sleep),
        breaker=CircuitBreaker(3, 600, clock=clock),
        now=lambda: NOW,
        sleep=clock.sleep,
    )

    def sent() -> int:
        return len(archive.calls("/api/posts/search")) + len(archive.calls("/api/comments/search"))

    requests_after_the_first_round = 0
    for round_number in (1, 2):
        progress = ThreadProgress()
        find_threads(source, "laptop", "laptop", RequestBudget(20), ResearchSettings(), progress)
        before = {t.id: t.quality for t in progress.snapshot()}
        assert before
        read_comments(source, RequestBudget(20), ResearchSettings(), progress)
        after = progress.snapshot()
        assert {t.id: t.quality for t in after} == before
        with_comments = [c for t in after for c in t.comments]
        assert with_comments
        assert all(c.quality != 0.5 for c in with_comments)
        if round_number == 1:
            requests_after_the_first_round = sent()
    assert sent() == requests_after_the_first_round  # so the second round really came from the cache
    handed_in = Source.model_validate({**after[0].model_dump(), "quality": 0.11})
    read_again = source.with_comments(handed_in, depth=3, limit=50)
    assert read_again.sources[0].quality == 0.11  # the cache gives only comments, so the handed-in score stays


def test_an_old_archives_threads_reach_the_result_with_its_note() -> None:
    """PullPush stops in May 2025, so its threads can't pass the threshold. They stay, under the older-archive note."""
    plain = _thread("aaa", days=600)
    old = Source.model_validate({**plain.model_dump(), "source_detail": "pullpush"})
    usage = UsageBook(
        {
            "serper": ProviderCap(per_search=6, per_day=60),
            "reddit_archive": ProviderCap(per_search=10, per_day=500),
            "pages": ProviderCap(per_search=16, per_day=200),
        },
        path=None,
    )
    stage = LiveResearchStage(
        settings=ResearchSettings(review_domains=("reviews.example",), deadline_s=5.0),
        serper=FakeSerper(default=SerperResult()),  # type: ignore[arg-type]
        pages=FakePages(pages={}),  # type: ignore[arg-type]
        reddit=FakeReddit(threads=[old]),  # type: ignore[arg-type]
        usage=usage,
        pool=ThreadPoolExecutor(max_workers=6),
    )
    stages = dataclasses.replace(default_search_stages(), research=stage)
    request = SearchRequest(session_id="sess_aaaaaaaaaaaaaaaaaaaaaaaa", raw_query="a made-up request", rubric=rubric())
    result = PipelineRunner(stages, InMemoryResultStore()).run(request)
    (kept,) = [s for s in result.sources if s.type is SourceType.REDDIT]
    assert kept.quality < load_quality_settings().thread.min_score
    assert result.coverage_note is not None
    assert OLDER_ARCHIVE_NOTE in result.coverage_note
