"""Reddit through the Arctic Shift archive (US-12): the default Reddit source.

Arctic Shift (arctic-shift.photon-reddit.com) keeps a copy of public Reddit posts and comments and offers a
free, keyless API. We follow its rules: at most one request a second from the whole app, our User-Agent on
every request, and on HTTP 429 (or 422 "Timeout. Maybe slow down a bit") we wait for X-RateLimit-Reset (at most
30 s) and retry once. After 3 failures in a row it is paused for 10 minutes, then one trial request.

Its copy lags Reddit by a few days and scores settle after about 36 hours, so results carry the coverage note
"Reddit via archive: may miss posts from the last few days". Comments reach the archive a day or two after their
thread: on 3 Nov 2026, threads from 31 Oct had all their comments there and most threads from 1-2 Nov had none.
So the listing stops 2 days before today (COMMENT_LAG): a thread is only worth reading once its replies are in.

Threads are found by asking for a subreddit's newest 100 posts inside a date window and matching the query's
words here. Arctic Shift's own keyword search (title, selftext, query) is "not supported with very active
subreddits": on 3 Nov 2026 it answered 422 at once for r/SuggestALaptop and r/laptops, while the plain listing
answered in about 2 s. The listing does not depend on the query, so one cached listing serves every query.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta

import httpx

from app.contracts import RedditOrigin, Source
from app.db.breaker import CircuitBreaker

from .archive import MAX_LIMIT, ArchiveSource, _utc_now
from .base import IntervalLimiter
from .cache import CacheKey, RedditCache
from .settings import DEFAULT_ARCTIC_SHIFT_BASE_URL

COVERAGE_NOTE = "Reddit via archive: may miss posts from the last few days"
DEFAULT_WINDOW = timedelta(days=180)
COMMENT_LAG = timedelta(days=2)
BREAKER_FAILURES = 3
BREAKER_COOLDOWN_S = 600.0

# One limiter for the whole process, however many sources are built: Arctic Shift asks for 1 request a second.
SHARED_LIMITER = IntervalLimiter(1.0)


class ArcticShiftSource(ArchiveSource):
    name: RedditOrigin = "arctic_shift"
    label = "Arctic Shift"
    listing_path = "/api/posts/search"
    comments_path = "/api/comments/search"
    lookup_path = "/api/posts/ids"
    rate_limit_statuses = frozenset({429, 422})

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
        super().__init__(
            http,
            base_url=base_url,
            salt=salt,
            cache=cache,
            limiter=limiter if limiter is not None else SHARED_LIMITER,
            breaker=breaker if breaker is not None else CircuitBreaker(BREAKER_FAILURES, BREAKER_COOLDOWN_S),
            now=now,
            sleep=sleep,
        )

    def _since(self, after: datetime | None) -> str:
        return (after or self._now() - DEFAULT_WINDOW).date().isoformat()

    def _until(self) -> str:
        return (self._now() - COMMENT_LAG).date().isoformat()

    def listing_params(self, subreddit: str, query: str, after: datetime | None) -> dict[str, str | int]:
        return {
            "subreddit": subreddit,
            "after": self._since(after),
            "before": self._until(),
            "limit": MAX_LIMIT,
            "sort": "desc",
        }

    def listing_key(self, subreddit: str, query: str, after: datetime | None) -> CacheKey:
        # The query is matched here, not sent, so one listing serves every query.
        return (self.name, "listing", subreddit.lower(), self._since(after), self._until())

    def comment_params(self, thread_id: str, size: int) -> dict[str, str | int]:
        return {"link_id": thread_id, "limit": size, "sort": "desc"}

    def coverage_note(self, sources: Sequence[Source]) -> str | None:
        return COVERAGE_NOTE
