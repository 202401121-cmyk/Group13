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
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
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

    # The shopper's own controls (US-11). Each names the user, so another user's id finds nothing.

    def listing(self, user_id: uuid.UUID) -> list[StoredPreference]:
        """Every row of the user, newest first (by created_at), at most MAX_PREFERENCES; embeddings left out."""
        ...

    def one(self, user_id: uuid.UUID, preference_id: uuid.UUID) -> StoredPreference | None:
        """The user's row with this id, or None (another user's id included); its embedding left out."""
        ...

    def update(self, user_id: uuid.UUID, preference_id: uuid.UUID, change: Change) -> StoredPreference | None:
        """The row changed and its time moved on; None if the user has no row with this id."""
        ...

    def delete(self, user_id: uuid.UUID, preference_id: uuid.UUID) -> bool:
        """Delete the row, and with it its embedding (the same row). False if the user has no row with this id."""
        ...

    def delete_all(self, user_id: uuid.UUID) -> int:
        """Delete every row of the user; the number deleted."""
        ...


@dataclass(frozen=True)
class Change:
    """What an edit sets (US-11). `embedding` None keeps the row's own. `read_at` is the updated_at the edit was made
    from: a row changed since then (a save in between) is not written over, and update answers None."""

    text: str
    weight: float | None
    rule: dict[str, Any] | None
    embedding: tuple[float, ...] | None = None
    read_at: datetime | None = None


OFF = "the database is off (no DATABASE_URL)"


class NullPreferenceStore:
    """No DATABASE_URL: nothing is kept, so nothing can be recalled, listed or changed."""

    def save(self, user_id: uuid.UUID, items: Sequence[tuple[Lasting, Vector]]) -> int:
        raise MemoryUnavailable(OFF)

    def candidates(self, user_id: uuid.UUID, category: str) -> list[StoredPreference]:
        raise MemoryUnavailable(OFF)

    def listing(self, user_id: uuid.UUID) -> list[StoredPreference]:
        raise MemoryUnavailable(OFF)

    def one(self, user_id: uuid.UUID, preference_id: uuid.UUID) -> StoredPreference | None:
        raise MemoryUnavailable(OFF)

    def update(self, user_id: uuid.UUID, preference_id: uuid.UUID, change: Change) -> StoredPreference | None:
        raise MemoryUnavailable(OFF)

    def delete(self, user_id: uuid.UUID, preference_id: uuid.UUID) -> bool:
        raise MemoryUnavailable(OFF)

    def delete_all(self, user_id: uuid.UUID) -> int:
        raise MemoryUnavailable(OFF)


