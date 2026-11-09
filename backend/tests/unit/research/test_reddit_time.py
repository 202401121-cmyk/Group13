"""Reddit's time goes on the threads that will be read (US-12), and a cached listing lasts its age, not its day.

Every thread here is synthetic. A fake clock stands in for time, except in the one test that runs the whole stage.
"""

from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from app.contracts import ResearchInput, ResearchResult, Source, SourceType, StageContext
from app.db.breaker import CircuitBreaker
from app.pipeline import build_coverage_note
from app.research.reddit import IntervalLimiter, RedditResult, RequestBudget
from app.research.reddit.arctic_shift import ArcticShiftSource
from app.research.reddit.cache import RedditCache, serve_limit
from app.research.reddit.subreddits import MAX_PER_SEARCH
from app.research.serper import SerperResult
from app.research.settings import ProviderCap, ResearchSettings, load_research_settings
from app.research.stage import LiveResearchStage
from app.research.threads import CountingBudget, ThreadProgress, find_threads, read_comments
from app.research.usage import UsageBook
from tests.fakes.reddit import Archive, Clock, fixture, ok
from tests.fakes.research import FakePages, FakeReddit, FakeSerper, rubric, thread

POSTS = "/api/posts/search"
BEFORE_MIDNIGHT = datetime(2026, 11, 8, 23, 50, tzinfo=UTC)


def _thread(name: str, upvotes: int) -> Source:
    # Titles with no words in common, so near-duplicate merging leaves each thread alone.
    title = f"{name}one {name}two {name}three {name}four"
    return thread(name, title=title, text=f"A made-up post {name}.", upvotes=upvotes, comment_count=1)


def _ten() -> list[Source]:
    return [_thread(f"t{i:02d}", upvotes=(i + 1) * 10) for i in range(10)]


def _found(reddit: FakeReddit, settings: ResearchSettings) -> ThreadProgress:
    progress = ThreadProgress()
    find_threads(reddit, "laptop", "laptop", RequestBudget(30), settings, progress)
    return progress


def _urls_best_first(threads: list[Source]) -> list[str]:
    return [str(t.url) for t in sorted(threads, key=lambda t: -(t.upvotes or 0))]


# --- comments for the top N only --------------------------------------------------------------------


def test_comments_are_fetched_for_the_top_n_only() -> None:
    threads = _ten()
    reddit = FakeReddit(threads=threads)
    settings = ResearchSettings(reddit_comment_threads=3)
    read_comments(reddit, RequestBudget(30), settings, _found(reddit, settings))
    assert sorted(reddit.comment_calls) == sorted(_urls_best_first(threads)[:3])


def test_comments_are_fetched_in_score_order() -> None:
    threads = _ten()
    reddit = FakeReddit(threads=list(reversed(threads)))
    settings = ResearchSettings(reddit_comment_threads=8)
    read_comments(reddit, RequestBudget(30), settings, _found(reddit, settings))
    assert reddit.comment_calls == _urls_best_first(threads)[:8]


def test_threads_past_n_stay_as_sources_with_their_listing_data() -> None:
    threads = _ten()
    reddit = FakeReddit(threads=threads)
    settings = ResearchSettings(reddit_comment_threads=3)
    progress = _found(reddit, settings)
    read_comments(reddit, RequestBudget(30), settings, progress)
    kept = progress.snapshot()
    assert len(kept) == 10
    last = kept[-1]
    assert (last.upvotes, last.title, last.comments) == (10, threads[0].title, ())
    assert last.quality == min(t.quality for t in kept)


def test_n_comes_from_config_and_defaults_to_8() -> None:
    assert ResearchSettings().reddit_comment_threads == 8
    assert load_research_settings(env={}).reddit_comment_threads == 8


def test_n_can_be_set_in_the_config_file(tmp_path: Path) -> None:
    where = tmp_path / "research.yaml"
    where.write_text("reddit_comment_threads: 2\n", encoding="utf-8")
    assert load_research_settings(path=where, env={}).reddit_comment_threads == 2


