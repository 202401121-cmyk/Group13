"""Reddit through the Arctic Shift archive (US-12): the default Reddit source.

Arctic Shift (arctic-shift.photon-reddit.com) keeps a copy of public Reddit posts and comments and offers a
free, keyless API. We follow its rules: at most one request a second from the whole app, our User-Agent on
every request, and on HTTP 429 (or 422 "Timeout. Maybe slow down a bit") we wait for X-RateLimit-Reset (at most
30 s) and retry once. After 3 failures in a row it is paused for 10 minutes, then one trial request.

Its copy lags Reddit by a few days and scores settle after about 36 hours, so results carry the coverage note
"Reddit via archive: may miss posts from the last few days".

Threads are found by asking for a subreddit's newest 100 posts inside a date window and matching the query's
words here. Arctic Shift's own keyword search (title, selftext, query) is "not supported with very active
subreddits": on 3 Nov 2026 it answered 422 at once for r/SuggestALaptop and r/laptops, while the plain listing
answered in about 2 s. The listing does not depend on the query, so one cached listing serves every query.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta

import httpx

from app.contracts import RedditOrigin, Source, SourceFailure
from app.db.breaker import CircuitBreaker

from .base import (
    IntervalLimiter,
    RedditResult,
    RequestBudget,
    ThreadInfo,
    author_hash,
    bare_id,
    best_matches,
    failure,
    is_bot,
    is_gone,
    make_source,
    newest,
    query_words,
    utc_from_epoch,
    valid_subreddit,
)
from .cache import RedditCache
from .http import ArchiveHttp
from .parsing import RawComment, RawPost, parse_items
from .settings import DEFAULT_ARCTIC_SHIFT_BASE_URL

log = logging.getLogger(__name__)

COVERAGE_NOTE = "Reddit via archive: may miss posts from the last few days"
DEFAULT_WINDOW = timedelta(days=180)
MAX_LIMIT = 100
BREAKER_FAILURES = 3
BREAKER_COOLDOWN_S = 600.0

# One limiter for the whole process, however many sources are built: Arctic Shift asks for 1 request a second.
SHARED_LIMITER = IntervalLimiter(1.0)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ArcticShiftSource:
    name: RedditOrigin = "arctic_shift"

    def __init__(
        self,
        http: httpx.Client,
        *,
        base_url: str = DEFAULT_ARCTIC_SHIFT_BASE_URL,
        salt: str | None = None,
        cache: RedditCache | None = None,
        limiter: IntervalLimiter | None = None,
        breaker: CircuitBreaker | None = None,
        now: Callable[[], datetime] = _utc_now,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.salt = salt
        self.cache = cache if cache is not None else RedditCache()
        self.breaker = breaker if breaker is not None else CircuitBreaker(BREAKER_FAILURES, BREAKER_COOLDOWN_S)
        self.http = ArchiveHttp(
            "Arctic Shift",
            base_url,
            http,
            limiter if limiter is not None else SHARED_LIMITER,
            self.breaker,
            rate_limit_statuses=frozenset({429, 422}),
            sleep=sleep,
        )
        self._now = now
        self._threads: dict[str, ThreadInfo] = {}

    # --- threads ---------------------------------------------------------------------------------

    def search_threads(
        self,
        query: str,
        subreddits: Sequence[str],
        after: datetime | None = None,
        limit: int = 25,
        budget: RequestBudget | None = None,
    ) -> RedditResult:
        """The threads with most of the query's words, newest first among equals: one request per subreddit."""
        names = [s for s in subreddits if valid_subreddit(s)]
        if not names or not query.strip():
            return RedditResult(failure=failure("not_found", "no subreddit or no words to search for"))
        since = (after or self._now() - DEFAULT_WINDOW).date().isoformat()
        found: list[Source] = []
        failures: list[SourceFailure] = []
        for subreddit in names:
            sources, problem = self._listing(subreddit, since, budget)
            found.extend(sources)
            if problem is not None:
                failures.append(problem)
        if failures and len(failures) == len(names):
            return RedditResult(failure=failures[0])
        kept = tuple(best_matches(found, query_words(query), max(1, limit)))
        return RedditResult(kept, newest(kept), COVERAGE_NOTE, origin=self.name)

    def _listing(
        self, subreddit: str, since: str, budget: RequestBudget | None
    ) -> tuple[list[Source], SourceFailure | None]:
        """A subreddit's newest posts since a date, cleaned. Cached: the same listing serves every query."""
        key = (self.name, "listing", subreddit.lower(), since)
        cached = self.cache.get(key)
        if cached is not None:
            for source in cached:
                self._remember(source)
            return list(cached), None
        params: dict[str, str | int] = {"subreddit": subreddit, "after": since, "limit": MAX_LIMIT, "sort": "desc"}
        fetched = self.http.get("/api/posts/search", params, budget)
        if fetched.failure is not None:
            return [], fetched.failure
        sources = [s for post in parse_items(fetched.items, RawPost) if (s := self._post(post)) is not None]
        self.cache.put(key, sources)
        return sources, None

    def _post(self, post: RawPost) -> Source | None:
        thread_id = bare_id(post.id)
        if thread_id is None:
            return None
        self._threads[thread_id] = ThreadInfo(post.subreddit, post.title, bool(post.over_18))
        if post.over_18 or post.removed_by_category or is_gone(post.author) or is_bot(post.author):
            return None
        if is_gone(post.title) or (post.selftext or "").strip().lower() in ("[deleted]", "[removed]"):
            return None
        selftext = (post.selftext or "").strip()
        return make_source(
            origin=self.name,
            subreddit=post.subreddit,
            thread_id=thread_id,
            title=post.title,
            text=f"{post.title.strip()}\n\n{selftext}" if selftext else post.title,
            published_at=utc_from_epoch(post.created_utc),
            author_digest=author_hash(post.author, self.salt),
            fetched_at=self._now(),
        )

    def _remember(self, source: Source) -> None:
        """Rebuild a thread's details from its cached Source, so its comments need no extra request."""
        parts = str(source.url).rstrip("/").split("/")
        if len(parts) == 7 and parts[5] == "comments" and source.community:  # a thread, not a comment
            self._threads[parts[6]] = ThreadInfo(source.community.removeprefix("r/"), source.title)

    # --- comments --------------------------------------------------------------------------------

    def get_comments(
        self, thread_id: str, depth: int = 3, limit: int = 100, budget: RequestBudget | None = None
    ) -> RedditResult:
        """A thread's comments down to `depth` replies deep, newest first."""
        tid = bare_id(thread_id)
        if tid is None:
            return RedditResult(failure=failure("not_found", "not a Reddit thread id"))
        size = max(1, min(limit, MAX_LIMIT))
        key = (self.name, "comments", tid, depth, size)
        cached = self.cache.get(key)
        if cached is not None:
            return RedditResult(cached, newest(cached), COVERAGE_NOTE, origin=self.name)
        thread, problem = self._thread(tid, budget)
        if problem is not None or thread is None:
            return RedditResult(failure=problem or failure("not_found", "Arctic Shift: no such thread"))
        if thread.over_18:
            return RedditResult(coverage_note=COVERAGE_NOTE, origin=self.name)  # never read an over-18 thread
        fetched = self.http.get("/api/comments/search", {"link_id": tid, "limit": size, "sort": "desc"}, budget)
        if fetched.failure is not None:
            return RedditResult(failure=fetched.failure)
        sources = comments_to_sources(
            parse_items(fetched.items, RawComment), tid, thread, depth, self.salt, self.name, self._now()
        )
        self.cache.put(key, sources)
        return RedditResult(tuple(sources), newest(sources), COVERAGE_NOTE, origin=self.name)

    def _thread(self, tid: str, budget: RequestBudget | None) -> tuple[ThreadInfo | None, SourceFailure | None]:
        if tid in self._threads:
            return self._threads[tid], None
        fetched = self.http.get("/api/posts/ids", {"ids": tid}, budget)
        if fetched.failure is not None:
            return None, fetched.failure
        for post in parse_items(fetched.items, RawPost):
            if bare_id(post.id) == tid:
                self._post(post)
                return self._threads.get(tid), None
        return None, failure("not_found", "Arctic Shift: no such thread")


