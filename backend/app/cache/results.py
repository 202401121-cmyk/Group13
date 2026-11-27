"""An exact-match cache for whole searches (US-34, D-80): a search repeated within its limit answers from here.

The key (CacheKey) is exact, never a likeness:
- the request's own words, normalised (case and runs of white space only);
- the category, and a hash of the whole rubric: every weight with its share, origin and uncertainty, the hard rules,
  the exclusions, the budget, the region and the use. A result scored for other priorities is never reused;
- for a signed-in shopper, their user id and a hash of their stored preferences (US-06): an edit or a new preference
  changes the key. A guest's key holds no user id, so a guest never gets a signed-in shopper's result, and a signed-in
  key is never another user's. If the preferences can't be read, the search runs and nothing is kept for it.

What is kept is the finished result as a saved search keeps it: without Reddit text (app/research/retention.py, D-53),
without the user id and session, and only a full one: not partial, not one where no model could answer, not sample data,
and never a cached answer kept again. Each entry expires at serve_limit() from when its search finished, not a full 24
hours (D-53), checked on every read and swept by the app's housekeeping every ten minutes (sweep_results). A broken
entry is deleted, and the search runs.

A hit (as_hit) is a new search: a new id, the current request (its session and owner), cached true and cached_at the
time the kept search finished. It is then saved as any search is. Nothing here ever raises into a search (build rule
9): any fault is a miss, or nothing kept.

On disk under backend/.cache/results/, one JSON file a key, named by its hash. RESULT_CACHE=on|off (default on).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.contracts import SearchRequest, SearchResult, StageStatus
from app.research.reddit.cache import delete_expired, serve_limit
from app.research.retention import without_reddit_text
from app.search_run_limits import SWEEP_EVERY_S, environment

log = logging.getLogger(__name__)

RESULTS_DIR = Path(__file__).resolve().parents[2] / ".cache" / "results"
SWITCH = "RESULT_CACHE"
FORMAT = 1
FULL = frozenset({StageStatus.OK, StageStatus.SKIPPED})  # a stage that ran as itself, or had nothing to do
KEPT_SESSION = "sess_resultcache"  # a kept result's stand-in session: a hit takes the current request's
SAMPLE_WORDS = "sample results"  # the sample note's words (app/pipeline.py SAMPLE_NOTE): never kept as a real answer

PreferenceHash = Callable[[str], str]  # a signed-in user's id -> a hash of their stored preferences


def _utc_now() -> datetime:
    return datetime.now(UTC)


def cache_on(env: Mapping[str, str] | None = None) -> bool:
    """RESULT_CACHE: "off" turns the cache off; "on", blank or unset keeps it on. Anything else: on, with a warning."""
    values = env if env is not None else environment()
    raw = (values.get(SWITCH) or "").strip().casefold()
    if raw in ("", "on"):
        return True
    if raw == "off":
        return False
    log.warning("%s must be on or off; using on", SWITCH)
    return True


def normalised(text: str) -> str:
    """The request's words as the key holds them: case and runs of white space don't count, anything else does."""
    return " ".join(text.casefold().split())


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CacheKey:
    text: str
    category: str
    rubric: str  # a hash of the whole rubric
    user: str | None  # a hash of the user's id; None for a guest
    preferences: str  # a hash of the user's stored preferences; empty for a guest

    @property
    def name(self) -> str:
        return _digest(asdict(self))[:40]


def key_for(request: SearchRequest, preferences: str) -> CacheKey:
    rubric = request.rubric
    return CacheKey(
        text=normalised(request.raw_query),
        category=rubric.category,
        rubric=_digest(rubric.model_dump(mode="json")),
        user=_digest(request.user_id) if request.user_id is not None else None,  # never the id itself on disk
        preferences=preferences if request.user_id is not None else "",
    )


def stored_preferences_hash(user_id: str, listing: Callable[[uuid.UUID], Sequence[Any]] | None = None) -> str:
    """A hash of everything about the user's stored preferences that can change a search (US-06), read by `listing`
    (the app's memory service by default). Raises when they can't be read: the search then runs uncached."""
    if listing is None:
        from app.memory import default_memory

        memory = default_memory()
        if not memory.usable:  # memory off, or no database: no stored preference can take part in a search
            return _digest([])
        listing = memory.listing
    rows = listing(uuid.UUID(user_id))
    kept = [
        {
            "id": str(row.id),
            "kind": row.kind,
            "category": row.category,
            "criterion_key": row.criterion_key,
            "text": row.text,
            "weight": row.weight,
            "rule": row.rule,
            "always": row.always,
            "updated_at": row.updated_at.isoformat(),
        }
        for row in rows
    ]
    return _digest(sorted(kept, key=lambda row: row["id"]))


def cacheable(result: SearchResult) -> bool:
    """A full search worth repeating: every stage ran as itself or had nothing to do (none fell back, timed out or
    failed), it decided, a model answered, it is real data, and it is not itself a hit. A degraded search runs again
    next time, so a thin answer from a bad hour is never repeated for a day."""
    note = (result.coverage_note or "").casefold()
    return (
        not result.partial
        and not result.llm_unavailable
        and not result.cached
        and result.decision is not None
        and SAMPLE_WORDS not in note
        and all(report.status in FULL for report in result.stage_reports)
    )


def as_hit(
    kept: SearchResult, made_at: datetime, request: SearchRequest, search_id: str, now: datetime
) -> SearchResult:
    """The kept result as a new search for this request: its id, its session and owner, cached since `made_at`."""
    return SearchResult.model_validate(
        {
            **kept.model_dump(),
            "search_id": search_id,
            "request": request.model_dump(),
            "started_at": now,
            "finished_at": now,
            "cached": True,
            "cached_at": made_at,
            "saved": True,  # what the stored row must say; the runner returns a saved=False copy if it is not committed
        }
    )


@dataclass(frozen=True)
class Lookup:
    """One search's key, made once (its preferences read once), and the kept result for it, if any."""

    key: CacheKey | None
    hit: tuple[SearchResult, datetime] | None = None


class ResultCache:
    def __init__(
        self,
        folder: Path | None,
        preferences: PreferenceHash = stored_preferences_hash,
        enabled: bool = True,
        ttl: timedelta | None = None,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.folder = folder
        self.preferences = preferences
        self.enabled = enabled and folder is not None
        self.ttl = ttl if ttl is not None else serve_limit()
        self._now = now

    def key(self, request: SearchRequest) -> CacheKey | None:
        """The request's key, or None when it can't be made exactly (a signed-in user's preferences unread)."""
        preferences = ""
        if request.user_id is not None:
            try:
                preferences = self.preferences(request.user_id)
            except Exception as exc:  # unread preferences: never guess a key, so never serve or keep (rule 9)
                log.warning("result cache: preferences not read (%s); searching uncached", type(exc).__name__)
                return None
        return key_for(request, preferences)

    def lookup(self, request: SearchRequest) -> Lookup:
        """The search's key and the kept result for exactly it, if one is within the limit. Never raises."""
        if not self.enabled or self.folder is None:
            return Lookup(None)
        key = self.key(request)
        if key is None:
            return Lookup(None)
        path = self.folder / f"{key.name}.json"
        try:
            if not path.exists():
                return Lookup(key)
            entry = json.loads(path.read_text(encoding="utf-8"))
            if entry.get("format") != FORMAT or entry.get("key") != asdict(key):
                raise ValueError("not this key's entry")
            made_at = datetime.fromisoformat(entry["made_at"])
            kept = SearchResult.model_validate(entry["result"])
            expired = self._now() - made_at >= self.ttl  # a time with no zone raises here: a broken entry
        except Exception as exc:  # broken, or from an older format: deleted, and the search runs
            log.warning("result cache: a broken entry was deleted (%s)", type(exc).__name__)
            _unlink(path)
            return Lookup(key)
        if expired:  # past serve_limit (D-53): deleted, and the search runs
            _unlink(path)
            return Lookup(key)
        return Lookup(key, (kept, made_at))

    def get(self, request: SearchRequest) -> tuple[SearchResult, datetime] | None:
        """The kept result for exactly this request and when its search finished, or None. Never raises."""
        return self.lookup(request).hit

    def put(self, request: SearchRequest, result: SearchResult) -> bool:
        """Keep a full result for an exact repeat of `request`. True when kept."""
        if not self.enabled or not cacheable(result):
            return False
        return self.keep(self.key(request), result)

    def keep(self, key: CacheKey | None, result: SearchResult) -> bool:
        """Keep a full result under the key its search was looked up with, without Reddit text, user id or session.
        True when kept. Never raises: a fault means it just isn't kept (rule 9)."""
        if not self.enabled or self.folder is None or key is None or not cacheable(result):
            return False
        path = self.folder / f"{key.name}.json"
        copy = path.with_name(f"{path.name}.{uuid.uuid4().hex}.writing")  # each writer its own: never a mixed file
        try:
            shown = result.model_dump(mode="json")
            # The next request's owner and session go in at a hit: neither the user's id nor their session is kept.
            shown["request"] = {**shown["request"], "user_id": None, "session_id": KEPT_SESSION}
            entry = {
                "format": FORMAT,
                "key": asdict(key),
                "made_at": result.finished_at.isoformat(),
                "result": without_reddit_text(shown),
            }
            SearchResult.model_validate(entry["result"])  # only what can be served is kept
            self.folder.mkdir(parents=True, exist_ok=True)
            copy.write_bytes(json.dumps(entry, ensure_ascii=False).encode("utf-8"))
            os.replace(copy, path)  # whole or not at all
        except Exception as exc:
            log.warning("result cache: a result was not kept (%s)", type(exc).__name__)
            _unlink(copy)  # its words (the request, the rubric) never stay behind in a half-written file
            return False
        return True

    def sweep(self) -> None:
        """Every entry past the limit, by when it was written (just after its search finished), and any half-written
        file older than one housekeeping tick (a writer stopped mid-write). Never raises."""
        delete_expired(self.folder, self.ttl, self._now)
        if self.folder is None:
            return
        oldest = (self._now() - timedelta(seconds=SWEEP_EVERY_S)).timestamp()
        try:
            leftovers = list(self.folder.glob("*.writing"))
        except OSError as exc:
            log.warning("result cache: the folder could not be read (%s)", type(exc).__name__)
            return
        for path in leftovers:
            try:
                stale = path.stat().st_mtime <= oldest
            except OSError:
                continue
            if stale:
                _unlink(path)

    def forget_user(self, user_id: str) -> int:
        """Delete every entry kept for this signed-in user (their account is deleted, US-11): how many. Never raises."""
        if self.folder is None:
            return 0
        mine = _digest(user_id)
        gone = 0
        try:
            paths = list(self.folder.glob("*.json"))
        except OSError as exc:
            log.warning("result cache: the folder could not be read (%s)", type(exc).__name__)
            return 0
        for path in paths:
            try:
                owner = json.loads(path.read_text(encoding="utf-8")).get("key", {}).get("user")
            except Exception:  # unreadable: the next read deletes it as broken, or the sweep at its limit
                continue
            if owner == mine:
                _unlink(path)
                gone += 1
        return gone


def _unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        log.warning("result cache: a file could not be deleted (%s); the sweep tries again", type(exc).__name__)


def sweep_results(folder: Path | None = None, now: Callable[[], datetime] = _utc_now) -> None:
    """The housekeeping's sweep of the result cache (app/main.py, every ten minutes)."""
    ResultCache(folder if folder is not None else RESULTS_DIR, now=now).sweep()


def default_result_cache() -> ResultCache:
    """The cache the API's searches use: on unless RESULT_CACHE=off."""
    return ResultCache(RESULTS_DIR, enabled=cache_on())  # the folder as it is now, read here


def forget_user(user_id: str) -> int:
    """Every kept result of a deleted account (app/api/account.py), whatever RESULT_CACHE says."""
    return ResultCache(RESULTS_DIR).forget_user(user_id)
