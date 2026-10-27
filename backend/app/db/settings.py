"""Database settings (US-01): one optional connection string and one time limit.

DATABASE_URL not set, or blank, means the database is "off": everything works and nothing is saved (build rule 10).
The URL holds the password, so it is never printed or logged. describe_host() gives the only part logs may show.
Both values come from the root .env (the same file as the LLM keys) or the real environment, which wins.
"""

from __future__ import annotations

import logging
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

from dotenv import load_dotenv

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 2.0
MIN_TIMEOUT_S = 0.5
MAX_TIMEOUT_S = 30.0


@dataclass(frozen=True, repr=False)
class DatabaseSettings:
    url: str | None = None
    timeout_s: float = DEFAULT_TIMEOUT_S

    @property
    def enabled(self) -> bool:
        return self.url is not None

    def __repr__(self) -> str:
        # Never the URL, not even in a traceback or a debugger.
        return f"DatabaseSettings(enabled={self.enabled}, host={describe_host(self.url)!r}, timeout_s={self.timeout_s})"


def load_settings(env: Mapping[str, str] | None = None) -> DatabaseSettings:
    """Read DATABASE_URL and DB_TIMEOUT_SECONDS. With no `env`, python-dotenv loads .env first."""
    if env is None:
        load_dotenv()  # finds the root .env walking up from here; real environment variables win
        env = os.environ
    url = (env.get("DATABASE_URL") or "").strip() or None
    return DatabaseSettings(url=url, timeout_s=_timeout(env.get("DB_TIMEOUT_SECONDS")))


def _timeout(raw: str | None) -> float:
    if raw is None or not raw.strip():
        return DEFAULT_TIMEOUT_S
    try:
        value = float(raw)
    except ValueError:
        value = math.nan
    if not math.isfinite(value) or value <= 0:
        log.warning("DB_TIMEOUT_SECONDS must be a positive number of seconds; using %s", DEFAULT_TIMEOUT_S)
        return DEFAULT_TIMEOUT_S
    return min(max(value, MIN_TIMEOUT_S), MAX_TIMEOUT_S)


def describe_host(url: str | None) -> str:
    """The host name only, the one part of the URL that logs may show."""
    if not url:
        return "none"
    try:
        host = urlsplit(url).hostname
    except ValueError:
        host = None
    return host or "unknown"
