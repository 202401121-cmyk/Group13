"""Stage commerce (US-24): the current price and a buy link for the exact model of the top products.

ShoppingCommerceStage is the real stage. The top 3 shown products are looked up in Serper shopping, in parallel, one
query each, cached in the research cache and counted against Serper's daily cap (DECIDED 1). A listing is kept only
when its title names the product's brand and model (matching.py); its price is read by US-17's reader, and an
unreadable price is no price. Every price carries the time it was fetched (DR-04), and one above the budget is flagged
(DR-01). A product with no accepted listing, or none with a readable price, gets a store search link (no_offer).

Links: a store's own page is kept as it is, and a Google redirect to one is unwrapped. For India, Serper gives only
Google Shopping links (live, 15 Nov 2026). For an offer at a store in config/stores.yaml, one Serper web search a
product looks for the same model on the store's own site, and a page that matches replaces the Google link (links.py,
D-67); the price and its time stay the shopping result's. Otherwise the Google link is kept and its store named
"... via Google Shopping", so the page never passes Google's page off as the store's. The app never checks out.

Every request follows S4-12's one-retry rule, inside this stage's 15 seconds, and takes a Serper unit. When some
look-ups failed and others answered, the coverage note says how many products could not be priced.

CommerceStage is the Sprint 1 stub, kept as the baseline: its run() returns the example offers, for tests and the
sample walk-through only. It is the real stage's fallback, and its fallback is honest: no offers at all, only a store
search for each product (DECIDED 4). Example offers never appear in a live result.
"""

from __future__ import annotations

import functools
import logging
import os
import re
import threading
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, wait
from datetime import datetime
from time import monotonic
from types import SimpleNamespace
from typing import Any, Literal, Protocol

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
from app.contracts.common import STAGE_TIME_LIMITS_S
from app.decide.units import rupee_price
from app.llm.retry import RetryClock
from app.research.cache import ResearchCache
from app.research.client import build_research_client
from app.research.serper import SerperSource, host_of, shared_limiter
from app.research.settings import ResearchSettings, load_research_settings
from app.research.shopping import Listing, SerperShopping, ShoppingResult
from app.research.usage import SearchUsage, UsageBook

from .links import REGION, VIA_GOOGLE, WebSearchLike, load_stores, sites_for, store_key, with_store_pages
from .matching import accepts, query_for, store_search_url, variant_for

log = logging.getLogger(__name__)

MAX_LOOKUPS = 3  # the top 3 shown products (team's choice): at most 3 Serper shopping queries a search
MAX_STORE_SEARCHES = 3  # one web search a priced product, for its stores' own pages: at most 3 more (D-67)
MAX_OFFERS = 3  # a product's offers: one a store, cheapest first
LOOKUP_DEADLINE_S = 12.0  # inside the stage's 15 s: a late look-up is no answer, and the others are kept
STAGE_LIMIT_S = STAGE_TIME_LIMITS_S[StageName.COMMERCE]
MARGIN_S = 1.0  # the stage hands its answer back this long before its limit
MAX_NOTES_KEPT = 64
WEB_MIN_S = 3.0  # the store-page searches are sent only with this much of the stage left: else they'd be late
# When every look-up failed, the note gives the likeliest cause first.
_REASON_ORDER = ("blocked", "rate_limited", "timeout", "error", "quota_exhausted")
Availability = Literal["in_stock", "out_of_stock", "unknown"]
_GOOGLE = re.compile(r"(?:^|\.)google\.[a-z]{2,3}(?:\.[a-z]{2})?$")  # google.com, google.co.in: not a store's page


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
        web: WebSearchLike | None = None,
        usage: UsageBook | None = None,
        settings: ResearchSettings | None = None,
    ) -> None:
        self._shopping = shopping
        self._web = web  # with an injected shopping client, store pages are looked up only if this is given too
        self._usage = usage
        self._settings = settings
        self._lock = threading.Lock()
        self._notes: OrderedDict[str, str] = OrderedDict()

    def run(self, inp: CommerceInput, ctx: StageContext) -> CommerceResult:
        started = monotonic()
        ends = started + max(0.0, min(STAGE_LIMIT_S, ctx.seconds_left) - MARGIN_S)
        lookups_end = min(ends, started + LOOKUP_DEADLINE_S)
        # A retry goes only while its answer can still be used: a shopping look-up's until the look-ups' deadline,
        # a store-page search's until the stage's.
        shopping_retry = RetryClock(time_left=lambda: lookups_end - monotonic())
        web_retry = RetryClock(time_left=lambda: ends - monotonic())
        top = inp.products[:MAX_LOOKUPS]
        shopping, web, client = self._wire(shopping_retry, web_retry)
        pool = ThreadPoolExecutor(max_workers=len(top), thread_name_prefix="commerce")
        pending: list[Future[Any]] = []
        try:
            usage = (self._usage or _shared_book()).search()
            futures = [pool.submit(shopping.search, query_for(product), usage) for product in top]
            done, _ = wait(futures, timeout=max(0.0, lookups_end - monotonic()))
            pending += [future for future in futures if future not in done]
            late = ShoppingResult(failure="timeout", detail="shopping: no answer in time")
            answers = [future.result() if future in done else late for future in futures]
            failed = sum(1 for answer in answers if answer.failure is not None)
            if failed == len(answers):
                reasons = {answer.failure for answer in answers}
                cause = next((r for r in _REASON_ORDER if r in reasons), "error")
                log.info("commerce: no price look-up answered (%s)", cause)
                raise PricesUnavailable("no price look-up answered") from _CAUSES[cause]()
            if failed:
                self._keep(ctx.search_id, _not_priced(failed, len(answers)))
            offers = [_offers(product, answer, inp) for product, answer in zip(top, answers, strict=True)]
            if web is not None and ends - monotonic() >= WEB_MIN_S:
                try:
                    offers = _with_store_pages(top, offers, web, usage, pool, ends, pending)
                except Exception as exc:  # the prices are good already: their Google links stay
                    log.warning("commerce: the store page search failed (%s); Google links stay", type(exc).__name__)
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
            if client is not None:
                _close_when_done(client, pending)
        kept = [offer for each in offers for offer in each]
        priced = {offer.product_id for offer in kept if offer.price is not None}  # a price the card can show
        no_offer = {p.id: store_search_url(p) for p in inp.products if p.id not in priced}
        return CommerceResult.model_validate({"offers": kept, "no_offer": no_offer})

    def coverage_note(self, search_id: str) -> str | None:
        """How many products could not be priced, when some look-ups failed and others answered. Read once."""
        with self._lock:
            return self._notes.pop(search_id, None)

    def _keep(self, search_id: str, note: str) -> None:
        with self._lock:
            self._notes[search_id] = note
            self._notes.move_to_end(search_id)
            while len(self._notes) > MAX_NOTES_KEPT:
                self._notes.popitem(last=False)

    def fallback(self, inp: CommerceInput, ctx: StageContext, error: Exception) -> CommerceResult:
        """The stub's honest fallback: no offers, a store search for each product. Never the example offers."""
        return CommerceStage().fallback(inp, ctx, error)

    def _wire(
        self, shopping_retry: RetryClock, web_retry: RetryClock
    ) -> tuple[ShoppingLike, WebSearchLike | None, httpx.Client | None]:
        if self._shopping is not None:
            return self._shopping, self._web, None
        settings = self._settings or load_research_settings()
        client = build_research_client()
        load_dotenv()
        key = os.environ.get("SERPER_API_KEY", "").strip() or None
        cache = ResearchCache(enabled=settings.cache_on)
        limiter = shared_limiter(settings.serper_interval_s)
        shopping = SerperShopping(client, key, cache=cache, limiter=limiter, retry=shopping_retry)
        web = SerperSource(client, key, cache=cache, limiter=limiter, retry=web_retry)
        return shopping, web, client


