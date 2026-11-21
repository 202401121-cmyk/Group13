"""Where remembered preferences live (US-06): public.preferences (S5-1), or memory in tests and the simulator.

Every read and write names the user: `where user_id = %s` in every statement, as well as row level security (which the
backend's own login doesn't go through). Recall reads one user's rows for one category, plus their brands left out with
Always (the only rows with no category, S5-1), newest first, at most 500 (MAX_PREFERENCES).

Saving the same preference again updates it, and its time, instead of adding a row. The same preference: a weight for
the same criterion, a must-have with the same rule id, a leave-out of the same kind and value (any case), each in the
same category. One user's saves never interleave (a transaction-scoped lock on the user).

The Postgres store borrows the request store's connection pool (app/db), with its statement time limit, but keeps a
circuit breaker of its own: memory's failures never stop request and search saves (they share theirs). A database that
is down, slow or off is one MemoryUnavailable, and the service logs it once. A save has `save_timeout_s` in all; past
it the transaction is rolled back, so a save reported as failed never commits later. A deleted account's save is
refused as such, never counted as a database failure. Weights are read in text mode, so a stored 7.3 reads back as 7.3.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Protocol, TypeVar

from psycopg import Connection, errors
from psycopg.rows import TupleRow
from psycopg.types.json import Jsonb

from app.contracts import MAX_PREFERENCES
from app.db import CircuitBreaker, PostgresRequestStore, RequestStore

from .embedder import MemoryUnavailable
from .lasting import Lasting

Vector = Sequence[float]
T = TypeVar("T")


@dataclass(frozen=True)
class StoredPreference:
    """One row of public.preferences, as recall reads it."""

    id: uuid.UUID
    kind: str
    category: str | None
    criterion_key: str | None
    text: str
    weight: float | None
    rule: dict[str, Any] | None
    always: bool
    created_at: datetime
    updated_at: datetime
    embedding: tuple[float, ...]


class PreferenceStore(Protocol):
    def save(self, user_id: uuid.UUID, items: Sequence[tuple[Lasting, Vector]]) -> int:
        """Insert or update each item; the number written. Raises MemoryUnavailable."""
        ...

    def candidates(self, user_id: uuid.UUID, category: str) -> list[StoredPreference]:
        """The user's rows recall may use for this category. Raises MemoryUnavailable."""
        ...


class NullPreferenceStore:
    """No DATABASE_URL: nothing is kept, so nothing can be recalled."""

    def save(self, user_id: uuid.UUID, items: Sequence[tuple[Lasting, Vector]]) -> int:
        raise MemoryUnavailable("the database is off (no DATABASE_URL)")

    def candidates(self, user_id: uuid.UUID, category: str) -> list[StoredPreference]:
        raise MemoryUnavailable("the database is off (no DATABASE_URL)")


