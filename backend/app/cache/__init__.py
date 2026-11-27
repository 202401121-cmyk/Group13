"""Whole-search caches (US-34): app/cache/results.py keeps finished searches for an exact repeat."""

from .results import (
    RESULTS_DIR,
    CacheKey,
    Lookup,
    ResultCache,
    cache_on,
    default_result_cache,
    forget_user,
    sweep_results,
)

__all__ = [
    "RESULTS_DIR",
    "CacheKey",
    "Lookup",
    "ResultCache",
    "cache_on",
    "default_result_cache",
    "forget_user",
    "sweep_results",
]
