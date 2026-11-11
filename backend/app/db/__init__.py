"""Saving requests and finished searches (US-01, US-52). Optional: with no DATABASE_URL nothing is saved."""

from __future__ import annotations

import functools

from .aliases import AliasStore, InMemoryAliasStore, PostgresAliasStore, alias_store_for, shared_memory_aliases
from .breaker import CircuitBreaker
from .postgres import PostgresRequestStore
from .settings import DatabaseSettings, describe_host, load_settings
from .store import (
    DbHealth,
    InMemoryRequestStore,
    JsonObject,
    NullRequestStore,
    RequestRow,
    RequestStore,
    SaveOutcome,
    SearchRow,
    request_row,
    search_row,
)


@functools.cache
def default_request_store() -> RequestStore:
    """The store the API and the CLI use. Built on first use, never at start-up; Null when DATABASE_URL is unset."""
    settings = load_settings()
    if not settings.enabled:
        return NullRequestStore()
    return PostgresRequestStore(settings)


__all__ = [
    "AliasStore",
    "CircuitBreaker",
    "DatabaseSettings",
    "DbHealth",
    "InMemoryAliasStore",
    "InMemoryRequestStore",
    "JsonObject",
    "NullRequestStore",
    "PostgresAliasStore",
    "PostgresRequestStore",
    "RequestRow",
    "RequestStore",
    "SaveOutcome",
    "SearchRow",
    "alias_store_for",
    "default_request_store",
    "describe_host",
    "load_settings",
    "request_row",
    "search_row",
    "shared_memory_aliases",
]
