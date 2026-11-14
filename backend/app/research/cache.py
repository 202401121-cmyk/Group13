"""Cache of search results, pages and robots files (US-12, P-12), in memory and in backend/.cache/research/.

D-53 keeps Reddit text at most 24 hours. So every folder that can hold text from a Reddit page is served and kept only
until serve_limit() (24 hours less the longest search, the time a result is kept, one housekeeping tick and a margin:
app/research/reddit/cache.py), and a folder that can't keeps the full 24 hours:

- searches/      serve_limit()  A web search's results (Serper, Firecrawl search). They hold no Reddit text themselves:
                                a result on reddit.com never becomes a source. But the search is cached as one answer
                                with the titles and links below, so they expire together, or a cached search would come
                                back without its Reddit links.
- search_text/   serve_limit()  The titles and links of a search's results on reddit.com (Reddit text: a thread's link
                                holds its title's words too). They only help find product names and threads to read.
- reddit_pages/  serve_limit()  Reddit threads read through Firecrawl (US-38).
- sources/       24 hours       Review pages read in full. pages.py never fetches reddit.com (NEVER_FETCH).
- text/          24 hours       robots.txt files.

Each entry goes to its folder by its key: a page is ("page", url) and a robots file ("robots", host); any other key is
taken as part of a search, so a new kind of entry gets the shorter limit until it is placed here. Sources go through
RedditCache's code, which does the same job. Only complete answers are cached, never a failure, and a failed write
never stops a search (build rule 10). What is cached is what we chose to keep: a search result pointing at Reddit keeps
its title and link only, and its snippet never gets this far (D-53).
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
from .reddit.cache import CacheKey, RedditCache, delete_expired, serve_limit

log = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = Path(__file__).resolve().parents[2] / ".cache" / "research"
DEFAULT_TTL = timedelta(hours=24)
REDDIT_PAGES = "reddit_pages"  # Reddit threads read through Firecrawl: Reddit's own limit, serve_limit()
SEARCHES = "searches"  # a search's results, kept with its Reddit titles and links: serve_limit()
SEARCH_TEXT = "search_text"  # the titles and links of a search's results on reddit.com: serve_limit()
PAGES = "sources"  # review pages read in full: 24 hours
ROBOTS = "text"  # robots.txt files: 24 hours


def _is_page(key: CacheKey) -> bool:
    return len(key) > 0 and key[0] == "page"


def _is_robots(key: CacheKey) -> bool:
    return len(key) > 0 and key[0] == "robots"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def sweep_expired_files(
    folder: Path | None = None,
    reddit_folder: Path | None = None,
    now: Callable[[], datetime] = _utc_now,
) -> None:
    """Everything past its limit. It needs no client or source, so D-53 holds whatever else is broken.

    It runs at app start-up and then every ten minutes (the housekeeping in app/main.py), and at each search (the
    research stage), so expired Reddit text goes even on a server nobody searches with. The folders are read when it
    runs (not when this module loads), so the defaults are always the current ones.
    """
    folder = folder if folder is not None else DEFAULT_CACHE_DIR
    reddit_folder = reddit_folder if reddit_folder is not None else REDDIT_CACHE_DIR
    delete_expired(reddit_folder, serve_limit(), now)
    for name in (REDDIT_PAGES, SEARCHES, SEARCH_TEXT):
        delete_expired(folder / name, serve_limit(), now)
    for name in (PAGES, ROBOTS):
        delete_expired(folder / name, DEFAULT_TTL, now)


class ResearchCache:
    def __init__(
        self,
        folder: Path | None = DEFAULT_CACHE_DIR,
        ttl: timedelta = DEFAULT_TTL,
        now: Callable[[], datetime] = _utc_now,
        enabled: bool = True,
        reddit_ttl: timedelta | None = None,
    ) -> None:
        self.folder = folder
        self.ttl = ttl
        self.reddit_ttl = reddit_ttl if reddit_ttl is not None else serve_limit()
        self.enabled = enabled
        self._now = now
        self._pages = RedditCache(folder=folder / PAGES if folder else None, ttl=ttl, now=now)
        self._searches = RedditCache(folder=folder / SEARCHES if folder else None, ttl=self.reddit_ttl, now=now)
        reddit_pages = folder / REDDIT_PAGES if folder else None
        self._reddit_pages = RedditCache(folder=reddit_pages, ttl=self.reddit_ttl, now=now)
        self._lock = threading.Lock()
        self._text: dict[str, tuple[datetime, str]] = {}
        if folder is not None:  # switched off or not: Reddit text lives here, and goes in time (D-53)
            for name in (PAGES, ROBOTS):
                delete_expired(folder / name, ttl, now)
            for name in (REDDIT_PAGES, SEARCHES, SEARCH_TEXT):
                delete_expired(folder / name, self.reddit_ttl, now)

    def _sources_for(self, key: CacheKey) -> RedditCache:
        return self._pages if _is_page(key) else self._searches

    def _text_place(self, key: CacheKey) -> tuple[str, timedelta]:
        """A robots file keeps 24 hours; any other text is a search's Reddit titles or links, so Reddit's limit."""
        return (ROBOTS, self.ttl) if _is_robots(key) else (SEARCH_TEXT, self.reddit_ttl)

    def get_sources(self, key: CacheKey) -> tuple[Source, ...] | None:
        return self._sources_for(key).get(key) if self.enabled else None

    def put_sources(self, key: CacheKey, sources: Sequence[Source]) -> None:
        if self.enabled:
            self._sources_for(key).put(key, sources)

    def get_reddit_pages(self, key: CacheKey) -> tuple[Source, ...] | None:
        """A Reddit thread read through Firecrawl: served only before serve_limit(), as every Reddit read (D-53)."""
        return self._reddit_pages.get(key) if self.enabled else None

    def put_reddit_pages(self, key: CacheKey, sources: Sequence[Source]) -> None:
        if self.enabled:
            self._reddit_pages.put(key, sources)

    def get_text(self, key: CacheKey) -> str | None:
        if not self.enabled:
            return None
        name = _name(key)
        place, ttl = self._text_place(key)
        with self._lock:
            hit = self._text.get(name)
        if hit is None:
            hit = self._read_text(place, name)
            if hit is not None:
                with self._lock:
                    self._text[name] = hit
        if hit is None or self._now() - hit[0] >= ttl:
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
            folder = self.folder / self._text_place(key)[0]
            folder.mkdir(parents=True, exist_ok=True)
            body = {"saved_at": saved[0].isoformat(), "text": text}
            (folder / f"{name}.json").write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        except OSError as exc:
            log.warning("research cache: could not save (%s); carrying on", type(exc).__name__)

    def _read_text(self, place: str, name: str) -> tuple[datetime, str] | None:
        if self.folder is None:
            return None
        path = self.folder / place / f"{name}.json"
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
