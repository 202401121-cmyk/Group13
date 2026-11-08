"""24-hour cache of search results, pages and robots files (US-12, P-12), in memory and in backend/.cache/research/.

Sources go through RedditCache's code, which does the same job. A little text (robots files, and titles of results that
point at Reddit) is kept the same way. Only complete answers are cached, never a failure, and a failed write never stops
a search (build rule 10). What is cached is what we chose to keep: a search result pointing at Reddit keeps its title
only, and its snippet never gets this far (D-53).
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.contracts import Source

from .reddit.cache import DEFAULT_CACHE_DIR as REDDIT_CACHE_DIR
from .reddit.cache import CacheKey, RedditCache, delete_expired

log = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = Path(__file__).resolve().parents[2] / ".cache" / "research"
DEFAULT_TTL = timedelta(hours=24)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def sweep_expired_files() -> None:
    """Everything past its 24 hours. It needs no client or source, so D-53 holds whatever else is broken."""
    delete_expired(REDDIT_CACHE_DIR, DEFAULT_TTL)
    delete_expired(DEFAULT_CACHE_DIR / "text", DEFAULT_TTL)
    delete_expired(DEFAULT_CACHE_DIR / "sources", DEFAULT_TTL)


class ResearchCache:
    def __init__(
        self,
        folder: Path | None = DEFAULT_CACHE_DIR,
        ttl: timedelta = DEFAULT_TTL,
        now: Callable[[], datetime] = _utc_now,
        enabled: bool = True,
    ) -> None:
        self.folder = folder
        self.ttl = ttl
        self.enabled = enabled
        self._now = now
        self._sources = RedditCache(folder=folder / "sources" if folder else None, ttl=ttl, now=now)
        self._lock = threading.Lock()
        self._text: dict[str, tuple[datetime, str]] = {}
        if folder is not None:  # switched off or not: Reddit titles live here, at most 24 hours (D-53)
            delete_expired(folder / "text", ttl, now)
            delete_expired(folder / "sources", ttl, now)

    def get_sources(self, key: CacheKey) -> tuple[Source, ...] | None:
        return self._sources.get(key) if self.enabled else None

    def put_sources(self, key: CacheKey, sources: Sequence[Source]) -> None:
        if self.enabled:
            self._sources.put(key, sources)

    def get_text(self, key: CacheKey) -> str | None:
        if not self.enabled:
            return None
        name = _name(key)
        with self._lock:
            hit = self._text.get(name)
        if hit is None:
            hit = self._read_text(name)
            if hit is not None:
                with self._lock:
                    self._text[name] = hit
        if hit is None or self._now() - hit[0] >= self.ttl:
            return None
        return hit[1]

    def put_text(self, key: CacheKey, text: str) -> None:
        if not self.enabled:
            return
        name = _name(key)
        saved = (self._now(), text)
        with self._lock:
            self._text[name] = saved
        if self.folder is None:
            return
        try:
            folder = self.folder / "text"
            folder.mkdir(parents=True, exist_ok=True)
            body = {"saved_at": saved[0].isoformat(), "text": text}
            (folder / f"{name}.json").write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        except OSError as exc:
            log.warning("research cache: could not save (%s); carrying on", type(exc).__name__)

    def _read_text(self, name: str) -> tuple[datetime, str] | None:
        if self.folder is None:
            return None
        path = self.folder / "text" / f"{name}.json"
        if not path.exists():
            return None
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
            saved_at = datetime.fromisoformat(body["saved_at"])
            text = body["text"]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log.warning("research cache: ignoring an unreadable file (%s)", type(exc).__name__)
            return None
        if saved_at.tzinfo is None or not isinstance(text, str):
            return None
        return saved_at, text


def _name(key: CacheKey) -> str:
    return hashlib.sha256(json.dumps(list(key), ensure_ascii=False).encode()).hexdigest()[:32]
