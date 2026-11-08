"""Reddit through public archives (US-12): what both archives share, and the chain that tries them in order.

ArchiveSource does the work for Arctic Shift and PullPush: one request per subreddit, the query's words matched
here, comments read down to a depth, threads remembered so their comments need no extra request, every item
cleaned into a Source (base.py's rules), and the 24-hour cache. Each archive only says which paths and
parameters it uses, and how to describe its coverage.

ArchiveRedditSource tries the archives in order (REDDIT_ARCHIVES, default Arctic Shift then PullPush). The next
one is asked only when the one before failed or found nothing, so an older archive never replaces fresher data.
If every archive fails, the result is empty with the failures joined: the search still finishes. An archive that
failed stays named in `partial` if a later one then found nothing. If a later one has threads, its own note says
they are older, and only a cap still needs saying. An older archive failing after a fresh one found nothing is not
named at all: the fresh answer stands.
"""

from __future__ import annotations

import dataclasses
import logging
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

import httpx

from app.contracts import RedditOrigin, Source, SourceComment, SourceFailure
from app.contracts.research import MAX_COMMENT_CHARS, MAX_COMMENT_DEPTH, MAX_SOURCE_COMMENTS
from app.db.breaker import CircuitBreaker
from app.research.cleaning import clean_text

from .base import (
    IntervalLimiter,
    RedditResult,
    RedditSource,
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
    worth_naming,
)
from .cache import CacheKey, RedditCache
from .http import ArchiveHttp
from .parsing import RawComment, RawPost, parse_items

log = logging.getLogger(__name__)

