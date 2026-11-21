"""Where requests and finished searches are saved (US-01, US-52), behind one interface so the database is optional.

save_request() answers "saved", "off" (no database configured) or "failed" (configured, but it did not work).
save_search() answers True only when the row was committed. Neither raises or takes longer than DB_TIMEOUT_SECONDS,
so a search always finishes (build rule 10). health() answers "ok", "off" or "down" for GET /api/health.

Stored requests and searches come from what people typed and may be personal. They are never sent to Gemini's free
tier or any other model service that may train on its inputs.

A signed-in shopper's rows carry their user id (US-30), set when the row is inserted and never afterwards: nothing here
updates a row, so a guest's row is never linked to an account (D-36), and the database refuses it too (S4-27).
"""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Protocol
from uuid import UUID, uuid4

from app.contracts import Intent

from .accounts import AccountsUnavailable, PageAfter, SavedSearchRow, UserGone, newest_first, saved_search_row

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
    user_id: UUID | None = None  # the signed-in owner (US-30); None for a guest


def request_row(session_id: str, request_text: str, intent: Intent | None, user_id: UUID | None = None) -> RequestRow:
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
        user_id=_owner(user_id),
    )


@dataclass(frozen=True)
class SearchRow:
    """One row of public.searches, before the database adds its id and created_at. Both JSON fields are JSON text."""

    session_id: str
    rubric: str
    result: str
    user_id: UUID | None = None  # the signed-in owner (US-30); None for a guest


def search_row(session_id: str, rubric: JsonObject, result: JsonObject, user_id: UUID | None = None) -> SearchRow:
    """Check a search before any store saves it. Raises ValueError or TypeError when it cannot fit the table.

    The check runs here, not in the database, so that one bad result is never counted as the database being down.
    """
    if not SESSION_ID_RE.fullmatch(session_id):  # fullmatch: "$" alone would let a trailing newline through
        raise ValueError("session_id does not match the contract's SessionId pattern")
    return SearchRow(
        session_id=session_id, rubric=_json_text(rubric), result=_json_text(result), user_id=_owner(user_id)
    )


def _owner(user_id: object) -> UUID | None:
    if user_id is not None and not isinstance(user_id, UUID):
        raise ValueError("user_id is a signed-in user's id (a UUID) or None")
    return user_id


def _json_text(value: JsonObject) -> str:
    text = json.dumps(value, allow_nan=False)  # NaN is not JSON, and a datetime means model_dump() without mode="json"
    # json.dumps writes NUL as \u0000, which jsonb refuses. Drop escaped backslashes first: \\u0000 is only plain text.
    if "\\u0000" in text.replace("\\\\", ""):
        raise ValueError("jsonb cannot hold the NUL character")
    return text


class RequestStore(Protocol):
    def save_request(
        self, session_id: str, request_text: str, intent: Intent | None, user_id: UUID | None = None
    ) -> SaveOutcome: ...

    def save_search(
        self, session_id: str, rubric: JsonObject, result: JsonObject, user_id: UUID | None = None
    ) -> bool: ...

    def health(self) -> DbHealth: ...

    def close(self) -> None: ...


class NullRequestStore:
    """The database is off (no DATABASE_URL): everything works and nothing is saved."""

    def save_request(
        self, session_id: str, request_text: str, intent: Intent | None, user_id: UUID | None = None
    ) -> SaveOutcome:
        return "off"

    def save_search(self, session_id: str, rubric: JsonObject, result: JsonObject, user_id: UUID | None = None) -> bool:
        log.info("search not saved: the database is off")  # info, not warning: this is a normal way to run
        return False

    def health(self) -> DbHealth:
        return "off"

    def close(self) -> None:
        pass


@dataclass(frozen=True)
class StoredSearch:
    """A saved search as the in-memory store keeps it, with the id and time the database would add."""

    row: SearchRow
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class InMemoryRequestStore:
    """For tests: keeps rows in a list. fail=True makes it behave like a database that is down.

    It is also an AccountStore (app/db/accounts.py), with the Postgres store's rules: a profile is made once, and a user
    sees only their own saved searches, newest first.
    """

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.rows: list[RequestRow] = []
        self.saved: list[StoredSearch] = []
        self.profiles: dict[UUID, str | None] = {}
        self.users: set[UUID] | None = None  # None: every user exists; else the ids auth.users holds
        self._lock = threading.Lock()

    @property
    def searches(self) -> list[SearchRow]:
        return [stored.row for stored in self.saved]

    def save_request(
        self, session_id: str, request_text: str, intent: Intent | None, user_id: UUID | None = None
    ) -> SaveOutcome:
        if self.fail:
            return "failed"
        try:
            row = request_row(session_id, request_text, intent, user_id)
        except ValueError:
            return "failed"
        with self._lock:
            self.rows.append(row)
        return "saved"

    def save_search(self, session_id: str, rubric: JsonObject, result: JsonObject, user_id: UUID | None = None) -> bool:
        if self.fail:
            return False
        try:
            row = search_row(session_id, rubric, result, user_id)
        except (TypeError, ValueError):
            return False
        with self._lock:
            self.saved.append(StoredSearch(row))
        return True

    def health(self) -> DbHealth:
        return "down" if self.fail else "ok"

    def close(self) -> None:
        pass

    # --- AccountStore (US-30) ----------------------------------------------------------------------------

    def ensure_profile(self, user_id: UUID) -> str | None:
        if self.fail:
            raise AccountsUnavailable("the database is down")
        with self._lock:
            if self.users is not None and user_id not in self.users:
                raise UserGone("no such user")
            return self.profiles.setdefault(user_id, None)  # made once; an existing profile is left as it is

    def saved_searches(self, user_id: UUID, after: PageAfter | None, limit: int) -> list[SavedSearchRow]:
        if self.fail:
            raise AccountsUnavailable("the database is down")
        with self._lock:
            mine = [stored for stored in self.saved if stored.row.user_id == user_id]
        rows = [saved_search_row(s.id, s.created_at, json.loads(s.row.result), json.loads(s.row.rubric)) for s in mine]
        return newest_first(rows, after)[:limit]

    def saved_search(self, user_id: UUID, search_id: UUID) -> JsonObject | None:
        if self.fail:
            raise AccountsUnavailable("the database is down")
        with self._lock:
            found = [s for s in self.saved if s.id == search_id and s.row.user_id == user_id]
        loaded: JsonObject | None = json.loads(found[0].row.result) if found else None
        return loaded

    def account_rows(self, user_id: UUID) -> dict[str, int]:
        """The read-back of US-11's account delete. This store holds no preferences (app/memory keeps them)."""
        if self.fail:
            raise AccountsUnavailable("the database is down")
        with self._lock:
            return {
                "requests": sum(1 for row in self.rows if row.user_id == user_id),
                "searches": sum(1 for stored in self.saved if stored.row.user_id == user_id),
                "profiles": int(user_id in self.profiles),
                "preferences": 0,
            }