def test_the_cap_leaves_room_for_the_listings_n_threads_and_two_retries() -> None:
    settings = load_research_settings(env={})
    assert settings.caps["reddit_archive"].per_search == 14
    assert MAX_PER_SEARCH + settings.reddit_comment_threads + 2 <= settings.caps["reddit_archive"].per_search
    assert ResearchSettings().caps["reddit_archive"].per_search == 14


# --- a slow archive ---------------------------------------------------------------------------------


class _Slow(FakeReddit):
    """Each comment read takes ten seconds on the fake clock, as a read that times out does."""

    clock: Clock | None = None

    def with_comments(
        self, thread: Source, depth: int = 3, limit: int = 100, budget: RequestBudget | None = None
    ) -> RedditResult:
        assert self.clock is not None
        self.comment_calls.append(str(thread.url))
        self.clock.sleep(10)
        words = Source.model_validate({**thread.model_dump(), "comment_count": 99})
        return RedditResult((words,), origin="arctic_shift")


def _slow_run(deadline_s: float) -> tuple[_Slow, ThreadProgress]:
    clock = Clock()
    reddit = _Slow(threads=_ten())
    reddit.clock = clock
    settings = ResearchSettings(reddit_comment_threads=8)
    progress = _found(reddit, settings)
    ends_at = clock() + deadline_s
    read_comments(reddit, RequestBudget(30), settings, progress, lambda: ends_at - clock())
    return reddit, progress


def test_a_slow_archive_stops_at_the_deadline_and_keeps_what_it_fetched() -> None:
    reddit, progress = _slow_run(deadline_s=25)
    assert len(reddit.comment_calls) == 3  # at 0, 10 and 20 seconds: at 30 the deadline has gone
    kept = progress.snapshot()
    assert [t.comment_count for t in kept[:3]] == [99, 99, 99]
    assert all(t.comment_count == 1 for t in kept[3:])
    assert len(kept) == 10
    assert progress.finished is False  # so the stage reports the timeout


def test_no_request_starts_past_the_deadline() -> None:
    reddit, progress = _slow_run(deadline_s=0.5)
    assert reddit.comment_calls == []
    assert len(progress.snapshot()) == 10


def test_a_fast_archive_finishes_every_top_thread() -> None:
    reddit, progress = _slow_run(deadline_s=1000)
    assert len(reddit.comment_calls) == 8
    assert progress.finished is True


@dataclass
class _Marked(FakeReddit):
    """Takes a real moment over each comment read, and marks the thread, so a read that was kept can be seen."""

    def with_comments(
        self, thread: Source, depth: int = 3, limit: int = 100, budget: RequestBudget | None = None
    ) -> RedditResult:
        self.comment_calls.append(str(thread.url))
        time.sleep(self.comment_delay_s)
        return RedditResult(
            (Source.model_validate({**thread.model_dump(), "comment_count": 99}),), origin="arctic_shift"
        )


def _whole_stage(
    deadline_s: float, comment_delay_s: float, reddit_type: type[_Marked] = _Marked
) -> tuple[_Marked, ResearchResult]:
    """The whole stage, with a short real deadline."""
    reddit = reddit_type(threads=_ten(), comment_delay_s=comment_delay_s)
    stage = LiveResearchStage(
        settings=ResearchSettings(review_domains=("reviews.example",), deadline_s=deadline_s, reddit_comment_threads=8),
        serper=FakeSerper(default=SerperResult(sources=())),  # type: ignore[arg-type]
        pages=FakePages(pages={}),  # type: ignore[arg-type]
        reddit=reddit,  # type: ignore[arg-type]
        usage=UsageBook(
            {
                "serper": ProviderCap(per_search=6, per_day=60),
                "reddit_archive": ProviderCap(per_search=14, per_day=500),
                "pages": ProviderCap(per_search=16, per_day=200),
            },
            path=None,
        ),
        pool=ThreadPoolExecutor(max_workers=6),
    )
    return reddit, stage.run(ResearchInput(rubric=rubric()), StageContext(search_id="srch_slow"))


