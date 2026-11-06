"""The HTTP API: a health check, the search endpoint (US-52) and the pre-search routes (US-05).

Run: uvicorn app.main:app --reload
No auth yet. Requests and finished searches are saved to Postgres when DATABASE_URL is set (US-01, US-52); the health
check reports the database as "ok", "off" or "down". CORS allows only the origins in FRONTEND_ORIGINS (default
http://localhost:3000).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping

from dotenv import dotenv_values, find_dotenv
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import db
from app.api import presearch_router
from app.api.presearch import plain_validation_message
from app.contracts import SCHEMA_VERSION, SearchRequest, SearchResult
from app.pipeline import DatabaseResultStore, PipelineRunner, default_search_stages

log = logging.getLogger(__name__)

DEFAULT_FRONTEND_ORIGINS = ("http://localhost:3000",)  # the frontend's dev server (npm run dev)


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


def search(request: SearchRequest) -> SearchResult:
    # A plain `def`, so FastAPI runs the (blocking) pipeline in its thread pool, not on the event loop.
    return PipelineRunner(default_search_stages(), DatabaseResultStore(db.default_request_store())).run(request)


async def plain_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """A bad request body gets one plain sentence, without echoing what was sent (it may be the user's words)."""
    return JSONResponse(status_code=422, content={"detail": plain_validation_message(exc.errors(), "Please check")})


def create_app(env: Mapping[str, str] | None = None) -> FastAPI:
    """Build the app. The allowed origins are read here, so tests can pass their own environment."""
    app = FastAPI(title="ShopSense", version=SCHEMA_VERSION)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins(env),
        allow_methods=["GET", "POST", "PUT"],  # only what the frontend calls
        allow_headers=["Content-Type"],
    )
    app.add_exception_handler(RequestValidationError, plain_validation_error)  # type: ignore[arg-type]
    app.get("/api/health")(health)
    app.post("/api/search", response_model=SearchResult)(search)
    app.include_router(presearch_router)
    return app


app = create_app()
