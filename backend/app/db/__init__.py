"""Saving requests and finished searches (US-01, US-52), and a signed-in shopper's own data (US-30).

Optional: with no DATABASE_URL nothing is saved, and the account reads are unavailable.
"""

from __future__ import annotations

import functools

from .accounts import (
    AccountStore,
    AccountsUnavailable,
    NullAccountStore,
    PageAfter,
    SavedSearchRow,
    UserGone,
)
from .aliases import AliasStore, InMemoryAliasStore, PostgresAliasStore, alias_store_for, shared_memory_aliases
from .breaker import CircuitBreaker
from .postgres import PostgresRequestStore
from .settings import DEFAULT_SAVE_TIMEOUT_S, SAVE_GRACE_S, DatabaseSettings, describe_host, load_settings
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


def default_account_store() -> AccountStore:
    """The account reads (US-30) share the request store's pool and circuit breaker; unavailable with no database."""
    store = default_request_store()
    return store if isinstance(store, PostgresRequestStore) else NullAccountStore()


__all__ = [
    "DEFAULT_SAVE_TIMEOUT_S",
    "SAVE_GRACE_S",
    "AccountStore",
    "AccountsUnavailable",
    "AliasStore",
    "CircuitBreaker",
    "DatabaseSettings",
    "DbHealth",
    "InMemoryAliasStore",
    "InMemoryRequestStore",
    "JsonObject",
    "NullAccountStore",
    "NullRequestStore",
    "PageAfter",
    "PostgresAliasStore",
    "PostgresRequestStore",
    "RequestRow",
    "RequestStore",
    "SaveOutcome",
    "SavedSearchRow",
    "SearchRow",
    "UserGone",
    "alias_store_for",
    "default_account_store",
    "default_request_store",
    "describe_host",
    "load_settings",
    "request_row",
    "search_row",
    "shared_memory_aliases",
]
