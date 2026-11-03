"""Build the Reddit source the settings ask for (US-12).

REDDIT_SOURCE=archive (the default): the archives in REDDIT_ARCHIVES order, Arctic Shift then PullPush.
REDDIT_SOURCE=official: Reddit's own API through PRAW (load_reddit_settings already fell back to "archive" if
any of its three settings is missing).
REDDIT_SOURCE=off: no Reddit source at all (None); research goes on without community evidence.
"""

from __future__ import annotations

import httpx

from .archive import ArchiveRedditSource
from .arctic_shift import ArcticShiftSource
from .base import RedditSource
from .cache import RedditCache
from .official import build_official_source
from .pullpush import PullPushSource
from .settings import RedditSettings, load_reddit_settings


def build_reddit_source(
    settings: RedditSettings | None = None,
    http: httpx.Client | None = None,
    cache: RedditCache | None = None,
) -> RedditSource | None:
    settings = settings if settings is not None else load_reddit_settings()
    if settings.mode == "off":
        return None
    http = http if http is not None else httpx.Client()
    cache = cache if cache is not None else RedditCache()
    if settings.mode == "official" and settings.client_id and settings.client_secret and settings.user_agent:
        return build_official_source(
            settings.client_id, settings.client_secret, settings.user_agent, salt=settings.user_salt, cache=cache
        )
    return build_archives(settings, http, cache)


def build_archives(settings: RedditSettings, http: httpx.Client, cache: RedditCache) -> ArchiveRedditSource:
    archives: list[RedditSource] = []
    for name in settings.archives:
        if name == "arctic_shift":
            archives.append(
                ArcticShiftSource(http, base_url=settings.arctic_shift_base_url, salt=settings.user_salt, cache=cache)
            )
        else:
            archives.append(
                PullPushSource(http, base_url=settings.pullpush_base_url, salt=settings.user_salt, cache=cache)
            )
    return ArchiveRedditSource(archives)