def _reddit_sources(result: ResearchResult) -> list[Source]:
    return [s for s in result.sources if s.type is SourceType.REDDIT]


def _says_it_timed_out(result: ResearchResult) -> bool:
    timed_out = [f for f in result.failures if f.source_type is SourceType.REDDIT and f.reason == "timeout"]
    return bool(timed_out) and "Reddit timed out" in (build_coverage_note([], result.failures, False) or "")


def test_hitting_the_deadline_keeps_what_was_read_and_adds_a_coverage_note() -> None:
    reddit, result = _whole_stage(deadline_s=1.6, comment_delay_s=0.4)
    assert 1 <= len(reddit.comment_calls) < 8
    kept = _reddit_sources(result)
    assert len(kept) == 10
    assert sum(1 for t in kept if t.comment_count == 99) >= 1  # the reads that finished are still there
    assert _says_it_timed_out(result)


def test_a_read_still_running_at_the_deadline_adds_a_coverage_note() -> None:
    reddit, result = _whole_stage(deadline_s=1.2, comment_delay_s=2)
    assert len(reddit.comment_calls) == 1
    kept = _reddit_sources(result)
    assert len(kept) == 10
    assert all(t.comment_count == 1 for t in kept)
    assert _says_it_timed_out(result)


def test_the_budget_sends_nothing_past_the_deadline() -> None:
    """A read already running must not retry, or fail over to the next archive, for a reply nobody will use."""
    left = [5.0]
    book = UsageBook({"reddit_archive": ProviderCap(per_search=14, per_day=500)}, path=None)
    usage = book.search()
    budget = CountingBudget(usage, lambda: left[0])
    assert budget.left == 14
    assert budget.take() is True
    assert budget.used == 1
    left[0] = 0.0
    assert budget.left == 0
    assert budget.take() is False
    assert budget.used == 1
    assert usage.used["reddit_archive"] == 1


def test_a_retry_past_the_deadline_is_never_sent() -> None:
    left = [5.0]

    def rate_limited(_: httpx.Request) -> httpx.Response:
        left[0] = 0.0  # the deadline comes while the first request is out
        return httpx.Response(429, headers={"retry-after": "1"})

    archive = Archive().on(POSTS, rate_limited)
    now = [BEFORE_MIDNIGHT]
    book = UsageBook({"reddit_archive": ProviderCap(per_search=14, per_day=500)}, path=None)
    budget = CountingBudget(book.search(), lambda: left[0])
    found = _at(archive, _cache(now), now).search_threads("laptop", ["SuggestALaptop"], budget=budget)
    assert len(archive.requests) == 1
    assert found.failure is not None


def test_the_stage_gives_the_budget_its_deadline() -> None:
    """A read still running at the deadline finds its budget closed, so it can't retry or try the next archive."""
    could_send: list[bool] = []
    finished = threading.Event()

    class Late(_Marked):
        def with_comments(
            self, thread: Source, depth: int = 3, limit: int = 100, budget: RequestBudget | None = None
        ) -> RedditResult:
            time.sleep(self.comment_delay_s)
            assert budget is not None
            could_send.append(budget.take())
            finished.set()
            return RedditResult((thread,), origin="arctic_shift")

    _whole_stage(deadline_s=1.2, comment_delay_s=2, reddit_type=Late)
    assert finished.wait(5)
    assert could_send == [False]


# --- a cached listing lasts its age -----------------------------------------------------------------


def _at(archive: Archive, cache: RedditCache, now: list[datetime]) -> ArcticShiftSource:
    clock = Clock()
    return ArcticShiftSource(
        archive.client(),
        base_url="https://arctic.example.test",
        cache=cache,
        limiter=IntervalLimiter(1.0, clock=clock, sleep=clock.sleep),
        breaker=CircuitBreaker(3, 600, clock=clock),
        now=lambda: now[0],
        sleep=clock.sleep,
    )


def _cache(now: list[datetime], folder: Path | None = None) -> RedditCache:
    # Its age limit is written out, so a member's own SEARCH_RUN_KEEP_MINUTES can't change these tests.
    return RedditCache(folder=folder, ttl=serve_limit({}), now=lambda: now[0])


