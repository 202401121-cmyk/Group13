"""Which store page could replace an offer's Google Shopping link (US-24, D-99, which replaces D-67's store list).

Serper shopping gives only Google Shopping links for India (S4-14, 15 Nov 2026), and US-24 says a link opens the
store's own page. The app recommends in any category, so any store can sell the model: no list decides which stores
are tried. One search a priced product looks for the exact model (store_pages.py), and a result stands for an offer
only when:

- its host is that store's own site, with www. or m. at most: the domain the offer shows ("gadgetsclub.in"); the domain
  of a store in config/stores.yaml ("Flipkart" is flipkart.com, never amazon.com for "Amazon"); or else a domain whose
  registrable name is the store's name in letters and digits, under .in, .co.in or .com, where India's stores live
  ("Gadgets Now" is gadgetsnow.com; "GameLoot" is never gameloot.shop). A look-alike never is:
  gadgetsclub.in.example.com belongs to example.com, and gadgets-club.in's name has a hyphen the store's name lacks;
- its title or its address names this model: the brand and model in order (matching.accepts), and each of the model's
  codes as a whole token, so X1504VA never stands for X1504VAP, nor the reverse.

store_pages.py then checks the page before a link to it is shown. config/stores.yaml is a speed-up, not a gate: a
known store's product_path proves a product page from its address alone. The price and its time stay the shopping
result's; the page is only where the shopper goes. Anything unproven keeps the Google link, "... via Google Shopping".
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from functools import cache
from pathlib import Path
from typing import Protocol
from urllib.parse import unquote

import httpx
import yaml  # type: ignore[import-untyped]  # PyYAML ships no type stubs
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.contracts import Offer, Product, Source
from app.entities.normalise import parse_name
from app.research.serper import SerperResult, host_of
from app.research.usage import SearchUsage

from .matching import accepts

log = logging.getLogger(__name__)

STORES_PATH = Path(__file__).resolve().parents[2] / "config" / "stores.yaml"
VIA_GOOGLE = " via Google Shopping"
REGION = "IN"
_OWN_HOSTS = ("", "www.", "m.")  # the store's own site; never a blog or other subdomain
_DOMAIN = re.compile(r"[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}")
# Second-level labels under a country's code, so that co.in, com.au or org.uk are suffixes, not names (no download of
# the public suffix list: a store under any other suffix simply keeps its Google link).
_SECOND_LEVEL = frozenset({"ac", "co", "com", "edu", "firm", "gen", "gov", "ind", "net", "nic", "org", "res"})
# Hosting where each shop has its own name under the host's: mystore.myshopify.com is one shop, other.myshopify.com
# another. A redirect from one to the other leaves the shop.
_SHARED_HOSTING = frozenset(
    {"myshopify.com", "wixsite.com", "blogspot.com", "wordpress.com", "github.io", "netlify.app", "vercel.app",
     "pages.dev", "web.app", "firebaseapp.com", "herokuapp.com"}
)  # fmt: skip
NAME_RULE_SUFFIXES = ("in", "co.in", "com")  # where a store named only by its name may be (India's stores)
_SEARCH_PAGE = re.compile(
    r"/(?:s|search|searches|searchresults?|search-results?|catalogsearch|find)(?:[/?#.]|$)", re.IGNORECASE
)
_SEARCH_KEYS = frozenset({"q", "k", "query", "search", "searchterm", "keyword", "keywords", "text"})


class Store(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=60)
    domain: str = Field(pattern=r"^[a-z0-9-]+(?:\.[a-z0-9-]+)+$", max_length=80)
    product_path: str = Field(min_length=1, max_length=200)  # a regular expression on a product page's path

    def is_product_page(self, path: str) -> bool:
        try:
            return re.search(self.product_path, path) is not None
        except re.error:
            return False  # a broken pattern finds no page: the page is read instead


class _StoreFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    stores: list[Store] = Field(default_factory=list)


@cache
def load_stores(path: Path = STORES_PATH) -> tuple[Store, ...]:
    """The known stores, or none at all if the file can't be read: then every page is read before it is shown."""
    try:
        return tuple(_StoreFile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8"))).stores)
    except (OSError, yaml.YAMLError, ValidationError, TypeError) as exc:
        log.warning("commerce: the store list could not be read (%s); every page is read instead", type(exc).__name__)
        return ()


def _letters(text: str) -> str:
    return "".join(ch for ch in text.casefold() if ch.isalnum())


def store_for(name: str, stores: Sequence[Store]) -> Store | None:
    """The known store a listing's store name means: "Amazon.in", "amazon" and "Amazon" all mean amazon.in."""
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


class WebSearchLike(Protocol):
    def search(self, query: str, region: str, usage: SearchUsage, sites: Sequence[str] = ()) -> SerperResult: ...


def _own_hosts(domain: str) -> set[str]:
    return {f"{prefix}{domain}" for prefix in _OWN_HOSTS}


def is_product_page(link: str, store: Store) -> bool:
    """One of a known store's own product pages: its own site (the domain, www. or m.), and a path in its product form.
    A search page, a list of products, a blog or a brand page is not."""
    host = host_of(link)
    if host is None or host not in _own_hosts(store.domain):
        return False
    return store.is_product_page(httpx.URL(link).path)


def known_product_page(link: str, stores: Sequence[Store]) -> bool:
    """The address alone proves a product page: on a known store's site, in that store's product form (D-99 #5)."""
    return any(is_product_page(link, store) for store in stores)


def store_key(label: str, stores: Sequence[Store]) -> str:
    """One key a store: its known domain when it has one ("Flipkart" and "Flipkart via Google Shopping" are one store),
    else its name's letters and digits."""
    name = label.removesuffix(VIA_GOOGLE)
    store = store_for(name, stores)
    return store.domain if store is not None else _letters(name)


def registrable(host: str) -> str:
    """The name a site is registered under: www.gameloot.in is gameloot.in, shop.example.co.in is example.co.in, and on
    shared hosting each shop's own: www.mystore.myshopify.com is mystore.myshopify.com."""
    labels = host.lower().rstrip(".").split(".")
    if len(labels) >= 3 and (
        (labels[-2] in _SECOND_LEVEL and len(labels[-1]) == 2) or ".".join(labels[-2:]) in _SHARED_HOSTING
    ):
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def shown_domain(name: str) -> str | None:
    """The domain a store's name is, when the listing names the store by its address ("gadgetsclub.in")."""
    said = name.strip().lower().removeprefix("www.")
    return said if _DOMAIN.fullmatch(said) else None


def is_store_host(name: str, host: str, stores: Sequence[Store]) -> bool:
    """Is this host the named store's own site (the module's first rule)? Never a look-alike or another subdomain."""
    shown = shown_domain(name)
    if shown is not None:
        return host in _own_hosts(shown)
    known = store_for(name, stores)
    if known is not None:
        return host in _own_hosts(known.domain)
    site = registrable(host)
    label, _, suffix = site.partition(".")
    if host not in _own_hosts(site) or suffix not in NAME_RULE_SUFFIXES:
        return False
    return label == _letters(name)  # as written: a hyphen in the name a store doesn't have is a look-alike


def names_model(product: Product, text: str) -> bool:
    """The text names this exact model: its brand and model in order (matching.accepts), and each of its model codes
    as a whole token. accepts lets a code agree with a longer one ending in it; a store's page must say the code."""
    if not accepts(product, text):
        return False
    found = set(parse_name(text).codes)
    return all(code in found for code in parse_name(product.name, product.brand).codes)


def address_words(url: str) -> str:
    """A page's address as words, for names_model: "/asus-vivobook-15-x1504va/p/itm1" reads "asus vivobook 15 x1504va
    p itm1"."""
    try:
        path = httpx.URL(url).path
    except (httpx.InvalidURL, TypeError, ValueError):
        return ""
    return " ".join(re.sub(r"[^0-9A-Za-z]+", " ", unquote(path)).split())


def candidate(product: Product, name: str, found: Sequence[Source], stores: Sequence[Store]) -> str | None:
    """The first search result, in the search's order, on the named store's own site that names this model in its
    title or its address. None if there is none."""
    for source in found:
        link = str(source.url)
        host = host_of(link)
        if host is None or not is_store_host(name, host, stores):
            continue
        if names_model(product, source.title or "") or names_model(product, address_words(link)):
            return link
    return None


def is_search_page(url: str) -> bool:
    """A store's search results, never a product: /search, /s?k=..., /catalogsearch, or the home page asked a query."""
    try:
        parts = httpx.URL(url)
        path = parts.path
    except (httpx.InvalidURL, TypeError, ValueError):
        return True
    if _SEARCH_PAGE.search(path):
        return True
    return path in ("", "/") and any(key.lower() in _SEARCH_KEYS for key in parts.params)


def with_page(offer: Offer, url: str, name: str) -> Offer:
    """The offer with its Google link swapped for the store's own page, named as the store. Price, time, title and
    variant stay the shopping result's. A link the contract refuses keeps the Google one."""
    try:
        return Offer.model_validate({**offer.model_dump(), "url": url, "store": name[:80]})
    except ValidationError:
        return offer
