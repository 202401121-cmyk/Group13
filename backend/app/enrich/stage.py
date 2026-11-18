"""Stage enrich (US-41, FR-24): prices from store listings for products held back only by an unknown price.

PriceLookupStage is the real stage (P-34, first slice). The pipeline picks the targets in code (targets.py) and hands
the stage the kept products first (`use_products`), as only a Product has the brand matching needs. Each target is
looked up in Serper shopping, one query each, in parallel, within the stage's 20 seconds. The query is the same one
commerce sends (brand and name), cached in the research cache as commerce's are. Enrich has its own room of
MAX_ENRICH_SEARCHES Serper calls a search, apart from research's (D-78); the day and the total are shared. A cached
answer or a capped search sends nothing, and a look-up whose time is up never takes a unit.

A listing counts only when its title names the product's brand and model (commerce's `accepts`) and its price is read
by US-17's reader: an EMI, a range or any unreadable price is no price. The lowest accepted price becomes one claim on
`price_inr`: its quote is the listing's own words (title, price and store, as one line), and its source is that
listing (type retailer, its link, the store's name, the time Serper answered). The pipeline runs verify again with
these claims, so decide sees the price, and its note counts the prices verify took. The look-ups keep LATER_S of
the search for verify and decide, and none is sent without MIN_LOOKUP_S. No LLM is called. Any failure gives no new
claims, and the search goes on.

EnrichStage is the Sprint 1 stub, kept as the fallback: it adds nothing, so it can never make a result a sample.
"""

from __future__ import annotations

import functools
import hashlib
import logging
import os
import threading
from collections import OrderedDict
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic
from typing import Any, Protocol

import httpx
from dotenv import load_dotenv
from pydantic import ValidationError

from app.commerce.links import VIA_GOOGLE
from app.commerce.matching import accepts, query_for
from app.commerce.stage import _close_when_done, _store_link
from app.contracts import (
    Claim,
    ClaimType,
    EnrichInput,
    EnrichResult,
    EnrichTarget,
    Product,
    Source,
    SourceType,
    StageContext,
    StageName,
)
from app.contracts.common import STAGE_TIME_LIMITS_S
from app.contracts.enrich import MAX_ENRICH_SEARCHES
from app.contracts.research import DEFAULT_TIER, MAX_SOURCE_CHARS
from app.decide.rules import PRICE_KEY
from app.decide.units import rupee_price
from app.llm.retry import RetryClock
from app.research.cache import ResearchCache
from app.research.client import build_research_client
from app.research.serper import host_of, shared_limiter
from app.research.settings import ResearchSettings, load_research_settings
from app.research.shopping import Listing, SerperShopping, ShoppingResult
from app.research.usage import SearchUsage, UsageBook

log = logging.getLogger(__name__)

STAGE_LIMIT_S = STAGE_TIME_LIMITS_S[StageName.ENRICH]
LOOKUP_DEADLINE_S = 15.0  # inside the stage's 20 s: a late look-up is no answer, and the others are kept
MARGIN_S = 1.0  # the stage hands its answer back this long before its limit
LATER_S = 6.0  # kept for verify again (2 s), decide (2 s) and a little over: enrich never eats their time
MIN_LOOKUP_S = 3.0  # with less than this for the look-ups, none is sent: its answer would come too late
MAX_KEPT = 64  # searches whose products, sources or note are kept at once
MAX_QUOTE = 600  # Claim.quote


class ShoppingLike(Protocol):
    def search(self, query: str, usage: SearchUsage) -> ShoppingResult: ...


@dataclass(frozen=True)
class Found:
    """One price from a store listing: the claim, and the listing it came from as its source."""

    claim: Claim
    source: Source


