"""Where requests and finished searches are saved (US-01, US-52), behind one interface so the database is optional.

save_request() answers "saved", "off" (no database configured) or "failed" (configured, but it did not work).
save_search() answers True only when the row was committed. Neither raises or takes longer than DB_TIMEOUT_SECONDS,
so a search always finishes (build rule 10). health() answers "ok", "off" or "down" for GET /api/health.

Stored requests and searches come from what people typed and may be personal. They are never sent to Gemini's free
tier or any other model service that may train on its inputs.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from app.contracts import Intent

log = logging.getLogger(__name__)

SaveOutcome = Literal["saved", "off", "failed"]
DbHealth = Literal["ok", "off", "down"]
JsonObject = dict[str, Any]

SESSION_ID_RE = re.compile(r"^sess_[A-Za-z0-9]{6,40}$")  # the contract's SessionId, also checked by the table
MAX_REQUEST_CHARS = 1000  # Intent.raw_query and SearchRequest.raw_query allow at most 1000 characters


@dataclass(frozen=True)
class RequestRow:
    """One row of public.requests, before the database adds its id and created_at."""

    session_id: str
    request_text: str
    intent: Intent | None  # only when the intent is ready for research
    intent_source: Literal["llm", "rules"] | None  # Intent.detected_by


def request_row(session_id: str, request_text: str, intent: Intent | None) -> RequestRow:
    """Check a request before any store saves it. Raises ValueError on a bad session ID or text."""
    if not SESSION_ID_RE.match(session_id):
        raise ValueError("session_id does not match the contract's SessionId pattern")
    if not 1 <= len(request_text) <= MAX_REQUEST_CHARS:
        raise ValueError(f"request_text must be 1 to {MAX_REQUEST_CHARS} characters")
    ready = intent is not None and intent.ready_for_research
    return RequestRow(
        session_id=session_id,
        request_text=request_text,
        intent=intent if ready else None,
        intent_source=intent.detected_by if intent is not None else None,
    )


@dataclass(frozen=True)
class SearchRow:
    """One row of public.searches, before the database adds its id and created_at. Both JSON fields are JSON text."""

    session_id: str
    rubric: str
    result: str


def search_row(session_id: str, rubric: JsonObject, result: JsonObject) -> SearchRow:
    """Check a search before any store saves it. Raises ValueError or TypeError when it cannot fit the table.

    The check runs here, not in the database, so that one bad result is never counted as the database being down.
    """
    if not SESSION_ID_RE.fullmatch(session_id):  # fullmatch: "$" alone would let a trailing newline through
        raise ValueError("session_id does not match the contract's SessionId pattern")
    return SearchRow(session_id=session_id, rubric=_json_text(rubric), result=_json_text(result))


def _json_text(value: JsonObject) -> str:
    text = json.dumps(value, allow_nan=False)  # NaN is not JSON, and a datetime means model_dump() without mode="json"
    # json.dumps writes NUL as \u0000, which jsonb refuses. Drop escaped backslashes first: \\u0000 is only plain text.
    if "\\u0000" in text.replace("\\\\", ""):
        raise ValueError("jsonb cannot hold the NUL character")
    return text


class RequestStore(Protocol):
    def save_request(self, session_id: str, request_text: str, intent: Intent | None) -> SaveOutcome: ...

    def save_search(self, session_id: str, rubric: JsonObject, result: JsonObject) -> bool: ...

    def health(self) -> DbHealth: ...

    def close(self) -> None: ...


class NullRequestStore:
    """The database is off (no DATABASE_URL): everything works and nothing is saved."""

    def save_request(self, session_id: str, request_text: str, intent: Intent | None) -> SaveOutcome:
        return "off"

    def save_search(self, session_id: str, rubric: JsonObject, result: JsonObject) -> bool:
        log.info("search not saved: the database is off")  # info, not warning: this is a normal way to run
        return False

    def health(self) -> DbHealth:
        return "off"

    def close(self) -> None:
        pass


class InMemoryRequestStore:
    """For tests: keeps rows in a list. fail=True makes it behave like a database that is down."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.rows: list[RequestRow] = []
        self.searches: list[SearchRow] = []
        self._lock = threading.Lock()

    def save_request(self, session_id: str, request_text: str, intent: Intent | None) -> SaveOutcome:
        if self.fail:
            return "failed"
        try:
            row = request_row(session_id, request_text, intent)
        except ValueError:
            return "failed"
        with self._lock:
            self.rows.append(row)
        return "saved"

    def save_search(self, session_id: str, rubric: JsonObject, result: JsonObject) -> bool:
        if self.fail:
            return False
        try:
            row = search_row(session_id, rubric, result)
        except (TypeError, ValueError):
            return False
        with self._lock:
            self.searches.append(row)
        return True

    def health(self) -> DbHealth:
        return "down" if self.fail else "ok"

    def close(self) -> None:
        pass
