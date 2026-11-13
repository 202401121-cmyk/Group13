"""Review, maker and store pages, fetched directly and politely (US-12), with readers for the hard ones (US-38).

Only pages on the configured sites are ever fetched: review sites, makers' own sites (tier 1) and stores (tier 5). Never
YouTube, social sites or a logged-in page (login, account, cart, checkout), and Reddit only through read_reddit. Each
page: robots.txt first (kept 24 hours), then that site's limiter and our clear User-Agent, then HTML only, up to a byte
limit, then clean_html and the per-source cap.

A page the plain fetch can't read (an error, a refusal, not HTML, or too little text, which most likely means it needs
JavaScript) goes to Jina Reader, then to Firecrawl when Jina fails or is thin too (readers.py). robots.txt has already
said yes by then: a page it disallows never reaches a reader, and neither does one that asks for a login (HTTP 401).
Reader text goes through the same cleaning and cap, and the Source says via jina or via firecrawl.

Robots follow RFC 9309 (robots.py): a missing file allows, and an unreachable one (5xx, 429, no reply, or a redirect off
the configured sites) disallows until the next search asks again. We also take 401 and 403 as a no, the cautious
reading. A failed fetch is never cached, and nothing here raises.

httpx's timeout is per read, so a server sending a little at a time never trips it. A page or robots file has
MAX_FETCH_S to arrive once it has started.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic
from typing import Literal
from urllib.parse import urljoin

import httpx
from pydantic import ValidationError

from app.contracts import Source, SourceFailure, SourceType
from app.contracts.research import DEFAULT_TIER, MAX_SOURCE_CHARS

from .cache import ResearchCache
from .cleaning import clean_html, decode_page
from .readers import FIRECRAWL, FirecrawlClient, JinaReader, ReaderText, reader_failure
from .reddit.base import FailureReason, IntervalLimiter
from .reddit.settings import archive_user_agent
from .robots import RobotsRules
from .serper import host_is_under, host_of
from .settings import ResearchSettings, maker_domains_of
from .usage import SearchUsage

log = logging.getLogger(__name__)

ROBOTS_AGENT = "ShopSense"
TIMEOUT_S = 10.0
MAX_FETCH_S = 15.0
MAX_ROBOTS_BYTES = 500 * 1024  # RFC 9309's limit
MAX_REDIRECTS = 3
NEVER_FETCH = ("reddit.com", "redd.it", "youtube.com", "youtu.be")  # whatever the config says (D-53)
# Social sites are never read, by a plain fetch or a reader (US-38). Nor is a page behind a login.
NEVER_READ = (
    *NEVER_FETCH,
    "facebook.com",
    "instagram.com",
    "x.com",
    "twitter.com",
    "threads.net",
    "tiktok.com",
    "linkedin.com",
    "pinterest.com",
)
_LOGGED_IN = re.compile(
    r"/(?:login|log-in|signin|sign-in|signup|sign-up|account|my-account|myaccount|cart|checkout|wishlist|orders|auth)"
    r"(?:[/?#.]|$)",
    re.IGNORECASE,
)
_THREAD = re.compile(r"^https://(?:www\.|old\.)?reddit\.com/r/([A-Za-z0-9_]{2,21})/comments/([a-z0-9]{3,12})(?:/|$)")
_USERNAME = re.compile(
    r"(?<![\w/])/?u/[A-Za-z0-9_-]{2,}|(?:https?://)?(?:www\.|old\.)?reddit\.com/(?:user|u)/[A-Za-z0-9_-]+/?"
)
_PROFILE_LINK = re.compile(r"\[[^\]]*\]\(\s*(?:https?://)?(?:[a-z]+\.)?reddit\.com/(?:user|u)/[^)]*\)", re.IGNORECASE)
_DISALLOW_ALL = "User-agent: *\nDisallow: /\n"
Kind = Literal["review", "maker", "store"]
_TYPES: dict[Kind, SourceType] = {
    "review": SourceType.REVIEW_SITE,
    "maker": SourceType.MANUFACTURER,
    "store": SourceType.RETAILER,
}
# What a plain fetch can end with besides a page. "refused" (HTTP 403) is often a bot check, so a reader may try.
_Plain = httpx.Response | FailureReason | Literal["refused"]
_FOR_READERS = ("error", "timeout", "rate_limited")  # and "refused", not HTML or too thin


def _utc_now() -> datetime:
    return datetime.now(UTC)


def page_kind(url: str, settings: ResearchSettings) -> tuple[str, Kind] | None:
    """The configured site this URL is on, and what kind it is. None for anything else, and never YouTube, a social
    site, Reddit or a logged-in page, whatever the config says."""
    host = host_of(url)
    if host is None or any(host_is_under(host, never) for never in NEVER_READ) or is_logged_in_page(url):
        return None
    groups: tuple[tuple[Kind, tuple[str, ...]], ...] = (
        ("review", settings.review_domains),
        ("maker", maker_domains_of(settings)),
        ("store", settings.retailer_domains),
    )
    for kind, domains in groups:
        domain = next((d for d in domains if host_is_under(host, d)), None)
        if domain is not None:
            return domain, kind
    return None


def thread_id(url: str) -> str | None:
    """A Reddit thread's id from its link, or None if the link isn't a thread."""
    found = _THREAD.match(url)
    return found.group(2) if found is not None else None


def is_logged_in_page(url: str) -> bool:
    """A login, account, cart or checkout page: never read, by a plain fetch or a reader."""
    try:
        path = httpx.URL(url).raw_path.decode("ascii", errors="replace")
    except (httpx.InvalidURL, TypeError, ValueError):
        return True
    return bool(_LOGGED_IN.search(path))


def without_profile_links(markdown: str) -> str:
    """A link to a Reddit profile, whatever its words, before cleaning keeps only the words: they are a username."""
    return _PROFILE_LINK.sub("a Reddit user", markdown)


def without_usernames(text: str) -> str:
    """Reddit text read through a reader: every username and profile link gone (D-53). Never kept, never hashed here."""
    return _USERNAME.sub("a Reddit user", text)


@dataclass(frozen=True)
class PageResult:
    source: Source | None = None
    reason: FailureReason | None = None
    from_cache: bool = False
    lost_to: tuple[SourceFailure, ...] = ()  # the readers that failed or were capped, even if a later one read it


@dataclass(frozen=True)
class PageReaders:
    """The readers for pages a plain fetch can't read, in order. Either may be missing (no key, or switched off)."""

    jina: JinaReader | None = None
    firecrawl: FirecrawlClient | None = None


