"""Review pages, fetched directly and politely (US-12).

Only pages on the configured review sites are ever fetched, so never Reddit or YouTube. Each one: robots.txt first (kept
24 hours), then that site's limiter and our clear User-Agent, then HTML only, up to a byte limit, then clean_html and
the per-source cap. A page with too little text probably needs JavaScript, and is skipped until US-38 adds the page
reader.

Robots follow RFC 9309 (robots.py): a missing file allows, and an unreachable one (5xx, 429, no reply, or a redirect off
the review sites) disallows until the next search asks again. We also take 401 and 403 as a no, the cautious reading. A
failed fetch is never cached, and nothing here raises.

httpx's timeout is per read, so a server sending a little at a time never trips it. A page or robots file has
MAX_FETCH_S to arrive once it has started.

clean_html stands in for the shared cleaning function (US-28), which does not exist yet: swapping it in is one line, in
ReviewPages._page. It removes what a reader can't see, where a hostile page hides its orders. Visible text stays, and it
is data, never instructions (build rule 3): whatever hands it to an LLM must wrap it as data.
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
from html.parser import HTMLParser
from time import monotonic
from urllib.parse import urljoin

import httpx
from pydantic import ValidationError

from app.contracts import Source, SourceType
from app.contracts.research import DEFAULT_TIER, MAX_SOURCE_CHARS

from .cache import ResearchCache
from .reddit.base import FailureReason, IntervalLimiter, tidy_text
from .reddit.settings import archive_user_agent
from .robots import RobotsRules
from .serper import host_is_under, host_of
from .settings import ResearchSettings
from .usage import SearchUsage

log = logging.getLogger(__name__)

ROBOTS_AGENT = "ShopSense"
TIMEOUT_S = 10.0
MAX_FETCH_S = 15.0
MAX_ROBOTS_BYTES = 500 * 1024  # RFC 9309's limit
MAX_REDIRECTS = 3
NEVER_FETCH = ("reddit.com", "redd.it", "youtube.com", "youtu.be")  # whatever the config says (D-53)
_DISALLOW_ALL = "User-agent: *\nDisallow: /\n"

_SKIPPED_TAGS = frozenset(
    "script style noscript noframes noembed template svg canvas iframe object video audio dialog datalist "  # noqa: SIM905
    "nav header footer aside".split()
)
_VOID_TAGS = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
)
_BLOCK_TAGS = frozenset(
    {
        "p",
        "div",
        "br",
        "li",
        "ul",
        "ol",
        "tr",
        "td",
        "th",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "section",
        "article",
        "table",
    }
)
# Only a true zero hides: opacity:0.95 and font-size:0.9em are ordinary, visible text.
_HIDDEN_STYLE = re.compile(
    r"display\s*:\s*none|visibility\s*:\s*hidden|(?:font-size|opacity)\s*:\s*(?:0+(?:\.0*)?|\.0+)[a-z%]*\s*(?:;|!|$)",
    re.IGNORECASE,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class CleanedPage:
    title: str
    text: str


class _TextReader(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title_parts: list[str] = []
        self.og_title = ""
        self._skipping: list[str] = []
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "meta":
            values = dict(attrs)
            if values.get("property") == "og:title" and not self.og_title:
                self.og_title = values.get("content") or ""
            return
        if tag == "title" and not self._skipping:
            self._in_title = True
            return
        if tag in _VOID_TAGS:
            if tag == "br" and not self._skipping:
                self.parts.append("\n")
            return
        if self._skipping or tag in _SKIPPED_TAGS or _is_hidden(attrs):
            self._skipping.append(tag)
            return
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
            return
        if self._skipping:
            if tag in self._skipping:
                while self._skipping.pop() != tag:  # an unclosed inner tag closes with its parent
                    pass
            return
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
        elif not self._skipping:
            self.parts.append(data)


def _is_hidden(attrs: list[tuple[str, str | None]]) -> bool:
    values = dict(attrs)
    if "hidden" in values or values.get("aria-hidden") == "true":
        return True
    return bool(_HIDDEN_STYLE.search(values.get("style") or ""))


def clean_html(html: str) -> CleanedPage:
    """The readable text and title of a page: no tags, no scripts, nothing hidden, no invisible characters."""
    reader = _TextReader()
    try:
        reader.feed(html)
        reader.close()
    except Exception as exc:  # the parser is lenient, but a page we can't read must never crash a search (rule 9)
        log.info("a page could not be parsed (%s)", type(exc).__name__)
    title = reader.og_title or "".join(reader.title_parts)
    return CleanedPage(tidy_text(title).replace("\n", " "), tidy_text("".join(reader.parts)))


@dataclass(frozen=True)
class PageResult:
    source: Source | None = None
    reason: FailureReason | None = None
    from_cache: bool = False


class ReviewPages:
    def __init__(
        self,
        http: httpx.Client,
        settings: ResearchSettings,
        *,
        cache: ResearchCache,
        now: Callable[[], datetime] = _utc_now,
        clock: Callable[[], float] = monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.http = http
        self.settings = settings
        self.cache = cache
        self._now = now
        self._clock = clock
        self._sleep = sleep
        self._headers = {"User-Agent": archive_user_agent(), "Accept": "text/html,application/xhtml+xml"}
        self._lock = threading.Lock()
        self._limiters: dict[str, IntervalLimiter] = {}
        self._robots: dict[str, RobotsRules] = {}
        self._robots_locks: dict[str, threading.Lock] = {}

    def domain_of(self, url: str) -> str | None:
        """The configured review site this URL is on, or None. Never Reddit or YouTube, whatever the config says."""
        host = host_of(url)
        if host is None or any(host_is_under(host, never) for never in NEVER_FETCH):
            return None
        return next((d for d in self.settings.review_domains if host_is_under(host, d)), None)

    def fetch(self, url: str, usage: SearchUsage) -> PageResult:
        if self.domain_of(url) is None:
            return PageResult(reason="not_found")
        cached = self.cache.get_sources(("page", url))
        if cached:
            return PageResult(source=cached[0], from_cache=True)
        result = self._follow(url, usage)
        if result.source is not None:
            self.cache.put_sources(("page", url), [result.source])
        return result

    def _follow(self, url: str, usage: SearchUsage) -> PageResult:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            domain = self.domain_of(current)
            if domain is None:
                return PageResult(reason="blocked")
            problem = self._may_fetch(current, domain, usage)
            if problem is not None:
                return PageResult(reason=problem)
            if not usage.take("pages"):
                return PageResult(reason="quota_exhausted")
            outcome = self._get(current, domain)
            if isinstance(outcome, str):
                return PageResult(reason=outcome)
            if outcome.is_redirect:
                location = outcome.headers.get("location")
                outcome.close()
                if not location:
                    return PageResult(reason="error")
                try:
                    current = urljoin(current, location)
                except ValueError:  # a Location that isn't a URL at all
                    return PageResult(reason="error")
                continue
            return self._page(current, domain, outcome)
        return PageResult(reason="error")

    def _may_fetch(self, url: str, domain: str, usage: SearchUsage) -> FailureReason | None:
        host = host_of(url) or ""
        rules = self._robots_for(url, host, domain, usage)
        if rules is None:
            return "quota_exhausted"
        if not rules.allowed(httpx.URL(url).raw_path.decode("ascii", errors="replace")):
            log.info("research: robots.txt disallows a page on %s", host)
            return "blocked"
        if rules.crawl_delay > self.settings.max_crawl_delay_s:
            log.info("research: %s asks for a longer crawl delay than we allow", host)
            return "blocked"
        return None

    def _robots_for(self, url: str, host: str, domain: str, usage: SearchUsage) -> RobotsRules | None:
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
                read = self._read_robots(url, usage)
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

    def _read_robots(self, url: str, usage: SearchUsage) -> tuple[str, bool] | None:
        """(the rules, whether to keep them for 24 hours). None if the page cap stops us reading them."""
        current = str(httpx.URL(url).copy_with(path="/robots.txt", query=None, fragment=None))
        for _ in range(MAX_REDIRECTS + 1):
            domain = self.domain_of(current)
            if domain is None:
                return _DISALLOW_ALL, False  # sent off the review sites, so we can't say
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

    def _get(self, url: str, domain: str) -> httpx.Response | FailureReason:
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
        if status in (401, 403):
            return "blocked"
        if status == 429:
            return "rate_limited"
        if status in (404, 410):
            return "not_found"
        return "error"

    def _page(self, url: str, domain: str, response: httpx.Response) -> PageResult:
        try:
            if "html" not in response.headers.get("content-type", "").lower():
                return PageResult(reason="not_found")
            data = self._read_limited(response, self.settings.max_page_bytes)
            encoding = response.encoding or "utf-8"
        except httpx.TimeoutException:
            return PageResult(reason="timeout")
        except httpx.HTTPError as exc:
            log.info("research: a page could not be read (%s)", type(exc).__name__)
            return PageResult(reason="error")
        finally:
            response.close()
        try:
            html = data.decode(encoding, errors="replace")
        except LookupError:  # a charset name Python doesn't know
            html = data.decode("utf-8", errors="replace")
        page = clean_html(html)
        if len(page.text) < self.settings.min_page_chars:
            return PageResult(reason="not_found")  # most likely a page that needs JavaScript
        source = self._source(url, domain, page)
        return PageResult(source=source) if source is not None else PageResult(reason="error")

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

    def _source(self, url: str, domain: str, page: CleanedPage) -> Source | None:
        body = page.text[:MAX_SOURCE_CHARS].strip()
        try:
            return Source(
                id=f"src_{hashlib.sha256(url.encode()).hexdigest()[:16]}",
                url=url,
                type=SourceType.REVIEW_SITE,
                tier=DEFAULT_TIER[SourceType.REVIEW_SITE],
                title=page.title[:300],
                text=body,
                text_hash=hashlib.sha256(body.encode()).hexdigest(),
                fetched_at=self._now(),
                site=domain,
                via="direct",
            )
        except (ValidationError, ValueError):
            return None


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
