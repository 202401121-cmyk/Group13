"""The alias store (US-15, DR-06): names a product went by in earlier searches, so later ones reuse its id.

An alias is a product name in normalised form (lower case, generic words gone, at most 80 characters), keyed by
category. Names are names, not sentences, so aliases from any source are kept (Om, 11 Nov 2026). Two stores:

- InMemoryAliasStore, always: one per process, shared by every search.
- PostgresAliasStore, when the database is on (D-28): the public.product_aliases table, through the request store's own
  pool, time limit and circuit breaker. It writes memory first, so a database that is off, down or slow costs the
  search nothing (build rule 10), and it never raises.

Before the migration is applied the table is missing. That is logged once and is not counted as a database failure, so
request and search saves, which share the breaker, are never skipped because of aliases.
"""

from __future__ import annotations

import functools
import logging
import re
import threading
import weakref
from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor, wait
from typing import Protocol

from psycopg import errors

from .postgres import PostgresRequestStore
from .store import RequestStore

log = logging.getLogger(__name__)

MAX_ALIAS_CHARS = 80
MAX_ALIASES_LOADED = 5000  # per category: far more than one search's names, and a bound on what one load reads
_PRODUCT_ID = re.compile(r"^prd_[A-Za-z0-9_-]{3,64}$")  # the contract's ProductId
_CATEGORY = re.compile(r"^[a-z0-9]+(?:[_-][a-z0-9]+)*$")  # the contract's Slug

# The most recently used first, so a category past the limit still reads the aliases its searches use now.
SELECT = (
    "select alias, product_id from public.product_aliases where category = %s order by last_seen desc, alias limit %s"
)
UPSERT = (
    "insert into public.product_aliases (alias, product_id, category) values (%s, %s, %s) "
    "on conflict (category, alias) do update set last_seen = now()"
)


class AliasStore(Protocol):
    def prefetch(self, category: str) -> None:
        """Start reading this category's aliases from wherever they are kept, without waiting. Never raises."""
        ...

    def load(self, category: str) -> dict[str, str]:
        """alias -> product id for this category, from what is already read. Never waits, never raises."""
        ...

    def save(self, category: str, aliases: Mapping[str, str]) -> bool:
        """Keep these aliases. True when they are kept in memory; a database write goes on in the background."""
        ...


def fitting(category: str, aliases: Mapping[str, str]) -> dict[str, str]:
    """Only aliases the table can hold: a slug category, a 1 to 80 character alias and a valid product id."""
    if not _CATEGORY.match(category) or len(category) > 64:
        return {}
    return {
        alias: product_id
        for alias, product_id in aliases.items()
        if 0 < len(alias) <= MAX_ALIAS_CHARS and alias == alias.strip() and _PRODUCT_ID.match(product_id)
    }


class InMemoryAliasStore:
    """Aliases for as long as the process runs. The first product id seen for an alias is kept."""

    def __init__(self) -> None:
        self._rows: dict[str, dict[str, str]] = {}
        self._lock = threading.Lock()

    def prefetch(self, category: str) -> None:
        """Nothing to read: memory is already here."""

    def load(self, category: str) -> dict[str, str]:
        with self._lock:
            return dict(self._rows.get(category, {}))

    def save(self, category: str, aliases: Mapping[str, str]) -> bool:
        rows = fitting(category, aliases)
        with self._lock:
            kept = self._rows.setdefault(category, {}) if rows else {}
            for alias, product_id in rows.items():
                kept.setdefault(alias, product_id)
        return True

    def clear(self) -> None:
        with self._lock:
            self._rows.clear()


