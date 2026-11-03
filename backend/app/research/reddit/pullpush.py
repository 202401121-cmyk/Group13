"""Reddit through the PullPush archive (US-12): the fallback when Arctic Shift fails or finds nothing.

PullPush (api.pullpush.io) is an older copy of Reddit that has not been kept up to date since about May 2025.
So its results always say how old they are ("Reddit via older archive (to May 2025)"), and they are only used
when Arctic Shift had nothing: an older archive never replaces fresher data.

It is asked politely: at most one request every 2 seconds from the whole app, our User-Agent, a 10 s timeout,
one retry after a rate limit, and a short breaker (paused for 30 s after 3 failures in a row).

UNCONFIRMED: PullPush's documentation could not be read on 3 Nov 2026 (pullpush.io answered with a Cloudflare
challenge and api.pullpush.io with HTTP 522). The paths and parameters below follow the Pushshift-style API it
is known to copy (/reddit/search/submission/ and /reddit/search/comment/ with q, subreddit, after, size, sort,
sort_type, link_id, ids), and replies are read leniently. Check them with `python -m scripts.smoke_sources`.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from datetime import datetime

import httpx

from app.contracts import RedditOrigin, Source
from app.db.breaker import CircuitBreaker

from .archive import MAX_LIMIT, ArchiveSource, _utc_now
from .base import IntervalLimiter, newest, query_words
from .cache import CacheKey, RedditCache
from .settings import DEFAULT_PULLPUSH_BASE_URL

BREAKER_FAILURES = 3
BREAKER_COOLDOWN_S = 30.0

# One limiter for the whole process: one request every 2 seconds.
SHARED_LIMITER = IntervalLimiter(2.0)


class PullPushSource(ArchiveSource):
    name: RedditOrigin = "pullpush"
    label = "PullPush"
    listing_path = "/reddit/search/submission/"
    comments_path = "/reddit/search/comment/"
    lookup_path = "/reddit/search/submission/"

    def __init__(
        self,
        http: httpx.Client,
        *,
        base_url: str = DEFAULT_PULLPUSH_BASE_URL,
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

    def listing_params(self, subreddit: str, query: str, after: datetime | None) -> dict[str, str | int]:
        # No default date window: the archive stops in 2025, so a window counted back from today finds nothing.
        params: dict[str, str | int] = {
            "subreddit": subreddit,
            "q": " ".join(query_words(query)) or query.strip(),
            "size": MAX_LIMIT,
            "sort": "desc",
            "sort_type": "created_utc",
        }
        if after is not None:
            params["after"] = int(after.timestamp())
        return params

    def listing_key(self, subreddit: str, query: str, after: datetime | None) -> CacheKey:
        since = int(after.timestamp()) if after is not None else 0
        return (self.name, "search", subreddit.lower(), " ".join(query_words(query)) or query.strip().lower(), since)

    def comment_params(self, thread_id: str, size: int) -> dict[str, str | int]:
        return {"link_id": thread_id, "size": size, "sort": "desc", "sort_type": "created_utc"}

    def coverage_note(self, sources: Sequence[Source]) -> str | None:
        """How far this archive goes, from its newest item: "Reddit via older archive (to May 2025)"."""
        last = newest(sources)
        return f"Reddit via older archive (to {last:%B %Y})" if last is not None else None