def comments_to_sources(
    comments: Sequence[RawComment],
    tid: str,
    thread: ThreadInfo,
    max_depth: int,
    salt: str | None,
    origin: RedditOrigin,
    fetched_at: datetime,
) -> list[Source]:
    """Clean comments into Sources: only this thread's, at most `max_depth` replies deep, none gone or by a bot."""
    mine = [c for c in comments if bare_id(c.link_id) == tid and bare_id(c.id) is not None]
    parents = {bare_id(c.id): c.parent_id for c in mine}
    sources: list[Source] = []
    for comment in mine:
        if is_gone(comment.body) or is_gone(comment.author) or is_bot(comment.author):
            continue
        if comment_depth(comment.parent_id, parents) > max_depth:
            continue
        source = make_source(
            origin=origin,
            subreddit=thread.subreddit,
            thread_id=tid,
            title=thread.title,
            text=comment.body or "",
            published_at=utc_from_epoch(comment.created_utc),
            author_digest=author_hash(comment.author, salt),
            fetched_at=fetched_at,
            comment_id=bare_id(comment.id),
        )
        if source is not None:
            sources.append(source)
    return sources


def comment_depth(parent_id: str | None, parents: dict[str | None, str | None]) -> int:
    """1 for a reply to the thread, 2 for a reply to that, and so on. A parent we were not sent counts as 2+."""
    depth = 1
    seen: set[str] = set()
    while parent_id and parent_id.lower().startswith("t1_"):
        parent = bare_id(parent_id)
        if parent is None or parent in seen:
            break
        seen.add(parent)
        depth += 1
        if parent not in parents:
            break
        parent_id = parents[parent]
    return depth