class PostgresAliasStore:
    """Aliases in public.product_aliases, with memory underneath. Uses the request store's pool and breaker.

    A search never waits for it (D-28, build rule 10): the pipeline asks for a category's aliases when the search
    starts (prefetch), they are read in the background while research runs, and match takes whatever has arrived. A save
    goes to memory at once and to the table in the background. A database that is down or slow only means fewer aliases.
    """

    def __init__(self, requests: PostgresRequestStore, memory: InMemoryAliasStore) -> None:
        self.requests = requests
        self.memory = memory
        self._table_missing = False
        self._read_rows: dict[str, dict[str, str]] = {}
        self._lock = threading.Lock()
        self._background = ThreadPoolExecutor(max_workers=1, thread_name_prefix="alias-store")
        self._pending: list[Future[None]] = []

    def prefetch(self, category: str) -> None:
        if self._table_missing or not _CATEGORY.match(category):
            return
        self._submit(lambda: self._refresh(category))

    def load(self, category: str) -> dict[str, str]:
        with self._lock:
            read = dict(self._read_rows.get(category, {}))
        return {**self.memory.load(category), **read}  # the table's id wins: it was seen first, by any process

    def save(self, category: str, aliases: Mapping[str, str]) -> bool:
        rows = fitting(category, aliases)
        self.memory.save(category, rows)
        if rows and not self._table_missing:
            self._submit(lambda: self._store(category, rows))
        return True

    def settle(self, timeout_s: float) -> bool:
        """Wait for the background reads and writes started so far. True when they all finished. For tests."""
        with self._lock:
            pending = list(self._pending)
        return not wait(pending, timeout=timeout_s).not_done

    def _submit(self, work: Callable[[], None]) -> None:
        try:
            future = self._background.submit(work)
        except RuntimeError:  # shut down: nothing more is read or written, and memory still has everything
            return
        with self._lock:
            self._pending = [f for f in self._pending if not f.done()] + [future]

    def _refresh(self, category: str) -> None:
        found: list[dict[str, str]] = []
        if self.requests._guarded("alias load", lambda: found.append(self._read(category))) and found:
            with self._lock:
                self._read_rows[category] = found[0]

    def _store(self, category: str, rows: Mapping[str, str]) -> None:
        self.requests._guarded("alias save", lambda: self._write(category, rows))

    # --- the work, on the request store's worker thread ------------------------------------------------------

    def _read(self, category: str) -> dict[str, str]:
        try:
            return self._select(category)
        except errors.UndefinedTable:
            self._missing()
            return {}

    def _write(self, category: str, rows: Mapping[str, str]) -> None:
        try:
            self._upsert(category, rows)
        except errors.UndefinedTable:
            self._missing()

    def _select(self, category: str) -> dict[str, str]:
        with self.requests._get_pool().connection(timeout=self.requests._timeout_s) as conn:
            found = conn.execute(SELECT, (category, MAX_ALIASES_LOADED)).fetchall()
        return {str(alias): str(product_id) for alias, product_id in found}

    def _upsert(self, category: str, rows: Mapping[str, str]) -> None:
        pool = self.requests._get_pool()
        with pool.connection(timeout=self.requests._timeout_s) as conn, conn.transaction(), conn.cursor() as cursor:
            cursor.executemany(UPSERT, [(alias, product_id, category) for alias, product_id in rows.items()])

    def _missing(self) -> None:
        if not self._table_missing:
            self._table_missing = True
            log.warning("product_aliases table is missing: aliases are kept in memory until the migration is applied")


@functools.cache
def shared_memory_aliases() -> InMemoryAliasStore:
    """The one in-memory alias store of this process, so later searches reuse earlier aliases."""
    return InMemoryAliasStore()


_FOR_REQUESTS: weakref.WeakKeyDictionary[PostgresRequestStore, PostgresAliasStore] = weakref.WeakKeyDictionary()
_FOR_REQUESTS_LOCK = threading.Lock()


def alias_store_for(requests: RequestStore | None) -> AliasStore:
    """Postgres (with memory underneath) when the request store is Postgres; else memory alone (D-28).

    One Postgres alias store per request store, however many searches run: so a missing table is found once, and its
    one background thread puts at most one alias job at a time on the request store's two database workers.
    """
    if not isinstance(requests, PostgresRequestStore):
        return shared_memory_aliases()
    with _FOR_REQUESTS_LOCK:
        store = _FOR_REQUESTS.get(requests)
        if store is None:
            store = _FOR_REQUESTS[requests] = PostgresAliasStore(requests, shared_memory_aliases())
        return store
