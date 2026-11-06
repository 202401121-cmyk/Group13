"""Pre-search sessions, kept in memory (US-05). No database (build rule 10); a later story can add one behind
the same SessionStore interface.

A session follows one shopper from their first request to the finished rubric: the request, how many unclear
replies they gave, the latest Intent, the CriteriaSet, the InterviewState, and the latest InterviewStep (so a
page reload can redraw the chat or the finished rubric).
"""

from __future__ import annotations

import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.contracts import CriteriaSet, Intent, InterviewState, InterviewStep
from app.db import SaveOutcome

SESSION_TTL_S = 2 * 60 * 60  # a session not used for 2 hours expires
MAX_SESSIONS = 1_000  # beyond this, the session unused for longest is dropped first
MAX_UNCLEAR_ATTEMPTS = 2  # IntakeInput.unclear_attempts limit
SESSION_ID_PATTERN = r"^sess_[A-Za-z0-9]{6,40}$"  # the contract's SessionId


def new_session_id() -> str:
    return "sess_" + secrets.token_hex(12)


class PresearchSession(BaseModel):
    """Everything the pre-search API knows about one shopper. Not a contract: it never leaves this API."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str = Field(pattern=SESSION_ID_PATTERN)
    request: str = Field(max_length=1000, description="The request as first typed, trimmed. Empty until there is one")
    unclear_attempts: int = Field(default=0, ge=0, le=MAX_UNCLEAR_ATTEMPTS)
    intent: Intent | None = None
    criteria: CriteriaSet | None = None
    interview: InterviewState | None = None
    interview_step: InterviewStep | None = Field(default=None, description="The latest step, for a page reload")
    request_saved: SaveOutcome | None = Field(
        default=None, description="Whether the request reached the database: saved, off (no database) or failed"
    )

    def changed(self, **changes: object) -> PresearchSession:
        """A validated copy with some fields changed (the model is frozen)."""
        return PresearchSession.model_validate({**self.model_dump(), **changes})


class SessionStore(Protocol):
    def get(self, session_id: str) -> PresearchSession | None: ...

    def put(self, session: PresearchSession) -> None: ...

    def delete(self, session_id: str) -> None: ...

    def lock(self, session_id: str) -> AbstractContextManager[None]:
        """Hold one session while a route reads it, checks it and writes it back."""
        ...


@dataclass
class _SessionLock:
    lock: threading.Lock = field(default_factory=threading.Lock)
    users: int = 0  # the requests holding it or waiting for it


class InMemorySessionStore:
    """Thread-safe. A session expires 2 hours after it was last used; at most 1,000 are kept."""

    def __init__(
        self,
        ttl_s: float = SESSION_TTL_S,
        max_sessions: int = MAX_SESSIONS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.ttl_s = ttl_s
        self.max_sessions = max_sessions
        self._clock = clock
        self._lock = threading.Lock()
        self._sessions: OrderedDict[str, tuple[float, PresearchSession]] = OrderedDict()
        self._session_locks: dict[str, _SessionLock] = {}

    def get(self, session_id: str) -> PresearchSession | None:
        with self._lock:
            found = self._sessions.get(session_id)
            if found is None:
                return None
            used_at, session = found
            now = self._clock()
            if now - used_at > self.ttl_s:
                del self._sessions[session_id]
                return None
            self._sessions[session_id] = (now, session)
            self._sessions.move_to_end(session_id)
            return session

    def put(self, session: PresearchSession) -> None:
        with self._lock:
            now = self._clock()
            self._sessions[session.session_id] = (now, session)
            self._sessions.move_to_end(session.session_id)
            self._drop_expired(now)
            while len(self._sessions) > self.max_sessions:
                self._sessions.popitem(last=False)  # the one unused for longest

    def delete(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    @contextmanager
    def lock(self, session_id: str) -> Iterator[None]:
        """One request at a time for a session; different sessions never wait for each other.

        Routes are plain `def`s, so two requests can run at the same moment in different threads. A lock lasts only
        while it is in use, so the session IDs that clients send can never fill this table.
        """
        with self._lock:
            entry = self._session_locks.setdefault(session_id, _SessionLock())
            entry.users += 1
        try:
            with entry.lock:
                yield
        finally:
            with self._lock:
                entry.users -= 1
                if entry.users == 0:
                    del self._session_locks[session_id]

    def locks_in_use(self) -> int:
        with self._lock:
            return len(self._session_locks)

    def __len__(self) -> int:
        with self._lock:
            return len(self._sessions)

    def _drop_expired(self, now: float) -> None:
        while self._sessions:
            oldest_id, (used_at, _) = next(iter(self._sessions.items()))
            if now - used_at <= self.ttl_s:
                return
            del self._sessions[oldest_id]