class InMemoryPreferenceStore:
    """The same rules in memory, for tests and the interview simulator."""

    def __init__(self, clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._clock = clock
        self._rows: dict[uuid.UUID, tuple[uuid.UUID, tuple[str, ...], StoredPreference]] = {}
        self._lock = threading.Lock()

    def save(self, user_id: uuid.UUID, items: Sequence[tuple[Lasting, Vector]]) -> int:
        now = self._clock()
        written = 0
        with self._lock:
            for item, vector in items:
                same = next(
                    (row for owner, key, row in self._rows.values() if owner == user_id and key == item.key), None
                )
                if same is not None:
                    row = replace(same, **_fields(item), embedding=tuple(vector), updated_at=now)
                elif item.refresh_only:
                    continue  # deleted by the shopper since it was recalled: never made again
                else:
                    row = StoredPreference(
                        id=uuid.uuid4(), **_fields(item), created_at=now, updated_at=now, embedding=tuple(vector)
                    )
                self._rows[row.id] = (user_id, item.key, row)
                written += 1
        return written

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

    def listing(self, user_id: uuid.UUID) -> list[StoredPreference]:
        rows = [replace(row, embedding=()) for row in self.rows(user_id)]
        return sorted(rows, key=lambda r: (r.created_at, str(r.id)), reverse=True)[:MAX_PREFERENCES]

    def one(self, user_id: uuid.UUID, preference_id: uuid.UUID) -> StoredPreference | None:
        with self._lock:
            held = self._rows.get(preference_id)
        return replace(held[2], embedding=()) if held is not None and held[0] == user_id else None

    def update(self, user_id: uuid.UUID, preference_id: uuid.UUID, change: Change) -> StoredPreference | None:
        with self._lock:
            held = self._rows.get(preference_id)
            if held is None or held[0] != user_id:
                return None
            owner, key, row = held
            if change.read_at is not None and row.updated_at != change.read_at:
                return None  # changed since it was read: never written over
            changed = replace(
                row,
                text=change.text,
                weight=change.weight,
                rule=change.rule,
                embedding=change.embedding if change.embedding is not None else row.embedding,
                updated_at=max(self._clock(), row.created_at),
            )
            self._rows[preference_id] = (owner, key, changed)
        return replace(changed, embedding=())

    def delete(self, user_id: uuid.UUID, preference_id: uuid.UUID) -> bool:
        with self._lock:
            held = self._rows.get(preference_id)
            if held is None or held[0] != user_id:
                return False
            del self._rows[preference_id]
        return True

    def delete_all(self, user_id: uuid.UUID) -> int:
        with self._lock:
            gone = [key for key, (owner, _, _) in self._rows.items() if owner == user_id]
            for key in gone:
                del self._rows[key]
        return len(gone)


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
# The shopper's own controls (US-11): no embedding is read; every statement names the user.
COLUMNS = "id, kind, category, criterion_key, text, weight, rule, always, created_at, updated_at"
LISTING = f"select {COLUMNS} from public.preferences where user_id = %s order by created_at desc, id desc limit %s"
ONE = f"select {COLUMNS} from public.preferences where id = %s and user_id = %s"
EDIT = (
    "update public.preferences set text = %s, weight = %s, rule = %s, "
    "embedding = coalesce(%s::extensions.vector, embedding), updated_at = greatest(now(), created_at) "
    "where id = %s and user_id = %s and (%s::timestamptz is null or updated_at = %s::timestamptz) "
    f"returning {COLUMNS}"
)
DELETE_ONE = "delete from public.preferences where id = %s and user_id = %s"
DELETE_ALL = "delete from public.preferences where user_id = %s"


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
        # The shopper's controls run on a request (US-11), so each has a wall-clock limit (build rule 9): the pool's
        # wait and the statement's limit, with a little room.
        self._control_timeout_s = 2 * store._timeout_s + 0.5
        self._workers = ThreadPoolExecutor(max_workers=2, thread_name_prefix="memory-db")

    def save(self, user_id: uuid.UUID, items: Sequence[tuple[Lasting, Vector]]) -> int:
        if not items:
            return 0
        deadline = time.monotonic() + self._save_timeout_s
        return self._guard("be written", lambda conn: _save(conn, user_id, items, deadline))

    def candidates(self, user_id: uuid.UUID, category: str) -> list[StoredPreference]:
        return self._guard("be read", lambda conn: _candidates(conn, user_id, category))

    def listing(self, user_id: uuid.UUID) -> list[StoredPreference]:
        def work(conn: Connection[TupleRow]) -> list[StoredPreference]:
            return [_row_of(row) for row in conn.execute(LISTING, (user_id, MAX_PREFERENCES)).fetchall()]

        return self._bounded("be read", work)

    def one(self, user_id: uuid.UUID, preference_id: uuid.UUID) -> StoredPreference | None:
        def work(conn: Connection[TupleRow]) -> StoredPreference | None:
            found = conn.execute(ONE, (preference_id, user_id)).fetchone()
            return _row_of(found) if found is not None else None

        return self._bounded("be read", work)

    def update(self, user_id: uuid.UUID, preference_id: uuid.UUID, change: Change) -> StoredPreference | None:
        vector = _vector_text(change.embedding) if change.embedding is not None else None
        rule = Jsonb(change.rule) if change.rule is not None else None
        deadline = time.monotonic() + self._control_timeout_s
        values = (change.text, change.weight, rule, vector, preference_id, user_id, change.read_at, change.read_at)

        def work(conn: Connection[TupleRow]) -> StoredPreference | None:
            with conn.transaction():
                found = conn.execute(EDIT, values).fetchone()
                if time.monotonic() >= deadline:  # rolled back: an edit reported as failed never lands later
                    raise TimeoutError("the edit ran past its time limit")
            return _row_of(found) if found is not None else None

        return self._bounded("be written", work)

    def delete(self, user_id: uuid.UUID, preference_id: uuid.UUID) -> bool:
        return self._bounded("be written", lambda conn: conn.execute(DELETE_ONE, (preference_id, user_id)).rowcount > 0)

    def delete_all(self, user_id: uuid.UUID) -> int:
        return self._bounded("be written", lambda conn: max(0, conn.execute(DELETE_ALL, (user_id,)).rowcount))

    def _bounded(self, what: str, work: Callable[[Connection[TupleRow]], T]) -> T:
        """`_guard` on a worker, waited for at most the control's time limit. Past it: MemoryUnavailable."""
        try:
            future = self._workers.submit(self._guard, what, work)
        except RuntimeError as exc:  # shut down
            raise MemoryUnavailable(f"the database could not {what} (the store is closed)") from exc
        try:
            return future.result(timeout=self._control_timeout_s)
        except FutureTimeout:
            future.cancel()
            self.breaker.record_failure()
            raise MemoryUnavailable(f"the database could not {what} in time") from None

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
            if found is None and item.refresh_only:
                continue  # deleted by the shopper since it was recalled (US-11): never made again
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


def _row_of(row: TupleRow) -> StoredPreference:
    """One row read without its embedding (the shopper's own controls never need it)."""
    return StoredPreference(
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
        embedding=(),
    )


def preference_store_for(store: RequestStore) -> PreferenceStore:
    """The Postgres store when the database is on, else one that keeps nothing."""
    if isinstance(store, PostgresRequestStore):
        return PostgresPreferenceStore(store)
    return NullPreferenceStore()
