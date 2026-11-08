"""Build the Reddit source the settings ask for (US-12).

REDDIT_SOURCE=archive (the default): the archives in REDDIT_ARCHIVES order, Arctic Shift then PullPush.
REDDIT_SOURCE=official: Reddit's own API through PRAW (load_reddit_settings already fell back to "archive" if
any of its three settings is missing).
REDDIT_SOURCE=off: no Reddit source at all (None); research goes on without community evidence.

A stage builds its sources per search, so anything that must outlive a search is shared here or in its module: the
breakers below, and the rate limiters. A breaker built per search could never reach its 3 failures.
"""

from __future__ import annotations

import functools

import httpx

from app.db.breaker import CircuitBreaker
from app.research.client import build_research_client

from . import arctic_shift, official, pullpush
from .archive import ArchiveRedditSource
from .arctic_shift import ArcticShiftSource
from .base import RedditSource
from .cache import RedditCache
from .official import build_official_source
from .pullpush import PullPushSource
from .settings import RedditSettings, load_reddit_settings

_BREAKER_LIMITS = {
    "arctic_shift": (arctic_shift.BREAKER_FAILURES, arctic_shift.BREAKER_COOLDOWN_S),
    "pullpush": (pullpush.BREAKER_FAILURES, pullpush.BREAKER_COOLDOWN_S),
    "official": (official.BREAKER_FAILURES, official.BREAKER_COOLDOWN_S),
}


@functools.cache
def shared_breakers(name: str) -> CircuitBreaker:
    """One breaker per Reddit source for the whole process."""
    return CircuitBreaker(*_BREAKER_LIMITS[name])


def build_reddit_source(
    settings: RedditSettings | None = None,
    http: httpx.Client | None = None,
    cache: RedditCache | None = None,
) -> RedditSource | None:
    """Raises ClientUnavailable if the archives need a client and none can be built; the stage then skips Reddit."""
    settings = settings if settings is not None else load_reddit_settings()
    if settings.mode == "off":
        return None
    cache = cache if cache is not None else RedditCache()
    if settings.mode == "official" and settings.client_id and settings.client_secret and settings.user_agent:
        return build_official_source(
            settings.client_id,
            settings.client_secret,
            settings.user_agent,
            salt=settings.user_salt,
            cache=cache,
            breaker=shared_breakers("official"),
        )
    return build_archives(settings, http if http is not None else build_research_client(), cache)


def build_archives(settings: RedditSettings, http: httpx.Client, cache: RedditCache) -> ArchiveRedditSource:
    archives: list[RedditSource] = []
    for name in settings.archives:
        if name == "arctic_shift":
            archives.append(
                ArcticShiftSource(
                    http,
                    base_url=settings.arctic_shift_base_url,
                    salt=settings.user_salt,
                    cache=cache,
                    breaker=shared_breakers("arctic_shift"),
                )
            )
        else:
            archives.append(
                PullPushSource(
                    http,
                    base_url=settings.pullpush_base_url,
                    salt=settings.user_salt,
                    cache=cache,
                    breaker=shared_breakers("pullpush"),
                )
            )
    return ArchiveRedditSource(archives)
