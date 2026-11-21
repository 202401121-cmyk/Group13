"""The HTTP API: a health check, the search endpoint (US-52), the pre-search routes (US-05) and search runs (US-12).

Run: uvicorn app.main:app --reload
Sign-in (US-30): a bearer token from Supabase Auth names the user, checked against the project's public signing keys
(app/auth); no token, or a bad one, is a guest. Requests and finished searches are saved to Postgres when
DATABASE_URL is set (US-01, US-52), a signed-in user's with their id; the health
check reports the database as "ok", "off" or "down". CORS allows only the origins in FRONTEND_ORIGINS (default
http://localhost:3000).

While the app runs, its housekeeping deletes expired Reddit text without waiting for a search (D-53, US-12): at
start-up and then every SWEEP_EVERY_S, the cache files past their limit, and the finished runs past their keep-time.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager

import anyio
from dotenv import dotenv_values, find_dotenv
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import db
from app.api import (
    SearchRuns,
    account_router,
    default_runner,
    load_run_settings,
    owned_by,
    presearch_router,
    search_runs_router,
)
from app.api.exclusions import router as exclusions_router
from app.api.presearch import plain_validation_message
from app.auth import OptionalUser
from app.contracts import SCHEMA_VERSION, SearchRequest, SearchResult
from app.pipeline import DatabaseResultStore, PipelineRunner, default_search_stages
from app.research.cache import sweep_expired_files
from app.search_run_limits import SWEEP_EVERY_S

log = logging.getLogger(__name__)

DEFAULT_FRONTEND_ORIGINS = ("http://localhost:3000",)  # the frontend's dev server (npm run dev)


class Housekeeping:
    """D-53 without a search: at start-up, then every `every_s`, finished runs past their keep-time and expired cache
    files go.

    Each tick runs in a worker thread of its own, so file work never holds up a request on the event loop and never
    waits for the requests' threads. The runs go first: that is quick and in memory, so a slow disk never holds it up.
    Each part, and each pass of the loop, has its own guard: a failure is logged by its type only (build rule 9), and
    nothing else is stopped. The loop ends only when the app shuts down and cancels it; a tick still in its thread is
    left to finish on its own, so shutting down never waits for a stuck disk.
    """

    def __init__(
        self,
        runs: SearchRuns,
        sweep: Callable[[], None] = sweep_expired_files,
        every_s: float = SWEEP_EVERY_S,
        wait: Callable[[float], Awaitable[None]] = anyio.sleep,
    ) -> None:
        self.runs = runs
        self.sweep = sweep
        self.every_s = every_s
        self.wait = wait
        self.ticks = 0
        self.ticked = threading.Event()  # set after each tick
        self.running = False

    def tick(self) -> None:
        for part, job in (("run store", self.runs.drop_expired), ("cache sweep", self.sweep)):
            try:
                job()
            except Exception as exc:  # never let housekeeping stop the app, a request or the next tick
                log.warning("housekeeping: the %s failed (%s)", part, type(exc).__name__)
        self.ticks += 1
        self.ticked.set()

    async def run(self) -> None:
        self.running = True
        limiter = anyio.CapacityLimiter(1)  # one thread of its own, never queued behind busy request threads
        try:
            while True:
                try:
                    await anyio.to_thread.run_sync(self.tick, abandon_on_cancel=True, limiter=limiter)
                except Exception as exc:  # such as no thread to be had: log it, and try again at the next tick
                    log.warning("housekeeping: a tick failed (%s)", type(exc).__name__)
                await self.wait(self.every_s)
        finally:
            self.running = False


def allowed_origins(env: Mapping[str, str] | None = None) -> list[str]:
    """The browser origins that may call the API (D-35), from FRONTEND_ORIGINS (comma-separated).

    FRONTEND_ORIGIN, the older one-origin name, still works when FRONTEND_ORIGINS is not set. "*" is never allowed:
    the API serves only our own frontend, so a wildcard is dropped with a warning. With no `env`, the root .env is
    read (without changing os.environ) and real environment variables win.
    """
    if env is None:
        env = {**{k: v for k, v in dotenv_values(find_dotenv()).items() if v is not None}, **os.environ}
    raw = env.get("FRONTEND_ORIGINS") or env.get("FRONTEND_ORIGIN") or ""
    origins = []
    for part in raw.split(","):
        origin = part.strip().rstrip("/")
        if origin == "*":
            log.warning("FRONTEND_ORIGINS may not contain '*'; it was ignored")
        elif origin and origin not in origins:
            origins.append(origin)
    return origins or list(DEFAULT_FRONTEND_ORIGINS)


def health() -> dict[str, str]:
    # A plain `def`: the database check (at most DB_TIMEOUT_SECONDS) runs in the thread pool, not the event loop.
    return {"status": "ok", "schema_version": SCHEMA_VERSION, "database": db.default_request_store().health()}


def search(request: SearchRequest, user: OptionalUser) -> SearchResult:
    # A plain `def`, so FastAPI runs the (blocking) pipeline in its thread pool, not on the event loop.
    # The owner comes from the checked token, never the body: a guest's search is never linked (US-30, D-36).
    runner = PipelineRunner(default_search_stages(), DatabaseResultStore(db.default_request_store()))
    return runner.run(owned_by(request, user))


async def plain_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """A bad request body gets one plain sentence, without echoing what was sent (it may be the user's words)."""
    return JSONResponse(status_code=422, content={"detail": plain_validation_message(exc.errors(), "Please check")})


def create_app(env: Mapping[str, str] | None = None) -> FastAPI:
    """Build the app. The allowed origins are read here, so tests can pass their own environment."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(app.state.housekeeping.run)  # its first tick is the start-up sweep
                yield
                tasks.cancel_scope.cancel()  # a tick still in its thread is left to finish on its own
        finally:
            # Ask running searches to stop before their next stage. Uvicorn runs this once open progress streams have
            # ended, or its graceful-shutdown timeout has passed; a forced stop skips it (docs/frontend-api.md).
            app.state.search_runs.close()

    app = FastAPI(title="ShopSense", version=SCHEMA_VERSION, lifespan=lifespan)
    app.state.search_runs = SearchRuns(default_runner, load_run_settings(env))
    app.state.housekeeping = Housekeeping(app.state.search_runs)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins(env),
        # Only what the frontend calls: DELETE cancels a search run and deletes what is remembered; PATCH edits a
        # remembered preference (US-11).
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        # Last-Event-ID resumes a progress stream; Authorization carries a signed-in user's token (US-30).
        allow_headers=["Content-Type", "Last-Event-ID", "Authorization"],
        # So the frontend can read when to try a busy server again, the rubric editor's note (US-09), the memory
        # screen's note on an edit, and the export's file name (US-11).
        expose_headers=["Retry-After", "X-Rubric-Note", "X-Memory-Note", "Content-Disposition"],
    )
    app.add_exception_handler(RequestValidationError, plain_validation_error)  # type: ignore[arg-type]
    app.get("/api/health")(health)
    app.post("/api/search", response_model=SearchResult)(search)
    app.include_router(presearch_router)
    app.include_router(exclusions_router)  # US-10: stateless, reads exclusions from words
    app.include_router(search_runs_router)
    app.include_router(account_router)  # US-30: /api/me
    return app


app = create_app()
