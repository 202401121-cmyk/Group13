"""The HTTP API: a health check, the search endpoint (US-52) and the pre-search routes (US-05).

Run: uvicorn app.main:app --reload
No auth and no database yet. CORS allows only FRONTEND_ORIGIN (default http://localhost:3000).
"""

from __future__ import annotations

import os

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import presearch_router
from app.api.presearch import plain_validation_message
from app.contracts import SCHEMA_VERSION, SearchRequest, SearchResult
from app.pipeline import InMemoryResultStore, PipelineRunner, default_search_stages

DEFAULT_FRONTEND_ORIGIN = "http://localhost:3000"

store = InMemoryResultStore()


def health() -> dict[str, str]:
    return {"status": "ok", "schema_version": SCHEMA_VERSION}


def search(request: SearchRequest) -> SearchResult:
    # A plain `def`, so FastAPI runs the (blocking) pipeline in its thread pool, not on the event loop.
    return PipelineRunner(default_search_stages(), store).run(request)


async def plain_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """A bad request body gets one plain sentence, without echoing what was sent (it may be the user's words)."""
    return JSONResponse(status_code=422, content={"detail": plain_validation_message(exc.errors(), "Please check")})


def create_app() -> FastAPI:
    """Build the app. FRONTEND_ORIGIN is read here, so tests can build one with a different origin."""
    app = FastAPI(title="ShopSense", version=SCHEMA_VERSION)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[os.environ.get("FRONTEND_ORIGIN", DEFAULT_FRONTEND_ORIGIN)],
        allow_methods=["GET", "POST", "PUT"],
        allow_headers=["Content-Type"],
    )
    app.add_exception_handler(RequestValidationError, plain_validation_error)  # type: ignore[arg-type]
    app.get("/api/health")(health)
    app.post("/api/search", response_model=SearchResult)(search)
    app.include_router(presearch_router)
    return app


app = create_app()