def _listing_archive() -> Archive:
    return Archive().on(POSTS, ok(fixture("arctic_shift_posts.json")))


def test_a_listing_cached_at_23_50_is_still_served_at_00_10() -> None:
    now = [BEFORE_MIDNIGHT]
    archive = _listing_archive()
    source = _at(archive, _cache(now), now)
    first = source.search_threads("laptop", ["SuggestALaptop"])
    now[0] = BEFORE_MIDNIGHT + timedelta(minutes=20)
    again = source.search_threads("laptop", ["SuggestALaptop"])
    assert len(archive.requests) == 1
    assert again.sources == first.sources


def test_the_listing_key_holds_no_dates() -> None:
    now = [BEFORE_MIDNIGHT]
    source = _at(Archive(), RedditCache(folder=None, now=lambda: now[0]), now)
    key = source.listing_key("SuggestALaptop", "laptop", None)
    now[0] += timedelta(days=3)
    assert source.listing_key("SuggestALaptop", "laptop", None) == key
    assert not re.search(r"\d{4}-\d{2}-\d{2}", " ".join(str(part) for part in key))


def test_an_after_date_keeps_its_own_entry() -> None:
    now = [BEFORE_MIDNIGHT]
    source = _at(Archive(), RedditCache(folder=None, now=lambda: now[0]), now)
    plain = source.listing_key("SuggestALaptop", "laptop", None)
    assert source.listing_key("SuggestALaptop", "laptop", BEFORE_MIDNIGHT - timedelta(days=9)) != plain


def test_the_request_still_asks_for_the_date_window() -> None:
    now = [BEFORE_MIDNIGHT]
    archive = _listing_archive()
    _at(archive, RedditCache(folder=None, now=lambda: now[0]), now).search_threads("laptop", ["SuggestALaptop"])
    params = archive.requests[0].url.params
    assert params["before"] == "2026-11-06"
    assert params["after"] == "2026-05-12"


def test_a_listing_still_expires_at_its_age_limit() -> None:
    now = [BEFORE_MIDNIGHT]
    archive = _listing_archive()
    source = _at(archive, _cache(now), now)
    source.search_threads("laptop", ["SuggestALaptop"])
    now[0] = BEFORE_MIDNIGHT + serve_limit({}) - timedelta(minutes=1)
    source.search_threads("laptop", ["SuggestALaptop"])
    assert len(archive.requests) == 1
    now[0] = BEFORE_MIDNIGHT + serve_limit({})
    source.search_threads("laptop", ["SuggestALaptop"])
    assert len(archive.requests) == 2


def _old_key() -> tuple[str, ...]:
    return ("arctic_shift", "listing", "suggestalaptop", "2026-05-12", "2026-11-06")


def test_an_entry_under_an_old_dated_key_is_ignored(tmp_path: Path) -> None:
    now = [BEFORE_MIDNIGHT]
    cache = _cache(now, tmp_path)
    cache.put(_old_key(), [thread("old", fetched_at=BEFORE_MIDNIGHT)])  # young enough to be served, if anything asked
    assert cache.get(_old_key()) is not None
    archive = _listing_archive()
    found = _at(archive, cache, now).search_threads("laptop", ["SuggestALaptop"])
    assert len(archive.requests) == 1  # it asked, as nothing under the new key was there
    assert "old" not in {str(s.url).rstrip("/").split("/")[-1] for s in found.sources}


def test_an_old_dated_entry_is_swept_past_its_age_limit(tmp_path: Path) -> None:
    now = [BEFORE_MIDNIGHT]
    _cache(now, tmp_path).put(_old_key(), [thread("old", fetched_at=BEFORE_MIDNIGHT)])
    assert len(list(tmp_path.glob("*.json"))) == 1
    now[0] = BEFORE_MIDNIGHT + serve_limit({}) + timedelta(minutes=1)
    _cache(now, tmp_path).get(("arctic_shift", "listing", "suggestalaptop", "recent"))
    assert list(tmp_path.glob("*.json")) == []
