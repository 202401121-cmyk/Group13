"""Database settings (US-01, US-52): one optional connection string and two time limits.

DATABASE_URL not set, or blank, means the database is "off": everything works and nothing is saved (build rule 10).
DB_TIMEOUT_SECONDS (2 by default) bounds every call but one: a finished search's save has DB_SAVE_TIMEOUT_SECONDS (15 by
default, 2 to 15), because its row is the largest and it runs after the search, outside the 150 seconds (P-38). Never
more than 15: the frontend waits 170 seconds for research, which must cover the 150, the save and SAVE_GRACE_S, and the
Reddit cache serves an answer only for 24 hours less all three (serve_limit(), D-53).
The URL holds the password, so it is never printed or logged. describe_host() gives the only part logs may show.
The values come from the root .env (the same file as the LLM keys) or the real environment, which wins.
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
DEFAULT_SAVE_TIMEOUT_S = 15.0  # P-38: S4-11's save timed out at 2 s from a slow network while every other call worked
MIN_SAVE_TIMEOUT_S = 2.0
MAX_SAVE_TIMEOUT_S = 15.0  # 150 + 15 + SAVE_GRACE_S stays inside the frontend's 170-second wait
SAVE_GRACE_S = 1.0  # the run waits this much past the save's own limit, only to stop a store that never answers


@dataclass(frozen=True, repr=False)
class DatabaseSettings:
    url: str | None = None
    timeout_s: float = DEFAULT_TIMEOUT_S
    save_timeout_s: float = DEFAULT_SAVE_TIMEOUT_S  # save_search only (P-38)

    @property
    def enabled(self) -> bool:
        return self.url is not None

    def __repr__(self) -> str:
        # Never the URL, not even in a traceback or a debugger.
        return (
            f"DatabaseSettings(enabled={self.enabled}, host={describe_host(self.url)!r}, timeout_s={self.timeout_s}, "
            f"save_timeout_s={self.save_timeout_s})"
        )


def load_settings(env: Mapping[str, str] | None = None) -> DatabaseSettings:
    """Read DATABASE_URL, DB_TIMEOUT_SECONDS and DB_SAVE_TIMEOUT_SECONDS. With no `env`, python-dotenv loads .env
    first."""
    if env is None:
        load_dotenv()  # finds the root .env walking up from here; real environment variables win
        env = os.environ
    url = (env.get("DATABASE_URL") or "").strip() or None
    return DatabaseSettings(
        url=url,
        timeout_s=_timeout(env.get("DB_TIMEOUT_SECONDS")),
        save_timeout_s=save_timeout_s(env),
    )


def save_timeout_s(env: Mapping[str, str]) -> float:
    """DB_SAVE_TIMEOUT_SECONDS alone, from `env`: for the Reddit cache's serve_limit(), which must allow for it."""
    return _save_timeout(env.get("DB_SAVE_TIMEOUT_SECONDS"))


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


def _save_timeout(raw: str | None) -> float:
    """DB_SAVE_TIMEOUT_SECONDS: 15 when unset or not a positive number. A value outside 2 to 15 is refused, saying
    so, and the nearest allowed one is used: anything above 15 gives 15."""
    if raw is None or not raw.strip():
        return DEFAULT_SAVE_TIMEOUT_S
    try:
        value = float(raw)
    except ValueError:
        value = math.nan
    if not math.isfinite(value) or value <= 0:
        log.warning("DB_SAVE_TIMEOUT_SECONDS must be a positive number of seconds; using %s", DEFAULT_SAVE_TIMEOUT_S)
        return DEFAULT_SAVE_TIMEOUT_S
    kept = min(max(value, MIN_SAVE_TIMEOUT_S), MAX_SAVE_TIMEOUT_S)
    if kept != value:  # the style of every other bounded setting (app/search_run_limits.py number())
        log.warning(
            "DB_SAVE_TIMEOUT_SECONDS must be between %g and %g; using %g", MIN_SAVE_TIMEOUT_S, MAX_SAVE_TIMEOUT_S, kept
        )
    return kept


def describe_host(url: str | None) -> str:
    """The host name only, the one part of the URL that logs may show."""
    if not url:
        return "none"
    try:
        host = urlsplit(url).hostname
    except ValueError:
        host = None
    return host or "unknown"
