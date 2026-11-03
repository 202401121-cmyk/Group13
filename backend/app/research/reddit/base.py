"""What every Reddit source does, and the rules all of them share (US-12).

Three sources implement RedditSource: the Arctic Shift archive (the default), the PullPush archive (an older
fallback), and Reddit's own API through PRAW (only once our access request is approved). Whichever one answers,
the stage gets the same contract models: a Source per post or comment, with the canonical reddit.com permalink,
the time we fetched it and which source it came from.

Rules for Reddit text, everywhere in ShopSense:
- It is data, never instructions (build rule 3), and it goes only to LLM providers that do not train on inputs
  (DataClass.REDDIT, US-46). It is never used to train or fine-tune any model of ours.
- Deleted, removed and over-18 posts and comments are skipped, and so are bot posts (AutoModerator).
- Usernames are never stored, logged or shown. Only a salted hash of the name is kept (author_hash), so US-13 can
  count how many different people agree.
"""

from __future__ import annotations

import hashlib
import re
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol

from app.contracts import RedditOrigin, Source, SourceFailure, SourceTier, SourceType
from app.contracts.research import MAX_SOURCE_CHARS

FailureReason = Literal["timeout", "rate_limited", "blocked", "not_found", "quota_exhausted", "error"]

SUBREDDIT_PATTERN = re.compile(r"^[A-Za-z0-9_]{2,21}$")
REDDIT_ID_PATTERN = re.compile(r"^[a-z0-9]{1,13}$")
GONE = frozenset({"[deleted]", "[removed]", ""})  # what Reddit leaves when a post, comment or account is gone
BOTS = frozenset({"automoderator"})
DEFAULT_QUALITY = 0.5  # US-13 scores threads; until then every Reddit source is average


@dataclass(frozen=True)
class RedditResult:
    """What one call returns. Never raises: a failure is reported here, and the search goes on."""

    sources: tuple[Source, ...] = ()
    newest_at: datetime | None = None  # the newest post or comment in `sources`
    coverage_note: str | None = None  # plain words for SearchResult.coverage_note, such as an archive's lag
    failure: SourceFailure | None = None
    origin: RedditOrigin | None = None  # which source answered (None if none did)


class RedditSource(Protocol):
    """Search threads in some subreddits, and read one thread's comments."""

    name: RedditOrigin

    def search_threads(
        self,
        query: str,
        subreddits: Sequence[str],
        after: datetime | None = None,
        limit: int = 25,
        budget: RequestBudget | None = None,
    ) -> RedditResult: ...

    def get_comments(
        self, thread_id: str, depth: int = 3, limit: int = 100, budget: RequestBudget | None = None
    ) -> RedditResult: ...


def failure(reason: FailureReason, detail: str) -> SourceFailure:
    return SourceFailure(source_type=SourceType.REDDIT, reason=reason, detail=detail[:300])


# --- limits ---------------------------------------------------------------------------------------


class RequestBudget:
    """How many requests one search may still send (about 10, US-12). Cached answers cost nothing."""

    def __init__(self, max_requests: int = 10) -> None:
        self.max_requests = max_requests
        self.used = 0
        self._lock = threading.Lock()

    @property
    def left(self) -> int:
        return max(0, self.max_requests - self.used)

    def take(self) -> bool:
        with self._lock:
            if self.used >= self.max_requests:
                return False
            self.used += 1
            return True


class IntervalLimiter:
    """At most one request per `interval_s`, shared by every search in the process. Waiting calls go in order."""

    def __init__(
        self,
        interval_s: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.interval_s = interval_s
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._last: float | None = None

    def wait(self) -> None:
        with self._lock:
            if self._last is not None:
                pause = self._last + self.interval_s - self._clock()
                if pause > 0:
                    self._sleep(pause)
            self._last = self._clock()


# --- mapping archive items to contract Sources ----------------------------------------------------


def valid_subreddit(name: str) -> bool:
    return bool(SUBREDDIT_PATTERN.match(name))


def bare_id(value: str | None) -> str | None:
    """A Reddit id without its t1_/t3_ prefix, or None if it is not an id."""
    if not value:
        return None
    text = value.strip().lower()
    text = text[3:] if text[:3] in ("t1_", "t3_") else text
    return text if REDDIT_ID_PATTERN.match(text) else None


def permalink(subreddit: str, thread_id: str, comment_id: str | None = None) -> str:
    """The canonical reddit.com address, built from ids (never copied from the archive, so it is always clean)."""
    base = f"https://www.reddit.com/r/{subreddit}/comments/{thread_id}/"
    return f"{base}_/{comment_id}/" if comment_id else base


def author_hash(author: str | None, salt: str | None) -> str | None:
    """A salted sha256 of the username, so the same person counts once. None without a salt or a real name."""
    if not salt or not author or author.strip().lower() in GONE:
        return None
    return hashlib.sha256(f"{salt}:{author.strip().lower()}".encode()).hexdigest()


def is_gone(text: str | None) -> bool:
    return text is None or text.strip().lower() in GONE


def is_bot(author: str | None) -> bool:
    return (author or "").strip().lower() in BOTS


def utc_from_epoch(value: float | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(float(value), UTC)
    except (OverflowError, OSError, ValueError):
        return None


def make_source(
    *,
    origin: RedditOrigin,
    subreddit: str,
    thread_id: str,
    title: str,
    text: str,
    published_at: datetime | None,
    author_digest: str | None,
    fetched_at: datetime,
    comment_id: str | None = None,
) -> Source | None:
    """One contract Source, or None if the item cannot be one (an odd id, no text). Never raises."""
    if not valid_subreddit(subreddit) or bare_id(thread_id) is None:
        return None
    if comment_id is not None and bare_id(comment_id) is None:
        return None
    body = text.strip()[:MAX_SOURCE_CHARS].strip()  # strip after the cut too, as the contract does: the hash must match
    if not body:
        return None
    url = permalink(subreddit, thread_id, comment_id)
    try:
        return Source(
            id=f"src_{hashlib.sha256(url.encode()).hexdigest()[:16]}",
            url=url,
            type=SourceType.REDDIT,
            tier=SourceTier.COMMUNITY,
            title=title.strip()[:300],
            text=body,
            text_hash=hashlib.sha256(body.encode()).hexdigest(),
            fetched_at=fetched_at,
            published_at=published_at,
            community=f"r/{subreddit}",
            quality=DEFAULT_QUALITY,
            via="api",
            source_detail=origin,
            author_hash=author_digest,
        )
    except ValueError:  # pydantic's ValidationError is a ValueError: skip the item, never crash the search
        return None


def newest(sources: Sequence[Source]) -> datetime | None:
    dates = [s.published_at for s in sources if s.published_at is not None]
    return max(dates) if dates else None


@dataclass(frozen=True)
class ThreadInfo:
    """What a comment needs from its thread: the subreddit, the title, and whether the thread is over 18."""

    subreddit: str
    title: str
    over_18: bool = False
