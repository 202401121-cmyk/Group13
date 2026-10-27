"""Where shopper requests are saved (US-01), behind one small interface so the database is optional.

save_request() answers "saved", "off" (no database configured) or "failed" (configured, but it did not work). It
never raises and never takes longer than DB_TIMEOUT_SECONDS, so a search always finishes (build rule 10).
health() answers "ok", "off" or "down" for GET /api/health.

Stored requests are what people typed and may be personal. They are never sent to Gemini's free tier or any other
model service that may train on its inputs.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from typing import Literal, Protocol

from app.contracts import Intent

SaveOutcome = Literal["saved", "off", "failed"]
DbHealth = Literal["ok", "off", "down"]

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


class RequestStore(Protocol):
    def save_request(self, session_id: str, request_text: str, intent: Intent | None) -> SaveOutcome: ...

    def health(self) -> DbHealth: ...

    def close(self) -> None: ...


class NullRequestStore:
    """The database is off (no DATABASE_URL): everything works and nothing is saved."""

    def save_request(self, session_id: str, request_text: str, intent: Intent | None) -> SaveOutcome:
        return "off"

    def health(self) -> DbHealth:
        return "off"

    def close(self) -> None:
        pass


class InMemoryRequestStore:
    """For tests: keeps rows in a list. fail=True makes it behave like a database that is down."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.rows: list[RequestRow] = []
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

    def health(self) -> DbHealth:
        return "down" if self.fail else "ok"

    def close(self) -> None:
        pass
