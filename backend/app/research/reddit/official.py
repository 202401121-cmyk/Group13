"""Reddit's own API through PRAW (US-12), for when our Data API access request is approved.

Used only with REDDIT_SOURCE=official and all of REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET and REDDIT_USER_AGENT
(otherwise the archives are used). Read-only, app-only sign-in: the app's own id and secret, never a user's
password. PRAW keeps to Reddit's rate limits itself; on top of that every call counts against the search's
request budget, has a 10 s timeout, and goes through a breaker (paused 10 minutes after 3 failures in a row).

The same rules as the archives apply (base.py): over-18, deleted, removed and bot items are skipped, only a
salted hash of each username is kept, and Reddit text never trains or fine-tunes any model.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from app.contracts import RedditOrigin, Source, SourceFailure
from app.db.breaker import CircuitBreaker

from .archive import add_comments, comments_to_sources, comments_to_tree, post_to_source
from .base import (
    FailureReason,
    RedditResult,
    RequestBudget,
    ThreadInfo,
    bare_id,
    best_matches,
    failure,
    newest,
    query_words,
    valid_subreddit,
    worth_naming,
)
from .cache import CacheKey, RedditCache
from .parsing import RawComment, RawPost

log = logging.getLogger(__name__)

MAX_LIMIT = 100
BREAKER_FAILURES = 3
BREAKER_COOLDOWN_S = 600.0
REQUEST_TIMEOUT_S = 10

# prawcore's exception names, and what each means for us. Subclasses count too (matched along the MRO).
_REASONS: dict[str, FailureReason] = {
    "TooManyRequests": "rate_limited",
    "NotFound": "not_found",
    "Redirect": "not_found",  # Reddit redirects a missing subreddit to its search page
    "Forbidden": "blocked",
    "UnavailableForLegalReasons": "blocked",
    "OAuthException": "blocked",
    "InvalidToken": "blocked",
    "ResponseException": "error",
    "RequestException": "error",
}


def _utc_now() -> datetime:
    return datetime.now(UTC)


def failure_for(exc: Exception) -> SourceFailure:
    """What went wrong, from the exception's class only: its message may echo request details."""
    for cls in type(exc).__mro__:
        if cls.__name__ in _REASONS:
            return failure(_REASONS[cls.__name__], f"Reddit API: {type(exc).__name__}")
    if isinstance(exc, TimeoutError):
        return failure("timeout", f"Reddit API: no reply within {REQUEST_TIMEOUT_S}s")
    return failure("error", f"Reddit API: {type(exc).__name__}")


