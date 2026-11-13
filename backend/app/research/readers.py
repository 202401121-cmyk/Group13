"""Readers for pages a plain fetch can't read (US-38, the page half; D-62, D-64), and Firecrawl search as a backup.

- Jina Reader (r.jina.ai) is tried first: it keeps no request data, and works without a key (JINA_API_KEY raises its
  limit). Then Firecrawl /v2/scrape, when Jina fails or its text is also thin.
- Firecrawl's free plan (D-62, read 11 Nov 2026): 1,000 credits a month, 2 requests at once, 10 a minute. A scrape is 1
  credit; a search 2 credits per 10 results. Our own caps (research.yaml) keep a search and a day well inside that.
  HTTP 401 or 402 switches Firecrawl off for the run; 429 cools it for a minute. Its pacing never waits for the minute:
  a full minute means the page is skipped, and the coverage note says so.

The caller decides what may be read: robots.txt first, and never YouTube, social sites or logged-in pages (pages.py).
Reader text goes through the one cleaning function, and the stage caps it, as for every page. Keys never reach a log or
a repr; only HTTP statuses do.
"""

from __future__ import annotations

import contextlib
import logging
import re
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from app.contracts import SourceFailure, SourceType

from .cache import ResearchCache
from .cleaning import clean_text
from .reddit.base import FailureReason
from .serper import SerperResult, read_results
from .usage import SearchUsage

log = logging.getLogger(__name__)

JINA_URL = "https://r.jina.ai/"
FIRECRAWL_SCRAPE_URL = "https://api.firecrawl.dev/v2/scrape"
FIRECRAWL_SEARCH_URL = "https://api.firecrawl.dev/v2/search"
READER_TIMEOUT_S = 12.0  # inside the page's own limit, so a stuck reader can't cost the search
FIRECRAWL_TIMEOUT_MS = 10_000  # what Firecrawl itself may take; our request waits a little longer
MAX_AGE_MS = 86_400_000  # a copy of the page up to a day old is fine, and costs less
COUNTRY = "IN"
SEARCH_RESULTS = 10
SCRAPE_CREDITS = 1
SEARCH_CREDITS = 2  # per 10 results
PER_MINUTE = 10
AT_ONCE = 2
COOL_S = 60.0
JINA, FIRECRAWL = "Jina Reader", "Firecrawl"  # the names the coverage note uses (app/pipeline.py)

_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_JINA_BODY = "Markdown Content:"


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class ReaderText:
    title: str
    text: str


def reader_failure(reader: str, reason: FailureReason, detail: str, kind: SourceType) -> SourceFailure:
    """A reader that failed or was capped, named so the coverage note can say which (criterion 5)."""
    return SourceFailure(source_type=kind, reason=reason, detail=f"{reader}: {detail}"[:300])


def plain_text(markdown: str) -> str:
    """A reader's markdown as plain text: images dropped, links kept as their words, then the one cleaning function."""
    return clean_text(_LINK.sub(r"\1", _IMAGE.sub("", markdown)))


def _reason(status: int) -> FailureReason:
    if status in (401, 403):
        return "blocked"
    if status == 402:
        return "quota_exhausted"
    if status == 429:
        return "rate_limited"
    if status in (404, 410):
        return "not_found"
    return "error"


