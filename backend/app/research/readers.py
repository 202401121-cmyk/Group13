"""Readers for pages a plain fetch can't read (US-38, the page half; D-62, D-64), and Firecrawl search as a backup.

- Jina Reader (r.jina.ai) is tried first: it keeps no request data, and works without a key (JINA_API_KEY raises its
  limit). Then Firecrawl /v2/scrape, when Jina fails or its text is also thin, and then, the last step (D-100 item 1),
  Firecrawl /v2/scrape with the page rendered: a wait for its scripts and scrolling down, never a click.
- D-100 (Firecrawl's docs read 27 Nov 2026, docs.firecrawl.dev: billing, rate-limits, features/map, advanced scraping
  guide, scrape and batch-scrape references): a scrape is 1 credit with or without actions and waitFor; /v2/map is 1
  credit a call; a search 2 credits per 10 results (rounded up) and 1 for each page it scrapes; a batch scrape 1 a page.
  Every request asks for the basic proxy: never Firecrawl's enhanced (anti-bot) proxy, which its default "auto" can
  step up to. maxAge is our 24-hour page cache (Firecrawl caps it at 7 days); a rendered scrape is never kept in
  Firecrawl's cache (actions turn storeInCache off).
- map (D-100 items 2 and 9) finds a page's address on one site by the product's name, for 1 credit. Batch scrape is
  here for a stage's several pages; under our plan's limits single calls never lose to it (docs/api-setup.md).
- Firecrawl's limits (docs.firecrawl.dev/rate-limits, read 25 Nov 2026): the free plan allows 10 requests a minute
  and 2 at once. They live in research.yaml (firecrawl_limits), not here (D-99). A scrape is 1 credit; a search 2
  credits per 10 results. Our own caps (research.yaml) keep a search, a day and the plan's credits inside that.
  HTTP 401 or 402 switches Firecrawl off for the run; 429 cools it. Its pacing never waits for the minute: a full minute
  means the page is skipped, and the coverage note says so.
- Commerce (US-24, D-99) asks either reader for a page's snapshot: its own HTTP status, its address after redirects,
  its title and its HTML as served, so code can check the page is a product page before a link to it is shown.

The caller decides what may be read: robots.txt first, and never YouTube, social sites or logged-in pages (pages.py).
Reader text goes through the one cleaning function, and the stage caps it, as for every page. Keys never reach a log or
a repr; only HTTP statuses do.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from typing import Any

import httpx

from app.contracts import SourceFailure, SourceType
from app.llm.retry import RetryClock

from .cache import ResearchCache
from .cleaning import clean_text
from .reddit.base import FailureReason
from .retrying import send_once_more
from .serper import SerperResult, read_results
from .settings import FirecrawlLimits, load_research_settings
from .usage import SearchUsage

log = logging.getLogger(__name__)

JINA_URL = "https://r.jina.ai/"
FIRECRAWL_SCRAPE_URL = "https://api.firecrawl.dev/v2/scrape"
FIRECRAWL_SEARCH_URL = "https://api.firecrawl.dev/v2/search"
FIRECRAWL_MAP_URL = "https://api.firecrawl.dev/v2/map"
FIRECRAWL_BATCH_URL = "https://api.firecrawl.dev/v2/batch/scrape"
READER_TIMEOUT_S = 12.0  # inside the page's own limit, so a stuck reader can't cost the search
FIRECRAWL_TIMEOUT_MS = 10_000  # what Firecrawl itself may take; our request waits a little longer
MAX_AGE_MS = 86_400_000  # a copy of the page up to a day old is fine, and costs less
COUNTRY = "IN"
SEARCH_RESULTS = 10
SCRAPE_CREDITS = 1
SEARCH_CREDITS = 2  # per 10 results
SNAPSHOT_MAX_CHARS = 1_500_000  # a page's HTML kept for commerce's checks, as research.yaml's max_page_bytes
SEARCH_PAGES = 3  # D-101: the results a search has Firecrawl read in the same call (2 credits, and 1 a page)
FIRECRAWL_MIN_S = 1.0  # Firecrawl's least timeout: a request due sooner isn't sent
SEARCH_STEP_S = 2.0  # of a reading search's time, kept for Firecrawl's search itself and its reply, beyond the reads
MAP_CREDITS = 1  # a map call, however many links come back (docs.firecrawl.dev/billing, read 27 Nov 2026)
MAP_LIMIT = 20  # links a map gives back: the few that best match the product's name are all we read
PROXY = "basic"  # D-100 item 7: never the enhanced (anti-bot) proxy that Firecrawl's default "auto" may step up to
RENDER_WAIT_MS = 2_000  # the rendered step's wait for the page's scripts, before it scrolls
RENDER_ACTIONS: tuple[dict[str, Any], ...] = (
    {"type": "scroll", "direction": "down"},
    {"type": "wait", "milliseconds": 1_000},
    {"type": "scroll", "direction": "down"},
    {"type": "wait", "milliseconds": 1_000},
)  # scrolling only: a click on a section that isn't on the page fails the whole scrape (D-100 item 1)
RENDER_MIN_S = 6.0  # with less than this left, the rendered step isn't sent: its waits alone take 4 seconds
RENDER_MAX_S = 15.0  # the most a rendered scrape may take, deadline or not: waits, scrolling and the read
BATCH_POLL_S = 1.0  # how often a batch scrape's progress is asked for
JINA, FIRECRAWL = "Jina Reader", "Firecrawl"  # the names the coverage note uses (app/pipeline.py)

_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_JINA_BODY = "Markdown Content:"
_JINA_STATUS = re.compile(r"returned error (\d{3})")  # its warning when the page itself answered with an error


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class ReaderText:
    title: str
    text: str


@dataclass(frozen=True)
class PageSnapshot:
    """A page as a reader opened it, for commerce's checks (D-99), never shown to anyone: the page's own HTTP status
    (None when the reader didn't say), its address after redirects ("" when the reader didn't say), its title and its
    HTML as served, scripts kept, up to SNAPSHOT_MAX_CHARS."""

    status: int | None
    final_url: str
    title: str
    html: str


@dataclass(frozen=True)
class SearchedPage:
    """One search result Firecrawl read in the search's own call (D-101): the address it landed on, as plain text."""

    url: str
    title: str
    text: str


