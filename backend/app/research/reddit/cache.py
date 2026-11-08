"""Cache of Reddit answers (US-12, build rule 7), in memory and in the git-ignored backend/.cache/reddit/.

What is cached is the cleaned Sources, never the archive's raw reply: so no username is ever written to disk.
Only complete answers are cached, never a failure. A broken or old file is ignored, and a failed write never
stops a search (build rule 10).

D-53 keeps Reddit data at most 24 hours, counted from when it was read, and covers every copy we hold. So an answer's
age is its oldest source's, and storing a thread again with its comments doesn't restart the clock. An answer is served
only while younger than 24 hours minus SEARCH_RUN_KEEP_MINUTES, so a finished run holding it is gone in time. Files past
deleted by the next search (the research stage sweeps first, whatever else works), and by the first use of a cache. A
machine that runs no search deletes nothing until it does.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import ValidationError

from app.contracts import Source
from app.search_run_limits import keep_minutes

log = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = Path(__file__).resolve().parents[3] / ".cache" / "reddit"
DEFAULT_TTL = timedelta(hours=24)
_FORMAT = 1

CacheKey = tuple[str | int, ...]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def serve_limit(env: Mapping[str, str] | None = None) -> timedelta:
    """How long a Reddit answer may be served."""
    return DEFAULT_TTL - timedelta(minutes=keep_minutes(env))


class RedditCache:
    def __init__(
        self,
        folder: Path | None = DEFAULT_CACHE_DIR,
        ttl: timedelta | None = None,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.folder = folder
        self.ttl = ttl if ttl is not None else serve_limit()
        self._now = now
        self._lock = threading.Lock()
        self._memory: dict[str, tuple[datetime, tuple[Source, ...]]] = {}
        self._swept = False

    def _sweep_once(self) -> None:
        with self._lock:
            if self._swept:
                return
            self._swept = True
        delete_expired(self.folder, self.ttl, self._now)

    def get(self, key: CacheKey) -> tuple[Source, ...] | None:
        self._sweep_once()
        name = _name(key)
        with self._lock:
            hit = self._memory.get(name)
        if hit is None:
            hit = self._read(name)
            if hit is not None:
                with self._lock:
                    self._memory[name] = hit
        if hit is None or self._now() - hit[0] >= self.ttl:
            return None
        return hit[1]

    def put(self, key: CacheKey, sources: Sequence[Source]) -> None:
        self._sweep_once()
        name = _name(key)
        saved = (min([self._now(), *(s.fetched_at for s in sources)]), tuple(sources))
        with self._lock:
            self._memory[name] = saved
        if self.folder is None:
            return
        body = {
            "format": _FORMAT,
            "saved_at": saved[0].isoformat(),
            "sources": [s.model_dump(mode="json") for s in sources],
        }
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            path = self.folder / f"{name}.json"
            path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
            os.utime(path, (saved[0].timestamp(), saved[0].timestamp()))
        except OSError as exc:
            log.warning("reddit cache: could not save an answer (%s); carrying on", type(exc).__name__)

    def _read(self, name: str) -> tuple[datetime, tuple[Source, ...]] | None:
        if self.folder is None:
            return None
        path = self.folder / f"{name}.json"
        if not path.exists():
            return None
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
            if body.get("format") != _FORMAT:
                return None
            saved_at = datetime.fromisoformat(body["saved_at"])
            sources = tuple(Source.model_validate(s) for s in body["sources"])
        except (OSError, ValueError, KeyError, TypeError, AttributeError, ValidationError) as exc:
            log.warning("reddit cache: ignoring an unreadable file (%s)", type(exc).__name__)
            return None
        if saved_at.tzinfo is None:
            return None
        return saved_at, sources


def delete_expired(folder: Path | None, ttl: timedelta, now: Callable[[], datetime] = _utc_now) -> None:
    """Delete this folder's cache files older than `ttl`. Never raises: one we can't delete is tried again next time."""
    if folder is None:
        return
    oldest_allowed = (now() - ttl).timestamp()
    try:
        paths = list(folder.glob("*.json"))
    except OSError as exc:
        log.warning("cache: could not read a folder (%s); carrying on", type(exc).__name__)
        return
    for path in paths:
        try:
            if path.stat().st_mtime <= oldest_allowed:
                path.unlink()
        except OSError as exc:
            log.warning("cache: could not delete an old file (%s); carrying on", type(exc).__name__)


def _name(key: CacheKey) -> str:
    return hashlib.sha256(json.dumps(list(key), ensure_ascii=False).encode()).hexdigest()[:32]
