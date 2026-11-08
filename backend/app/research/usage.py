"""Counts what each provider has really been sent, against its caps (US-12).

Serper's free plan is 2,500 queries once, and the archives ask for restraint, so use is counted per search, per UTC day
and in total. Only a request really sent counts; a cache hit is free. The counts live in the git-ignored
backend/.cache/research/usage.json, so they survive a restart. Each take reads the file first, so the CLI and a running
server see each other's use, and the larger of the file's count and this process's own is believed, so a file that
reads but won't save can't lift a cap. Two processes taking in the same instant could lose one count, which is close
enough for a limit set well under the real one.

A broken file starts fresh, and a failed save never stops a search (build rule 10). Logs carry the error's type only.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
import threading
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime
from pathlib import Path

from .settings import ProviderCap

log = logging.getLogger(__name__)

DEFAULT_USAGE_PATH = Path(__file__).resolve().parents[2] / ".cache" / "research" / "usage.json"
PROVIDERS = ("serper", "reddit_archive", "pages")
_FORMAT = 1


def _utc_today() -> date:
    return datetime.now(UTC).date()


class UsageBook:
    def __init__(
        self,
        caps: Mapping[str, ProviderCap],
        path: Path | None = DEFAULT_USAGE_PATH,
        today: Callable[[], date] = _utc_today,
    ) -> None:
        self.caps = dict(caps)
        self.path = path
        self._today = today
        self._lock = threading.Lock()
        self._counts_today: dict[str, int] = {}
        self._counts_total: dict[str, int] = {}
        self._day = ""

    def search(self) -> SearchUsage:
        """One search's own counts, with this book behind them."""
        return SearchUsage(self)

    def today(self) -> dict[str, int]:
        with self._lock:
            self._read()
            return {name: self._counts_today.get(name, 0) for name in self.caps}

    def total(self) -> dict[str, int]:
        with self._lock:
            self._read()
            return {name: self._counts_total.get(name, 0) for name in self.caps}

    def _left(self, provider: str, used_in_search: int) -> int:
        """How many more may be sent. Call with the lock held and the counts fresh."""
        cap = self.caps.get(provider)
        if cap is None:
            return 0
        room = [cap.per_search - used_in_search, cap.per_day - self._counts_today.get(provider, 0)]
        if cap.total is not None:
            room.append(cap.total - self._counts_total.get(provider, 0))
        return max(0, min(room))

    def _take(self, provider: str, used_in_search: int) -> bool:
        with self._lock:
            self._read()
            if self._left(provider, used_in_search) <= 0:
                return False
            self._counts_today[provider] = self._counts_today.get(provider, 0) + 1
            self._counts_total[provider] = self._counts_total.get(provider, 0) + 1
            self._save()
            return True

    def _room(self, provider: str, used_in_search: int) -> int:
        with self._lock:
            self._read()
            return self._left(provider, used_in_search)

    def _read(self) -> None:
        day = self._today().isoformat()
        if self._day != day:
            self._day, self._counts_today = day, {}
        saved = self._load() if self.path is not None else None
        if saved is None:
            return
        self._counts_total = _larger(self._counts_total, _counts(saved.get("total")))
        if saved.get("day") == day:
            self._counts_today = _larger(self._counts_today, _counts(saved.get("today")))

    def _load(self) -> dict[str, object] | None:
        assert self.path is not None
        try:
            body = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            log.warning("research usage: ignoring an unreadable counts file (%s)", type(exc).__name__)
            return None
        if not isinstance(body, dict) or body.get("format") != _FORMAT:
            log.warning("research usage: ignoring a counts file in another format")
            return None
        return body

    def _save(self) -> None:
        if self.path is None:
            return
        body = {"format": _FORMAT, "day": self._day, "today": self._counts_today, "total": self._counts_total}
        temporary: str | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle, temporary = tempfile.mkstemp(dir=self.path.parent, prefix=".usage-", suffix=".tmp")
            with os.fdopen(handle, "w", encoding="utf-8") as file:
                json.dump(body, file)
            os.replace(temporary, self.path)
        except OSError as exc:
            log.warning("research usage: could not save the counts (%s); carrying on", type(exc).__name__)
            if temporary is not None:
                with contextlib.suppress(OSError):
                    os.unlink(temporary)


class SearchUsage:
    """What one search has used. Thread-safe: every fetch in a search shares one of these."""

    def __init__(self, book: UsageBook) -> None:
        self._book = book
        self._lock = threading.Lock()
        self.used: dict[str, int] = dict.fromkeys(book.caps, 0)

    def per_search(self, provider: str) -> int:
        cap = self._book.caps.get(provider)
        return cap.per_search if cap is not None else 0

    def take(self, provider: str) -> bool:
        """True if one more request may be sent, which is then counted. False if any cap says no."""
        with self._lock:
            if not self._book._take(provider, self.used.get(provider, 0)):
                return False
            self.used[provider] = self.used.get(provider, 0) + 1
            return True

    def left(self, provider: str) -> int:
        with self._lock:
            return self._book._room(provider, self.used.get(provider, 0))


def _larger(first: Mapping[str, int], second: Mapping[str, int]) -> dict[str, int]:
    return {name: max(first.get(name, 0), second.get(name, 0)) for name in {*first, *second}}


def _counts(raw: object) -> dict[str, int]:
    """Only whole, non-negative counts are believed. Anything odd counts as nothing."""
    if not isinstance(raw, dict):
        return {}
    return {
        k: v for k, v in raw.items() if isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool) and v >= 0
    }