@dataclass(frozen=True)
class MappedLink:
    """One address a map found on a site, with its title when Firecrawl gave one."""

    url: str
    title: str = ""


@dataclass(frozen=True)
class SearchPages:
    """A search with its results read: the pages that came back with text, in the search's order, or why none did.
    `unread`: results that came back without text (a read that failed or timed out, a status other than 200), which a
    later search may well read."""

    pages: tuple[SearchedPage, ...] = ()
    failure: SourceFailure | None = None
    unread: int = 0


def search_credits(results: int, *, read: bool) -> int:
    """What a Firecrawl search costs: 2 credits for each 10 results, and 1 for each result it reads (D-101)."""
    return SEARCH_CREDITS * -(-results // SEARCH_RESULTS) + (results * SCRAPE_CREDITS if read else 0)


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

    def __init__(self, http: httpx.Client, api_key: str | None, *, retry: RetryClock | None = None) -> None:
        self.http = http
        self._api_key = api_key
        self.retry = retry if retry is not None else RetryClock()  # the one retry (US-31)

    def __repr__(self) -> str:
        return f"JinaReader(key_set={self._api_key is not None})"

    def read(self, url: str, usage: SearchUsage) -> ReaderText | tuple[FailureReason, str]:
        response = self._get(url, usage, {"Accept": "text/plain", "X-Return-Format": "markdown"})
        if isinstance(response, tuple):
            return response
        title, _, body = response.text.partition(_JINA_BODY)
        found = re.search(r"^Title:\s*(.+)$", title, re.MULTILINE)
        return ReaderText(found.group(1).strip() if found else "", plain_text(body if body else response.text))

    def snapshot(self, url: str, usage: SearchUsage) -> PageSnapshot | tuple[FailureReason, str]:
        """The page as JSON with its HTML (x-respond-with: html; the HTML is under data.html, or data.content): its
        address after loading, its title, and a status read from Jina's warning when the page answered with an error.
        No warning is taken as the page's 200."""
        response = self._get(url, usage, {"Accept": "application/json", "X-Respond-With": "html"})
        if isinstance(response, tuple):
            return response
        try:
            reply = response.json()
        except ValueError:
            return "error", "the reply was not JSON"
        data = reply.get("data") if isinstance(reply, dict) else None
        html = (
            next((data[k] for k in ("html", "content") if isinstance(data.get(k), str)), None)
            if isinstance(data, dict)
            else None
        )
        if not isinstance(data, dict) or html is None:
            return "error", "the reply had no page"
        final, title, warning = data.get("url"), data.get("title"), data.get("warning")
        said = _JINA_STATUS.search(warning) if isinstance(warning, str) else None
        status = int(said.group(1)) if said else (None if isinstance(warning, str) else 200)
        return PageSnapshot(
            status,
            final if isinstance(final, str) else "",
            title if isinstance(title, str) else "",
            html[:SNAPSHOT_MAX_CHARS],
        )

    def _get(self, url: str, usage: SearchUsage, headers: dict[str, str]) -> httpx.Response | tuple[FailureReason, str]:
        """One read of the page through Jina, with its one retry: the reply, or why there is none."""
        if not usage.take("jina"):
            return "quota_exhausted", "a cap is reached"
        if self._api_key:
            headers = {**headers, "Authorization": f"Bearer {self._api_key}"}
        response = send_once_more(
            lambda: self.http.get(f"{JINA_URL}{url}", headers=headers, timeout=READER_TIMEOUT_S),
            self.retry,
            READER_TIMEOUT_S,
            lambda: usage.take("jina"),  # a retry is a page read like any other
            "Jina",
        )
        if isinstance(response, httpx.TimeoutException):
            return "timeout", f"no reply within {READER_TIMEOUT_S:g}s"
        if isinstance(response, httpx.HTTPError):
            return "error", f"request failed ({type(response).__name__})"
        if response.status_code != 200:
            return _reason(response.status_code), f"HTTP {response.status_code}"
        return response


@cache
def firecrawl_limits() -> FirecrawlLimits:
    """Firecrawl's limits from research.yaml, read once a process (no .env needed: they are not secrets)."""
    return load_research_settings(env={}).firecrawl_limits


class FirecrawlGate:
    """Firecrawl's limits for the whole process: so many at once and a minute (research.yaml), and its health.

    A request past the ones at once waits briefly for a slot. A full minute is not waited for: the page is skipped. 401
    or 402 switches Firecrawl off until the process restarts; 429 cools it for the configured seconds.
    """

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        *,
        limits: FirecrawlLimits | None = None,
        per_minute: int | None = None,
        wait_s: float = 5.0,
    ) -> None:
        known = limits if limits is not None else firecrawl_limits()
        self._clock = clock
        self._per_minute = per_minute if per_minute is not None else known.per_minute
        self._cool_s = known.cool_s
        self._wait_s = wait_s
        self._slots = threading.BoundedSemaphore(known.at_once)
        self._lock = threading.Lock()
        self._mine = threading.local()  # this thread's place in the minute, while its request is held
        self._started: deque[float] = deque()
        self._dead: FailureReason | None = None
        self._cool_until = 0.0

    def try_start(self, wait_s: float | None = None) -> FailureReason | None:
        """None means go (a slot is taken, so finish() must follow). Otherwise why not. `wait_s`: the longest a slot is
        waited for, when less than the gate's own (D-101: a search whose answer is due inside a window).

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
            self._mine.place = now
        if not self._slots.acquire(timeout=self._wait_s if wait_s is None else min(self._wait_s, wait_s)):
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

    def book_retry(self) -> bool:
        """A retry inside a running request's slot (US-31): it needs its own place in the minute, and Firecrawl well."""
        with self._lock:
            if self._problem() is not None:
                return False
            now = self._clock()
            while self._started and self._started[0] <= now - 60.0:
                self._started.popleft()
            if len(self._started) >= self._per_minute:
                return False
            self._started.append(now)
            return True

    def _give_back(self, started: float) -> None:
        """A held place in the minute whose request never went out."""
        with self._lock, contextlib.suppress(ValueError):
            self._started.remove(started)

    def unsent(self) -> None:
        """This thread's request never went (no time left, or a cap refused it): its slot and its place in the minute
        go back, so 10 a minute counts only requests sent."""
        self._slots.release()
        self._give_back(getattr(self._mine, "place", -1.0))

    def finish(self, status: int | None) -> None:
        """The request is over: give its slot back, and note what its status says about Firecrawl."""
        with self._lock:
            if status in (401, 402) and self._dead is None:
                self._dead = "blocked" if status == 401 else "quota_exhausted"
                log.warning("research: Firecrawl switched off for this run (HTTP %d)", status)
            elif status == 429:
                self._cool_until = max(self._cool_until, self._clock() + self._cool_s)
        self._slots.release()


