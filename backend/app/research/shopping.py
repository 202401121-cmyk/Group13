"""Serper shopping (US-24): store listings for one product, with title, store, link and price.

POST https://google.serper.dev/shopping, as /search: the key in X-API-KEY, a JSON body with the query, country India
("gl": "in") and English. Each result carries title, source (the store), link, price (text, such as "₹64,990"),
delivery and rating (serper.dev, read 15 Nov 2026; docs/api-setup.md). For India the link is a Google Shopping page
for the offer, never the store's own (S4-14's live run): commerce looks the store's own page up separately (links.py).

Like web search, every request sent counts one Serper unit against the daily and total caps (research.yaml); a
capped search sends nothing. A timeout, a 5xx or a broken connection is tried once more, and a 429 only after the wait
it states; no other 4xx is (S4-12's rule). A retry is a request like any other, so it takes a unit too, and it goes
only if the caller's time allows. An answer is kept in the research cache with the time it was fetched, so a cached
price keeps its true age (DR-04). Nothing here raises: a failure comes back as a reason, as serper.py reads them. Only
listings are kept: no page text, nothing from Reddit, and nothing the shopper typed beyond the product's name.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from app.llm.retry import RetryClock

from .cache import ResearchCache
from .reddit.base import FailureReason, IntervalLimiter
from .retrying import send_once_more
from .serper import SHARED_LIMITER, TIMEOUT_S, host_of
from .usage import SearchUsage

log = logging.getLogger(__name__)

SHOPPING_URL = "https://google.serper.dev/shopping"
COUNTRY = "in"
MAX_LISTINGS = 40  # Serper answers about 40; more is never read
MAX_TEXT = 300  # an Offer's listing_title limit, and plenty for the other fields
MAX_LINK = 2000  # links stay whole: a cut one still looks valid. Longer is refused


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Listing:
    """One shopping result, as Serper gave it, trimmed. Nothing is read into it yet: matching and prices come later."""

    title: str
    link: str
    store: str = ""
    price: str = ""
    delivery: str = ""
    position: int = 0


@dataclass(frozen=True)
class ShoppingResult:
    listings: tuple[Listing, ...] = ()
    fetched_at: datetime | None = None  # when Serper answered: a cached answer keeps its first time
    failure: FailureReason | None = None
    detail: str = ""
    from_cache: bool = False


def _failure(reason: FailureReason, detail: str) -> ShoppingResult:
    return ShoppingResult(failure=reason, detail=f"shopping: {detail}")


class SerperShopping:
    def __init__(
        self,
        http: httpx.Client,
        api_key: str | None,
        *,
        cache: ResearchCache,
        limiter: IntervalLimiter = SHARED_LIMITER,
        now: Callable[[], datetime] = _utc_now,
        retry: RetryClock | None = None,
    ) -> None:
        self.http = http
        self._api_key = api_key
        self.cache = cache
        self.limiter = limiter
        self._now = now
        self.retry = retry if retry is not None else RetryClock()  # the one retry (S4-12)

    def __repr__(self) -> str:
        return f"SerperShopping(key_set={self._api_key is not None})"  # never the key, not even in a traceback

    def search(self, query: str, usage: SearchUsage) -> ShoppingResult:
        """Listings for one product's name. A cached answer sends nothing; a capped search sends nothing either."""
        key = ("serper_shopping", query, COUNTRY)
        cached = _from_text(self.cache.get_text(key))
        if cached is not None:
            return cached
        if not self._api_key:
            return _failure("blocked", "SERPER_API_KEY is not set")
        if not usage.take("serper"):
            return _failure("quota_exhausted", "a Serper cap is reached")
        result = self._send(query, usage)
        if result.failure is None:
            self.cache.put_text(key, _to_text(result))
        return result

    def _send(self, query: str, usage: SearchUsage) -> ShoppingResult:
        body = {"q": query, "gl": COUNTRY, "hl": "en"}

        def send() -> httpx.Response:
            self.limiter.wait()  # inside, so the retry keeps to Serper's pace too
            return self.http.post(
                SHOPPING_URL, json=body, headers={"X-API-KEY": self._api_key or ""}, timeout=TIMEOUT_S
            )

        # One retry after a timeout, a 5xx or a broken connection, or a 429 that says how long (S4-12). It is a
        # request like any other, so it takes one from the cap; with none left, or no time, it doesn't go.
        response = send_once_more(send, self.retry, TIMEOUT_S, lambda: usage.take("serper"), "shopping")
        if isinstance(response, httpx.TimeoutException):
            return _failure("timeout", f"no reply within {TIMEOUT_S:g}s")
        if isinstance(response, httpx.HTTPError):
            return _failure("error", f"request failed ({type(response).__name__})")
        status = response.status_code
        if status in (401, 403):
            return _failure("blocked", f"HTTP {status} (refused)")
        if status == 402:
            return _failure("quota_exhausted", "HTTP 402 (no credits left)")
        if status == 429:
            return _failure("rate_limited", "HTTP 429 (rate limited)")
        if not 200 <= status < 300:
            return _failure("error", f"HTTP {status}")
        try:
            reply = response.json()
        except ValueError:
            return _failure("error", "the reply was not JSON")
        items = reply.get("shopping") if isinstance(reply, dict) else None
        if not isinstance(items, list):
            return _failure("error", "the reply had no shopping list")
        return ShoppingResult(listings=read_listings(items), fetched_at=self._now())


def read_listings(items: list[Any]) -> tuple[Listing, ...]:
    """Lenient: a result needs a title and an http(s) link, or it is skipped. Every text is trimmed to MAX_TEXT."""
    found: list[Listing] = []
    for item in items[:MAX_LISTINGS]:
        if not isinstance(item, dict):
            continue
        title = _text(item.get("title"))
        raw_link = item.get("link")
        link = raw_link.strip() if isinstance(raw_link, str) else ""
        if not title or len(link) > MAX_LINK or host_of(link) is None:
            continue
        position = item.get("position")
        found.append(
            Listing(
                title=title,
                link=link,
                store=_text(item.get("source")),
                price=_text(item.get("price")),
                delivery=_text(item.get("delivery")),
                position=position if isinstance(position, int) and not isinstance(position, bool) else len(found) + 1,
            )
        )
    return tuple(found)


def _text(value: object) -> str:
    if isinstance(value, bool) or value is None:
        return ""
    if isinstance(value, int | float):
        value = str(value)
    return " ".join(value.split())[:MAX_TEXT] if isinstance(value, str) else ""


def _to_text(result: ShoppingResult) -> str:
    return json.dumps(
        {
            "fetched_at": result.fetched_at.isoformat() if result.fetched_at else None,
            "listings": [listing.__dict__ for listing in result.listings],
        },
        ensure_ascii=False,
    )


def _from_text(text: str | None) -> ShoppingResult | None:
    """A cached answer, or None if there is none or it can't be read (then it is asked again)."""
    if not text:
        return None
    try:
        data = json.loads(text)
        fetched = datetime.fromisoformat(data["fetched_at"])
        listings = tuple(Listing(**item) for item in data["listings"])
    except (ValueError, TypeError, KeyError):
        log.info("research: a cached shopping answer could not be read; asking again")
        return None
    if fetched.tzinfo is None:
        return None
    return ShoppingResult(listings=listings, fetched_at=fetched, from_cache=True)
