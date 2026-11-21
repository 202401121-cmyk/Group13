"""The Postgres store for requests and finished searches (US-01, US-52): Supabase in production, any Postgres in tests.

The backend's routes are plain (sync) functions, so this uses psycopg's sync pool directly. Every call runs on a
small worker pool and the caller waits at most DB_TIMEOUT_SECONDS, whatever the network does: a search never
waits longer than that for the database, and a failure never raises (build rule 10).

- The connection pool opens on first use, never at start-up, so start-up never waits for the database.
- connect_timeout and statement_timeout bound the work inside psycopg as well.
- After 3 failures in a row the circuit breaker skips the database for 30 seconds (saves fail at once).
- Logs show the host and the error type only: never the URL, the password, the user name or the request text.

It is also the AccountStore (US-30, app/db/accounts.py): a signed-in shopper's profile and saved searches, read with
the same pool, time limit and circuit breaker. Every read filters on the user's id. A signed-in shopper's rows are
inserted with their id; nothing here ever updates user_id (D-36, S4-27).
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import TypeVar
from uuid import UUID

from psycopg import Connection, errors, sql
from psycopg.rows import TupleRow
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from app.contracts import Intent

from .accounts import ACCOUNT_TABLES, AccountsUnavailable, PageAfter, SavedSearchRow, UserGone
from .breaker import CircuitBreaker
from .settings import DatabaseSettings, describe_host
from .store import DbHealth, JsonObject, RequestRow, SaveOutcome, SearchRow, request_row, search_row

log = logging.getLogger(__name__)

# psycopg's own log lines can name the database user; ours say only the host and the error type.
for _name in ("psycopg", "psycopg.pool"):
    logging.getLogger(_name).setLevel(logging.CRITICAL)

INSERT = (
    "insert into public.requests (session_id, request_text, intent, intent_source, user_id) values (%s, %s, %s, %s, %s)"
)
INSERT_SEARCH = (
    "insert into public.searches (session_id, rubric, result, user_id) values (%s, %s::jsonb, %s::jsonb, %s)"
)
# A profile is made at first sign-in, and an existing one is left as it is (US-30).
INSERT_PROFILE = "insert into public.profiles (id) values (%s) on conflict (id) do nothing"
SELECT_PROFILE = "select display_name from public.profiles where id = %s"
# One page of a user's saved searches, newest first. The first-ranked product's name only when the ranking scored it.
SAVED_SEARCHES = """
select s.id, s.created_at, s.result #>> '{{request,raw_query}}', s.rubric ->> 'category',
  (select p #>> '{{product,name}}'
     from jsonb_array_elements(
       case jsonb_typeof(s.result -> 'products') when 'array' then s.result -> 'products' else '[]'::jsonb end
     ) as p
    where jsonb_typeof(s.result #> '{{decision,ranked,0,score}}') = 'number'
      and p #>> '{{product,id}}' = s.result #>> '{{decision,ranked,0,product_id}}'
    limit 1)
from public.searches as s
where s.user_id = %s {after}
order by s.created_at desc, s.id desc
limit %s
"""
FIRST_PAGE = SAVED_SEARCHES.format(after="")
NEXT_PAGE = SAVED_SEARCHES.format(after="and (s.created_at, s.id) < (%s::timestamptz, %s::uuid)")
SAVED_SEARCH = "select result from public.searches where id = %s and user_id = %s"
# Deleting an account (US-11): every table that holds the user's rows, in ACCOUNT_TABLES' order. Each names the user.
COUNT_ACCOUNT_ROWS = (
    "select (select count(*) from public.requests where user_id = %(user)s), "
    "(select count(*) from public.searches where user_id = %(user)s), "
    "(select count(*) from public.profiles where id = %(user)s), "
    "(select count(*) from public.preferences where user_id = %(user)s)"
)

T = TypeVar("T")


class PostgresRequestStore:
    def __init__(self, settings: DatabaseSettings, breaker: CircuitBreaker | None = None) -> None:
        if settings.url is None:
            raise ValueError("PostgresRequestStore needs DATABASE_URL")
        self._url = settings.url
        self._timeout_s = settings.timeout_s
        self._host = describe_host(settings.url)
        self.breaker = breaker or CircuitBreaker()
        self._workers = ThreadPoolExecutor(max_workers=2, thread_name_prefix="request-store")
        self._pool: ConnectionPool[Connection[TupleRow]] | None = None
        self._pool_lock = threading.Lock()

    def __repr__(self) -> str:
        return f"PostgresRequestStore(host={self._host!r}, timeout_s={self._timeout_s})"

    def save_request(
        self, session_id: str, request_text: str, intent: Intent | None, user_id: UUID | None = None
    ) -> SaveOutcome:
        try:
            row = request_row(session_id, request_text, intent, user_id)
        except ValueError:
            log.warning("request not saved: it does not fit the requests table")
            return "failed"
        return "saved" if self._guarded("save", lambda: self._insert(row)) else "failed"

    def save_search(self, session_id: str, rubric: JsonObject, result: JsonObject, user_id: UUID | None = None) -> bool:
        """Insert one finished search and commit it. True only when the row was committed. Never raises.

        `rubric` and `result` are model_dump(mode="json") output: a plain model_dump() holds datetimes and URLs, which
        are not JSON. False, with one log line, when the breaker is open, the database is down or slower than
        DB_TIMEOUT_SECONDS, the store is closed, the insert fails, or the search cannot fit the table.
        """
        try:
            row = search_row(session_id, rubric, result, user_id)
        except Exception as exc:  # whatever the caller passed, a failed save must not reach the search
            log.warning("search not saved: it does not fit the searches table (%s)", type(exc).__name__)
            return False
        deadline = time.monotonic() + self._timeout_s
        return self._guarded("search save", lambda: self._insert_search(row, deadline), log_skips=True)

    def health(self) -> DbHealth:
        return "ok" if self._guarded("health check", self._ping) else "down"

    # --- AccountStore (US-30) ----------------------------------------------------------------------------

    def ensure_profile(self, user_id: UUID) -> str | None:
        return self._read("profile", lambda: self._profile(user_id))

    def saved_searches(self, user_id: UUID, after: PageAfter | None, limit: int) -> list[SavedSearchRow]:
        return self._read("saved searches", lambda: self._page(user_id, after, limit))

    def saved_search(self, user_id: UUID, search_id: UUID) -> JsonObject | None:
        return self._read("saved search", lambda: self._one(user_id, search_id))

    # --- deleting an account (US-11) ---------------------------------------------------------------------

    def account_rows(self, user_id: UUID) -> dict[str, int]:
        """How many of this user's rows each table still holds: the read-back after the user's delete."""
        return self._read("account read-back", lambda: self._count_rows(user_id))

    def close(self) -> None:
        self._close_pool()
        self._workers.shutdown(wait=False, cancel_futures=True)

    # --- the guarded call -------------------------------------------------------------------------------

    def _guarded(self, what: str, work: Callable[[], None], *, log_skips: bool = False) -> bool:
        """Run `work` on a worker thread and wait at most DB_TIMEOUT_SECONDS. Never raises."""
        if not self.breaker.allow():
            if log_skips:
                log.warning("database %s skipped (host=%s): the circuit breaker is open", what, self._host)
            return False
        try:
            future = self._workers.submit(work)
        except RuntimeError:  # the store was closed (the CLI closes it when it is done)
            self.breaker.record_success()  # not a database failure: just give back the trial slot
            if log_skips:
                log.warning("database %s skipped (host=%s): the store is closed", what, self._host)
            return False
        try:
            future.result(timeout=self._timeout_s)
        except UserGone:  # a deleted account's row is refused: the database answered, so it is not a failure
            self.breaker.record_success()
            log.warning("database %s refused (host=%s): the account no longer exists", what, self._host)
            return False
        except Exception as exc:  # a timeout, a refused connection, a bad password, a paused project, ...
            future.cancel()  # if it has not started yet, it never will
            opened = self.breaker.record_failure()
            skip = f"; skipping the database for {self.breaker.cooldown_s:g} s" if opened else ""
            log.warning("database %s failed (host=%s): %s%s", what, self._host, type(exc).__name__, skip)
            if opened:
                self._close_pool()  # stop the pool retrying in the background; the next trial opens a new one
            return False
        self.breaker.record_success()
        return True

    def _read(self, what: str, work: Callable[[], T]) -> T:
        """Run a read on a worker thread and wait at most DB_TIMEOUT_SECONDS. Raises AccountsUnavailable (or
        UserGone, which is not a database failure); never anything that names the URL."""
        if not self.breaker.allow():
            raise AccountsUnavailable("the circuit breaker is open")
        try:
            future = self._workers.submit(work)
        except RuntimeError:  # the store was closed
            self.breaker.record_success()
            raise AccountsUnavailable("the store is closed") from None
        try:
            value = future.result(timeout=self._timeout_s)
        except UserGone:
            self.breaker.record_success()
            raise
        except Exception as exc:  # a timeout, a refused connection, a paused project, ...
            future.cancel()
            opened = self.breaker.record_failure()
            skip = f"; skipping the database for {self.breaker.cooldown_s:g} s" if opened else ""
            log.warning("database %s read failed (host=%s): %s%s", what, self._host, type(exc).__name__, skip)
            if opened:
                self._close_pool()
            raise AccountsUnavailable(f"the {what} read failed") from None
        self.breaker.record_success()
        return value

    # --- the work, on a worker thread -------------------------------------------------------------------

    def _insert(self, row: RequestRow) -> None:
        intent = Jsonb(row.intent.model_dump(mode="json")) if row.intent is not None else None
        with self._get_pool().connection(timeout=self._timeout_s) as conn, _owner_exists():
            conn.execute(INSERT, (row.session_id, row.request_text, intent, row.intent_source, row.user_id))

    def _insert_search(self, row: SearchRow, deadline: float) -> None:
        with self._get_pool().connection(timeout=self._timeout_s) as conn, _owner_exists(), conn.transaction():
            conn.execute(INSERT_SEARCH, (row.session_id, row.rubric, row.result, row.user_id))
            if time.monotonic() >= deadline:  # the caller has already been told "not saved": keep that true
                raise TimeoutError("the search save ran past its time limit")

    def _profile(self, user_id: UUID) -> str | None:
        with self._get_pool().connection(timeout=self._timeout_s) as conn:
            with _owner_exists():
                conn.execute(INSERT_PROFILE, (user_id,))
            found = conn.execute(SELECT_PROFILE, (user_id,)).fetchone()
        name = found[0] if found is not None else None
        return name if isinstance(name, str) else None

    def _page(self, user_id: UUID, after: PageAfter | None, limit: int) -> list[SavedSearchRow]:
        with self._get_pool().connection(timeout=self._timeout_s) as conn:
            if after is None:
                rows = conn.execute(FIRST_PAGE, (user_id, limit)).fetchall()
            else:
                rows = conn.execute(NEXT_PAGE, (user_id, after.created_at, after.id, limit)).fetchall()
        return [
            SavedSearchRow(id=row[0], created_at=row[1], request_text=row[2], category=row[3], top_product=row[4])
            for row in rows
        ]

    def _one(self, user_id: UUID, search_id: UUID) -> JsonObject | None:
        with self._get_pool().connection(timeout=self._timeout_s) as conn:
            found = conn.execute(SAVED_SEARCH, (search_id, user_id)).fetchone()
        result = found[0] if found is not None else None
        return result if isinstance(result, dict) else None

    def _count_rows(self, user_id: UUID) -> dict[str, int]:
        with self._get_pool().connection(timeout=self._timeout_s) as conn:
            found = conn.execute(COUNT_ACCOUNT_ROWS, {"user": user_id}).fetchone()
        return dict(zip(ACCOUNT_TABLES, (int(n) for n in (found or (0, 0, 0, 0))), strict=True))

    def _ping(self) -> None:
        with self._get_pool().connection(timeout=self._timeout_s) as conn:
            conn.execute("select 1")

    def _get_pool(self) -> ConnectionPool[Connection[TupleRow]]:
        with self._pool_lock:
            if self._pool is None:
                pool: ConnectionPool[Connection[TupleRow]] = ConnectionPool(
                    self._url,
                    min_size=1,
                    max_size=2,
                    open=False,
                    timeout=self._timeout_s,
                    kwargs={
                        "connect_timeout": max(2, math.ceil(self._timeout_s)),  # libpq's minimum is 2 seconds
                        "autocommit": True,
                        "application_name": "shopsense-backend",
                    },
                    configure=self._configure,
                    check=ConnectionPool.check_connection,
                    name="shopsense-requests",
                )
                pool.open(wait=False)
                self._pool = pool
            return self._pool

    def _configure(self, conn: Connection[TupleRow]) -> None:
        milliseconds = int(self._timeout_s * 1000)
        conn.execute(sql.SQL("set statement_timeout = {}").format(sql.Literal(milliseconds)))

    def _close_pool(self) -> None:
        with self._pool_lock:
            pool, self._pool = self._pool, None
        if pool is not None:
            # Closing waits for the pool's own threads, so do it off the caller's thread.
            threading.Thread(target=pool.close, kwargs={"timeout": 1.0}, daemon=True).start()


@contextmanager
def _owner_exists() -> Iterator[None]:
    """A row whose user_id is not in auth.users (the account was deleted after its token was issued) is UserGone."""
    try:
        yield
    except errors.ForeignKeyViolation:
        raise UserGone("no such user") from None
