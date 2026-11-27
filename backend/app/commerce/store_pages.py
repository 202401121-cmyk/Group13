"""A store's own page for any store, checked by code before a link to it is shown (US-24, D-99).

For each of the top 3 priced products with a Google Shopping offer, one search for the exact model (matching.query_for:
the brand and the product's name, never anything the shopper typed, D-99 #8): Firecrawl search first, Serper web search
when Firecrawl has no room or fails (FirecrawlFirst). links.candidate picks each offer's result on that store's own
site. Then every check is code, and any check that fails keeps the Google link:

1. https only, and never a site research never reads (Reddit, YouTube, social sites);
2. never a login, account, cart, checkout or search-results page;
3. robots.txt allows us, read as research reads it (pages.py): RFC 9309 rules (robots.py) on the raw path, a crawl
   delay above research.yaml's max_crawl_delay_s refused, one retry (S4-12's rule), at most 5 seconds to arrive, kept
   24 hours under research's key;
4. Google Safe Browsing reports no threat (safe_browsing.py: skipped without a key; an error keeps the Google link);
5. a product page. A known store's product_path proves it from the address, with no read (config/stores.yaml, a
   speed-up). Otherwise the page is read by Firecrawl, or by Jina when Firecrawl has no room or fails: it opens with
   200, stays https on the same registrable domain after redirects, holds schema.org Product data (JSON-LD or
   microdata), and names this exact model, whole tokens, in its title or its Product's name. The link shown is where
   the page ended, so when that differs from the search's link it passes checks 2 to 4 itself.

When the search finds no page on an offer's store (D-100 item 9), Firecrawl's map looks on that store's own site,
known from config/stores.yaml or the store's name, with the model's name as its search term: 1 credit, at most
MAX_STORE_MAPS a search, inside commerce's time. The address it finds then passes the same checks, 1 to 5. The checks
for different offers run at once (commerce's own pool), inside commerce's limit.

The price and its time stay the shopping result's: a store page's own price never replaces it (D-99 #4).

Verdicts are kept per (model, store host) in the research cache: a verified link, or a page that failed a check, for a
day, so a bad page isn't tried again that day (D-99 #6); a Safe Browsing threat for 30 minutes, as Google's terms allow.
The cache keeps such entries a little under 24 hours (its limit for new kinds of entry), and each carries its own end.
What says nothing about the page is never kept, so the next search tries again: a reader or Safe Browsing down, a
robots.txt that couldn't be read, a reader that didn't say the page's status, or the stage's own time running out.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from time import monotonic
from typing import Any
from urllib.parse import urljoin

import httpx

from app.contracts import Product, Source
from app.entities.normalise import plain_name
from app.llm.retry import RetryClock
from app.research.cache import ResearchCache
from app.research.pages import NEVER_READ, ROBOTS_AGENT, is_logged_in_page
from app.research.readers import FirecrawlClient, JinaReader, PageSnapshot
from app.research.reddit.settings import archive_user_agent
from app.research.retrying import send_tracked
from app.research.robots import RobotsRules
from app.research.serper import SerperResult, host_is_under, host_of
from app.research.settings import ResearchSettings
from app.research.usage import SearchUsage

from .links import (
    Store,
    WebSearchLike,
    address_words,
    candidate,
    is_search_page,
    is_store_host,
    known_product_page,
    load_stores,
    names_model,
    registrable,
    shown_domain,
    store_for,
)
from .matching import query_for
from .safe_browsing import SafeBrowsing

log = logging.getLogger(__name__)

VERIFIED_FOR = timedelta(hours=24)
FAILED_FOR = timedelta(hours=24)
THREAT_FOR = timedelta(minutes=30)  # Google's terms: an address is unsafe only on an answer this fresh
STEP_MIN_S = 0.5  # a request is started only with this much of the stage left
ROBOTS_TIMEOUT_S = 5.0  # a robots.txt request, its retry and its body, all of it
MAX_ROBOTS_BYTES = 500 * 1024  # RFC 9309's limit
MAX_REDIRECTS = 3
MAX_STORE_MAPS = 3  # D-100 item 9: Firecrawl maps a search may send for store pages, 1 credit each
MAX_CRAWL_DELAY_S = ResearchSettings().max_crawl_delay_s  # research.yaml's; the stage passes the file's value
_DISALLOW_ALL = "User-agent: *\nDisallow: /\n"
_LD_TYPE = "application/ld+json"
_MICRODATA = re.compile(r"\bitemtype\s*=\s*[\"']https?://schema\.org/Product(?:Group)?[\"'/]", re.IGNORECASE)
_PRODUCT_TYPES = frozenset({"product", "productgroup"})  # schema.org's Product, and its group of variants
_MAX_DEPTH = 8


def _utc_now() -> datetime:
    return datetime.now(UTC)


class FirecrawlFirst:
    """The store-page search: Firecrawl first, Serper when Firecrawl has no room, no key, or fails (D-99 #1)."""

    def __init__(self, firecrawl: WebSearchLike | None, serper: WebSearchLike) -> None:
        self.firecrawl = firecrawl
        self.serper = serper

    def __repr__(self) -> str:
        return f"FirecrawlFirst({self.firecrawl!r}, {self.serper!r})"

    def search(self, query: str, region: str, usage: SearchUsage, sites: Sequence[str] = ()) -> SerperResult:
        if self.firecrawl is not None:
            found = self.firecrawl.search(query, region, usage, sites)
            if found.failure is None:
                return found
        return self.serper.search(query, region, usage, sites)


@dataclass(frozen=True)
class Verdict:
    """A page's check: the link to show (None: keep Google's), which check decided, and how long to keep the verdict
    (None: not kept, as it says nothing about the page)."""

    url: str | None
    why: str
    keep: timedelta | None


def _failed(why: str) -> Verdict:
    return Verdict(None, why, FAILED_FOR)


def _passing(why: str) -> Verdict:
    """A failure that says nothing about the page: never kept."""
    return Verdict(None, why, None)


def _things(data: Any, depth: int = 0) -> Iterator[dict[str, Any]]:
    """Every object in a JSON-LD block, through lists, @graph and mainEntity, a few levels deep."""
    if depth > _MAX_DEPTH:
        return
    if isinstance(data, list):
        for item in data:
            yield from _things(item, depth + 1)
    elif isinstance(data, dict):
        yield data
        for key in ("@graph", "mainEntity"):
            if key in data:
                yield from _things(data[key], depth + 1)


def _is_product(kind: Any) -> bool:
    kinds = kind if isinstance(kind, list) else [kind]
    return any(isinstance(k, str) and re.split(r"[/:#]", k)[-1].casefold() in _PRODUCT_TYPES for k in kinds)


def _ld_blocks(html: str) -> Iterator[str]:
    """The text of each <script type="application/ld+json"> block, found by plain searching, never a regular
    expression: each step moves on, so a hostile page full of unclosed tags can't stall the process."""
    lower = html.lower()
    at = 0
    while (start := lower.find("<script", at)) >= 0:
        opened = lower.find(">", start)
        closed = lower.find("</script", opened) if opened >= 0 else -1
        if closed < 0:
            return
        if _LD_TYPE in lower[start:opened]:
            yield html[opened + 1 : closed]
        at = closed + len("</script")


def product_names(html: str) -> list[str] | None:
    """The names of the schema.org Products on the page, from JSON-LD, or None when the page holds no Product data at
    all (JSON-LD or microdata). A Product with no name adds none."""
    names: list[str] = []
    found = False
    for block in _ld_blocks(html):
        try:
            data = json.loads(block.strip(), strict=False)
        except ValueError:
            continue  # a broken block is no data
        for item in _things(data):
            if _is_product(item.get("@type")):
                found = True
                if isinstance(item.get("name"), str):
                    names.append(item["name"])
    return names if found or _MICRODATA.search(html) else None


def _target(url: str) -> str:
    """A page's path and query as sent, which robots.txt rules read (as research does: %2F is not a /)."""
    return httpx.URL(url).raw_path.decode("ascii", errors="replace")


def _model(product: Product) -> str:
    return " ".join(plain_name(query_for(product)).split())


class StorePageChecker:
    """The checks in the module's list, and their verdicts kept per (model, store host)."""

    def __init__(
        self,
        http: httpx.Client,
        *,
        cache: ResearchCache,
        safe_browsing: SafeBrowsing,
        firecrawl: FirecrawlClient | None,
        jina: JinaReader | None,
        stores: Sequence[Store] | None = None,
        max_crawl_delay_s: float = MAX_CRAWL_DELAY_S,
        retry: RetryClock | None = None,
        now: Callable[[], datetime] = _utc_now,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self.http = http
        self.cache = cache
        self.safe_browsing = safe_browsing
        self.readers = [reader for reader in (firecrawl, jina) if reader is not None]
        self.firecrawl = firecrawl
        self.maps = 0  # Firecrawl maps sent for this search's store pages (MAX_STORE_MAPS)
        self._maps_lock = threading.Lock()
        self.stores = tuple(stores) if stores is not None else load_stores()
        self.max_crawl_delay_s = max_crawl_delay_s
        self.retry = retry if retry is not None else RetryClock()  # robots.txt's one retry (S4-12's rule)
        self._now = now
        self._clock = clock
        self._agent = archive_user_agent()

    def store_page(
        self, product: Product, name: str, found: Sequence[Source], usage: SearchUsage, ends: float
    ) -> str | None:
        """The checked store page for this offer's store, or None to keep the Google link."""
        link = candidate(product, name, found, self.stores) or self._mapped(product, name, usage, ends)
        if link is None:
            return None
        key = ("store_page", _model(product), host_of(link) or "")
        known, url = self._kept(key)
        if known:
            return url
        verdict = self.verify(product, link, usage, ends)
        if verdict.keep is not None:
            self._keep(key, verdict.url, verdict.keep)
        log.info("commerce: a store page was %s (%s)", "used" if verdict.url else "not used", verdict.why)
        return verdict.url

    def _mapped(self, product: Product, name: str, usage: SearchUsage, ends: float) -> str | None:
        """The store's own page for this model, found by Firecrawl's map on the store's own site when the search found
        none (D-100 item 9): the first address on that site naming the model in its title or its words. None when the
        store's site isn't known, Firecrawl isn't there, MAX_STORE_MAPS were sent, or commerce's time is short."""
        known = store_for(name, self.stores)
        domain = shown_domain(name) or (known.domain if known is not None else None)
        mapping = getattr(self.firecrawl, "map", None)
        if domain is None or mapping is None or ends - self._clock() < STEP_MIN_S:
            return None
        with self._maps_lock:
            if self.maps >= MAX_STORE_MAPS:
                return None
            self.maps += 1
        found = mapping(f"https://www.{domain}", query_for(product), usage, until=ends)
        if isinstance(found, tuple):
            log.info("commerce: a store's site was not mapped (%s)", found[0])
            return None
        for each in found:
            host = host_of(each.url) or ""
            if is_store_host(name, host, self.stores) and (
                names_model(product, each.title) or names_model(product, address_words(each.url))
            ):
                return str(each.url)
        return None

    def verify(self, product: Product, link: str, usage: SearchUsage, ends: float) -> Verdict:
        address = self._address(link, usage, ends)
        if address is not None:
            return address
        if known_product_page(link, self.stores):
            return Verdict(link, "verified", VERIFIED_FOR)  # the address proves it: no read (D-99 #5)
        if ends - self._clock() < STEP_MIN_S:
            return _passing("late")
        page = self._read(link, usage)
        if page is None:
            return _passing("unread")
        return self._judge(product, link, page, usage, ends)

    def _judge(self, product: Product, link: str, page: PageSnapshot, usage: SearchUsage, ends: float) -> Verdict:
        if page.status is None or not page.final_url:
            return _passing("unread")  # the reader didn't say: nothing is known about the page
        if page.status != 200:
            return _failed("status")
        final_host = host_of(page.final_url)
        if (
            final_host is None
            or httpx.URL(page.final_url).scheme != "https"
            or registrable(final_host) != registrable(host_of(link) or "")
        ):
            return _failed("redirect")
        if page.final_url != link:  # the address shown is where the page ended: it passes the same checks
            address = self._address(page.final_url, usage, ends)
            if address is not None:
                return address
        names = product_names(page.html)
        if names is None and not known_product_page(page.final_url, self.stores):
            return _failed("not_product")
        if not any(names_model(product, text) for text in (page.title, *(names or ()))):
            return _failed("other_model")
        return Verdict(page.final_url, "verified", VERIFIED_FOR)

    def _address(self, link: str, usage: SearchUsage, ends: float) -> Verdict | None:
        """Checks 1 to 4, on an address alone: None when it passes them all, else the failed verdict."""
        host = host_of(link)
        if host is None or httpx.URL(link).scheme != "https":
            return _failed("not_https")
        if any(host_is_under(host, site) for site in NEVER_READ):
            return _failed("never_read")
        if is_logged_in_page(link):
            return _failed("logged_in")
        if is_search_page(link):
            return _failed("search_page")
        robots = self._robots(link, usage, ends)
        if robots is not None:
            return robots
        if ends - self._clock() < STEP_MIN_S:
            return _passing("late")
        safe = self.safe_browsing.check(link)
        if safe == "threat":
            return Verdict(None, "threat", THREAT_FOR)
        if safe == "error":
            return _passing("unchecked")
        return None

    def _read(self, link: str, usage: SearchUsage) -> PageSnapshot | None:
        """The page through Firecrawl, then Jina when Firecrawl has no room or fails. None when every reader failed."""
        for reader in self.readers:
            page = reader.snapshot(link, usage)
            if isinstance(page, PageSnapshot):
                return page
            log.info("commerce: %s could not read a store page (%s)", reader.name, page[0])
        return None

    # --- robots.txt, as research reads it ---------------------------------------------------------------------------

    def _robots(self, url: str, usage: SearchUsage, ends: float) -> Verdict | None:
        """None when robots.txt allows the page; else the failed verdict, kept only when a robots file said so. A crawl
        delay longer than research allows is a no, as in research."""
        host = host_of(url) or ""
        text = self.cache.get_text(("robots", host))
        lasting = True
        if text is None:
            read = self._read_robots(url, usage, ends)
            if read is None:
                return _passing("late")  # no time, or the page cap: not asked
            text, lasting = read
            if lasting:
                self.cache.put_text(("robots", host), text)
        rules = RobotsRules(text, ROBOTS_AGENT)
        if rules.allowed(_target(url)) and rules.crawl_delay <= self.max_crawl_delay_s:
            return None
        return Verdict(None, "robots", FAILED_FOR if lasting else None)

    def _read_robots(self, url: str, usage: SearchUsage, ends: float) -> tuple[str, bool] | None:
        """(the rules, whether they may be kept). A file we couldn't read disallows for now (RFC 9309), and a redirect
        off the store's own registered domain says nothing. None when there was no time or the page cap is reached."""
        site = registrable(host_of(url) or "")
        current = str(httpx.URL(url).copy_with(path="/robots.txt", query=None, fragment=None))
        for _ in range(MAX_REDIRECTS + 1):
            host = host_of(current)
            if host is None or registrable(host) != site:
                return _DISALLOW_ALL, False
            left = ends - self._clock()
            if left < STEP_MIN_S or not usage.take("pages"):
                return None
            until = self._clock() + min(ROBOTS_TIMEOUT_S, left)
            try:
                outcome, _ = send_tracked(
                    partial(self._send_get, current, until),
                    self.retry,
                    ROBOTS_TIMEOUT_S,
                    partial(self._may_retry, usage, until),
                    "robots.txt",
                )
            except ValueError as exc:  # an address httpx can't send
                log.info("commerce: robots.txt could not be read (%s)", type(exc).__name__)
                return _DISALLOW_ALL, False
            if isinstance(outcome, httpx.HTTPError):
                log.info("commerce: robots.txt could not be read (%s)", type(outcome).__name__)
                return _DISALLOW_ALL, False
            response = outcome
            if not response.is_redirect:
                return self._robots_answer(response, until)
            location = response.headers.get("location")
            response.close()
            if not location:
                return _DISALLOW_ALL, False
            current = urljoin(current, location)
        return _DISALLOW_ALL, False

    def _may_retry(self, usage: SearchUsage, until: float) -> bool:
        """A retry is a request like any other: it takes a page, and goes only with time for it."""
        return until - self._clock() >= STEP_MIN_S and usage.take("pages")

    def _send_get(self, url: str, until: float) -> httpx.Response:
        """One GET with our User-Agent, streamed, no redirect followed, waiting no later than `until`."""
        timeout = max(0.1, until - self._clock())
        request = self.http.build_request("GET", url, headers={"User-Agent": self._agent}, timeout=timeout)
        return self.http.send(request, stream=True, follow_redirects=False)

    def _robots_answer(self, response: httpx.Response, until: float) -> tuple[str, bool]:
        """research's reading (pages.py): a file read is kept; 401 or 403 means no, and so does a server that failed;
        any other 4xx is no file, which allows everything. A body still arriving at `until` is a failed read."""
        status = response.status_code
        try:
            if 200 <= status < 300:
                return self._read_limited(response, until).decode("utf-8", errors="replace"), True
        except httpx.HTTPError as exc:
            log.info("commerce: robots.txt could not be read (%s)", type(exc).__name__)
            return _DISALLOW_ALL, False
        finally:
            response.close()
        if status in (401, 403):
            return _DISALLOW_ALL, True
        if status != 429 and 400 <= status < 500:
            return "", True
        return _DISALLOW_ALL, False

    def _read_limited(self, response: httpx.Response, until: float) -> bytes:
        """Up to MAX_ROBOTS_BYTES, arriving by `until`: httpx's timeout is per read, so a server sending a byte at a
        time never trips it, and this does."""
        chunks: list[bytes] = []
        size = 0
        for chunk in response.iter_bytes():
            chunks.append(chunk)
            size += len(chunk)
            if size >= MAX_ROBOTS_BYTES:
                break
            if self._clock() > until:
                raise httpx.ReadTimeout("robots.txt took too long")
        return b"".join(chunks)[:MAX_ROBOTS_BYTES]

    # --- verdicts ---------------------------------------------------------------------------------------------------

    def _kept(self, key: tuple[str, ...]) -> tuple[bool, str | None]:
        """(whether a verdict is kept for this key, its link). A verdict past its own end is none."""
        text = self.cache.get_text(key)
        if text is None:
            return False, None
        try:
            body = json.loads(text)
            until = datetime.fromisoformat(body["until"])
            url = body["url"]
        except (ValueError, KeyError, TypeError):
            return False, None
        if until.tzinfo is None or until <= self._now() or not (url is None or isinstance(url, str)):
            return False, None
        return True, url

    def _keep(self, key: tuple[str, ...], url: str | None, keep: timedelta) -> None:
        self.cache.put_text(key, json.dumps({"url": url, "until": (self._now() + keep).isoformat()}))