@cache
def shared_gate() -> FirecrawlGate:
    """The one gate every Firecrawl client in the process shares, made when first needed."""
    return FirecrawlGate()


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
        retry: RetryClock | None = None,
    ) -> None:
        self.http = http
        self._api_key = api_key
        self.cache = cache
        self.gate = gate if gate is not None else shared_gate()
        self._now = now
        self.retry = retry if retry is not None else RetryClock()  # the one retry (US-31)

    def __repr__(self) -> str:
        return f"FirecrawlClient(key_set={self._api_key is not None})"

    def scrape(
        self,
        url: str,
        usage: SearchUsage,
        *,
        keep_in_their_cache: bool = True,
        before_cleaning: Callable[[str], str] | None = None,
        rendered: bool = False,
        until: float | None = None,
    ) -> ReaderText | tuple[FailureReason, str]:
        """One page as markdown, made plain. Reddit text passes keep_in_their_cache=False (D-53), and a filter that
        runs on the markdown itself, while a profile link still shows whose name its words are. `rendered` (D-100 item
        1): the page's scripts are waited for and it is scrolled, never clicked; with less than RENDER_MIN_S before
        `until` it isn't sent."""
        if not self._api_key:
            return "blocked", "FIRECRAWL_API_KEY is not set"
        if usage.left("firecrawl_pages") < 1 or usage.left("firecrawl") < SCRAPE_CREDITS:
            return "quota_exhausted", "a cap is reached"
        if rendered and until is not None and until - time.monotonic() < RENDER_MIN_S:
            return "timeout", "no time left to render the page"
        body: dict[str, Any] = {
            "url": url,
            "formats": ["markdown"],
            "onlyMainContent": True,
            "maxAge": MAX_AGE_MS,
            "location": {"country": COUNTRY},
            "timeout": FIRECRAWL_TIMEOUT_MS,
            "proxy": PROXY,
        }
        if not keep_in_their_cache:
            body["storeInCache"] = False
        least = FIRECRAWL_MIN_S
        retry = True
        if rendered:
            body["waitFor"] = RENDER_WAIT_MS
            body["actions"] = [dict(action) for action in RENDER_ACTIONS]
            body["maxAge"] = 0  # the plain scrape just read Firecrawl's copy: the rendered step asks for a fresh one
            latest = time.monotonic() + RENDER_MAX_S
            until = latest if until is None else min(until, latest)
            least, retry = RENDER_MIN_S, False  # one try: a second would cost another page for the same page
        reply = self._post(
            FIRECRAWL_SCRAPE_URL, body, usage, SCRAPE_CREDITS, page=1, retry=retry, until=until, least_s=least
        )
        if isinstance(reply, tuple):
            return reply
        return _page_text(reply.get("data"), before_cleaning)

    def map(
        self, site: str, search: str, usage: SearchUsage, *, until: float | None = None
    ) -> list[MappedLink] | tuple[FailureReason, str]:
        """The addresses on one site (`site`: its own https address) that best match `search`, the product's name
        only, for MAP_CREDITS (D-100 items 2 and 9). Kept 24 hours, so a second ask that day costs nothing."""
        if not self._api_key:
            return "blocked", "FIRECRAWL_API_KEY is not set"
        key = ("firecrawl_map", site, search)
        kept = self.cache.get_text(key)
        if kept is not None:
            return _links(kept)
        if usage.left("firecrawl") < MAP_CREDITS:
            return "quota_exhausted", "a cap is reached"
        body: dict[str, Any] = {
            "url": site,
            "search": search,
            "limit": MAP_LIMIT,
            "includeSubdomains": False,
            "location": {"country": COUNTRY},
        }
        reply = self._post(FIRECRAWL_MAP_URL, body, usage, MAP_CREDITS, until=until)
        if isinstance(reply, tuple):
            return reply
        found = reply.get("links")
        if not isinstance(found, list):
            return "error", "the reply had no links"
        links = [link for item in found if (link := _mapped(item)) is not None]
        self.cache.put_text(key, json.dumps([[link.url, link.title] for link in links]))
        return links

    def batch_scrape(
        self, urls: Sequence[str], usage: SearchUsage, *, until: float
    ) -> dict[str, ReaderText | tuple[FailureReason, str]]:
        """Several pages in one request (D-100 item 4): every page and credit counted before it goes, all or none, its
        progress asked for until `until`, then what was read. A page not read by then is cancelled with the job, so it
        costs nothing more; one Firecrawl couldn't read says why. Never retried."""
        wanted = list(dict.fromkeys(urls))
        if not self._api_key:
            return dict.fromkeys(wanted, ("blocked", "FIRECRAWL_API_KEY is not set"))
        body: dict[str, Any] = {
            "urls": wanted,
            "formats": ["markdown"],
            "onlyMainContent": True,
            "maxAge": MAX_AGE_MS,
            "location": {"country": COUNTRY},
            "proxy": PROXY,
            "maxConcurrency": firecrawl_limits().at_once,
        }
        reply = self._post(FIRECRAWL_BATCH_URL, body, usage, len(wanted), page=len(wanted), retry=False, until=until)
        if isinstance(reply, tuple):
            return dict.fromkeys(wanted, reply)
        job = reply.get("id")
        if not isinstance(job, str) or not job:
            return dict.fromkeys(wanted, ("error", "the reply had no job"))
        read: dict[str, ReaderText | tuple[FailureReason, str]] = {}
        while True:
            status = self._job(job, until)
            for item in status.get("data", []) if isinstance(status.get("data"), list) else []:
                url, page = _batched(item)
                if url in wanted and url not in read and page is not None:
                    read[url] = page
            if status.get("status") in ("completed", "failed") or len(read) == len(wanted):
                break
            if time.monotonic() + BATCH_POLL_S >= until:
                self._cancel(job)
                break
            time.sleep(BATCH_POLL_S)
        return {url: read.get(url, ("timeout", "not read in time")) for url in wanted}

    def _job(self, job: str, until: float) -> dict[str, Any]:
        """A batch's progress, or nothing when it can't be read (the pages it has are then not waited for)."""
        try:
            response = self.http.get(
                f"{FIRECRAWL_BATCH_URL}/{job}",
                headers={"Authorization": f"Bearer {self._api_key or ''}"},
                timeout=max(FIRECRAWL_MIN_S, until - time.monotonic()),
            )
            reply = response.json() if response.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            reply = None
        return reply if isinstance(reply, dict) else {"status": "failed"}

    def _cancel(self, job: str) -> None:
        """A batch past its time: cancelled, so the pages it hasn't read cost nothing."""
        with contextlib.suppress(httpx.HTTPError):
            self.http.delete(
                f"{FIRECRAWL_BATCH_URL}/{job}",
                headers={"Authorization": f"Bearer {self._api_key or ''}"},
                timeout=FIRECRAWL_MIN_S,
            )

    def snapshot(self, url: str, usage: SearchUsage) -> PageSnapshot | tuple[FailureReason, str]:
        """The page's own status, its address after redirects, its title and its raw HTML (rawHtml keeps the page's
        scripts, so its schema.org data). One credit and one page, as any scrape."""
        if not self._api_key:
            return "blocked", "FIRECRAWL_API_KEY is not set"
        if usage.left("firecrawl_pages") < 1 or usage.left("firecrawl") < SCRAPE_CREDITS:
            return "quota_exhausted", "a cap is reached"
        body: dict[str, Any] = {
            "url": url,
            "formats": ["rawHtml"],
            "maxAge": MAX_AGE_MS,
            "location": {"country": COUNTRY},
            "timeout": FIRECRAWL_TIMEOUT_MS,
            "proxy": PROXY,
        }
        reply = self._post(FIRECRAWL_SCRAPE_URL, body, usage, SCRAPE_CREDITS, page=1)
        if isinstance(reply, tuple):
            return reply
        data = reply.get("data")
        html = data.get("rawHtml") if isinstance(data, dict) else None
        meta = data.get("metadata") if isinstance(data, dict) else None
        if not isinstance(html, str) or not isinstance(meta, dict):
            return "error", "the reply had no page"
        status, final, title = meta.get("statusCode"), meta.get("url"), meta.get("title")
        if isinstance(title, list):
            title = next((t for t in title if isinstance(t, str)), "")
        return PageSnapshot(
            status if isinstance(status, int) and not isinstance(status, bool) else None,
            final if isinstance(final, str) else "",
            title if isinstance(title, str) else "",
            html[:SNAPSHOT_MAX_CHARS],
        )

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

    def search_pages(
        self,
        query: str,
        region: str,
        usage: SearchUsage,
        *,
        results: int = SEARCH_PAGES,
        until: float,
        exclude: Sequence[str] = (),
    ) -> SearchPages:
        """One search whose top results Firecrawl reads in the same call (D-101), so no read step follows. Its credits
        (search_credits) are all counted before it is sent, and it is never retried. Its answer is due by `until`, on
        the monotonic clock: a slot isn't waited for past that, Firecrawl's own timeout is what is left once it goes,
        and with less than FIRECRAWL_MIN_S left it isn't sent. `exclude`: sites Firecrawl must not search, so it never
        reads their pages (D-53: Reddit and YouTube).

        A result comes back only with its page's text, read as plain text: one Firecrawl couldn't read (an error, or a
        status other than 200) is left out. Nothing is cached here: the caller keeps only the pages it may use."""
        if not self._api_key:
            return SearchPages(failure=_search_failure("blocked", "FIRECRAWL_API_KEY is not set").failure)
        credits = search_credits(results, read=True)
        if usage.left("firecrawl") < credits:
            return SearchPages(failure=_search_failure("quota_exhausted", "a cap is reached").failure)
        body: dict[str, Any] = {
            "query": query,
            "limit": results,
            "country": region.upper() if len(region) == 2 else COUNTRY,
            # PDFs aren't parsed: a parsed PDF costs a credit a page, and only one a result is counted.
            "scrapeOptions": {
                "formats": ["markdown"],
                "onlyMainContent": True,
                "maxAge": MAX_AGE_MS,
                "parsers": [],
                "proxy": PROXY,
            },
        }
        if exclude:
            body["excludeDomains"] = list(exclude)
        reply = self._post(FIRECRAWL_SEARCH_URL, body, usage, credits, retry=False, until=until)
        if isinstance(reply, tuple):
            return SearchPages(failure=_search_failure(*reply).failure)
        _count_more(usage, reply.get("creditsUsed"), credits)
        data = reply.get("data")
        web = data.get("web") if isinstance(data, dict) else None
        if not isinstance(web, list):
            return SearchPages(failure=_search_failure("error", "the reply had no results list").failure)
        pages = tuple(page for item in web if (page := _searched_page(item)) is not None)
        return SearchPages(pages, unread=sum(1 for item in web if isinstance(item, dict)) - len(pages))

    def _post(
        self,
        url: str,
        body: dict[str, Any],
        usage: SearchUsage,
        credits: int,
        *,
        page: int = 0,
        retry: bool = True,
        until: float | None = None,
        least_s: float = FIRECRAWL_MIN_S,
    ) -> dict[str, Any] | tuple[FailureReason, str]:
        """One request through the gate, its credits counted before it goes, all of them or none (D-78). `retry`: the
        one retry a failure may get (US-31). `until`: when its answer is due (D-101): the slot is waited for no longer,
        Firecrawl's timeout fields become the time then left, and with less than `least_s` left, before or after the
        wait for the slot, it isn't sent."""
        least = least_s + (SEARCH_STEP_S if "scrapeOptions" in body else 0.0)
        if until is not None and until - time.monotonic() < least:
            return "timeout", "no time left to ask"
        slot_s = None if until is None else max(0.0, until - time.monotonic() - least)
        problem = self.gate.try_start(slot_s)
        if problem is not None:
            return problem, "switched off for this run" if problem in ("blocked", "quota_exhausted") else "paced"
        status: int | None = None
        went = False
        timeout_s = READER_TIMEOUT_S
        try:
            if until is not None:
                left = until - time.monotonic()
                if left < least:
                    return "timeout", "no time left to ask"
                body = _with_timeout(body, int(left * 1000))
                timeout_s = left + 1.0  # our own wait: a little past Firecrawl's
            if not self._take(usage, credits, page):
                return "quota_exhausted", "a cap is reached"

            def again() -> bool:
                """A retry costs what the first request did: a place in the minute, its page and its credits (US-31)."""
                return retry and self.gate.book_retry() and self._take(usage, credits, page)

            went = True
            response = send_once_more(
                lambda: self.http.post(
                    url,
                    json=body,
                    headers={"Authorization": f"Bearer {self._api_key or ''}"},
                    timeout=timeout_s,
                ),
                self.retry,
                timeout_s,
                again,
                "Firecrawl",
                rate_limits=False,  # its 429 pauses Firecrawl for the whole process (cool_s): never retried
            )
            if isinstance(response, httpx.TimeoutException):
                return "timeout", f"no reply within {timeout_s:g}s"
            if isinstance(response, httpx.HTTPError):
                return "error", f"request failed ({type(response).__name__})"
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
            if went:
                self.gate.finish(status)
            else:
                self.gate.unsent()

    @staticmethod
    def _take(usage: SearchUsage, credits: int, page: int) -> bool:
        """A request's pages (1 for a scrape, one each for a batch's) and all its credits, or nothing: pages taken
        before a cap said no are given back, so a request that never goes counts nothing (D-78)."""
        taken = 0
        while taken < int(page) and usage.take("firecrawl_pages"):
            taken += 1
        if taken == int(page) and usage.take("firecrawl", count=credits):
            return True
        for _ in range(taken):
            usage.untake("firecrawl_pages")
        return False


