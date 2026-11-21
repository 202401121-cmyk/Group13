"""The Postgres store for requests and finished searches (US-01, US-52): Supabase in production, any Postgres in tests.

The backend's routes are plain (sync) functions, so this uses psycopg's sync pool directly. Every call runs on a
small worker pool and the caller waits at most DB_TIMEOUT_SECONDS, whatever the network does, and a failure never
raises (build rule 10). One call has a limit of its own: a finished search's save waits at most DB_SAVE_TIMEOUT_SECONDS
(15 s by default, P-38). Its row is the largest, and it runs after the search, outside the 150 seconds. Saves have their
own two workers and a pool of their own (two connections, with a connect timeout as long as the save's), so slow saves
never hold up the other calls, nor the memory and alias stores that borrow the main pool. A save never takes the
breaker's one half-open trial (a quick call does), and a save that only waited for a worker is not a database failure.

- The connection pool opens on first use, never at start-up, so start-up never waits for the database.
- connect_timeout and statement_timeout bound the work inside psycopg as well; a save sets its own statement_timeout
  for its one transaction.
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
COMMIT_MARGIN_S = 1.0  # at most: the commit's round trip, kept free at the end of a save's time (P-38)


def commit_margin(limit_s: float) -> float:
    """What a save keeps free for its commit: a tenth of its time, at most COMMIT_MARGIN_S."""
    return min(COMMIT_MARGIN_S, 0.1 * limit_s)


class PostgresRequestStore:
    def __init__(self, settings: DatabaseSettings, breaker: CircuitBreaker | None = None) -> None:
        if settings.url is None:
            raise ValueError("PostgresRequestStore needs DATABASE_URL")
        self._url = settings.url
        self._timeout_s = settings.timeout_s
        self._save_timeout_s = settings.save_timeout_s
        self._host = describe_host(settings.url)
        self.breaker = breaker or CircuitBreaker()
        self._workers = ThreadPoolExecutor(max_workers=2, thread_name_prefix="request-store")
        self._save_workers = ThreadPoolExecutor(max_workers=2, thread_name_prefix="search-save")
        self._pool: ConnectionPool[Connection[TupleRow]] | None = None
        self._save_pool: ConnectionPool[Connection[TupleRow]] | None = None
        self._pool_lock = threading.Lock()

    def __repr__(self) -> str:
        return (
            f"PostgresRequestStore(host={self._host!r}, timeout_s={self._timeout_s}, "
            f"save_timeout_s={self._save_timeout_s})"
        )

    def save_request(
        self, session_id: str, request_text: str, intent: Intent | None, user_id: UUID | None = None
    ) -> SaveOutcome:
        try:
            row = request_row(session_id, request_text, intent, user_id)
        except ValueError:
            log.warning("request not saved: it does not fit the requests table")
            return "failed"
        return "saved" if self._guarded("save", lambda: self._insert(row)) else "failed"

    def save_search(
        self,
        session_id: str,
        rubric: JsonObject,
        result: JsonObject,
        user_id: UUID | None = None,
        *,
        within_s: float | None = None,
    ) -> bool:
        """Insert one finished search and commit it. True only when the row was committed. Never raises.

        `rubric` and `result` are model_dump(mode="json") output: a plain model_dump() holds datetimes and URLs, which
        are not JSON. It takes at most DB_SAVE_TIMEOUT_SECONDS from the call, or `within_s` if that is shorter (what the
        caller has left), building the row included. The row's own work must end COMMIT_MARGIN earlier, so its commit
        lands before the caller stops waiting: a row never appears after the answer False.

        False, with one log line, when the breaker is open or half-open (at once: a save never holds the one trial
        call), the database is down or slower than that, the store is closed, the insert fails, or the search cannot fit
        the table. A save that times out counts once against the breaker, as any other failure does; one that only
        waited for a worker does not count.
        """
        entered = time.monotonic()
        limit = self._save_timeout_s if within_s is None else min(self._save_timeout_s, within_s)
        if self.breaker.state != "closed":
            log.warning("database search save skipped (host=%s): the circuit breaker is open", self._host)
            return False
        try:
            row = search_row(session_id, rubric, result, user_id)
        except Exception as exc:  # whatever the caller passed, a failed save must not reach the search
            log.warning("search not saved: it does not fit the searches table (%s)", type(exc).__name__)
            return False
        left = limit - (time.monotonic() - entered)
        if left <= 0:
            log.warning("database search save skipped (host=%s): no time was left for it", self._host)
            return False
        deadline = time.monotonic() + left - commit_margin(left)
        return self._guarded(
            "search save",
            lambda: self._insert_search(row, deadline),
            log_skips=True,
            timeout_s=left,
            workers=self._save_workers,
        )

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
        self._save_workers.shutdown(wait=False, cancel_futures=True)

    # --- the guarded call -------------------------------------------------------------------------------

    def _guarded(
        self,
        what: str,
        work: Callable[[], None],
        *,
        log_skips: bool = False,
        timeout_s: float | None = None,
        workers: ThreadPoolExecutor | None = None,
    ) -> bool:
        """Run `work` on a worker thread and wait at most `timeout_s` (DB_TIMEOUT_SECONDS unless given). Never
        raises. A timeout is one failure for the breaker: the work's own late end is never counted again."""
        if not self.breaker.allow():
            if log_skips:
                log.warning("database %s skipped (host=%s): the circuit breaker is open", what, self._host)
            return False
        try:
            future = (workers or self._workers).submit(work)
        except RuntimeError:  # the store was closed (the CLI closes it when it is done)
            self.breaker.record_success()  # not a database failure: just give back the trial slot
            if log_skips:
                log.warning("database %s skipped (host=%s): the store is closed", what, self._host)
            return False
        try:
            future.result(timeout=self._timeout_s if timeout_s is None else timeout_s)
        except UserGone:  # a deleted account's row is refused: the database answered, so it is not a failure
            self.breaker.record_success()
            log.warning("database %s refused (host=%s): the account no longer exists", what, self._host)
            return False
        except Exception as exc:  # a timeout, a refused connection, a bad password, a paused project, ...
            never_started = future.cancel()  # if it has not started yet, it never will
            if never_started and workers is not None:
                # A save that only waited behind another save: the database was never asked, and the slow save is
                # counted on its own. The quick calls still count a wait, so a hung database still opens the breaker.
                self.breaker.release()
                log.warning("database %s skipped (host=%s): the other saves held every worker", what, self._host)
                return False
            opened = self.breaker.record_failure()
            skip = f"; skipping the database for {self.breaker.cooldown_s:g} s" if opened else ""
            log.warning("database %s failed (host=%s): %s%s", what, self._host, type(exc).__name__, skip)
            if opened:
                self._close_pool()  # stop the pools retrying in the background; the next trial opens new ones
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
        """Waiting for a connection and the statement count against `deadline`; the commit has COMMIT_MARGIN after it.
        Past the deadline before the insert, nothing is sent; past it after, the row is rolled back."""
        with (
            self._get_save_pool().connection(timeout=max(0.1, deadline - time.monotonic())) as conn,
            _owner_exists(),
            conn.transaction(),
        ):
            left = deadline - time.monotonic()
            if left <= 0:  # the wait for a connection took it all: send nothing
                raise TimeoutError("the search save ran past its time limit")
            # The connection's own statement_timeout is DB_SAVE_TIMEOUT_SECONDS; for this one transaction, what is left.
            left_ms = max(1, int(left * 1000))
            conn.execute(sql.SQL("set local statement_timeout = {}").format(sql.Literal(left_ms)))
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
                self._pool = self._new_pool("shopsense-requests", self._timeout_s)
            return self._pool

    def _get_save_pool(self) -> ConnectionPool[Connection[TupleRow]]:
        """Finished searches' own pool (P-38): a slow save never holds a connection the other calls need."""
        with self._pool_lock:
            if self._save_pool is None:
                self._save_pool = self._new_pool("shopsense-search-saves", self._save_timeout_s)
            return self._save_pool

    def _new_pool(self, name: str, timeout_s: float) -> ConnectionPool[Connection[TupleRow]]:
        """Two connections, each with `timeout_s` as its connect timeout and its statement_timeout."""
        milliseconds = int(timeout_s * 1000)

        def configure(conn: Connection[TupleRow]) -> None:
            conn.execute(sql.SQL("set statement_timeout = {}").format(sql.Literal(milliseconds)))

        pool: ConnectionPool[Connection[TupleRow]] = ConnectionPool(
            self._url,
            min_size=1,
            max_size=2,
            open=False,
            timeout=timeout_s,
            kwargs={
                "connect_timeout": max(2, math.ceil(timeout_s)),  # libpq's minimum is 2 seconds
                "autocommit": True,
                "application_name": "shopsense-backend",
            },
            configure=configure,
            check=ConnectionPool.check_connection,
            name=name,
        )
        pool.open(wait=False)
        return pool

    def _close_pool(self) -> None:
        with self._pool_lock:
            pools, self._pool, self._save_pool = (self._pool, self._save_pool), None, None
        for pool in pools:
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
