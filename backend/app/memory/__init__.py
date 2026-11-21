"""Preference memory (US-06, P-40): a signed-in shopper's lasting preferences, saved with an embedding made on our own
server, and recalled by meaning at the start of their next interview. Nothing here ever reaches a provider."""

from __future__ import annotations

import functools

from app import db

from .embedder import EmbedderHolder, MemoryUnavailable, shared_embedder
from .lasting import Lasting, lasting_items
from .service import EditConflict, Edited, EditRefused, MemoryService, RememberedItem
from .settings import MemorySettings, load_memory_settings
from .store import (
    InMemoryPreferenceStore,
    NullPreferenceStore,
    PostgresPreferenceStore,
    PreferenceStore,
    StoredPreference,
    preference_store_for,
)


@functools.cache
def default_memory() -> MemoryService:
    """The service the API uses: built on first use, never at start-up. MEMORY=off gives one that does nothing."""
    settings = load_memory_settings()
    return MemoryService(settings, shared_embedder(settings), preference_store_for(db.default_request_store()))


__all__ = [
    "EditConflict",
    "EditRefused",
    "Edited",
    "EmbedderHolder",
    "InMemoryPreferenceStore",
    "Lasting",
    "MemoryService",
    "MemorySettings",
    "MemoryUnavailable",
    "NullPreferenceStore",
    "PostgresPreferenceStore",
    "PreferenceStore",
    "RememberedItem",
    "StoredPreference",
    "default_memory",
    "lasting_items",
    "load_memory_settings",
    "preference_store_for",
    "shared_embedder",
]