def _page_text(
    data: object, before_cleaning: Callable[[str], str] | None = None
) -> ReaderText | tuple[FailureReason, str]:
    """A scraped page's markdown and title, made plain."""
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


def _batched(item: object) -> tuple[str, ReaderText | tuple[FailureReason, str] | None]:
    """One page of a batch: the address it was asked as, and its text, why Firecrawl couldn't read it, or None."""
    if not isinstance(item, dict):
        return "", None
    meta = item.get("metadata")
    meta = meta if isinstance(meta, dict) else {}
    url = meta.get("sourceURL") or meta.get("url") or ""
    status = meta.get("statusCode")
    if isinstance(status, int) and not isinstance(status, bool) and status != 200:
        return str(url), (_reason(status), f"the page answered HTTP {status}")
    if meta.get("error"):
        return str(url), ("error", "Firecrawl couldn't read the page")
    return str(url), _page_text(item)


def _mapped(item: object) -> MappedLink | None:
    """A map's link: an address, or an address and its title."""
    if isinstance(item, str) and item.startswith("https://"):
        return MappedLink(item)
    if isinstance(item, dict) and isinstance(item.get("url"), str) and item["url"].startswith("https://"):
        title = item.get("title")
        return MappedLink(item["url"], title if isinstance(title, str) else "")
    return None