class JinaReader:
    """GET r.jina.ai/<url>. Counted against the "jina" cap: 10 pages a search (research.yaml)."""

    name = JINA

    def __init__(self, http: httpx.Client, api_key: str | None) -> None:
        self.http = http
        self._api_key = api_key

    def __repr__(self) -> str:
        return f"JinaReader(key_set={self._api_key is not None})"

    def read(self, url: str, usage: SearchUsage) -> ReaderText | tuple[FailureReason, str]:
        if not usage.take("jina"):
            return "quota_exhausted", "a cap is reached"
        headers = {"Accept": "text/plain", "X-Return-Format": "markdown"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        try:
            response = self.http.get(f"{JINA_URL}{url}", headers=headers, timeout=READER_TIMEOUT_S)
        except httpx.TimeoutException:
            return "timeout", f"no reply within {READER_TIMEOUT_S:g}s"
        except httpx.HTTPError as exc:
            return "error", f"request failed ({type(exc).__name__})"
        if response.status_code != 200:
            return _reason(response.status_code), f"HTTP {response.status_code}"
        title, _, body = response.text.partition(_JINA_BODY)
        found = re.search(r"^Title:\s*(.+)$", title, re.MULTILINE)
        return ReaderText(found.group(1).strip() if found else "", plain_text(body if body else response.text))


class FirecrawlGate:
    """Firecrawl's limits for the whole process: 2 at once, 10 a minute, and its health for the run.

    A third request waits briefly for a slot. A full minute is not waited for: the page is skipped. 401 or 402 switches
    Firecrawl off until the process restarts; 429 cools it for COOL_S.
    """

    def __init__(
        self, clock: Callable[[], float] = time.monotonic, *, per_minute: int = PER_MINUTE, wait_s: float = 5.0
    ) -> None:
        self._clock = clock
        self._per_minute = per_minute
        self._wait_s = wait_s
        self._slots = threading.BoundedSemaphore(AT_ONCE)
        self._lock = threading.Lock()
        self._started: deque[float] = deque()
        self._dead: FailureReason | None = None
        self._cool_until = 0.0

    def try_start(self) -> FailureReason | None:
        """None means go (a slot is taken, so finish() must follow). Otherwise why not.

        The minute's place is held before waiting for a slot, so requests waiting together can't pass 10 a minute, and
        Firecrawl's health is read again after the wait: another request may have just switched it off or cooled it.
        """
        with self._lock:
            problem = self._problem()
            if problem is not None:
                return problem
            now = self._clock()
            while self._started and self._started[0] <= now - 60.0:
                self._started.popleft()
            if len(self._started) >= self._per_minute:
                return "rate_limited"
            self._started.append(now)
        if not self._slots.acquire(timeout=self._wait_s):
            self._give_back(now)
            return "rate_limited"
        with self._lock:
            problem = self._problem()
        if problem is not None:
            self._slots.release()
            self._give_back(now)
            return problem
        return None

    def _problem(self) -> FailureReason | None:
        """Dead for the run, or cooling. The caller holds the lock."""
        if self._dead is not None:
            return self._dead
        return "rate_limited" if self._clock() < self._cool_until else None

    def _give_back(self, started: float) -> None:
        """A held place in the minute whose request never went out."""
        with self._lock, contextlib.suppress(ValueError):
            self._started.remove(started)

    def finish(self, status: int | None) -> None:
        """The request is over: give its slot back, and note what its status says about Firecrawl."""
        with self._lock:
            if status in (401, 402) and self._dead is None:
                self._dead = "blocked" if status == 401 else "quota_exhausted"
                log.warning("research: Firecrawl switched off for this run (HTTP %d)", status)
            elif status == 429:
                self._cool_until = max(self._cool_until, self._clock() + COOL_S)
        self._slots.release()


_SHARED_GATE = FirecrawlGate()


class FirecrawlClient:
    """/v2/scrape for a page, /v2/search as Serper's backup. Credits count against the "firecrawl" cap."""

    name = FIRECRAWL

    def __init__(
        self,
        http: httpx.Client,
        api_key: str | None,
        *,
        cache: ResearchCache,
        gate: FirecrawlGate | None = None,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.http = http
        self._api_key = api_key
        self.cache = cache
        self.gate = gate if gate is not None else _SHARED_GATE
        self._now = now

    def __repr__(self) -> str:
        return f"FirecrawlClient(key_set={self._api_key is not None})"

    def scrape(
        self,
        url: str,
        usage: SearchUsage,
        *,
        keep_in_their_cache: bool = True,
        before_cleaning: Callable[[str], str] | None = None,
    ) -> ReaderText | tuple[FailureReason, str]:
        """One page as markdown, made plain. Reddit text passes keep_in_their_cache=False (D-53), and a filter that
        runs on the markdown itself, while a profile link still shows whose name its words are."""
        if not self._api_key:
            return "blocked", "FIRECRAWL_API_KEY is not set"
        if usage.left("firecrawl_pages") < 1 or usage.left("firecrawl") < SCRAPE_CREDITS:
            return "quota_exhausted", "a cap is reached"
        body: dict[str, Any] = {
            "url": url,
            "formats": ["markdown"],
            "onlyMainContent": True,
            "maxAge": MAX_AGE_MS,
            "location": {"country": COUNTRY},
            "timeout": FIRECRAWL_TIMEOUT_MS,
        }
        if not keep_in_their_cache:
            body["storeInCache"] = False
        reply = self._post(FIRECRAWL_SCRAPE_URL, body, usage, SCRAPE_CREDITS, page=True)
        if isinstance(reply, tuple):
            return reply
        data = reply.get("data")
        markdown = data.get("markdown") if isinstance(data, dict) else None
        if not isinstance(markdown, str):
            return "error", "the reply had no page text"
        meta = data.get("metadata") if isinstance(data, dict) else None
        title = meta.get("title") if isinstance(meta, dict) else ""
        if isinstance(title, list):
            title = next((t for t in title if isinstance(t, str)), "")
        if before_cleaning is not None:
            markdown = before_cleaning(markdown)
        return ReaderText(title if isinstance(title, str) else "", plain_text(markdown))

    def search(self, query: str, region: str, usage: SearchUsage, sites: Sequence[str] = ()) -> SerperResult:
        """The same query Serper would get, as web_search snippets (via search_snippet). Kept 24 hours."""
        text = f"{query} ({' OR '.join(f'site:{site}' for site in sites)})" if sites else query
        country = region.upper() if len(region) == 2 else COUNTRY
        key = ("firecrawl_search", text, country)
        cached = self.cache.get_sources(key)
        if cached is not None:
            links = self.cache.get_text((*key, "reddit_links")) or ""
            titles = self.cache.get_text((*key, "reddit_titles")) or ""
            return SerperResult(
                cached,
                tuple(line for line in titles.split("\n") if line),
                reddit_links=tuple(line for line in links.split("\n") if line),
                from_cache=True,
            )
        if not self._api_key:
            return _search_failure("blocked", "FIRECRAWL_API_KEY is not set")
        if usage.left("firecrawl") < SEARCH_CREDITS:
            return _search_failure("quota_exhausted", "a cap is reached")
        reply = self._post(
            FIRECRAWL_SEARCH_URL, {"query": text, "limit": SEARCH_RESULTS, "country": country}, usage, SEARCH_CREDITS
        )
        if isinstance(reply, tuple):
            return _search_failure(*reply)
        data = reply.get("data")
        web = data.get("web") if isinstance(data, dict) else None
        if not isinstance(web, list):
            return _search_failure("error", "the reply had no results list")
        items = [
            {"link": item.get("url"), "title": item.get("title"), "snippet": item.get("description")}
            for item in web
            if isinstance(item, dict)
        ]
        found = read_results(items, self._now)
        self.cache.put_sources(key, found.sources)
        self.cache.put_text((*key, "reddit_links"), "\n".join(found.reddit_links))
        self.cache.put_text((*key, "reddit_titles"), "\n".join(found.reddit_titles))
        return found

    def _post(
        self, url: str, body: dict[str, Any], usage: SearchUsage, credits: int, *, page: bool = False
    ) -> dict[str, Any] | tuple[FailureReason, str]:
        problem = self.gate.try_start()
        if problem is not None:
            return problem, "switched off for this run" if problem in ("blocked", "quota_exhausted") else "paced"
        status: int | None = None
        try:
            if page and not usage.take("firecrawl_pages"):
                return "quota_exhausted", "a cap is reached"
            if not all(usage.take("firecrawl") for _ in range(credits)):
                return "quota_exhausted", "a cap is reached"
            try:
                response = self.http.post(
                    url,
                    json=body,
                    headers={"Authorization": f"Bearer {self._api_key or ''}"},
                    timeout=READER_TIMEOUT_S,
                )
            except httpx.TimeoutException:
                return "timeout", f"no reply within {READER_TIMEOUT_S:g}s"
            except httpx.HTTPError as exc:
                return "error", f"request failed ({type(exc).__name__})"
            status = response.status_code
            if status != 200:
                return _reason(status), f"HTTP {status}"
            try:
                reply = response.json()
            except ValueError:
                return "error", "the reply was not JSON"
            if not isinstance(reply, dict) or reply.get("success") is False:
                return "error", "the reply said it failed"
            return reply
        finally:
            self.gate.finish(status)


def _search_failure(reason: FailureReason, detail: str) -> SerperResult:
    return SerperResult(failure=reader_failure(FIRECRAWL, reason, f"search {detail}", SourceType.WEB_SEARCH))


class SearchWithBackup:
    """Serper first. When it errors, is capped for the day or has no credits, the same query goes to Firecrawl search.

    Our own six-queries-a-search cap is not Serper failing, so it never sends a query to the backup. A missing or
    refused Serper key is a set-up problem the team must see, so it doesn't either.
    """

    def __init__(self, primary: Any, backup: FirecrawlClient) -> None:
        self.primary = primary
        self.backup = backup

    def __repr__(self) -> str:
        return f"SearchWithBackup({self.primary!r}, {self.backup!r})"

    def search(self, query: str, region: str, usage: SearchUsage, sites: Sequence[str] = ()) -> SerperResult:
        found: SerperResult = self.primary.search(query, region, usage, sites)
        failure = found.failure
        if failure is None or failure.reason == "blocked":
            return found
        if failure.reason == "quota_exhausted" and usage.used.get("serper", 0) >= usage.per_search("serper"):
            return found  # this search's own share of queries is used up: not a reason to spend credits
        backup = self.backup.search(query, region, usage, sites)
        if backup.failure is None:
            return backup
        return SerperResult(  # both failed: Serper's failure for web search, and Firecrawl named too (criterion 5)
            found.sources,
            found.reddit_titles,
            failure,
            reddit_links=found.reddit_links,
            also_failed=(backup.failure,),
        )
