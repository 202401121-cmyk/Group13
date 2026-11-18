"""The store's own page for an offer (US-24, D-67).

Serper shopping gives only Google Shopping links for India (S4-14, 15 Nov 2026), and US-24 says a link opens the
store's own page. So for a product whose offers include a Google Shopping link at a store in config/stores.yaml, one
Serper web search asks for the product on those stores' own sites ("<name> (site:flipkart.com OR site:amazon.in)").
A result replaces a store's Google link only when it is on that store's own site, its path has the store's
product-page form (product_path), and its title names this exact model (matching.accepts). The price and its time
stay the shopping result's: the page is only where the shopper goes. No match keeps the Google link, named
"... via Google Shopping".
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from functools import cache
from pathlib import Path
from typing import Protocol

import httpx
import yaml  # type: ignore[import-untyped]  # PyYAML ships no type stubs
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.contracts import Offer, Product, Source
from app.research.serper import SerperResult, host_of
from app.research.usage import SearchUsage

from .matching import accepts

log = logging.getLogger(__name__)

STORES_PATH = Path(__file__).resolve().parents[2] / "config" / "stores.yaml"
VIA_GOOGLE = " via Google Shopping"
REGION = "IN"
_OWN_HOSTS = ("", "www.", "m.")  # the store's own site; never a blog or other subdomain


class Store(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=60)
    domain: str = Field(pattern=r"^[a-z0-9-]+(?:\.[a-z0-9-]+)+$", max_length=80)
    product_path: str = Field(min_length=1, max_length=200)  # a regular expression on a product page's path

    def is_product_page(self, path: str) -> bool:
        try:
            return re.search(self.product_path, path) is not None
        except re.error:
            return False  # a broken pattern finds no page: the Google link stays


class _StoreFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    stores: list[Store] = Field(default_factory=list)


@cache
def load_stores(path: Path = STORES_PATH) -> tuple[Store, ...]:
    """The store map, or none at all if the file can't be read: then every Google link simply stays."""
    try:
        return tuple(_StoreFile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8"))).stores)
    except (OSError, yaml.YAMLError, ValidationError, TypeError) as exc:
        log.warning("commerce: the store map could not be read (%s); Google links stay", type(exc).__name__)
        return ()


def _letters(text: str) -> str:
    return "".join(ch for ch in text.casefold() if ch.isalnum())


def store_for(name: str, stores: Sequence[Store]) -> Store | None:
    """The map's store a listing's store name means: "Amazon.in", "amazon" and "Amazon" all mean amazon.in."""
    said = _letters(name)
    if not said:
        return None
    for store in stores:
        if said in {_letters(store.name), _letters(store.domain), _letters(store.domain.split(".")[0])}:
            return store
    return None


def google_store(offer: Offer) -> str | None:
    """The store's own name for an offer whose link is a Google Shopping page, or None for a store's own page."""
    return offer.store.removesuffix(VIA_GOOGLE) if offer.store.endswith(VIA_GOOGLE) else None


def sites_for(offers: Sequence[Offer], stores: Sequence[Store]) -> list[Store]:
    """The mapped stores behind this product's Google Shopping links, once each, in the offers' order."""
    found: list[Store] = []
    for offer in offers:
        name = google_store(offer)
        store = store_for(name, stores) if name is not None else None
        if store is not None and store not in found:
            found.append(store)
    return found


class WebSearchLike(Protocol):
    def search(self, query: str, region: str, usage: SearchUsage, sites: Sequence[str] = ()) -> SerperResult: ...


def is_product_page(link: str, store: Store) -> bool:
    """One of the store's own product pages: its own site (the domain, www. or m.), and a path in its product form.
    A search page, a list of products, a blog or a brand page is not."""
    host = host_of(link)
    if host is None or host not in {f"{prefix}{store.domain}" for prefix in _OWN_HOSTS}:
        return False
    return store.is_product_page(httpx.URL(link).path)


def store_key(label: str, stores: Sequence[Store]) -> str:
    """One key a store: its map domain when it has one ("Flipkart" and "Flipkart via Google Shopping" are one store),
    else its name's letters and digits."""
    name = label.removesuffix(VIA_GOOGLE)
    store = store_for(name, stores)
    return store.domain if store is not None else _letters(name)


def with_store_pages(
    product: Product, offers: Sequence[Offer], found: Sequence[Source], stores: Sequence[Store]
) -> list[Offer]:
    """The offers, each Google Shopping link at a mapped store replaced by that store's own page for this model when
    the search found one. The first such result in Serper's order wins. Price, time, title and variant stay."""
    swapped: list[Offer] = []
    for offer in offers:
        name = google_store(offer)
        store = store_for(name, stores) if name is not None else None
        if store is None:
            swapped.append(offer)
            continue
        page = next(
            (s for s in found if is_product_page(str(s.url), store) and accepts(product, s.title or "")),
            None,
        )
        if page is None:
            swapped.append(offer)
            continue
        try:
            swapped.append(
                Offer.model_validate({**offer.model_dump(), "url": str(page.url), "store": (name or store.name)[:80]})
            )
        except ValidationError:
            swapped.append(offer)  # a link the contract refuses: the Google link stays
    return swapped
