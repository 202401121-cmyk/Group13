"""The Postgres store for requests and finished searches (US-01, US-52): Supabase in production, any Postgres in tests.

The backend's routes are plain (sync) functions, so this uses psycopg's sync pool directly. Every call runs on a
small worker pool and the caller waits at most DB_TIMEOUT_SECONDS, whatever the network does: a search never
waits longer than that for the database, and a failure never raises (build rule 10).

- The connection pool opens on first use, never at start-up, so start-up never waits for the database.
- connect_timeout and statement_timeout bound the work inside psycopg as well.
- After 3 failures in a row the circuit breaker skips the database for 30 seconds (saves fail at once).
- Logs show the host and the error type only: never the URL, the password, the user name or the request text.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from psycopg import Connection, sql
from psycopg.rows import TupleRow
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from app.contracts import Intent

from .breaker import CircuitBreaker
from .settings import DatabaseSettings, describe_host
from .store import DbHealth, JsonObject, RequestRow, SaveOutcome, SearchRow, request_row, search_row

log = logging.getLogger(__name__)

# psycopg's own log lines can name the database user; ours say only the host and the error type.
for _name in ("psycopg", "psycopg.pool"):
    logging.getLogger(_name).setLevel(logging.CRITICAL)

INSERT = "insert into public.requests (session_id, request_text, intent, intent_source) values (%s, %s, %s, %s)"
INSERT_SEARCH = "insert into public.searches (session_id, rubric, result) values (%s, %s::jsonb, %s::jsonb)"


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

    def save_request(self, session_id: str, request_text: str, intent: Intent | None) -> SaveOutcome:
        try:
            row = request_row(session_id, request_text, intent)
        except ValueError:
            log.warning("request not saved: it does not fit the requests table")
            return "failed"
        return "saved" if self._guarded("save", lambda: self._insert(row)) else "failed"

    def save_search(self, session_id: str, rubric: JsonObject, result: JsonObject) -> bool:
        """Insert one finished search and commit it. True only when the row was committed. Never raises.

        `rubric` and `result` are model_dump(mode="json") output: a plain model_dump() holds datetimes and URLs, which
        are not JSON. False, with one log line, when the breaker is open, the database is down or slower than
        DB_TIMEOUT_SECONDS, the store is closed, the insert fails, or the search cannot fit the table.
        """
        try:
            row = search_row(session_id, rubric, result)
        except Exception as exc:  # whatever the caller passed, a failed save must not reach the search
            log.warning("search not saved: it does not fit the searches table (%s)", type(exc).__name__)
            return False
        deadline = time.monotonic() + self._timeout_s
        return self._guarded("search save", lambda: self._insert_search(row, deadline), log_skips=True)

    def health(self) -> DbHealth:
        return "ok" if self._guarded("health check", self._ping) else "down"

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

    # --- the work, on a worker thread -------------------------------------------------------------------

    def _insert(self, row: RequestRow) -> None:
        intent = Jsonb(row.intent.model_dump(mode="json")) if row.intent is not None else None
        with self._get_pool().connection(timeout=self._timeout_s) as conn:
            conn.execute(INSERT, (row.session_id, row.request_text, intent, row.intent_source))

    def _insert_search(self, row: SearchRow, deadline: float) -> None:
        with self._get_pool().connection(timeout=self._timeout_s) as conn, conn.transaction():
            conn.execute(INSERT_SEARCH, (row.session_id, row.rubric, row.result))
            if time.monotonic() >= deadline:  # the caller has already been told "not saved": keep that true
                raise TimeoutError("the search save ran past its time limit")

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