class OfficialRedditSource:
    name: RedditOrigin = "official"

    def __init__(
        self,
        reddit: Any,  # a praw.Reddit in read-only mode; tests pass a fake with the same few methods
        *,
        salt: str | None = None,
        cache: RedditCache | None = None,
        breaker: CircuitBreaker | None = None,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.reddit = reddit
        self.salt = salt
        self.cache = cache if cache is not None else RedditCache()
        self.breaker = breaker if breaker is not None else CircuitBreaker(BREAKER_FAILURES, BREAKER_COOLDOWN_S)
        self._now = now

    def search_threads(
        self,
        query: str,
        subreddits: Sequence[str],
        after: datetime | None = None,
        limit: int = 25,
        budget: RequestBudget | None = None,
    ) -> RedditResult:
        """Reddit's own search in each subreddit, newest first, then ranked by the query's words like the archives."""
        names = [s for s in subreddits if valid_subreddit(s)]
        words = query_words(query)
        if not names or not query.strip():
            return RedditResult(failure=failure("not_found", "no subreddit or no words to search for"))
        size = max(1, min(limit, MAX_LIMIT))
        found: list[Source] = []
        failures: list[SourceFailure] = []
        for subreddit in names:
            sources, problem = self._search(subreddit, " ".join(words) or query.strip(), size, budget)
            found.extend(s for s in sources if after is None or (s.published_at and s.published_at >= after))
            if problem is not None:
                failures.append(problem)
        if failures and len(failures) == len(names):
            return RedditResult(failure=failures[0], partial=worth_naming(failures[1:]))
        kept = tuple(best_matches(found, words, max(1, limit)))
        return RedditResult(kept, newest(kept), None, origin=self.name, partial=worth_naming(failures))

    def _search(
        self, subreddit: str, q: str, size: int, budget: RequestBudget | None
    ) -> tuple[list[Source], SourceFailure | None]:
        key: CacheKey = (self.name, "search", subreddit.lower(), q.lower(), size)
        cached = self.cache.get(key)
        if cached is not None:
            return list(cached), None
        problem = self._may_call(budget)
        if problem is not None:
            return [], problem
        try:
            listing = self.reddit.subreddit(subreddit).search(q, sort="new", time_filter="year", limit=size)
            posts = [raw_post(s) for s in listing]
        except Exception as exc:  # PRAW and prawcore raise many kinds; none may crash the search (build rule 9)
            self.breaker.record_failure()
            return [], failure_for(exc)
        self.breaker.record_success()
        sources = [s for p in posts if p is not None if (s := post_to_source(p, self.name, self.salt, self._now()))]
        self.cache.put(key, sources)
        return sources, None

    def get_comments(
        self, thread_id: str, depth: int = 3, limit: int = 100, budget: RequestBudget | None = None
    ) -> RedditResult:
        tid = bare_id(thread_id)
        if tid is None:
            return RedditResult(failure=failure("not_found", "not a Reddit thread id"))
        size = max(1, min(limit, MAX_LIMIT))
        key: CacheKey = (self.name, "comments", tid, depth, size)
        cached = self.cache.get(key)
        if cached is not None:
            return RedditResult(cached, newest(cached), None, origin=self.name)
        problem = self._may_call(budget)
        if problem is not None:
            return RedditResult(failure=problem)
        try:
            submission = self.reddit.submission(id=tid)
            thread = ThreadInfo(
                _text(submission.subreddit.display_name), _text(submission.title), bool(submission.over_18)
            )
            if thread.over_18:
                self.breaker.record_success()
                return RedditResult(origin=self.name)  # never read an over-18 thread
            submission.comments.replace_more(limit=0)  # no extra requests for collapsed replies
            comments = [raw_comment(c, tid, thread.subreddit) for c in submission.comments.list()[:size]]
        except Exception as exc:  # see _search
            self.breaker.record_failure()
            return RedditResult(failure=failure_for(exc))
        self.breaker.record_success()
        raw = [c for c in comments if c is not None]
        sources = comments_to_sources(raw, tid, thread, depth, self.salt, self.name, self._now())
        self.cache.put(key, sources)
        return RedditResult(tuple(sources), newest(sources), None, origin=self.name)

    def with_comments(
        self, thread: Source, depth: int = 3, limit: int = 100, budget: RequestBudget | None = None
    ) -> RedditResult:
        """The thread as it was handed in, with its comments as a tree on it, as the archives give it."""
        tid = bare_id(str(thread.url).rstrip("/").split("/")[-1])
        if tid is None:
            return RedditResult(failure=failure("not_found", "not a Reddit thread"))
        size = max(1, min(limit, MAX_LIMIT))
        key: CacheKey = (self.name, "thread_comments", tid, depth, size)
        cached = self.cache.get(key)
        if cached:  # only the comments: a near-duplicate's URL on this copy of the thread must survive
            again = add_comments(thread, cached[0].comments)
            return RedditResult((again,), newest([again]), None, origin=self.name)
        problem = self._may_call(budget)
        if problem is not None:
            return RedditResult(failure=problem)
        subreddit = (thread.community or "").removeprefix("r/")
        try:
            submission = self.reddit.submission(id=tid)
            submission.comments.replace_more(limit=0)  # no extra requests for collapsed replies
            comments = [raw_comment(c, tid, subreddit) for c in submission.comments.list()[:size]]
        except Exception as exc:  # see _search
            self.breaker.record_failure()
            return RedditResult(failure=failure_for(exc))
        self.breaker.record_success()
        tree = comments_to_tree([c for c in comments if c is not None], tid, depth, self.salt)
        updated = add_comments(thread, tree)
        self.cache.put(key, [updated])
        return RedditResult((updated,), newest([updated]), None, origin=self.name)

    def _may_call(self, budget: RequestBudget | None) -> SourceFailure | None:
        if budget is not None and budget.left == 0:
            return failure("quota_exhausted", "Reddit API: this search's Reddit requests are used up")
        if not self.breaker.allow():
            return failure("error", "Reddit API is paused after repeated failures")
        if budget is not None:
            budget.take()
        return None


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _author(item: Any) -> str | None:
    """The author's name only to hash it here and then drop it; None (a deleted account) when there is none."""
    author = getattr(item, "author", None)
    name = getattr(author, "name", None)
    return name if isinstance(name, str) else None


def raw_post(submission: Any) -> RawPost | None:
    """A PRAW Submission as the same lenient model the archives use, or None if it does not fit."""
    try:
        return RawPost(
            id=_text(getattr(submission, "id", "")),
            subreddit=_text(getattr(getattr(submission, "subreddit", None), "display_name", "")),
            title=_text(getattr(submission, "title", "")),
            selftext=_text(getattr(submission, "selftext", "")),
            author=_author(submission) or "[deleted]",
            created_utc=getattr(submission, "created_utc", None),
            over_18=bool(getattr(submission, "over_18", False)),
            removed_by_category=getattr(submission, "removed_by_category", None),
            score=getattr(submission, "score", None),
            num_comments=getattr(submission, "num_comments", None),
        )
    except ValueError:
        return None


def raw_comment(comment: Any, thread_id: str, subreddit: str) -> RawComment | None:
    try:
        return RawComment(
            id=_text(getattr(comment, "id", "")),
            link_id=f"t3_{thread_id}",
            parent_id=_text(getattr(comment, "parent_id", "")) or None,
            subreddit=subreddit,
            body=_text(getattr(comment, "body", "")),
            author=_author(comment) or "[deleted]",
            created_utc=getattr(comment, "created_utc", None),
            score=getattr(comment, "score", None),
        )
    except ValueError:
        return None


def build_official_source(
    client_id: str,
    client_secret: str,
    user_agent: str,
    *,
    salt: str | None,
    cache: RedditCache | None = None,
    breaker: CircuitBreaker | None = None,
) -> OfficialRedditSource:
    """A read-only PRAW client with app-only sign-in. Creating it sends no request."""
    import praw  # here, not at the top: only REDDIT_SOURCE=official needs it

    reddit = praw.Reddit(
        client_id=client_id,
        client_secret=client_secret,
        user_agent=user_agent,
        check_for_updates=False,
        check_for_async=False,
        timeout=REQUEST_TIMEOUT_S,
    )
    reddit.read_only = True
    log.info("reddit: using Reddit's own API (read-only)")
    return OfficialRedditSource(reddit, salt=salt, cache=cache, breaker=breaker)
