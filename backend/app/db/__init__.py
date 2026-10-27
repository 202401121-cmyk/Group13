"""Saving shopper requests (US-01). The database is optional: with no DATABASE_URL nothing is saved."""

from __future__ import annotations

import functools

from .breaker import CircuitBreaker
from .postgres import PostgresRequestStore
from .settings import DatabaseSettings, describe_host, load_settings
from .store import (
    DbHealth,
    InMemoryRequestStore,
    NullRequestStore,
    RequestRow,
    RequestStore,
    SaveOutcome,
    request_row,
)


@functools.cache
def default_request_store() -> RequestStore:
    """The store the API and the CLI use. Built on first use, never at start-up; Null when DATABASE_URL is unset."""
    settings = load_settings()
    if not settings.enabled:
        return NullRequestStore()
    return PostgresRequestStore(settings)


__all__ = [
    "CircuitBreaker",
    "DatabaseSettings",
    "DbHealth",
    "InMemoryRequestStore",
    "NullRequestStore",
    "PostgresRequestStore",
    "RequestRow",
    "RequestStore",
    "SaveOutcome",
    "default_request_store",
    "describe_host",
    "load_settings",
    "request_row",
]