def _links(kept: str) -> list[MappedLink] | tuple[FailureReason, str]:
    try:
        return [MappedLink(str(url), str(title)) for url, title in json.loads(kept)]
    except (ValueError, TypeError):
        return "error", "a kept map could not be read"


def _with_timeout(body: dict[str, Any], ms: int) -> dict[str, Any]:
    """The request with Firecrawl's own timeout set. A search that reads its results gives each read SEARCH_STEP_S
    less, so one slow page comes back as unread instead of making the whole reply late."""
    timed = {**body, "timeout": ms}
    if isinstance(body.get("scrapeOptions"), dict):
        timed["scrapeOptions"] = {**body["scrapeOptions"], "timeout": ms - int(SEARCH_STEP_S * 1000)}
    return timed


def _count_more(usage: SearchUsage, reported: object, counted: int) -> None:
    """Credits Firecrawl says a request used beyond those counted before it went: spent, so counted too, as far as
    the caps let them be (the day's and the total's counts then stay true)."""
    if not isinstance(reported, int) or reported <= counted:
        return
    taken = sum(1 for _ in range(reported - counted) if usage.take("firecrawl"))
    if taken < reported - counted:
        log.warning("research: Firecrawl used %d credits more than its caps had room for", reported - counted - taken)


def _search_failure(reason: FailureReason, detail: str) -> SerperResult:
    return SerperResult(failure=reader_failure(FIRECRAWL, reason, f"search {detail}", SourceType.WEB_SEARCH))


def _searched_page(item: object) -> SearchedPage | None:
    """One result of a search that read its results (D-101): its landed address and its text as plain text. None for a
    result Firecrawl couldn't read: no text, an error, or a status other than 200."""
    if not isinstance(item, dict):
        return None
    meta = item.get("metadata")
    meta = meta if isinstance(meta, dict) else {}
    markdown, status = item.get("markdown"), meta.get("statusCode")
    if not isinstance(markdown, str) or meta.get("error") or (status is not None and status != 200):
        return None
    url = next((u for u in (meta.get("url"), meta.get("sourceURL"), item.get("url")) if isinstance(u, str) and u), None)
    if url is None:
        return None
    title = next((t for t in (meta.get("title"), item.get("title")) if isinstance(t, str)), "")
    return SearchedPage(url=url, title=title, text=plain_text(markdown))


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
