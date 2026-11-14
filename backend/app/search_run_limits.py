"""How long a finished search stays in the server's memory (SEARCH_RUN_KEEP_MINUTES), and how often it is cleared.

It has its own module because three parts of the app must agree on these. The search runs (app/api/search_runs.py) keep
a finished run this long, even while a progress stream reads it. The app's housekeeping (app/main.py) drops runs past
that, and deletes expired cache files, every SWEEP_EVERY_S, search or no search. The Reddit cache
(app/research/reddit/cache.py) stops serving an answer this much sooner than 24 hours, less the longest search, one
housekeeping tick and a margin as well (serve_limit()), so a run that read the answer late, and the answer's own file,
are gone before the data turns 24 hours old even on an idle server (D-53 covers every copy).
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
SWEEP_EVERY_S = 600.0  # the team's choice (US-12): expired Reddit text goes within ten minutes, search or no search


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