@functools.cache
def _shared_book() -> UsageBook:
    """One book per process. Counts merge with research's and commerce's through usage.json: one daily cap for all."""
    return UsageBook(load_research_settings().caps)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class _Kept:
    """Per-search values, at most MAX_KEPT searches, read once."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: OrderedDict[str, Any] = OrderedDict()

    def put(self, search_id: str, value: Any) -> None:
        with self._lock:
            self._items[search_id] = value
            self._items.move_to_end(search_id)
            while len(self._items) > MAX_KEPT:
                self._items.popitem(last=False)

    def pop(self, search_id: str) -> Any:
        with self._lock:
            return self._items.pop(search_id, None)


class PriceLookupStage:
    """Prices from Serper shopping listings for the targets the pipeline picked. No LLM call."""

    name = StageName.ENRICH
    is_placeholder = False

    def __init__(
        self,
        shopping: ShoppingLike | None = None,
        *,
        usage: UsageBook | None = None,
        settings: ResearchSettings | None = None,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._shopping = shopping
        self._usage = usage
        self._settings = settings
        self._now = now
        self._products = _Kept()
        self._sources = _Kept()

    # --- what the pipeline tells it, and reads back ---------------------------------------------------------------

    def use_products(self, search_id: str, products: Sequence[Product]) -> None:
        """The kept products, so a target's id finds its brand and model. Read once, by run()."""
        self._products.put(search_id, {product.id: product for product in products})

    def sources_for(self, search_id: str) -> list[Source]:
        """The listings this search's new claims came from. Read once; anything else kept for it is dropped too."""
        self._products.pop(search_id)
        return list(self._sources.pop(search_id) or [])

    # --- the stage ------------------------------------------------------------------------------------------------

    def run(self, inp: EnrichInput, ctx: StageContext) -> EnrichResult:
        products: dict[str, Product] = self._products.pop(ctx.search_id) or {}
        pairs = [
            (target, products[target.product_id])
            for target in inp.targets
            if target.criterion_key == PRICE_KEY and target.product_id in products
        ][:MAX_ENRICH_SEARCHES]
        if not pairs:
            return EnrichResult()
        started = monotonic()
        room = min(STAGE_LIMIT_S - MARGIN_S, LOOKUP_DEADLINE_S, ctx.seconds_left - LATER_S)
        if room < MIN_LOOKUP_S:  # a short search: the price can wait, ranking what is known can't
            log.info("enrich: too little time left to look prices up")
            return EnrichResult()
        lookups_end = started + room
        retry = RetryClock(time_left=lambda: lookups_end - monotonic())  # a retry goes only while it can be used
        shopping, client = self._wire(retry)
        usage = (self._usage or _shared_book()).search(per_search={"serper": MAX_ENRICH_SEARCHES})  # its own room
        pool = ThreadPoolExecutor(max_workers=len(pairs), thread_name_prefix="enrich")
        pending: list[Future[Any]] = []
        try:
            futures = [pool.submit(look_up, shopping, query_for(product), usage, lookups_end) for _, product in pairs]
            done, _ = wait(futures, timeout=max(0.0, lookups_end - monotonic()))
            pending += [future for future in futures if future not in done]
            late = ShoppingResult(failure="timeout", detail="shopping: no answer in time")
            answers = [future.result() if future in done else late for future in futures]
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
            if client is not None:
                _close_when_done(client, pending)
        found = [self._price(target, product, answer) for (target, product), answer in zip(pairs, answers, strict=True)]
        kept = [each for each in found if each is not None]
        failed = sum(1 for answer in answers if answer.failure is not None)
        if failed:  # the counts only, never a query
            log.info("enrich: %d of %d price look-ups failed", failed, len(answers))
        if kept:
            self._sources.put(ctx.search_id, list({each.source.id: each.source for each in kept}.values()))
        sent = min(usage.used.get("serper", 0), MAX_ENRICH_SEARCHES)
        return EnrichResult(
            new_claims=[each.claim for each in kept],
            attempted=[target for target, _ in pairs],
            searches_used=sent,
            llm_calls_used=0,
        )

    def fallback(self, inp: EnrichInput, ctx: StageContext, error: Exception) -> EnrichResult:
        """The stub's: nothing added, so the search goes on as if enrich had found nothing."""
        self._products.pop(ctx.search_id)
        return EnrichStage().fallback(inp, ctx, error)

    def _price(self, target: EnrichTarget, product: Product, answer: ShoppingResult) -> Found | None:
        if answer.failure is not None or answer.fetched_at is None:
            return None
        best: tuple[float, int, Listing] | None = None
        for listing in answer.listings:
            amount = rupee_price(listing.price) if listing.price else None
            if amount is None or _store_link(listing) is None or not accepts(product, listing.title):
                continue
            if best is None or (amount, listing.position) < (best[0], best[1]):
                best = (amount, listing.position, listing)
        if best is None:
            return None
        return listing_claim(target, product, best[2], best[0], answer.fetched_at, self._now())

    def _wire(self, retry: RetryClock) -> tuple[ShoppingLike, httpx.Client | None]:
        if self._shopping is not None:
            return self._shopping, None
        settings = self._settings or load_research_settings()
        client = build_research_client()
        load_dotenv()
        key = os.environ.get("SERPER_API_KEY", "").strip() or None
        cache = ResearchCache(enabled=settings.cache_on)
        limiter = shared_limiter(settings.serper_interval_s)
        return SerperShopping(client, key, cache=cache, limiter=limiter, retry=retry), client


