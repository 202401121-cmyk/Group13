"""24-hour cache of Reddit answers (US-12, build rule 7), in memory and in the git-ignored backend/.cache/reddit/.

What is cached is the cleaned Sources, never the archive's raw reply: so no username is ever written to disk.
Only complete answers are cached, never a failure. A broken or old file is ignored, and a failed write never
stops a search (build rule 10).
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import ValidationError

from app.contracts import Source

log = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = Path(__file__).resolve().parents[3] / ".cache" / "reddit"
DEFAULT_TTL = timedelta(hours=24)
_FORMAT = 1

CacheKey = tuple[str | int, ...]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class RedditCache:
    def __init__(
        self,
        folder: Path | None = DEFAULT_CACHE_DIR,
        ttl: timedelta = DEFAULT_TTL,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.folder = folder
        self.ttl = ttl
        self._now = now
        self._lock = threading.Lock()
        self._memory: dict[str, tuple[datetime, tuple[Source, ...]]] = {}

    def get(self, key: CacheKey) -> tuple[Source, ...] | None:
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
        name = _name(key)
        saved = (self._now(), tuple(sources))
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
            (self.folder / f"{name}.json").write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
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


def _name(key: CacheKey) -> str:
    return hashlib.sha256(json.dumps(list(key), ensure_ascii=False).encode()).hexdigest()[:32]