MAX_LIMIT = 100


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ArchiveSource:
    """The shared flow of a Reddit archive. Subclasses set the class attributes and the four hooks below."""

    name: RedditOrigin
    label: str
    listing_path: str
    comments_path: str
    lookup_path: str
    rate_limit_statuses: frozenset[int] = frozenset({429})

    def __init__(
        self,
        http: httpx.Client,
        *,
        base_url: str,
        salt: str | None,
        cache: RedditCache | None,
        limiter: IntervalLimiter,
        breaker: CircuitBreaker,
        now: Callable[[], datetime] = _utc_now,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.salt = salt
        self.cache = cache if cache is not None else RedditCache()
        self.breaker = breaker
        self.http = ArchiveHttp(
            self.label, base_url, http, limiter, breaker, rate_limit_statuses=self.rate_limit_statuses, sleep=sleep
        )
        self._now = now
        self._threads: dict[str, ThreadInfo] = {}

    # --- the hooks each archive fills in ---------------------------------------------------------

    def listing_params(self, subreddit: str, query: str, after: datetime | None) -> dict[str, str | int]:
        raise NotImplementedError

    def listing_key(self, subreddit: str, query: str, after: datetime | None) -> CacheKey:
        raise NotImplementedError

    def comment_params(self, thread_id: str, size: int) -> dict[str, str | int]:
        raise NotImplementedError

    def coverage_note(self, sources: Sequence[Source]) -> str | None:
        raise NotImplementedError

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
        found: list[Source] = []
        failures: list[SourceFailure] = []
        for subreddit in names:
            sources, problem = self._listing(subreddit, query, after, budget)
            found.extend(sources)
            if problem is not None:
                failures.append(problem)
        if failures and len(failures) == len(names):
            return RedditResult(failure=failures[0], partial=worth_naming(failures[1:]))
        kept = tuple(best_matches(found, query_words(query), max(1, limit)))
        return RedditResult(
            kept, newest(kept), self.coverage_note(kept), origin=self.name, partial=worth_naming(failures)
        )

    def _listing(
        self, subreddit: str, query: str, after: datetime | None, budget: RequestBudget | None
    ) -> tuple[list[Source], SourceFailure | None]:
        key = self.listing_key(subreddit, query, after)
        cached = self.cache.get(key)
        if cached is not None:
            for source in cached:
                self._remember(source)
            return list(cached), None
        fetched = self.http.get(self.listing_path, self.listing_params(subreddit, query, after), budget)
        if fetched.failure is not None:
            return [], fetched.failure
        sources = [s for post in parse_items(fetched.items, RawPost) if (s := self._post(post)) is not None]
        self.cache.put(key, sources)
        return sources, None

    def _post(self, post: RawPost) -> Source | None:
        """A thread as a Source (see post_to_source). Its details are remembered for reading its comments."""
        thread_id = bare_id(post.id)
        if thread_id is not None:
            self._threads[thread_id] = ThreadInfo(post.subreddit, post.title, bool(post.over_18))
        return post_to_source(post, self.name, self.salt, self._now())

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
        key: CacheKey = (self.name, "comments", tid, depth, size)
        cached = self.cache.get(key)
        if cached is not None:
            return RedditResult(cached, newest(cached), self.coverage_note(cached), origin=self.name)
        thread, problem = self._thread(tid, budget)
        if thread is None:
            return RedditResult(failure=problem or failure("not_found", f"{self.label}: no such thread"))
        if thread.over_18:
            return RedditResult(origin=self.name)  # never read an over-18 thread
        fetched = self.http.get(self.comments_path, self.comment_params(tid, size), budget)
        if fetched.failure is not None:
            return RedditResult(failure=fetched.failure)
        sources = comments_to_sources(
            parse_items(fetched.items, RawComment), tid, thread, depth, self.salt, self.name, self._now()
        )
        self.cache.put(key, sources)
        return RedditResult(tuple(sources), newest(sources), self.coverage_note(sources), origin=self.name)

    def with_comments(
        self, thread: Source, depth: int = 3, limit: int = 100, budget: RequestBudget | None = None
    ) -> RedditResult:
        """The thread as it was handed in, with its comments as a tree on it. One request, cached for 24 hours."""
        tid = bare_id(str(thread.url).rstrip("/").split("/")[-1])
        if tid is None:
            return RedditResult(failure=failure("not_found", "not a Reddit thread"))
        size = max(1, min(limit, MAX_LIMIT))
        key: CacheKey = (self.name, "thread_comments", tid, depth, size)
        cached = self.cache.get(key)
        if cached:  # only the comments: a near-duplicate's URL on this copy of the thread must survive
            again = add_comments(thread, cached[0].comments)
            return RedditResult((again,), newest([again]), self.coverage_note([again]), origin=self.name)
        fetched = self.http.get(self.comments_path, self.comment_params(tid, size), budget)
        if fetched.failure is not None:
            return RedditResult(failure=fetched.failure)
        comments = comments_to_tree(parse_items(fetched.items, RawComment), tid, depth, self.salt)
        updated = add_comments(thread, comments)
        self.cache.put(key, [updated])
        return RedditResult((updated,), newest([updated]), self.coverage_note([updated]), origin=self.name)

    def _thread(self, tid: str, budget: RequestBudget | None) -> tuple[ThreadInfo | None, SourceFailure | None]:
        if tid in self._threads:
            return self._threads[tid], None
        fetched = self.http.get(self.lookup_path, {"ids": tid}, budget)
        if fetched.failure is not None:
            return None, fetched.failure
        for post in parse_items(fetched.items, RawPost):
            if bare_id(post.id) == tid:
                self._post(post)
                return self._threads.get(tid), None
        return None, failure("not_found", f"{self.label}: no such thread")


def post_to_source(post: RawPost, origin: RedditOrigin, salt: str | None, fetched_at: datetime) -> Source | None:
    """A thread as a Source: its title, then its text. None if it is gone, removed, over 18 or by a bot."""
    thread_id = bare_id(post.id)
    if thread_id is None:
        return None
    if post.over_18 or post.removed_by_category or is_gone(post.author) or is_bot(post.author):
        return None
    if is_gone(post.title) or (post.selftext or "").strip().lower() in ("[deleted]", "[removed]"):
        return None
    selftext = (post.selftext or "").strip()
    return make_source(
        origin=origin,
        subreddit=post.subreddit,
        thread_id=thread_id,
        title=post.title,
        text=f"{post.title.strip()}\n\n{selftext}" if selftext else post.title,
        published_at=utc_from_epoch(post.created_utc),
        author_digest=author_hash(post.author, salt),
        fetched_at=fetched_at,
        upvotes=post.score,
        comment_count=post.num_comments,
    )


def comments_to_tree(
    comments: Sequence[RawComment], tid: str, max_depth: int, salt: str | None
) -> tuple[SourceComment, ...]:
    """A thread's comments for Source.comments: their depth and parent kept, authors only as salted hashes (D-53)."""
    mine = [c for c in comments if bare_id(c.link_id) == tid and bare_id(c.id) is not None]
    parents = {bare_id(c.id): c.parent_id for c in mine}
    deepest = min(max_depth, MAX_COMMENT_DEPTH)
    tree: dict[str, SourceComment] = {}
    for comment in mine:
        cid = bare_id(comment.id)
        if cid is None or cid in tree or is_gone(comment.body) or is_gone(comment.author) or is_bot(comment.author):
            continue
        depth = comment_depth(comment.parent_id, parents)
        if depth > deepest:
            continue
        try:
            tree[cid] = SourceComment(
                id=cid,
                parent_id=bare_id(comment.parent_id),
                depth=depth,
                upvotes=comment.score or 0,
                text=clean_text(comment.body or "")[:MAX_COMMENT_CHARS].strip(),
                author_hash=author_hash(comment.author, salt),
            )
        except ValueError:  # pydantic's ValidationError is a ValueError: skip the comment, never crash the search
            continue
    return tuple(tree.values())[:MAX_SOURCE_COMMENTS]


def add_comments(thread: Source, comments: tuple[SourceComment, ...]) -> Source:
    return Source.model_validate({**thread.model_dump(), "comments": comments})


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


# --- the chain -------------------------------------------------------------------------------------


class ArchiveRedditSource:
    """The archives in order. The next is asked only if the one before failed or found nothing."""

    name = "archive"

    def __init__(self, archives: Sequence[RedditSource]) -> None:
        if not archives:
            raise ValueError("at least one archive is needed")
        self.archives = list(archives)

    def search_threads(
        self,
        query: str,
        subreddits: Sequence[str],
        after: datetime | None = None,
        limit: int = 25,
        budget: RequestBudget | None = None,
    ) -> RedditResult:
        return self._first(lambda a: a.search_threads(query, subreddits, after, limit, budget))

    def get_comments(
        self, thread_id: str, depth: int = 3, limit: int = 100, budget: RequestBudget | None = None
    ) -> RedditResult:
        return self._first(lambda a: a.get_comments(thread_id, depth, limit, budget))

    def with_comments(
        self, thread: Source, depth: int = 3, limit: int = 100, budget: RequestBudget | None = None
    ) -> RedditResult:
        return self._first(lambda a: a.with_comments(thread, depth, limit, budget))

    def _first(self, ask: Callable[[RedditSource], RedditResult]) -> RedditResult:
        failures: list[SourceFailure] = []
        partial: list[SourceFailure] = []
        before: list[SourceFailure] = []
        answered: RedditResult | None = None
        for archive in self.archives:
            result = ask(archive)
            if result.sources:
                caps = [f for f in (*failures, *partial) if f.reason == "quota_exhausted"]
                return dataclasses.replace(result, partial=(*result.partial, *caps))
            partial.extend(result.partial)
            if result.failure is not None:
                failures.append(result.failure)
            elif answered is None:
                answered = result  # it answered, with nothing: try the next, but this is not a failure
                before = [*failures, *partial]  # what had already gone wrong stays named
        if answered is not None:
            caps = [f for f in (*failures, *partial) if f.reason == "quota_exhausted"]
            return dataclasses.replace(answered, partial=worth_naming([*before, *caps]))
        if not failures:
            return RedditResult()
        detail = "; ".join(f.detail for f in failures)
        return RedditResult(
            failure=failure(failures[0].reason, detail), partial=worth_naming([*failures[1:], *partial])
        )