def _close_when_done(client: httpx.Client, pending: list[Future[Any]]) -> None:
    """Close the client now, or when the last late request is over: it is never left open."""
    if not pending:
        client.close()
        return
    left = [len(pending)]
    lock = threading.Lock()

    def one_done(_: Future[Any]) -> None:
        with lock:
            left[0] -= 1
            last = left[0] == 0
        if last:
            client.close()

    for future in pending:
        future.add_done_callback(one_done)


def _not_priced(failed: int, asked: int) -> str:
    """The coverage note's sentence for look-ups that failed while others answered (S4-26)."""
    products = "product" if asked == 1 else "products"
    return f"We couldn't check prices for {failed} of the top {asked} {products}, because the price search failed."


def _with_store_pages(
    top: list[Product],
    offers: list[list[Offer]],
    web: WebSearchLike,
    usage: SearchUsage,
    pool: ThreadPoolExecutor,
    ends: float,
    pending: list[Future[Any]],
) -> list[list[Offer]]:
    """Each product's offers, with Google Shopping links at mapped stores swapped for the store's own page where one
    web search a product finds it (links.py). At most MAX_STORE_SEARCHES a search; a late answer changes nothing."""
    stores = load_stores()
    jobs: list[tuple[int, Future[Any]]] = []
    for index, (product, each) in enumerate(zip(top, offers, strict=True)):
        sites = sites_for(each, stores)
        if not sites or len(jobs) >= MAX_STORE_SEARCHES:
            continue
        domains = [store.domain for store in sites]
        jobs.append((index, pool.submit(web.search, query_for(product), REGION, usage, domains)))
    if not jobs:
        return offers
    done, _ = wait([future for _, future in jobs], timeout=max(0.0, ends - monotonic()))
    pending += [future for _, future in jobs if future not in done]
    swapped = list(offers)
    for index, future in jobs:
        if future not in done:
            continue
        try:
            found = future.result()
        except Exception as exc:  # one search's error keeps that product's Google links, and the others' pages
            log.warning("commerce: a store page search failed (%s); its Google links stay", type(exc).__name__)
            continue
        failure = getattr(found, "failure", None)
        if failure is not None:
            log.info("commerce: a store page search failed (%s); its Google links stay", failure.reason)
            continue
        swapped[index] = with_store_pages(top[index], offers[index], getattr(found, "sources", ()), stores)
    return swapped


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
    known = load_stores()
    for offer, _ in found:
        store = store_key(offer.store, known)  # "Flipkart" and "Flipkart via Google Shopping" are one store
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
    fallback_is_example = False  # a store search link for each product, no offers (S4-35)

    def run(self, inp: CommerceInput, ctx: StageContext) -> CommerceResult:
        return examples.commerce()

    def fallback(self, inp: CommerceInput, ctx: StageContext, error: Exception) -> CommerceResult:
        # No prices is honest; a wrong price is not. Each product still gets a store search (US-24).
        return CommerceResult.model_validate({"no_offer": {p.id: store_search_url(p) for p in inp.products}})
