"""Stage commerce (US-24): the current price and a buy link for the exact model of the top products.

ShoppingCommerceStage is the real stage. The top 3 shown products are looked up in Serper shopping, in parallel, one
query each, cached in the research cache and counted against Serper's daily cap (DECIDED 1). A listing is kept only
when its title names the product's brand and model (matching.py); its price is read by US-17's reader, and an
unreadable price is no price. Every price carries the time it was fetched (DR-04), and one above the budget is flagged
(DR-01). A product with no accepted listing, or none with a readable price, gets a store search link (no_offer).

Links: a store's own page is kept as it is, and a Google redirect to one is unwrapped. For India, Serper gives only
Google Shopping links (live, 15 Nov 2026); such a link is kept and its store named "... via Google Shopping", so the
page never passes Google's page off as the store's. The app never checks out for anyone.

CommerceStage is the Sprint 1 stub, kept as the baseline: its run() returns the example offers, for tests and the
sample walk-through only. It is the real stage's fallback, and its fallback is honest: no offers at all, only a store
search for each product (DECIDED 4). Example offers never appear in a live result.
"""

from __future__ import annotations

import functools
import logging
import os
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime
from types import SimpleNamespace
from typing import Literal, Protocol

import httpx
from dotenv import load_dotenv
from pydantic import ValidationError

from app.contracts import (
    Budget,
    CommerceInput,
    CommerceResult,
    Money,
    Offer,
    Product,
    StageContext,
    StageName,
    examples,
)
from app.decide.units import rupee_price
from app.research.cache import ResearchCache
from app.research.client import build_research_client
from app.research.serper import host_of, shared_limiter
from app.research.settings import ResearchSettings, load_research_settings
from app.research.shopping import Listing, SerperShopping, ShoppingResult
from app.research.usage import SearchUsage, UsageBook

from .matching import accepts, query_for, store_search_url, variant_for

log = logging.getLogger(__name__)

MAX_LOOKUPS = 3  # the top 3 shown products (team's choice): at most 3 Serper queries a search
MAX_OFFERS = 3  # a product's offers: one a store, cheapest first
LOOKUP_DEADLINE_S = 12.0  # inside the stage's 15 s: a late look-up is no answer, and the others are kept
# When every look-up failed, the note gives the likeliest cause first.
_REASON_ORDER = ("blocked", "rate_limited", "timeout", "error", "quota_exhausted")
Availability = Literal["in_stock", "out_of_stock", "unknown"]
_GOOGLE = re.compile(r"(?:^|\.)google\.[a-z]{2,3}(?:\.[a-z]{2})?$")  # google.com, google.co.in: not a store's page
VIA_GOOGLE = " via Google Shopping"


class ShoppingLike(Protocol):
    def search(self, query: str, usage: SearchUsage) -> ShoppingResult: ...


class PricesUnavailable(Exception):
    """Every price look-up failed: the runner falls back to store searches, and its note says why (US-31)."""


class _Refused(Exception):
    """An HTTP status the runner's plain reasons can read (pipeline.plain_reason looks at .response.status_code)."""

    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
        self.response = SimpleNamespace(status_code=status)


# What a failed look-up reads as in the coverage note: took too long, could not be reached, busy, refused.
_CAUSES: dict[str, Callable[[], Exception]] = {
    "timeout": lambda: TimeoutError("Serper shopping did not answer in time"),
    "error": lambda: ConnectionError("Serper shopping could not be reached"),
    "rate_limited": lambda: _Refused(429),
    "blocked": lambda: _Refused(401),
    "quota_exhausted": lambda: _Refused(403),  # our own cap or no credits: no more requests may go
}


@functools.cache
def _shared_book() -> UsageBook:
    """One book per process. Counts merge with research's through usage.json, so Serper's daily cap holds for both."""
    return UsageBook(load_research_settings().caps)


class ShoppingCommerceStage:
    """Prices and buy links for the top 3 products from Serper shopping, or a store search where none matches."""

    name = StageName.COMMERCE
    is_placeholder = False

    def __init__(
        self,
        shopping: ShoppingLike | None = None,
        *,
        usage: UsageBook | None = None,
        settings: ResearchSettings | None = None,
    ) -> None:
        self._shopping = shopping
        self._usage = usage
        self._settings = settings

    def run(self, inp: CommerceInput, ctx: StageContext) -> CommerceResult:
        top = inp.products[:MAX_LOOKUPS]
        shopping, client = self._wire()
        pool = ThreadPoolExecutor(max_workers=len(top), thread_name_prefix="commerce")
        finished = False
        try:
            usage = (self._usage or _shared_book()).search()
            futures = [pool.submit(shopping.search, query_for(product), usage) for product in top]
            done, _ = wait(futures, timeout=max(0.0, min(LOOKUP_DEADLINE_S, ctx.seconds_left - 1.0)))
            finished = len(done) == len(futures)
            late = ShoppingResult(failure="timeout", detail="shopping: no answer in time")
            answers = [future.result() if future in done else late for future in futures]
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
            if client is not None and finished:
                client.close()  # one a late look-up still holds is left for it to finish with
        if all(answer.failure is not None for answer in answers):
            reasons = {answer.failure for answer in answers}
            cause = next((r for r in _REASON_ORDER if r in reasons), "error")
            log.info("commerce: no price look-up answered (%s)", cause)
            raise PricesUnavailable("no price look-up answered") from _CAUSES[cause]()
        offers = [
            offer for product, answer in zip(top, answers, strict=True) for offer in _offers(product, answer, inp)
        ]
        priced = {offer.product_id for offer in offers if offer.price is not None}  # a price the card can show
        no_offer = {p.id: store_search_url(p) for p in inp.products if p.id not in priced}
        return CommerceResult.model_validate({"offers": offers, "no_offer": no_offer})

    def fallback(self, inp: CommerceInput, ctx: StageContext, error: Exception) -> CommerceResult:
        """The stub's honest fallback: no offers, a store search for each product. Never the example offers."""
        return CommerceStage().fallback(inp, ctx, error)

    def _wire(self) -> tuple[ShoppingLike, httpx.Client | None]:
        if self._shopping is not None:
            return self._shopping, None
        settings = self._settings or load_research_settings()
        client = build_research_client()
        load_dotenv()
        key = os.environ.get("SERPER_API_KEY", "").strip() or None
        cache = ResearchCache(enabled=settings.cache_on)
        return SerperShopping(client, key, cache=cache, limiter=shared_limiter(settings.serper_interval_s)), client