@dataclass(frozen=True)
class _Attempt:
    """A plain fetch's outcome: a result, and whether a reader may try the page (and at which URL)."""

    result: PageResult
    reader_url: str | None = None


class ReviewPages:
    def __init__(
        self,
        http: httpx.Client,
        settings: ResearchSettings,
        *,
        cache: ResearchCache,
        readers: PageReaders | None = None,
        now: Callable[[], datetime] = _utc_now,
        clock: Callable[[], float] = monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.http = http
        self.settings = settings
        self.cache = cache
        self.readers = readers if readers is not None else PageReaders()
        self._now = now
        self._clock = clock
        self._sleep = sleep
        self._headers = {"User-Agent": archive_user_agent(), "Accept": "text/html,application/xhtml+xml"}
        self._lock = threading.Lock()
        self._limiters: dict[str, IntervalLimiter] = {}
        self._robots: dict[str, RobotsRules] = {}
        self._robots_locks: dict[str, threading.Lock] = {}

    def kind_of(self, url: str) -> tuple[str, Kind] | None:
        return page_kind(url, self.settings)

    def domain_of(self, url: str) -> str | None:
        """The configured site this URL is on, or None."""
        found = self.kind_of(url)
        return found[0] if found is not None else None

    def fetch(self, url: str, usage: SearchUsage) -> PageResult:
        found = self.kind_of(url)
        if found is None:
            return PageResult(reason="not_found")
        cached = self.cache.get_sources(("page", url))
        if cached:
            return PageResult(source=cached[0], from_cache=True)
        attempt = self._follow(url, usage)
        result = attempt.result
        # The page a redirect landed on is what a reader reads, so its own site and kind (and its own rules) apply.
        landed = page_kind(attempt.reader_url, self.settings) if attempt.reader_url is not None else None
        if result.source is None and attempt.reader_url is not None and landed is not None:
            result = self._read_through(attempt.reader_url, *landed, usage, plain=result)
        if result.source is not None:
            self.cache.put_sources(("page", url), [result.source])
        return result

    def read_reddit(self, url: str, usage: SearchUsage) -> PageResult:
        """A Reddit thread the archive lacks, through Firecrawl only (US-38): robots.txt first, Reddit data after.

        Firecrawl keeps no copy (storeInCache false); we keep it 24 hours, as every Reddit read. Usernames are removed
        from the text, so nothing names a person (D-53). The archive's copy is always preferred: the stage asks this
        only for a thread the archive didn't return.
        """
        thread = _THREAD.match(url)
        firecrawl = self.readers.firecrawl
        if thread is None or firecrawl is None:
            return PageResult(reason="not_found")
        cached = self.cache.get_sources(("reddit_page", url))
        if cached:
            return PageResult(source=cached[0], from_cache=True)
        problem = self._may_fetch(url, "reddit.com", usage, _reddit_site)
        if problem is not None:
            return PageResult(reason=problem)
        read = firecrawl.scrape(url, usage, keep_in_their_cache=False, before_cleaning=without_profile_links)
        if not isinstance(read, ReaderText):
            reason, detail = read
            return PageResult(reason=reason, lost_to=(reader_failure(FIRECRAWL, reason, detail, SourceType.REDDIT),))
        text = without_usernames(read.text)[:MAX_SOURCE_CHARS].strip()
        if not text:
            return PageResult(reason="not_found")
        try:
            source = Source(
                id=f"src_{hashlib.sha256(url.encode()).hexdigest()[:16]}",
                url=url,
                type=SourceType.REDDIT,
                tier=DEFAULT_TIER[SourceType.REDDIT],
                title=without_usernames(read.title)[:300],
                text=text,
                text_hash=hashlib.sha256(text.encode()).hexdigest(),
                fetched_at=self._now(),
                community=f"r/{thread.group(1)}",
                via="firecrawl",
            )
        except (ValidationError, ValueError):
            return PageResult(reason="error")
        self.cache.put_sources(("reddit_page", url), [source])
        return PageResult(source=source)

    def _read_through(self, url: str, domain: str, kind: Kind, usage: SearchUsage, *, plain: PageResult) -> PageResult:
        """Jina, then Firecrawl. The first with enough text wins. A reader that failed or was capped is named."""
        lost: list[SourceFailure] = []
        readers = [r for r in (self.readers.jina, self.readers.firecrawl) if r is not None]
        for reader in readers:
            read = reader.read(url, usage) if isinstance(reader, JinaReader) else reader.scrape(url, usage)
            if isinstance(read, ReaderText) and len(read.text) >= self.settings.min_page_chars:
                via: Literal["jina", "firecrawl"] = "jina" if isinstance(reader, JinaReader) else "firecrawl"
                source = self._source(url, domain, kind, read.title, read.text, via)
                if source is None:
                    return PageResult(reason="error", lost_to=tuple(lost))
                return PageResult(source=source, lost_to=tuple(lost))  # Jina's failure is still named (criterion 5)
            if isinstance(read, ReaderText):
                lost.append(reader_failure(reader.name, "not_found", "too little text", _TYPES[kind]))
            else:
                lost.append(reader_failure(reader.name, read[0], read[1], _TYPES[kind]))
        return PageResult(reason=plain.reason or "not_found", lost_to=tuple(lost))

    def _follow(self, url: str, usage: SearchUsage) -> _Attempt:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            found = self.kind_of(current)
            if found is None:
                return _Attempt(PageResult(reason="blocked"))
            domain = found[0]
            problem = self._may_fetch(current, domain, usage, self.domain_of)
            if problem is not None:
                return _Attempt(PageResult(reason=problem))  # robots said no, or its cap: never a reader
            if not usage.take("pages"):
                return _Attempt(PageResult(reason="quota_exhausted"))
            outcome = self._get(current, domain)
            if isinstance(outcome, str):
                if outcome == "refused":
                    return _Attempt(PageResult(reason="blocked"), current)
                reader_url = current if outcome in _FOR_READERS else None
                return _Attempt(PageResult(reason=outcome), reader_url)
            if outcome.is_redirect:
                location = outcome.headers.get("location")
                outcome.close()
                if not location:
                    return _Attempt(PageResult(reason="error"))
                try:
                    current = urljoin(current, location)
                except ValueError:  # a Location that isn't a URL at all
                    return _Attempt(PageResult(reason="error"))
                continue
            return self._page(current, domain, found[1], outcome)
        return _Attempt(PageResult(reason="error"))

    def _may_fetch(
        self, url: str, domain: str, usage: SearchUsage, site_of: Callable[[str], str | None]
    ) -> FailureReason | None:
        host = host_of(url) or ""
        rules = self._robots_for(url, host, domain, usage, site_of)
        if rules is None:
            return "quota_exhausted"
        if not rules.allowed(httpx.URL(url).raw_path.decode("ascii", errors="replace")):
            log.info("research: robots.txt disallows a page on %s", host)
            return "blocked"
        if rules.crawl_delay > self.settings.max_crawl_delay_s:
            log.info("research: %s asks for a longer crawl delay than we allow", host)
            return "blocked"
        return None

    def _robots_for(
        self, url: str, host: str, domain: str, usage: SearchUsage, site_of: Callable[[str], str | None]
    ) -> RobotsRules | None:
        """This host's rules. Pages of one host arriving together read the file once, and a dead host is asked once."""
        with self._lock:
            known = self._robots.get(host)
            lock = self._robots_locks.setdefault(host, threading.Lock())
        if known is not None:
            return known
        with lock:
            with self._lock:
                known = self._robots.get(host)
            if known is not None:
                return known
            text = self.cache.get_text(("robots", host))
            if text is None:
                read = self._read_robots(url, usage, site_of)
                if read is None:
                    return None
                text, keep = read
                if keep:
                    self.cache.put_text(("robots", host), text)
            rules = RobotsRules(text, ROBOTS_AGENT)
            with self._lock:
                self._robots[host] = rules  # one search's memory: the next search asks a dead host again
            if rules.crawl_delay <= self.settings.max_crawl_delay_s:
                self._limiter(domain, rules.crawl_delay)
            return rules

    def _read_robots(
        self, url: str, usage: SearchUsage, site_of: Callable[[str], str | None]
    ) -> tuple[str, bool] | None:
        """(the rules, whether to keep them for 24 hours). None if the page cap stops us reading them."""
        current = str(httpx.URL(url).copy_with(path="/robots.txt", query=None, fragment=None))
        for _ in range(MAX_REDIRECTS + 1):
            domain = site_of(current)
            if domain is None:
                return _DISALLOW_ALL, False  # sent off the configured sites, so we can't say
            if not usage.take("pages"):
                return None
            self._limiter(domain).wait()
            try:  # httpx reads a redirect's host as it replies, and a bad one raises a ValueError
                request = self.http.build_request("GET", current, headers=self._headers, timeout=TIMEOUT_S)
                response = self.http.send(request, stream=True, follow_redirects=False)
            except (httpx.HTTPError, httpx.InvalidURL, ValueError) as exc:
                log.info("research: robots.txt could not be read (%s)", type(exc).__name__)
                return _DISALLOW_ALL, False  # unreachable: no for now, and asked again next time
            if not response.is_redirect:
                return self._robots_answer(response)
            location = response.headers.get("location")
            response.close()
            if not location:
                return _DISALLOW_ALL, False
            try:
                current = urljoin(current, location)
            except ValueError:
                return _DISALLOW_ALL, False
        return _DISALLOW_ALL, False

    def _robots_answer(self, response: httpx.Response) -> tuple[str, bool]:
        status = response.status_code
        try:
            if 200 <= status < 300:
                return self._read_limited(response, MAX_ROBOTS_BYTES).decode("utf-8", errors="replace"), True
        except httpx.HTTPError as exc:
            log.info("research: robots.txt could not be read (%s)", type(exc).__name__)
            return _DISALLOW_ALL, False
        finally:
            response.close()
        if status in (401, 403):
            return _DISALLOW_ALL, True
        if status != 429 and 400 <= status < 500:
            return "", True  # no robots file: everything is allowed
        return _DISALLOW_ALL, False

    def _limiter(self, domain: str, delay: float = 0.0) -> IntervalLimiter:
        interval = max(self.settings.page_interval_s, delay)
        with self._lock:
            limiter = self._limiters.get(domain)
            if limiter is None or limiter.interval_s < interval:
                limiter = self._limiters[domain] = _shared_page_limiter(domain, interval, self._clock, self._sleep)
            return limiter

    def _get(self, url: str, domain: str) -> _Plain:
        self._limiter(domain).wait()
        try:  # httpx reads a redirect's host as it replies, and a bad one raises a ValueError
            request = self.http.build_request("GET", url, headers=self._headers, timeout=TIMEOUT_S)
            response = self.http.send(request, stream=True, follow_redirects=False)
        except httpx.TimeoutException:
            return "timeout"
        except (httpx.HTTPError, httpx.InvalidURL, ValueError) as exc:
            log.info("research: a page fetch failed (%s)", type(exc).__name__)
            return "error"
        status = response.status_code
        if response.is_redirect:
            return response
        if status == 200:
            return response
        response.close()
        if status == 401:
            return "blocked"  # it asks for a login: never a reader's either
        if status == 403:
            return "refused"
        if status == 429:
            return "rate_limited"
        if status in (404, 410):
            return "not_found"
        return "error"

    def _page(self, url: str, domain: str, kind: Kind, response: httpx.Response) -> _Attempt:
        try:
            if "html" not in response.headers.get("content-type", "").lower():
                return _Attempt(PageResult(reason="not_found"), url)
            data = self._read_limited(response, self.settings.max_page_bytes)
            encoding = response.encoding or "utf-8"
        except httpx.TimeoutException:
            return _Attempt(PageResult(reason="timeout"), url)
        except httpx.HTTPError as exc:
            log.info("research: a page could not be read (%s)", type(exc).__name__)
            return _Attempt(PageResult(reason="error"), url)
        finally:
            response.close()
        page = clean_html(decode_page(data, encoding))
        if len(page.text) < self.settings.min_page_chars:
            return _Attempt(PageResult(reason="not_found"), url)  # most likely a page that needs JavaScript
        source = self._source(url, domain, kind, page.title, page.text, "direct")
        return _Attempt(PageResult(source=source) if source is not None else PageResult(reason="error"))

    def _read_limited(self, response: httpx.Response, limit: int) -> bytes:
        """Up to `limit` bytes, within MAX_FETCH_S. No chunk size: httpx would hold a slow drip back from this check."""
        started = self._clock()
        chunks: list[bytes] = []
        size = 0
        for chunk in response.iter_bytes():
            chunks.append(chunk)
            size += len(chunk)
            if size >= limit:
                break
            if self._clock() - started > MAX_FETCH_S:
                raise httpx.ReadTimeout("the fetch took too long")
        return b"".join(chunks)[:limit]

    def _source(
        self, url: str, domain: str, kind: Kind, title: str, text: str, via: Literal["direct", "jina", "firecrawl"]
    ) -> Source | None:
        body = text[:MAX_SOURCE_CHARS].strip()
        kind_type = _TYPES[kind]
        try:
            return Source(
                id=f"src_{hashlib.sha256(url.encode()).hexdigest()[:16]}",
                url=url,
                type=kind_type,
                tier=DEFAULT_TIER[kind_type],
                title=title[:300],
                text=body,
                text_hash=hashlib.sha256(body.encode()).hexdigest(),
                fetched_at=self._now(),
                site=domain,
                via=via,
            )
        except (ValidationError, ValueError):
            return None


def _reddit_site(url: str) -> str | None:
    """For read_reddit's robots.txt: Reddit's own host, and nowhere else."""
    host = host_of(url)
    return "reddit.com" if host is not None and host_is_under(host, "reddit.com") else None


_page_limiters: dict[tuple[str, int, int], IntervalLimiter] = {}
_page_limiters_lock = threading.Lock()


def _shared_page_limiter(
    domain: str, interval: float, clock: Callable[[], float], sleep: Callable[[float], None]
) -> IntervalLimiter:
    """One limiter per site for the whole process, as a stage is built per search. A test's clock gets its own."""
    with _page_limiters_lock:
        key = (domain, id(clock), id(sleep))
        limiter = _page_limiters.get(key)
        if limiter is None:
            limiter = _page_limiters[key] = IntervalLimiter(interval, clock=clock, sleep=sleep)
        elif limiter.interval_s < interval:
            limiter.interval_s = interval  # in place: its memory of the last request must not be lost
        return limiter