def look_up(shopping: ShoppingLike, query: str, usage: SearchUsage, ends: float) -> ShoppingResult:
    """One look-up, unless its time is already up: then nothing is asked, so no Serper unit is taken (D-78)."""
    if monotonic() >= ends:
        return ShoppingResult(failure="timeout", detail="shopping: no time left to ask")
    return shopping.search(query, usage)


def prices_note(count: int) -> str:
    """The coverage note's sentence (P-34 item 5). The pipeline says it for the prices verify took, not those found."""
    return "1 price came from a store listing." if count == 1 else f"{count} prices came from store listings."


def listing_line(listing: Listing, store: str) -> str:
    """The listing's own words as one line: its title, its price and its store. The quote and the source's text."""
    return " ".join(f"{listing.title}, {listing.price}, at {store}".split())


def listing_claim(
    target: EnrichTarget, product: Product, listing: Listing, amount: float, fetched_at: datetime, now: datetime
) -> Found | None:
    """A claim on the price from one listing, and the listing as its source. None if the contract refuses either."""
    linked = _store_link(listing)
    if linked is None:
        return None
    link, store = linked
    line = listing_line(listing, store)[: min(MAX_QUOTE, MAX_SOURCE_CHARS)]
    host = (host_of(link) or "").removeprefix("www.")
    # The drawer names a source by its site. A Google Shopping link's host is Google's, so the store's own name
    # ("Croma via Google Shopping") names it instead; trust still reads the link's host (app/verify/trust.py).
    site = store if store.endswith(VIA_GOOGLE) or not host else host
    # The listing's words are part of the id, so it never takes the id of a page research read at the same address,
    # whose text holds other words (build rule 4), nor of another product's listing behind the same link.
    try:
        source = Source(
            id=f"src_{hashlib.sha256(f'{link}|{line}'.encode()).hexdigest()[:16]}",
            url=link,
            type=SourceType.RETAILER,
            tier=DEFAULT_TIER[SourceType.RETAILER],
            title=listing.title[:300],
            text=line,
            text_hash=hashlib.sha256(line.encode()).hexdigest(),
            fetched_at=fetched_at,
            site=site[:120],
            via="api",
        )
        key = f"{source.id}|{product.id}|{PRICE_KEY}|{line}"
        claim = Claim(
            id=f"clm_{hashlib.sha256(key.encode()).hexdigest()[:24]}",
            source_id=source.id,
            source_url=link,
            product_mention=target.product_name,
            criterion_key=PRICE_KEY,
            value=float(amount),
            quote=line,
            claim_type=ClaimType.EXPERIENCE,  # a shop's listing is unchecked: never a spec (app/extract/tiers.py)
            polarity="neutral",
            retrieved_at=fetched_at,
            extracted_at=max(now, fetched_at),
        )
    except (ValidationError, ValueError):
        return None
    return Found(claim=claim, source=source)


class EnrichStage:
    """Enriches nothing: no searches, no LLM calls, no new claims."""

    name = StageName.ENRICH
    is_placeholder = False  # it adds nothing, so it can't make a result a sample (S4-34)
    fallback_is_example = False  # an empty result (S4-35)

    def run(self, inp: EnrichInput, ctx: StageContext) -> EnrichResult:
        return EnrichResult()

    def fallback(self, inp: EnrichInput, ctx: StageContext, error: Exception) -> EnrichResult:
        return EnrichResult()
