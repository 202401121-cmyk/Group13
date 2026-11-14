"""How long a finished search stays in the server's memory (SEARCH_RUN_KEEP_MINUTES).

It has its own module because two parts of the app must agree on it. The search runs (app/api/search_runs.py) keep a
finished run this long, even while a progress stream reads it. The Reddit cache (app/research/reddit/cache.py) stops
serving an answer this much sooner than 24 hours, less the longest search and a margin as well (serve_limit()), so a run
that read the answer late is gone before the data turns 24 hours old when it is dropped at its keep-time (D-53 covers
every copy). On an idle server the app's housekeeping drops it within ten minutes of that (app/main.py).
"""

from __future__ import annotations

import logging
import math
import os
from collections.abc import Mapping

from dotenv import dotenv_values, find_dotenv

log = logging.getLogger(__name__)

DEFAULT_KEEP_MINUTES = 30.0
MIN_KEEP_MINUTES, MAX_KEEP_MINUTES = 1.0, 120.0


def environment() -> dict[str, str]:
    """The root .env (without changing os.environ), and real environment variables on top."""
    return {**{k: v for k, v in dotenv_values(find_dotenv()).items() if v is not None}, **os.environ}


def keep_minutes(env: Mapping[str, str] | None = None) -> float:
    values = env if env is not None else environment()
    return number(values, "SEARCH_RUN_KEEP_MINUTES", DEFAULT_KEEP_MINUTES, MIN_KEEP_MINUTES, MAX_KEEP_MINUTES)


def number(env: Mapping[str, str], name: str, default: float, low: float, high: float, whole: bool = False) -> float:
    """A blank value is the default. A bad one is the default with a warning; one out of range is brought inside it."""
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(int(raw)) if whole else float(raw)
    except (ValueError, OverflowError):  # OverflowError: a whole number too big for a float
        value = math.nan
    if not math.isfinite(value) or value <= 0:
        log.warning("%s must be a positive %s; using %s", name, "whole number" if whole else "number", default)
        return default
    kept = min(max(value, low), high)
    if kept != value:
        log.warning("%s must be between %g and %g; using %g", name, low, high, kept)
    return kept