def _offers(product: Product, answer: ShoppingResult, inp: CommerceInput) -> list[Offer]:
    """The listings that are this exact model, as offers: one a store, cheapest first, at most MAX_OFFERS."""
    if answer.failure is not None or answer.fetched_at is None:
        return []
    found: list[tuple[Offer, int]] = []
    for listing in answer.listings:
        offer = _offer(product, listing, answer.fetched_at, inp.budget)
        if offer is not None:
            found.append((offer, listing.position))
    found.sort(key=lambda pair: (pair[0].price is None, pair[0].price.amount if pair[0].price else 0.0, pair[1]))
    kept: list[Offer] = []
    stores: set[str] = set()
    for offer, _ in found:
        store = offer.store.casefold()
        if store in stores:
            continue
        stores.add(store)
        kept.append(offer)
    return kept[:MAX_OFFERS]


def _store_link(listing: Listing) -> tuple[str, str] | None:
    """(the link, the store's name) for an offer, or None for a link we can't send anyone to.

    The store's own page as it is. A Google redirect that names the store's page is unwrapped to it. A Google Shopping
    link for this one offer (all Serper gives for India, 15 Nov 2026) is kept, and the store is named "... via Google
    Shopping", so the page never passes it off as the store's own. Any other Google link is refused.
    """
    host = host_of(listing.link)
    if host is None:
        return None
    store = listing.store or host
    if not _GOOGLE.search(host) or host == "store.google.com":  # Google's own store sells its own products
        return listing.link, store[:80]
    url = httpx.URL(listing.link)
    target = url.params.get("url") or url.params.get("q") if url.path == "/url" else None
    target_host = host_of(target) if target else None
    if target and target_host and not _GOOGLE.search(target_host):
        return target, (listing.store or target_host)[:80]
    if url.path == "/search" and url.params.get("ibp") == "oshop" and url.params.get("prds"):
        return listing.link, store[: 80 - len(VIA_GOOGLE)] + VIA_GOOGLE
    return None


def _offer(product: Product, listing: Listing, as_of: datetime, budget: Budget | None) -> Offer | None:
    linked = _store_link(listing)
    if linked is None or not accepts(product, listing.title):
        return None
    link, store = linked
    amount = rupee_price(listing.price)
    price = Money(amount=amount) if amount is not None else None
    variant = variant_for(product, listing.title)
    try:
        return Offer(
            product_id=product.id,
            variant_id=variant.id if variant is not None else None,
            store=store,
            url=link,
            listing_title=listing.title[:300],
            price=price,
            availability=_availability(listing),
            as_of=as_of,
            title_matched=True,
            variant_matched=variant is not None,
            over_budget=_over_budget(price, budget),
        )
    except ValidationError:
        return None  # a link the contract refuses is no offer


def _availability(listing: Listing) -> Availability:
    said = " ".join(f"{listing.delivery} {listing.title}".casefold().split())
    if any(words in said for words in ("out of stock", "not in stock", "sold out", "unavailable")):
        return "out_of_stock"
    if "in stock" in said:
        return "in_stock"
    return "unknown"


def _over_budget(price: Money | None, budget: Budget | None) -> bool:
    """DR-01: above the rubric's maximum, in the same currency. No price, or no maximum, is never over."""
    top = budget.max if budget is not None else None
    return price is not None and top is not None and price.currency == top.currency and price.amount > top.amount


class CommerceStage:
    """The Sprint 1 stub: the two example offers and one store-search link. Never used for a live search."""

    name = StageName.COMMERCE
    is_placeholder = True

    def run(self, inp: CommerceInput, ctx: StageContext) -> CommerceResult:
        return examples.commerce()

    def fallback(self, inp: CommerceInput, ctx: StageContext, error: Exception) -> CommerceResult:
        # No prices is honest; a wrong price is not. Each product still gets a store search (US-24).
        return CommerceResult.model_validate({"no_offer": {p.id: store_search_url(p) for p in inp.products}})
