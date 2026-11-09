"""Reddit threads for the research stage (US-12): find them, merge near-duplicates, score them and drop the low scorers
(US-13), then read the top ones' comments and score those.

This runs on the fetch pool as two tasks, one after the other, and one request at a time, so waiting on an archive's
rate limiter never fills the pool. Threads are published as soon as they are found, so a deadline during the comment
reads still keeps them, and the stage can use their titles.

Nothing here checks the request cap first: a cached answer costs no request, so a repeat search at the cap still gets
it. The source refuses to send, and that refusal is reported here like any other failure.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from app.contracts import Source

from .dedupe import merge_near_duplicates
from .reddit.base import FailureReason, RedditSource, RequestBudget
from .reddit.quality import ThreadScore, load_quality_settings, score_comments, score_threads
from .reddit.subreddits import subreddits_for
from .settings import ResearchSettings
from .usage import SearchUsage

log = logging.getLogger(__name__)

PROVIDER = "reddit_archive"
MIN_SECONDS_FOR_COMMENTS = 1.0  # less than this left, and a comment read would only be abandoned
COMMENT_DEPTH = 3
COMMENTS_PER_THREAD = 50
SEARCH_LIMIT = 50  # before near-duplicates are merged


class CountingBudget(RequestBudget):
    """The search's Reddit requests, counted through the usage book, so its caps hold and counts survive a restart.

    It also ends at research's deadline, so a read already running can't retry or try the next archive after that: the
    reply would come too late to use, and the request would still count.
    """

    def __init__(self, usage: SearchUsage, time_left: Callable[[], float] = lambda: float("inf")) -> None:
        super().__init__(usage.per_search(PROVIDER))
        self.usage = usage
        self._time_left = time_left

    @property
    def left(self) -> int:
        return self.usage.left(PROVIDER) if self._time_left() > 0 else 0

    def take(self) -> bool:
        if self._time_left() <= 0 or not self.usage.take(PROVIDER):
            return False
        self.used += 1
        return True


class ThreadProgress:
    """What the Reddit task has so far. The task writes, and the stage reads from its own thread."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.threads: list[Source] = []
        self.titles: list[tuple[str, str]] = []  # (thread URL, title) for all found, also those past the cap
        self.dropped: list[ThreadScore] = []  # scored under the threshold (US-13)
        self.estimated: frozenset[str] = frozenset()  # URLs of threads scored with an unknown count or date
        self.failures: list[FailureReason] = []
        self.finished = False

    @property
    def failure(self) -> FailureReason | None:
        """The first thing that went wrong, if anything did."""
        with self._lock:
            return self.failures[0] if self.failures else None

    def add_failure(self, reason: FailureReason) -> None:
        with self._lock:
            self.failures.append(reason)

    def snapshot(self) -> list[Source]:
        with self._lock:
            return list(self.threads)

    def publish(self, threads: list[Source]) -> None:
        with self._lock:
            self.threads = threads

    def update(self, index: int, thread: Source) -> None:
        with self._lock:
            self.threads[index] = thread


def find_threads(
    reddit: RedditSource,
    query: str,
    category: str,
    budget: RequestBudget,
    settings: ResearchSettings,
    progress: ThreadProgress,
) -> None:
    """Step one: the listing, near-duplicates merged. A failure goes in `progress`. It raises only on a bug."""
    found = reddit.search_threads(query, subreddits_for(category), limit=SEARCH_LIMIT, budget=budget)
    for problem in found.partial:
        progress.add_failure(problem.reason)
    if found.failure is not None:
        progress.add_failure(found.failure.reason)
        progress.finished = True
        return
    progress.titles = [(str(t.url), t.title) for t in found.sources]
    # Sorted first, so which copy of a near-duplicate is kept never depends on the order they were listed in.
    listed = sorted(found.sources, key=lambda t: t.id)
    scored = score_threads(merge_near_duplicates(listed), category, load_quality_settings())
    progress.dropped = scored.dropped
    progress.estimated = frozenset(row.url for row in scored.scores if row.estimated)
    progress.publish(scored.kept[: settings.max_reddit_threads])


def read_comments(
    reddit: RedditSource,
    budget: RequestBudget,
    settings: ResearchSettings,
    progress: ThreadProgress,
    time_left: Callable[[], float] = lambda: float("inf"),
) -> None:
    """Step two: the top threads' comments, one request at a time. A thread keeps going without them if a read fails."""
    for index, thread in enumerate(progress.snapshot()[: settings.reddit_comment_threads]):
        if time_left() < MIN_SECONDS_FOR_COMMENTS:
            return  # not finished: the stage reports the timeout, and keeps the threads
        try:
            read = reddit.with_comments(thread, depth=COMMENT_DEPTH, limit=COMMENTS_PER_THREAD, budget=budget)
            scored = [score_comments(source, load_quality_settings()) for source in read.sources[:1]]
        except Exception as exc:  # one thread's comments must not lose the others (build rule 9)
            log.warning("research: reading a thread's comments failed: %s", type(exc).__name__)
            progress.add_failure("error")
            continue
        for problem in read.partial:
            progress.add_failure(problem.reason)
        if scored:
            progress.update(index, scored[0])
        elif read.failure is not None and read.failure.reason != "not_found":
            progress.add_failure(read.failure.reason)
    progress.finished = True
