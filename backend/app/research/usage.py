"""Counts what each provider has really been sent, against its caps (US-12).

Serper's free plan is 2,500 queries once, and the archives ask for restraint, so use is counted per search, per UTC day
and in total. Only a request really sent counts; a cache hit is free.

A unit is taken just before its request is sent, so threads sending at once can never pass a cap. A request then refused
before it leaves the machine (by a run budget, D-78) gives its unit back with `not_sent`: the search, the day and the
total each lose it again. The thread that took the units remembers them until `sent` or `not_sent` says what happened,
so one thread's refusal never gives back another's units (a Firecrawl call takes up to three). Research, commerce and
enrich each keep a book; books on one file in one process share their counts, so a unit one gives back is never written
over by another's older count. A search may have its own allowance in place of the caps'
per-search one (enrich's 6, US-41), while the day and the total stay shared. The counts live in the git-ignored
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
from collections import deque
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime
from pathlib import Path

from .settings import ProviderCap

log = logging.getLogger(__name__)

DEFAULT_USAGE_PATH = Path(__file__).resolve().parents[2] / ".cache" / "research" / "usage.json"
PROVIDERS = ("serper", "reddit_archive", "pages", "jina", "firecrawl_pages", "firecrawl", "youtube_search", "youtube")
_FORMAT = 1


def _utc_today() -> date:
    return datetime.now(UTC).date()


class _Counts:
    """One file's counts in this process, and the lock that guards them."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.today: dict[str, int] = {}
        self.total: dict[str, int] = {}
        self.day = ""


_shared_counts: dict[str, _Counts] = {}
_shared_lock = threading.Lock()


def _counts_for(path: Path | None) -> _Counts:
    """The counts every book on this file shares in this process. A book with no file keeps its own."""
    if path is None:
        return _Counts()
    with _shared_lock:
        return _shared_counts.setdefault(os.path.abspath(path), _Counts())


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
        self._state = _counts_for(path)  # shared with every other book on this file in this process
        self._lock = self._state.lock

    # The counts live in the shared state; these read like plain attributes.
    @property
    def _counts_today(self) -> dict[str, int]:
        return self._state.today

    @_counts_today.setter
    def _counts_today(self, counts: dict[str, int]) -> None:
        self._state.today = counts

    @property
    def _counts_total(self) -> dict[str, int]:
        return self._state.total

    @_counts_total.setter
    def _counts_total(self, counts: dict[str, int]) -> None:
        self._state.total = counts

    @property
    def _day(self) -> str:
        return self._state.day

    @_day.setter
    def _day(self, day: str) -> None:
        self._state.day = day

    def search(self, per_search: Mapping[str, int] | None = None) -> SearchUsage:
        """One search's own counts, with this book behind them. `per_search` replaces the caps' per-search allowance
        for the providers it names (D-78: enrich's own 6 Serper look-ups); the day and the total stay shared."""
        return SearchUsage(self, per_search)

    def today(self) -> dict[str, int]:
        with self._lock:
            self._read()
            return {name: self._counts_today.get(name, 0) for name in self.caps}

    def total(self) -> dict[str, int]:
        with self._lock:
            self._read()
            return {name: self._counts_total.get(name, 0) for name in self.caps}

    def _left(self, provider: str, used_in_search: int, allowance: int | None = None) -> int:
        """How many more may be sent. Call with the lock held and the counts fresh. `allowance` is the search's own
        per-search room, if it has one."""
        cap = self.caps.get(provider)
        if cap is None:
            return 0
        per_search = cap.per_search if allowance is None else allowance
        room = [per_search - used_in_search, cap.per_day - self._counts_today.get(provider, 0)]
        if cap.total is not None:
            room.append(cap.total - self._counts_total.get(provider, 0))
        return max(0, min(room))

    def _take(self, provider: str, used_in_search: int, allowance: int | None = None) -> bool:
        with self._lock:
            self._read()
            if self._left(provider, used_in_search, allowance) <= 0:
                return False
            self._counts_today[provider] = self._counts_today.get(provider, 0) + 1
            self._counts_total[provider] = self._counts_total.get(provider, 0) + 1
            self._save()
            return True

    def _give_back(self, provider: str) -> None:
        """One unit of a request that was never sent: off the day and the total again, never below zero."""
        with self._lock:
            self._read()
            for counts in (self._counts_today, self._counts_total):
                counts[provider] = max(0, counts.get(provider, 0) - 1)
            self._save()

    def _room(self, provider: str, used_in_search: int, allowance: int | None = None) -> int:
        with self._lock:
            self._read()
            return self._left(provider, used_in_search, allowance)

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
    """What one search (or one stage of it) has used. Thread-safe: every fetch in a search shares one of these."""

    def __init__(self, book: UsageBook, per_search: Mapping[str, int] | None = None) -> None:
        self._book = book
        self._lock = threading.Lock()
        self._allowance = dict(per_search or {})
        self.used: dict[str, int] = dict.fromkeys(book.caps, 0)

    def per_search(self, provider: str) -> int:
        if provider in self._allowance:
            return self._allowance[provider]
        cap = self._book.caps.get(provider)
        return cap.per_search if cap is not None else 0

    def take(self, provider: str) -> bool:
        """True if one more request may be sent, which is then counted. False if any cap says no.

        The unit is counted now, so threads sending at once can't pass a cap. If the request is then refused before it
        leaves, `not_sent(provider)` on this thread gives the unit back."""
        with self._lock:
            if not self._book._take(provider, self.used.get(provider, 0), self._allowance.get(provider)):
                return False
            self.used[provider] = self.used.get(provider, 0) + 1
        _waiting().setdefault(provider, deque(maxlen=MAX_WAITING)).append(self)
        return True

    def left(self, provider: str) -> int:
        with self._lock:
            return self._book._room(provider, self.used.get(provider, 0), self._allowance.get(provider))

    def _give_back(self, provider: str) -> None:
        with self._lock:
            if self.used.get(provider, 0) <= 0:
                return
            self.used[provider] -= 1
        self._book._give_back(provider)


_local = threading.local()
MAX_WAITING = 4  # one request takes at most 3 units of a provider (a Firecrawl search); older ones are long sent


def _waiting() -> dict[str, deque[SearchUsage]]:
    """This thread's units taken, per provider, whose request hasn't been said to be sent or refused yet. Without a
    run budget nobody says so; the few kept are then simply forgotten."""
    waiting: dict[str, deque[SearchUsage]] | None = getattr(_local, "waiting", None)
    if waiting is None:
        waiting = _local.waiting = {}
    return waiting


def sent(provider: str) -> None:
    """The request this thread took units for really left: they stay counted."""
    _waiting().pop(provider, None)


def not_sent(provider: str) -> int:
    """The request this thread took units for was refused before it left (D-78), so it counts nothing: each unit goes
    back to its search, the day and the total. How many went back: 0 if this thread has none waiting."""
    units = _waiting().pop(provider, None) or deque()
    for usage in units:
        usage._give_back(provider)
    return len(units)


def _larger(first: Mapping[str, int], second: Mapping[str, int]) -> dict[str, int]:
    return {name: max(first.get(name, 0), second.get(name, 0)) for name in {*first, *second}}


def _counts(raw: object) -> dict[str, int]:
    """Only whole, non-negative counts are believed. Anything odd counts as nothing."""
    if not isinstance(raw, dict):
        return {}
    return {
        k: v for k, v in raw.items() if isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool) and v >= 0
    }