class InMemoryPreferenceStore:
    """The same rules in memory, for tests and the interview simulator."""

    def __init__(self, clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._clock = clock
        self._rows: dict[uuid.UUID, tuple[uuid.UUID, tuple[str, ...], StoredPreference]] = {}
        self._lock = threading.Lock()

    def save(self, user_id: uuid.UUID, items: Sequence[tuple[Lasting, Vector]]) -> int:
        now = self._clock()
        with self._lock:
            for item, vector in items:
                same = next(
                    (row for owner, key, row in self._rows.values() if owner == user_id and key == item.key), None
                )
                if same is not None:
                    row = replace(same, **_fields(item), embedding=tuple(vector), updated_at=now)
                else:
                    row = StoredPreference(
                        id=uuid.uuid4(), **_fields(item), created_at=now, updated_at=now, embedding=tuple(vector)
                    )
                self._rows[row.id] = (user_id, item.key, row)
        return len(items)

    def candidates(self, user_id: uuid.UUID, category: str) -> list[StoredPreference]:
        with self._lock:
            rows = [
                row
                for owner, _, row in self._rows.values()
                if owner == user_id and row.embedding and (row.category == category or row.category is None)
            ]
        return sorted(rows, key=lambda r: (r.updated_at, str(r.id)), reverse=True)[:MAX_PREFERENCES]

    def rows(self, user_id: uuid.UUID) -> list[StoredPreference]:
        with self._lock:
            return [row for owner, _, row in self._rows.values() if owner == user_id]

    def count(self) -> int:
        with self._lock:
            return len(self._rows)


def _fields(item: Lasting) -> dict[str, Any]:
    return {
        "kind": item.kind,
        "category": item.category,
        "criterion_key": item.criterion_key,
        "text": item.text,
        "weight": item.weight,
        "rule": item.rule,
        "always": item.always,
    }


# --- Postgres ---------------------------------------------------------------------------------------------------------

LOCK_USER = "select pg_advisory_xact_lock(hashtextextended(%s::text, 6))"
FIND = {
    "weight": "select id from public.preferences where user_id = %s and kind = 'weight' and category = %s "
    "and criterion_key = %s limit 1 for update",
    "hard_rule": "select id from public.preferences where user_id = %s and kind = 'hard_rule' and category = %s "
    "and rule ->> 'id' = %s limit 1 for update",
    "exclusion": "select id from public.preferences where user_id = %s and kind = 'exclusion' "
    "and category is not distinct from %s and rule ->> 'kind' = %s and lower(rule ->> 'value') = lower(%s) "
    "limit 1 for update",
}
UPDATE = (
    "update public.preferences set criterion_key = %s, text = %s, weight = %s, rule = %s, always = %s, "
    "embedding = %s::extensions.vector, updated_at = greatest(now(), created_at) where id = %s and user_id = %s"
)
INSERT = (
    "insert into public.preferences (user_id, kind, category, criterion_key, text, weight, rule, always, embedding) "
    "values (%s, %s, %s, %s, %s, %s, %s, %s, %s::extensions.vector)"
)
CANDIDATES = (
    "select id, kind, category, criterion_key, text, weight, rule, always, created_at, updated_at, embedding::text "
    "from public.preferences where user_id = %s and embedding is not null and (category = %s or category is null) "
    "order by updated_at desc, id limit %s"
)


SAVE_TIMEOUT_S = 10.0  # a save runs after the reply has gone, so it may take longer than a request's 2 s


class PostgresPreferenceStore:
    """public.preferences, through the request store's pool, with a circuit breaker of its own."""

    def __init__(
        self,
        store: PostgresRequestStore,
        breaker: CircuitBreaker | None = None,
        save_timeout_s: float = SAVE_TIMEOUT_S,
    ) -> None:
        self._store = store
        self.breaker = breaker or CircuitBreaker()
        self._save_timeout_s = save_timeout_s

    def save(self, user_id: uuid.UUID, items: Sequence[tuple[Lasting, Vector]]) -> int:
        if not items:
            return 0
        deadline = time.monotonic() + self._save_timeout_s
        return self._guard("be written", lambda conn: _save(conn, user_id, items, deadline))

    def candidates(self, user_id: uuid.UUID, category: str) -> list[StoredPreference]:
        return self._guard("be read", lambda conn: _candidates(conn, user_id, category))

    def _guard(self, what: str, work: Callable[[Connection[TupleRow]], T]) -> T:
        """Run `work` on a pooled connection in this thread; the caller bounds the time (recall's share, a save's
        deadline). Raises MemoryUnavailable with a plain reason, never the URL."""
        if not self.breaker.allow():
            raise MemoryUnavailable("the database is skipped for a while after failures")
        try:
            with self._store._get_pool().connection(timeout=self._store._timeout_s) as conn:
                result = work(conn)
        except errors.ForeignKeyViolation as exc:  # the account was deleted: the database answered, so no failure
            self.breaker.record_success()
            raise MemoryUnavailable("the account no longer exists") from exc
        except Exception as exc:  # a timeout, a refused connection, a paused project, ...
            self.breaker.record_failure()
            raise MemoryUnavailable(f"the database could not {what} ({type(exc).__name__})") from exc
        self.breaker.record_success()
        return result


def _vector_text(vector: Vector) -> str:
    return "[" + ",".join(f"{float(x):.7g}" for x in vector) + "]"


def _save(
    conn: Connection[TupleRow], user_id: uuid.UUID, items: Sequence[tuple[Lasting, Vector]], deadline: float
) -> int:
    written = 0
    with conn.transaction():
        conn.execute(LOCK_USER, (str(user_id),))
        for item, vector in items:
            rule = item.rule or {}
            if item.kind == "weight":
                key: tuple[Any, ...] = (user_id, item.category, item.criterion_key)
            elif item.kind == "hard_rule":
                key = (user_id, item.category, str(rule.get("id", "")))
            else:
                key = (user_id, item.category, str(rule.get("kind", "")), str(rule.get("value", "")))
            found = conn.execute(FIND[item.kind], key).fetchone()
            payload = Jsonb(item.rule) if item.rule is not None else None
            if found is not None:
                conn.execute(
                    UPDATE,
                    (
                        item.criterion_key,
                        item.text,
                        item.weight,
                        payload,
                        item.always,
                        _vector_text(vector),
                        found[0],
                        user_id,
                    ),
                )
            else:
                conn.execute(
                    INSERT,
                    (
                        user_id,
                        item.kind,
                        item.category,
                        item.criterion_key,
                        item.text,
                        item.weight,
                        payload,
                        item.always,
                        _vector_text(vector),
                    ),
                )
            written += 1
        if time.monotonic() >= deadline:  # rolled back: a save reported as failed never commits later
            raise TimeoutError("the preferences save ran past its time limit")
    return written


def _candidates(conn: Connection[TupleRow], user_id: uuid.UUID, category: str) -> list[StoredPreference]:
    rows = conn.execute(CANDIDATES, (user_id, category, MAX_PREFERENCES)).fetchall()
    return [
        StoredPreference(
            id=row[0],
            kind=row[1],
            category=row[2],
            criterion_key=row[3],
            text=row[4],
            weight=float(row[5]) if row[5] is not None else None,
            rule=row[6] if isinstance(row[6], dict) else None,
            always=bool(row[7]),
            created_at=row[8],
            updated_at=row[9],
            embedding=tuple(float(x) for x in str(row[10]).strip("[]").split(",") if x),
        )
        for row in rows
    ]


def preference_store_for(store: RequestStore) -> PreferenceStore:
    """The Postgres store when the database is on, else one that keeps nothing."""
    if isinstance(store, PostgresRequestStore):
        return PostgresPreferenceStore(store)
    return NullPreferenceStore()
