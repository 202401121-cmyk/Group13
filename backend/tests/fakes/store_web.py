"""A made-up web for commerce's store-page checks (US-24, D-99): each store's robots.txt, Google Safe Browsing,
Firecrawl search and scrape, Serper web search and Jina Reader, each answering as a test sets it. Every request is
recorded, so a test can say what was sent and what was not. Every store, page and product here is made up; nothing is
ever called.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx

from app.commerce.safe_browsing import SafeBrowsing
from app.commerce.store_pages import FirecrawlFirst, StorePageChecker
from app.llm.retry import RetryClock
from app.research.cache import ResearchCache
from app.research.readers import FirecrawlClient, FirecrawlGate, JinaReader
from app.research.reddit import IntervalLimiter
from app.research.serper import SerperSource
from app.research.settings import FirecrawlLimits, ProviderCap
from app.research.usage import UsageBook

NOW = datetime(2026, 11, 25, 6, 0, tzinfo=UTC)
JINA_PREFIX = "https://r.jina.ai/"


def caps(**changed: ProviderCap) -> dict[str, ProviderCap]:
    """Commerce's caps as research.yaml sets them (D-99), with any of them changed."""
    return {
        "serper": ProviderCap(per_search=6, per_day=80, total=2400),
        "firecrawl": ProviderCap(per_search=30, per_day=300, total=9000),
        "firecrawl_pages": ProviderCap(per_search=4, per_day=30),
        "jina": ProviderCap(per_search=10, per_day=200),
        "pages": ProviderCap(per_search=24, per_day=200),
        **changed,
    }


def book(**changed: ProviderCap) -> UsageBook:
    return UsageBook(caps(**changed), path=None)


def product_html(title: str, *, product: str | None = None, ld: bool = True, price: str = "99") -> str:
    """A store's product page: its title, and schema.org Product data naming `product` (the title when None)."""
    data = {"@context": "https://schema.org", "@type": "Product", "name": product or title,
            "offers": {"@type": "Offer", "price": price, "priceCurrency": "INR"}}  # fmt: skip
    script = f'<script type="application/ld+json">{json.dumps(data)}</script>' if ld else ""
    return f"<html><head><title>{title}</title>{script}</head><body><h1>{title}</h1></body></html>"


def scraped(
    url: str, title: str, html: str | None = None, *, status: int | None = 200, final: str | None = None
) -> dict[str, Any]:
    """Firecrawl's data for a scraped page: its raw HTML and metadata (statusCode, url after redirects, title)."""
    meta: dict[str, Any] = {"title": title, "sourceURL": url, "url": final or url}
    if status is not None:
        meta["statusCode"] = status
    return {"rawHtml": html if html is not None else product_html(title), "metadata": meta}


def found(url: str, title: str) -> dict[str, str]:
    """One search result, as Firecrawl search gives it (url, title, description)."""
    return {"url": url, "title": title, "description": f"Buy {title} online."}


class StoreWeb:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.robots: dict[str, tuple[int, str]] = {}  # host -> (status, body); none set: 404, no file, all allowed
        self.robots_moves: dict[str, str] = {}  # host -> where its robots.txt redirects
        self.safe: Callable[[httpx.Request], httpx.Response] = lambda _: httpx.Response(200, json={})
        self.scraped: dict[str, dict[str, Any]] = {}  # page url -> Firecrawl's data
        self.firecrawl_status = 200
        self.firecrawl_found: list[dict[str, str]] = []
        self.mapped: dict[str, list[object]] = {}  # a site's https address -> what Firecrawl's map finds there (D-100)
        self.serper_found: list[dict[str, str]] = []
        self.jina: dict[str, dict[str, Any]] = {}  # page url -> Jina's data
        self.jina_status = 200
        self._lock = threading.Lock()

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handle))

    def handle(self, request: httpx.Request) -> httpx.Response:
        with self._lock:
            self.requests.append(request)
        host, path = request.url.host, request.url.path
        if host == "safebrowsing.googleapis.com":
            return self.safe(request)
        if host == "api.firecrawl.dev":
            return self._firecrawl(request)
        if host == "google.serper.dev":
            organic = [{"link": r["url"], "title": r["title"], "snippet": r["description"]} for r in self.serper_found]
            return httpx.Response(200, json={"organic": organic})
        if host == "r.jina.ai":
            return self._jina(request)
        if path == "/robots.txt":
            if host in self.robots_moves:
                return httpx.Response(301, headers={"location": self.robots_moves[host]})
            status, body = self.robots.get(host, (404, ""))
            return httpx.Response(status, text=body)
        return httpx.Response(404)

    def _firecrawl(self, request: httpx.Request) -> httpx.Response:
        if self.firecrawl_status != 200:
            return httpx.Response(self.firecrawl_status)
        body = json.loads(request.content)
        if request.url.path.endswith("/search"):
            return httpx.Response(200, json={"success": True, "data": {"web": self.firecrawl_found}})
        if request.url.path.endswith("/map"):
            return httpx.Response(200, json={"success": True, "links": self.mapped.get(body["url"], [])})
        data = self.scraped.get(body["url"])
        return httpx.Response(200, json={"success": data is not None, "data": data or {}})

    def _jina(self, request: httpx.Request) -> httpx.Response:
        if self.jina_status != 200:
            return httpx.Response(self.jina_status)
        data = self.jina.get(str(request.url).removeprefix(JINA_PREFIX))
        return httpx.Response(200, json={"code": 200, "data": data}) if data else httpx.Response(422)

    def sent_to(self, host: str) -> list[httpx.Request]:
        with self._lock:
            return [r for r in self.requests if r.url.host == host]

    def robots_asked(self) -> list[str]:
        with self._lock:
            return [r.url.host for r in self.requests if r.url.path == "/robots.txt"]

    def wiring(
        self,
        cache: ResearchCache | None = None,
        *,
        safe_key: str | None = "made-up-key",
        firecrawl_key: str | None = "fc-made-up",
        jina: bool = True,
        now: Callable[[], datetime] = lambda: NOW,
    ) -> tuple[FirecrawlFirst, StorePageChecker]:
        """The store-page search and checker, on this made-up web."""
        http = self.client()
        cache = cache if cache is not None else ResearchCache(folder=None, now=now)
        gate = FirecrawlGate(limits=FirecrawlLimits(per_minute=1000, at_once=8))
        retry = RetryClock(sleep=lambda _: None)  # the one retry, sent at once
        firecrawl = (
            FirecrawlClient(http, firecrawl_key, cache=cache, gate=gate, now=now, retry=retry)
            if firecrawl_key
            else None
        )
        serper = SerperSource(http, "made-up-key", cache=cache, limiter=IntervalLimiter(0), now=now, retry=retry)
        pages = StorePageChecker(
            http,
            cache=cache,
            safe_browsing=SafeBrowsing(http, safe_key),
            firecrawl=firecrawl,
            jina=JinaReader(http, None, retry=retry) if jina else None,
            retry=retry,
            now=now,
        )
        return FirecrawlFirst(firecrawl, serper), pages
