"""Reddit source settings (US-12), from the root .env or the real environment (which wins).

REDDIT_SOURCE      archive (default) | official | off
REDDIT_ARCHIVES    the archives to try, in order (default "arctic_shift,pullpush")
ARCTIC_SHIFT_BASE_URL, PULLPUSH_BASE_URL   where the archives are (https only)
REDDIT_USER_SALT   salt for hashing usernames (author_hash). Without it no author hash is kept at all.
REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT   Reddit's own API; only for REDDIT_SOURCE=official

A bad value never stops the app: it is logged by name (never its value) and the default is used. "official"
without all three API settings falls back to the archives, so a search never loses Reddit by mistake.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from importlib.metadata import PackageNotFoundError, version
from typing import Literal, get_args

from dotenv import load_dotenv

log = logging.getLogger(__name__)

RedditMode = Literal["archive", "official", "off"]
ArchiveName = Literal["arctic_shift", "pullpush"]

DEFAULT_ARCHIVES: tuple[ArchiveName, ...] = ("arctic_shift", "pullpush")
DEFAULT_ARCTIC_SHIFT_BASE_URL = "https://arctic-shift.photon-reddit.com"
DEFAULT_PULLPUSH_BASE_URL = "https://api.pullpush.io"
REPOSITORY_URL = "https://github.com/202401121-cmyk/Group13"
REQUEST_TIMEOUT_S = 10.0


def app_version() -> str:
    try:
        return version("shopsense-backend")
    except PackageNotFoundError:
        return "0.1.0"


def archive_user_agent() -> str:
    """Who we are, for the archives' operators: the app, why it exists, and where to find us."""
    return f"ShopSense/{app_version()} (IT314 student project; +{REPOSITORY_URL})"


@dataclass(frozen=True, repr=False)
class RedditSettings:
    mode: RedditMode = "archive"
    archives: tuple[ArchiveName, ...] = DEFAULT_ARCHIVES
    arctic_shift_base_url: str = DEFAULT_ARCTIC_SHIFT_BASE_URL
    pullpush_base_url: str = DEFAULT_PULLPUSH_BASE_URL
    user_salt: str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    user_agent: str | None = None  # Reddit's API wants its own format: "<platform>:<app id>:<version> (by /u/<name>)"

    @property
    def official_ready(self) -> bool:
        return bool(self.client_id and self.client_secret and self.user_agent)

    def __repr__(self) -> str:
        # Never the salt, the client id or the secret, not even in a traceback.
        return (
            f"RedditSettings(mode={self.mode!r}, archives={self.archives!r}, salt_set={self.user_salt is not None}, "
            f"official_ready={self.official_ready})"
        )


def load_reddit_settings(env: Mapping[str, str] | None = None) -> RedditSettings:
    """Read the Reddit settings. With no `env`, python-dotenv loads .env first."""
    if env is None:
        load_dotenv()
        env = os.environ

    def get(name: str) -> str | None:
        return (env.get(name) or "").strip() or None

    mode = _mode(get("REDDIT_SOURCE"))
    settings = RedditSettings(
        mode=mode,
        archives=_archives(get("REDDIT_ARCHIVES")),
        arctic_shift_base_url=_https(
            "ARCTIC_SHIFT_BASE_URL", get("ARCTIC_SHIFT_BASE_URL"), DEFAULT_ARCTIC_SHIFT_BASE_URL
        ),
        pullpush_base_url=_https("PULLPUSH_BASE_URL", get("PULLPUSH_BASE_URL"), DEFAULT_PULLPUSH_BASE_URL),
        user_salt=get("REDDIT_USER_SALT"),
        client_id=get("REDDIT_CLIENT_ID"),
        client_secret=get("REDDIT_CLIENT_SECRET"),
        user_agent=get("REDDIT_USER_AGENT"),
    )
    if settings.user_salt is None and mode != "off":
        log.warning("REDDIT_USER_SALT is not set: Reddit sources will carry no author hash")
    if mode == "official" and not settings.official_ready:
        log.warning(
            "REDDIT_SOURCE=official needs REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET and REDDIT_USER_AGENT; "
            "using the archives instead"
        )
        settings = replace(settings, mode="archive")
    return settings


def _mode(raw: str | None) -> RedditMode:
    if raw is None:
        return "archive"
    value = raw.lower()
    for mode in get_args(RedditMode):
        if value == mode:
            return mode  # type: ignore[no-any-return]
    log.warning("REDDIT_SOURCE must be archive, official or off; using archive")
    return "archive"


def _archives(raw: str | None) -> tuple[ArchiveName, ...]:
    if raw is None:
        return DEFAULT_ARCHIVES
    known: tuple[ArchiveName, ...] = get_args(ArchiveName)
    chosen: list[ArchiveName] = []
    for part in raw.lower().split(","):
        name = part.strip()
        match = next((a for a in known if a == name), None)
        if match is None:
            if name:
                log.warning("REDDIT_ARCHIVES: unknown archive ignored (known: arctic_shift, pullpush)")
            continue
        if match not in chosen:
            chosen.append(match)
    return tuple(chosen) or DEFAULT_ARCHIVES


def _https(name: str, raw: str | None, default: str) -> str:
    if raw is None:
        return default
    if not re.match(r"^https://[^\s/]+", raw):
        log.warning("%s must be an https:// URL; using the default", name)
        return default
    return raw.rstrip("/")
