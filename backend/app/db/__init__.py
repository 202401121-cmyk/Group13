"""Saving shopper requests (US-01). The database is optional: with no DATABASE_URL nothing is saved."""

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

__all__ = [
    "DatabaseSettings",
    "DbHealth",
    "InMemoryRequestStore",
    "NullRequestStore",
    "RequestRow",
    "RequestStore",
    "SaveOutcome",
    "describe_host",
    "load_settings",
    "request_row",
]
