"""Missing facts for the top products (US-41): a web search, a checked public page, one batched LLM call.

For each product with fact targets (a top product's criteria with no evidence, picked in code: targets.py), one search
asks for the product and the criterion's fixed public stand-in, never the shopper's words or a generated key (D-51).
A target may also be a hard rule's spec that held a product back ("can't tell"), searched by its own key's words
(spec_label) and asked for as extraction asks a rule's key (D-102 item 1).

When a product's search gives no maker's page, Firecrawl's map looks for one on the maker's own site by the product's
name (D-100 item 2): 1 credit, at most once a product, its answer kept 7 days, the page it finds read like any other.

The page is a maker's, then a review site's, then any other public page (D-102 item 2): never Reddit, YouTube, a social
site, a forum or Q&A site, a store's page, or a login or checkout page (other_page), with research's own readers, rules
and caps (robots.txt first), and one page a product. Another page is read by a plain fetch, or as Firecrawl's search
brought it back, and gets the lowest tier (research's OTHER_TIER). A page research already read is skipped, and
so is one that doesn't name its product, its words whole and in order, so the product's name sent with it is the page's
own words.

D-101 (Om, 25 Nov 2026), so the pages and the call fit enrich's window:
- Split by rank, in parallel: the top SERPER_TOP products search Serper (about 1 s) and their links are read by the
  usual readers; the others search Firecrawl, which reads the top results in the same call (no read step follows).
- Serper only while today's count leaves SERPER_RESERVE for research and commerce; past that, every product goes to
  Firecrawl. A path that fails, has no room, or hasn't given a page by PATH_SHARE of the window starts the other path
  too, inside the window. Every Serper query and Firecrawl credit is counted before it is sent, and never more than
  enrich's searches in all (US-41 criterion 2).
- One call reads the pages: the quickest model with room (app/llm `fastest`), sent at a cut-off that leaves it that
  model's measured 90th percentile before the look-up ends. Pages that arrive after the cut-off are not read. Each page
  goes as passages around its product's name and its missing criteria (passages.py), and the answer is kept short.

Each page's answer goes through extraction's own parser, so a claim is kept only when its quote is on that page word for
word and the page names the product (US-40's rule), and only on the criteria looked up for that page's product. Each
kept claim cites its page. Search links, pages and the call's answer are cached 7 days in enrich's own folder
(`app/research/cache.py`, ENRICH_FOLDER). A cached search or answer sends nothing. Any failure adds nothing; the search
goes on (build rule 9). If the call fails, nothing is added and the values stay estimated (criterion 5).
"""

from __future__ import annotations

import functools
import hashlib
import ipaddress
import json
import logging
import re
import threading
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime
from time import monotonic
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit

from pydantic import ValidationError

from app.commerce.matching import query_for
from app.contracts import (
    Claim,
    DataClass,
    Difficulty,
    EnrichTarget,
    ExtractInput,
    LLMClient,
    LLMTask,
    LLMUnavailable,
    Product,
    Source,
    SourceType,
)
from app.contracts.enrich import MAX_ENRICH_SEARCHES
from app.criteria.fallback import fallback_criteria
from app.extract.parsing import RawExtraction, to_extraction_result
from app.extract.prompts import PromptCriteria, SpecAsk, allowed_criteria, public_prompt_keys, source_text
from app.extract.settings import ExtractSettings
from app.extract.stage import _under_own_keys
from app.llm.pool import CALL_MARGIN_S, time_limit_s
from app.llm.usage import TokenMeter
from app.research.cache import ResearchCache
from app.research.pages import NEVER_FETCH, NEVER_READ, PageResult, ReviewPages, is_logged_in_page, page_kind
from app.research.readers import SearchPages
from app.research.serper import SerperResult, host_is_under, host_of
from app.research.settings import ResearchSettings
from app.research.usage import SearchUsage

from .parsing import MAX_PAGES, RawFacts, page_answers
from .passages import PAGE_CHARS, criterion_words, passages
from .prompts import FACTS_SYSTEM_PROMPT, PageAsk, facts_message

log = logging.getLogger(__name__)

TASK = LLMTask.ENRICH_EXTRACT  # enrich's own extraction: a fastest task, one call (models.yaml `fastest`, D-101)
SERPER_TOP = 2  # D-101: the top-ranked products whose look-ups search Serper; the rest search Firecrawl
SERPER_RESERVE = 25  # D-101: Serper's queries today that the fact look-ups leave to research and commerce
PATH_SHARE = 0.5  # of the window, a product's first path has to itself; past it, the other path starts too
PAGE_S = 4.0  # a link's time to itself: past it the next link starts too, and both are waited for
MAX_READS_AT_ONCE = 10  # page reads running at once: two links for each of the top 5 products
MIN_LEFT_S = 0.05  # less than this left of the window is none: Windows' clock moves in 15.6 ms steps on Python 3.11
MIN_READ_S = 1.5  # with less than this before the cut-off, nothing is searched: no page could be read in time
CALL_S = 5.0  # the call's time when no client measures it (a test's fake): models.yaml's unmeasured_s
PAID_READERS = ("jina", "firecrawl", "firecrawl_pages")  # no room for these once the window is over
ANSWER_TOKENS = 3000  # a short answer and Gemini's thinking, which counts against it (S5-6a)
PLANNED_TOKENS = MAX_PAGES * PAGE_CHARS // 3 + 1500  # the call's input at most: its pages' passages and the criteria
READ_KINDS = ("maker", "review")  # the listed sites read: never a store's page, a checkout, Reddit or YouTube
PREFERENCE = {"maker": 0, "review": 1}  # D-102: a maker's page, then a review site's, then any other checked page
OTHER_KIND = 2
# D-102 item 2: a site no list names is never a forum, a Q&A site or a store. These words in a subdomain or a folder, or
# these sites, say it may be one. Calling a real page a forum or a store is the safer mistake: the fact is not taken.
FORUM_WORDS = frozenset(
    {
        "answers",
        "board",
        "boards",
        "communities",
        "community",
        "discuss",
        "discussion",
        "discussions",
        "forum",
        "forums",
        "qa",
        "question",
        "questions",
        "thread",
        "threads",
        "topic",
        "topics",
    }
)
STORE_WORDS = frozenset(
    {
        "addtocart",
        "bag",
        "basket",
        "buy",
        "buynow",
        "cart",
        "checkout",
        "dp",
        "gp",
        "item",
        "itm",
        "offer",
        "offers",
        "product",
        "products",
        "shop",
        "shopping",
        "store",
    }
)
QA_SITES = ("quora.com", "stackexchange.com", "stackoverflow.com", "superuser.com", "answers.com", "brainly.in")
# A forum's own name inside a site's name: xdaforums.com. Folder words a forum's pages use: /t/, showthread.php.
FORUM_PARTS = ("forum", "community", "discuss")
FORUM_PAGES = frozenset({"t", "showthread", "viewtopic", "viewthread"})
# Social sites research's NEVER_READ doesn't list yet, and archives, which hold copies of any page, Reddit's too.
SOCIAL_SITES = (
    "bsky.app",
    "discord.com",
    "discord.gg",
    "snapchat.com",
    "t.me",
    "telegram.org",
    "threads.com",
    "tumblr.com",
    "vk.com",
    "weibo.com",
    "whatsapp.com",
)
ARCHIVE_SITES = ("archive.org", "archive.is", "archive.li", "archive.ph", "archive.today", "ghostarchive.org")
COPIED = ("reddit.com", "redd.it", "youtube.com", "youtu.be")  # anywhere in an address: a copy, never read (D-53)
STORE_SITES = (
    "aliexpress.com",
    "amazon.com",
    "amazon.in",
    "bajajelectronics.com",
    "bestbuy.com",
    "croma.com",
    "ebay.com",
    "ebay.in",
    "flipkart.com",
    "girias.com",
    "indiamart.com",
    "jiomart.com",
    "lotmobiles.com",
    "meesho.com",
    "myntra.com",
    "paiinternational.in",
    "paytmmall.com",
    "poorvika.com",
    "reliancedigital.in",
    "sangeethamobiles.com",
    "shopclues.com",
    "snapdeal.com",
    "tatacliq.com",
    "vijaysales.com",
    "walmart.com",
)
INTERNAL_ENDINGS = (".corp", ".home", ".internal", ".intranet", ".lan", ".local", ".localhost")
UNIT_WORDS = frozenset({"g", "gb", "h", "hz", "in", "kg", "mah", "mm", "w"})  # a spec key's unit: not searched for
MIN_CALL_S = 1.0  # with less left for the call, none is sent: no model answers that soon
CALL_FLOOR_S = CALL_MARGIN_S + MIN_CALL_S  # the least the cut-off leaves the call: the client starts none at its end
ANSWER_MARGIN_S = 1.0  # the call ends this long before the look-up does, so its answer is read in time
# D-53: the sites Firecrawl's reading search must leave out, so it never reads a Reddit or YouTube page for us.
NEVER_SEARCHED = (*NEVER_FETCH, "www.reddit.com", "old.reddit.com", "www.youtube.com", "m.youtube.com")

Path = Literal["serper", "firecrawl"]
OTHER: dict[Path, Path] = {"serper": "firecrawl", "firecrawl": "serper"}


class WebSearchLike(Protocol):
    def search(self, query: str, region: str, usage: SearchUsage, sites: Sequence[str] = ()) -> SerperResult: ...


class SearchPagesLike(Protocol):
    def search_pages(
        self,
        query: str,
        region: str,
        usage: SearchUsage,
        *,
        results: int = ...,
        until: float,
        exclude: Sequence[str] = ...,
    ) -> SearchPages: ...


class PageReaderLike(Protocol):
    def fetch(self, url: str, usage: SearchUsage) -> PageResult: ...

    def take_read(self, url: str, title: str, text: str, usage: SearchUsage) -> PageResult: ...


class CallTimer(Protocol):
    """The LLM client's measured answer times (app/llm/answer_times.py): the time to leave a fastest task's call."""

    def answer_time_s(
        self,
        task: LLMTask,
        data_class: DataClass,
        difficulty: Difficulty,
        input_tokens: int,
        max_output_tokens: int,
        within_s: float,
    ) -> float | None: ...


@dataclass(frozen=True)
class FactAsk:
    """What the pipeline tells the look-up about the search: no words of the shopper's."""

    category: str  # the rubric's category: picks the fixed criteria and their definitions
    region: str = "in"
    known_urls: frozenset[str] = frozenset()  # pages research read: their claims were already looked for
    ranked: tuple[str, ...] = ()  # the first ranking's product ids, best first: the split by rank (D-101)


@dataclass(frozen=True)
class FactsFound:
    claims: list[Claim]
    sources: list[Source]
    llm_calls: int = 0
    searches: int = 0  # searches sent (a cached one sends nothing), whichever service answered


NOTHING = FactsFound(claims=[], sources=[])


def stand_in_label(category: str, key: str, settings: ExtractSettings) -> str | None:
    """The fixed criterion's own label a criterion is searched by (D-51): its own if it is fixed, else its fixed
    stand-in's. None if it has none: then it can't be searched for."""
    public = public_prompt_keys(category, [key], (), False, settings)
    labels = {c.key: c.label for c in fallback_criteria(category).criteria}
    fixed = next((k for k in public.criteria if public.renames.get(k, k) == key), None)
    return labels.get(fixed) if fixed is not None else None


def spec_label(key: str, settings: ExtractSettings) -> str | None:
    """A known spec key's own words, for a search (D-102 item 1): "ram_gb" is searched as "ram", "has_backlit_keyboard"
    as "backlit keyboard". Only config's public spec keys have one, so a search holds our keys' words only (D-51)."""
    if key not in settings.public_spec_keys:
        return None
    words = key.removeprefix("has_").split("_")
    if len(words) > 1 and words[-1] in UNIT_WORDS:
        words = words[:-1]
    return " ".join(words)


def search_label(category: str, key: str, settings: ExtractSettings) -> str | None:
    """What a target is searched by: a known spec key's own words ("ram", not its criterion's "RAM and storage"), else a
    criterion's fixed label (D-51)."""
    return spec_label(key, settings) or stand_in_label(category, key, settings)


def names_product(text: str, name: str) -> bool:
    """Does this text name the product, its words whole and in order? Case and spacing aside."""
    words = [re.escape(w) for w in name.casefold().split()]
    return bool(words) and re.search(r"(?<!\w)" + r"\s+".join(words) + r"(?!\w)", text.casefold()) is not None


def is_the_product(mention: str, product: Product, target: EnrichTarget) -> bool:
    """Is the claim's product this one, by one of its own names, whole? A longer name ("Galaxy S24 Ultra" for the
    Galaxy S24) is another model, and a shorter one ("Vela Pro" for the Vela Pro 14) could be any of a range."""
    names = {product.name, target.product_name, query_for(product)}
    return _plain(mention) in {_plain(name) for name in names}


def nothing_sent(reason: str | None, detail: str | None) -> bool:
    """A failed search or look-up that never left the machine (D-78): our own cap, no key to ask with, no time left to
    ask, Firecrawl's own gate (paced, or switched off for the run), or a run budget's refusal (OverBudget: a live
    run's guard). A provider's own refusal (an HTTP 401, 402 or 403) did leave it, and counts."""
    said = detail or ""
    if "paced" in said or "switched off" in said or "OverBudget" in said:
        return True
    if reason == "quota_exhausted":
        return "HTTP" not in said
    if reason == "blocked":
        return "is not set" in said
    return reason == "timeout" and "no time left" in said


class Window:
    """The look-up's reading window, on the monotonic clock: over at `ends`, or once closed. The look-up closes it when
    it stops waiting, so a read still going sees it over at once, whatever Windows' coarse clock says (MIN_LEFT_S)."""

    def __init__(self, ends: float) -> None:
        self.ends = ends
        self._closed = threading.Event()

    def close(self) -> None:
        self._closed.set()

    def over(self) -> bool:
        return self._closed.is_set() or monotonic() >= self.ends

    def left(self) -> float:
        return 0.0 if self._closed.is_set() else max(0.0, self.ends - monotonic())


class UntilWindowEnds(SearchUsage):
    """The search's own usage, for one page read: the same counts, but once the window is over the paid readers (Jina,
    Firecrawl) get no more room. A read left to finish on its own then spends only its free plain fetch, kept for the
    next search; credits go to pages a search can still use, and Firecrawl's shared slots aren't held past it."""

    def __init__(self, usage: SearchUsage, window: Window) -> None:
        self.__dict__.update(usage.__dict__)  # the same book, lock, allowance and counts: one search's usage
        self.window = window

    def _over(self, provider: str) -> bool:
        return provider in PAID_READERS and self.window.over()

    def take(self, provider: str, *, keep: int = 0, count: int = 1) -> bool:
        return False if self._over(provider) else super().take(provider, keep=keep, count=count)

    def left(self, provider: str, *, keep: int = 0) -> int:
        return 0 if self._over(provider) else super().left(provider, keep=keep)


class KeepsReserve(SearchUsage):
    """The search's own usage, for a fact search: a Serper query goes only if today's count still leaves SERPER_RESERVE
    for research and commerce after it (D-101). Checked and counted under the book's lock, so searches running at once
    can't cut into the reserve."""

    def __init__(self, usage: SearchUsage, keep: int = SERPER_RESERVE) -> None:
        self.__dict__.update(usage.__dict__)
        self.reserve = keep

    def take(self, provider: str, *, keep: int = 0, count: int = 1) -> bool:
        return super().take(provider, keep=max(keep, self.reserve) if provider == "serper" else keep, count=count)

    def left(self, provider: str, *, keep: int = 0) -> int:
        return super().left(provider, keep=max(keep, self.reserve) if provider == "serper" else keep)


def _plain(name: str) -> str:
    return " ".join(name.casefold().split())


def readable(url: str, settings: ResearchSettings) -> bool:
    """A maker's or a review site's page, or another checked public page (D-102), and never Reddit or YouTube, whatever
    the config says (D-53). Never a forum either, even on a maker's or a review site's own domain (D-102)."""
    host = host_of(url)
    if host is None or any(host_is_under(host, never) for never in NEVER_FETCH) or _forum(url, host):
        return False
    kind = page_kind(url, settings)
    if kind is not None:
        return kind[1] in READ_KINDS
    return other_page(url, settings)


def other_page(url: str, settings: ResearchSettings) -> bool:
    """A page on a site no list names that a fact may come from (D-102 item 2). Never Reddit, YouTube, a copy of either
    or an archive, a social site, a forum or a Q&A site, a store's page, or a login, account, cart or checkout page; a
    listed site keeps its own kind, and a listed store is never read for a fact. Only a named host on the open internet:
    never an address, localhost, a name with no dot or an internal one, or another port. robots.txt is the reader's to
    obey, and the reader checks the name's addresses before anything is sent (research's pages.py)."""
    host = host_of(url)
    if host is None or page_kind(url, settings) is not None or is_logged_in_page(url) or not _named_host(url, host):
        return False
    lowered = url.lower()
    if any(copy in lowered for copy in COPIED) or _forum(url, host):
        return False
    never = (*NEVER_READ, *SOCIAL_SITES, *ARCHIVE_SITES, *QA_SITES, *STORE_SITES, *_store_domains())
    if any(host_is_under(host, site) for site in never):
        return False
    return _words(host.split(".")[:-2]).isdisjoint(STORE_WORDS) and _folders(url).isdisjoint(STORE_WORDS)


def _named_host(url: str, host: str) -> bool:
    """A host name on the open internet, on the usual port: never an address (127.0.0.1, [::1], 169.254.169.254),
    localhost, a name with no dot, or a name ending as internal ones do (.local, .internal)."""
    try:
        port = urlsplit(url).port
    except ValueError:
        return False
    if port not in (None, 80, 443):
        return False
    bare = host.strip("[]")
    try:
        ipaddress.ip_address(bare)
    except ValueError:
        return "." in bare and bare != "localhost" and not bare.endswith(INTERNAL_ENDINGS)
    return False


def _forum(url: str, host: str) -> bool:
    """A forum or a Q&A site: a site trust.yaml calls community, a forum's name inside the site's ("xdaforums"), a forum
    word in a subdomain or a folder, or a forum's own page names (/t/, showthread)."""
    if any(host_is_under(host, site) for site in (*QA_SITES, *_community_sites())):
        return True
    labels = host.split(".")
    if any(part in label for label in labels[:-1] for part in FORUM_PARTS):
        return True
    folders = _folders(url)
    return not (_words(labels[:-2]) | folders).isdisjoint(FORUM_WORDS) or not folders.isdisjoint(FORUM_PAGES)


def _words(labels: Sequence[str]) -> set[str]:
    return {word for label in labels for word in re.split(r"[-_]", label) if word}


def _folders(url: str) -> set[str]:
    """Every word of the address's folders and page: "/buy-online/x.php" gives buy, online, x and php."""
    return {word for part in _path_of(url).split("/") for word in re.split(r"[-_.]", part) if word}


@functools.cache
def _community_sites() -> tuple[str, ...]:
    """The sites config/trust.yaml calls community evidence: forums and Q&A sites (US-16)."""
    try:
        from app.verify.trust import default_trust_table

        return tuple(domain for domain, site in default_trust_table().sites.items() if site.tier == "community")
    except Exception as exc:  # the lists above still hold (build rule 9)
        log.warning("enrich: trust.yaml's community sites were not read (%s)", type(exc).__name__)
        return ()


@functools.cache
def _store_domains() -> tuple[str, ...]:
    """The stores config/stores.yaml knows (US-24): never read for a fact."""
    from app.commerce.links import load_stores

    return tuple(store.domain for store in load_stores())


def _path_of(url: str) -> str:
    """The URL's path in lower case, or "" when it has none or isn't a URL."""
    try:
        return (urlsplit(url).path or "").lower()
    except ValueError:
        return ""


def preference(url: str, settings: ResearchSettings) -> int:
    """D-102: a maker's page first, then a review site's, then any other checked page."""
    kind = page_kind(url, settings)
    return PREFERENCE.get(kind[1], OTHER_KIND) if kind is not None else OTHER_KIND


def _maps_to(link: Any, product: Product, settings: ResearchSettings) -> bool:
    """A mapped address is the product's maker page: on the maker's site, readable, and naming the model in its
    title or its address's words."""
    url = str(getattr(link, "url", ""))
    if preference(url, settings) != 0 or not readable(url, settings):
        return False
    words = " ".join(re.split(r"[/_.\-]+", _path_of(url)))
    return names_product(str(getattr(link, "title", "")), product.name) or names_product(words, product.name)


def other_site(source: Source) -> bool:
    """Is this fact page another checked page, not a maker's or a review site's? Research types those as web pages."""
    return source.type is SourceType.WEB_SEARCH and source.via != "search_snippet"


@dataclass
class _Run:
    """One look-up's state: its window, its executors, and enrich's searches it may still send (US-41 criterion 2)."""

    ask: FactAsk
    usage: SearchUsage
    window: Window
    searches: int
    sent: int = 0
    mapped: set[str] = field(default_factory=set)  # products whose maker's site was mapped: once each (D-100)
    paths: ThreadPoolExecutor = field(default_factory=lambda: ThreadPoolExecutor(12, thread_name_prefix="enrich-path"))
    reads: ThreadPoolExecutor = field(
        default_factory=lambda: ThreadPoolExecutor(MAX_READS_AT_ONCE, thread_name_prefix="enrich-pages")
    )
    lock: threading.Lock = field(default_factory=threading.Lock)

    def take_search(self) -> bool:
        """One of enrich's searches, taken before it is sent, whichever service sends it."""
        with self.lock:
            if self.sent >= self.searches:
                return False
            self.sent += 1
            return True

    def give_back(self) -> None:
        """A search that was answered from a cache or never left the machine (D-78)."""
        with self.lock:
            self.sent = max(0, self.sent - 1)


class FactLookup:
    """One search's fact look-ups. Every dependency comes in, so tests pass fakes. `search` is Serper's web search,
    `firecrawl` Firecrawl's search that reads its results (None without a key), and `timer` the LLM client's measured
    answer times (None: CALL_S)."""

    def __init__(
        self,
        llm: LLMClient,
        search: WebSearchLike,
        pages: PageReaderLike,
        cache: ResearchCache,
        *,
        research: ResearchSettings,
        extract: ExtractSettings,
        now: Callable[[], datetime],
        firecrawl: SearchPagesLike | None = None,
        timer: CallTimer | None = None,
    ) -> None:
        self.llm, self.search, self.pages, self.cache = llm, search, pages, cache
        self.research, self.extract, self._now = research, extract, now
        self.firecrawl, self.timer = firecrawl, timer
        # Searches and page reads still going when the window ended: the caller closes the client only once they are
        # over, so a page that comes late is kept in the cache for the next search instead of being cut off.
        self.in_flight: list[Future[Source | None]] = []
        self._lock = threading.Lock()

    def find(
        self,
        targets: Sequence[tuple[EnrichTarget, Product]],
        ask: FactAsk,
        usage: SearchUsage,
        ends: float,
        search_id: str | None = None,
        *,
        searches: int = MAX_ENRICH_SEARCHES,
    ) -> FactsFound:
        """The facts the pages support, for these targets, by `ends` (the pool's monotonic clock). `searches`: enrich's
        searches the price look-ups left."""
        answerable = self._answerable(targets, ask)
        labelled = [
            (t, p, label)
            for t, p in targets
            if t.criterion_key in answerable and (label := search_label(ask.category, t.criterion_key, self.extract))
        ]
        if not labelled or searches <= 0:
            return NOTHING
        call_s = self._call_s(ends)
        if call_s is None:
            log.info("enrich: no model has room to read the fact pages")
            return NOTHING
        cut_off = ends - ANSWER_MARGIN_S - max(call_s, CALL_FLOOR_S)  # the call's measured time is left to it
        if cut_off - monotonic() < MIN_READ_S:
            log.info("enrich: too little time to read fact pages and leave the call its time")
            return NOTHING
        run = _Run(ask, usage, Window(cut_off), searches)
        if isinstance(self.pages, ReviewPages):  # the ladder's rendered step goes only before the reads end (D-100)
            self.pages.ends = cut_off
        pages = self._read(labelled, run)
        if not pages:
            return FactsFound(claims=[], sources=[], searches=run.sent)
        found = self._ask(pages, labelled, targets, ask, ends, search_id)
        return FactsFound(found.claims, found.sources, found.llm_calls, run.sent)

    def _call_s(self, ends: float) -> float | None:
        """The time to leave the call: the measured 90th percentile of the model it would go to (D-101)."""
        if self.timer is None:
            return CALL_S
        within = max(0.0, ends - ANSWER_MARGIN_S - MIN_READ_S - monotonic())
        return self.timer.answer_time_s(TASK, DataClass.PUBLIC, Difficulty.LONG, PLANNED_TOKENS, ANSWER_TOKENS, within)

    def _answerable(self, targets: Sequence[tuple[EnrichTarget, Product]], ask: FactAsk) -> set[str]:
        """The targets' criteria the one call can carry: a generated key whose fixed stand-in is itself a target
        criterion can't be asked for apart from it (public_prompt_keys), so it is never searched for in vain."""
        keys = list(dict.fromkeys(t.criterion_key for t, _ in targets))[:10]
        if not keys:
            return set()
        specs = self._specs(keys)
        criteria = [key for key in keys if key not in specs]
        public = public_prompt_keys(ask.category, criteria, specs, False, self.extract)
        return {public.renames.get(key, key) for key in public.criteria} | (set(public.specs) & set(specs))

    def _specs(self, keys: Sequence[str]) -> tuple[str, ...]:
        """The keys that are a rule's known spec, asked for as extraction asks a rule's key: never as a criterion, which
        a synonym could turn into another one (ram_gb is never memory_and_storage)."""
        return tuple(key for key in keys if spec_label(key, self.extract) is not None)

    # --- searches and pages -----------------------------------------------------------------------------------------

    def _read(self, labelled: Sequence[tuple[EnrichTarget, Product, str]], run: _Run) -> list[tuple[Source, Product]]:
        """One page for each product looked up, best ranked first: the first to arrive in the window that names the
        product (_one_product). The window closes when this stops waiting."""
        queries = {t.product_id: f"{p.name} {label}" for t, p, label in reversed(labelled)}  # first target's wins
        products = {t.product_id: p for t, p, _ in labelled}
        order = self._by_rank(labelled, run.ask)
        first = self._first_paths(order, run)
        pool = ThreadPoolExecutor(max_workers=len(order), thread_name_prefix="enrich-facts")
        try:
            futures = {
                pid: pool.submit(self._one_product, queries[pid], products[pid], first[pid], run) for pid in order
            }
            with self._lock:  # a look-up still going at the window's end keeps the client open too
                self.in_flight.extend(futures.values())
            wait(list(futures.values()), timeout=run.window.left())
            found = [(futures[pid].result(), products[pid]) for pid in order if futures[pid].done()]
        finally:
            run.window.close()  # a read still going sees the window over now (UntilWindowEnds)
            pool.shutdown(wait=False, cancel_futures=True)
            with self._lock:  # no search or read can start, unrecorded, after this: the caller sees each in in_flight
                run.paths.shutdown(wait=False, cancel_futures=True)
                run.reads.shutdown(wait=False, cancel_futures=True)
        seen: set[tuple[str, str]] = set()
        pages: list[tuple[Source, Product]] = []
        for page, product in found:  # a page two products share goes once for each, with each one's passages
            if page is not None and (page.id, product.id) not in seen and len(pages) < MAX_PAGES:
                seen.add((page.id, product.id))
                pages.append((page, product))
        return pages

    @staticmethod
    def _by_rank(labelled: Sequence[tuple[EnrichTarget, Product, str]], ask: FactAsk) -> list[str]:
        """The products looked up, in the first ranking's order; any it doesn't place follow in their targets' order."""
        order = list(dict.fromkeys(t.product_id for t, _, _ in labelled))
        place = {pid: at for at, pid in enumerate(ask.ranked)}
        return sorted(order, key=lambda pid: place.get(pid, len(place)))

    def _first_paths(self, order: Sequence[str], run: _Run) -> dict[str, Path]:
        """D-101: the first ranking's top SERPER_TOP products search Serper, the rest Firecrawl (a product it doesn't
        place goes by its place in `order`). Without Firecrawl, all search Serper; once Serper is down to its reserve
        for the day, all search Firecrawl."""
        if self.firecrawl is None:
            return dict.fromkeys(order, "serper")
        if KeepsReserve(run.usage).left("serper") <= 0:
            log.info("enrich: Serper is down to its reserve for today: the fact searches go to Firecrawl")
            return dict.fromkeys(order, "firecrawl")
        place = {pid: at for at, pid in enumerate(run.ask.ranked)}
        return {pid: "serper" if place.get(pid, at) < SERPER_TOP else "firecrawl" for at, pid in enumerate(order)}

    def _one_product(self, query: str, product: Product, first: Path, run: _Run) -> Source | None:
        """The product's first path; the other too once the first has ended with no page, or has used PATH_SHARE of
        the window without one (D-101). The first page naming the product wins. The window's end ends the wait; a path
        still going then is left to finish on its own (in_flight), and its pages are kept for the next search."""
        try:
            switch_at = monotonic() + run.window.left() * PATH_SHARE
            pending: dict[Future[Source | None], Path] = {}
            job = self._start(first, query, product, run)
            if job is not None:
                pending[job] = first
            other: Path | None = OTHER[first]
            waiting: Source | None = None  # another site's page, kept while the other path may bring a listed one
            while True:
                if other is not None and (not pending or monotonic() >= switch_at or waiting is not None):
                    job = self._start(other, query, product, run)
                    if job is not None:
                        pending[job] = other
                    other = None
                left = run.window.left()
                if not pending or left < MIN_LEFT_S:
                    return waiting
                until = left if other is None else min(left, max(0.0, switch_at - monotonic()))
                done, _ = wait(list(pending), timeout=until, return_when=FIRST_COMPLETED)
                if not done and other is None:
                    return waiting  # the window is over: the clock isn't read again for it (see MIN_LEFT_S)
                for job in done:
                    del pending[job]
                    page = None if job.cancelled() else job.result()
                    if page is not None and not other_site(page):
                        return page
                    waiting = waiting or page
        except Exception as exc:  # build rule 9: one product's look-up failing loses only its own page
            log.info("enrich: a fact look-up failed (%s)", type(exc).__name__)
            return None

    def _start(self, path: Path, query: str, product: Product, run: _Run) -> Future[Source | None] | None:
        """One path, started and recorded together; None when it can't go (no Firecrawl, or the window is over)."""
        if path == "firecrawl" and self.firecrawl is None:
            return None
        work = self._by_serper if path == "serper" else self._by_firecrawl
        with self._lock:  # started and recorded together: the window's end can't fall between them
            try:
                job = run.paths.submit(work, query, product, run)
            except RuntimeError:  # the paths were shut: the window is over
                return None
            self.in_flight.append(job)
        return job

    def _by_serper(self, query: str, product: Product, run: _Run) -> Source | None:
        """Serper's path: a web search, then its readable links through research's readers (_first_naming)."""
        try:
            links = [url for url in self._links(query, run) if url not in run.ask.known_urls]
            if not any(preference(url, self.research) == 0 for url in links):
                mapped = self._maker_by_map(product, run)
                if mapped is not None:
                    return mapped
            return self._first_naming(links, product, run)
        except Exception as exc:  # build rule 9
            log.info("enrich: a Serper fact search failed (%s)", type(exc).__name__)
            return None

    def _by_firecrawl(self, query: str, product: Product, run: _Run) -> Source | None:
        """Firecrawl's path: a search that reads its top results in the same call; no read follows. Its pages are held
        to the rules a page read here is (take_read), and kept 7 days; the first that names the product, makers first,
        is the product's page."""
        key = ("fact_pages", query, run.ask.region)
        try:
            urls = self._kept_urls(key)
            if urls is not None:
                pages = [page for url in urls if (page := self._kept_page(url)) is not None]
            else:
                pages = self._searched(key, query, run)
            usable = [p for p in pages if str(p.url) not in run.ask.known_urls and names_product(p.text, product.name)]
            ranked = sorted(usable, key=lambda page: preference(str(page.url), self.research))  # makers, reviews, other
            if not ranked or preference(str(ranked[0].url), self.research) != 0:
                mapped = self._maker_by_map(product, run)
                if mapped is not None:
                    return mapped
            return next(iter(ranked), None)
        except Exception as exc:  # build rule 9
            log.info("enrich: a Firecrawl fact search failed (%s)", type(exc).__name__)
            return None

    def _searched(self, key: tuple[str, ...], query: str, run: _Run) -> list[Source]:
        assert self.firecrawl is not None  # _start never starts this path without it
        if not run.take_search():
            return []
        # A search still waiting at Firecrawl's gate when the window closes is refused before it goes (UntilWindowEnds).
        reading = UntilWindowEnds(run.usage, run.window)
        found = self.firecrawl.search_pages(
            query, run.ask.region, reading, until=run.window.ends, exclude=NEVER_SEARCHED
        )
        if found.failure is not None:
            if nothing_sent(found.failure.reason, found.failure.detail):
                run.give_back()
            log.info("enrich: a Firecrawl fact search failed (%s)", found.failure.reason)
            return []
        pages: list[Source] = []
        kept: list[str] = []
        passing = found.unread > 0  # a read that failed may work next time
        for each in found.pages:
            if not readable(each.url, self.research):  # never Reddit, YouTube or a store, wherever it landed
                continue
            result = self.pages.take_read(each.url, each.title, each.text, run.usage)
            if result.source is None:
                passing = passing or result.reason != "not_found"  # robots.txt unread or saying no, or a cap
                continue
            self.cache.put_sources(("page", each.url), [result.source])
            kept.append(each.url)
            pages.append(result.source)
        if not passing:  # the list is kept 7 days only when nothing in it could come out otherwise next time
            self.cache.put_text(key, json.dumps(kept))
        return pages

    def _maker_by_map(self, product: Product, run: _Run) -> Source | None:
        """The maker's page for this exact model, found by Firecrawl's map on the maker's own site (D-100 item 2): only
        when search gave none, once a product, and read like any page. The map is asked for the product's name only. A
        map sent is one of enrich's searches (US-41: at most 6, Om 27 Nov); one kept in enrich's cache, or refused
        before it left, is not."""
        mapping = getattr(self.firecrawl, "map", None)
        domains = self.research.maker_domains.get((product.brand or "").casefold(), ())
        with run.lock:
            if mapping is None or not domains or product.id in run.mapped or run.window.over():
                return None
            run.mapped.add(product.id)
        key = ("fact_map", domains[0], product.name)
        kept = self._kept_urls(key)
        if kept is None:
            if not run.take_search():
                return None
            found = mapping(
                f"https://www.{domains[0]}", product.name, UntilWindowEnds(run.usage, run.window), until=run.window.ends
            )
            if isinstance(found, tuple):
                if nothing_sent(found[0], found[1]):
                    run.give_back()
                log.info("enrich: a maker's site was not mapped (%s)", found[0])
                return None
            kept = [link.url for link in found if _maps_to(link, product, self.research)][:1]
            self.cache.put_text(key, json.dumps(kept))
        for url in kept:
            page = self._page(url, UntilWindowEnds(run.usage, run.window))
            if page is not None and names_product(page.text, product.name):
                return page
        return None

    def _kept_urls(self, key: tuple[str, ...]) -> list[str] | None:
        cached = self.cache.get_text(key)
        if cached is None:
            return None
        try:
            return [str(url) for url in json.loads(cached)]
        except (ValueError, TypeError):
            return None

    def _kept_page(self, url: str) -> Source | None:
        kept = self.cache.get_sources(("page", url))
        return kept[0] if kept else None

    def _first_naming(self, links: Sequence[str], product: Product, run: _Run) -> Source | None:
        """The first page to arrive that names the product, the links in the search's order breaking a tie. Each link
        has a share of the time the window left it (at most PAGE_S, at most half of it, so two links fit); past it the
        next link starts too, and every read is still waited for, until the window is over. A read still going then is
        left to finish on its own (in_flight), and its page is kept."""
        remaining = iter(links)
        share = min(PAGE_S, run.window.left() / 2)
        reading = UntilWindowEnds(run.usage, run.window)
        pending: dict[Future[Source | None], int] = {}
        listed: set[Future[Source | None]] = set()  # reads of a maker's or a review site's page, still going
        waiting: Source | None = None  # another site's page, kept while a listed page may still come (D-102)
        started = 0
        next_start = monotonic()
        more = True
        while True:
            now = monotonic()
            if run.window.left() < MIN_LEFT_S:
                return waiting
            if more and (not pending or now >= next_start):
                url = next(remaining, None)
                if url is None:
                    more = False
                    continue
                with self._lock:  # started and recorded together: the window's end can't fall between them
                    try:
                        job = run.reads.submit(self._page, url, reading)
                    except RuntimeError:  # the reads were shut: the window is over
                        return None
                    self.in_flight.append(job)
                pending[job] = started
                if preference(url, self.research) < OTHER_KIND:
                    listed.add(job)
                started += 1
                next_start = now + share
                continue
            if not pending:
                return waiting  # every link was read: another site's page if one named the product, else none
            ends = run.window.ends
            until = min(ends, next_start) if more else ends
            done, _ = wait(list(pending), timeout=max(0.0, until - now), return_when=FIRST_COMPLETED)
            if not done and until == ends:
                return waiting  # the window is over: the clock isn't read again for it (see MIN_LEFT_S)
            for job in sorted(done, key=pending.__getitem__):
                del pending[job]
                listed.discard(job)
                if job.cancelled():  # the reads were shut before it started
                    continue
                page = job.result()
                if page is None or not names_product(page.text, product.name):
                    continue
                if not other_site(page):
                    return page
                waiting = waiting or page
            if waiting is not None and not listed:
                return waiting  # no maker's or review page is still coming (they are read first): another site's

    def _links(self, query: str, run: _Run) -> list[str]:
        """Serper's readable links, maker pages first. Cached as those links only: nothing from Reddit is kept."""
        key = ("fact_search", query, run.ask.region)
        cached = self._kept_urls(key)
        if cached is not None:
            return cached
        if not run.take_search():
            return []
        try:
            result = self.search.search(query, run.ask.region, KeepsReserve(run.usage))
        except Exception as exc:  # build rule 9: one search failing loses only its own page
            log.info("enrich: a fact search failed (%s)", type(exc).__name__)
            return []
        failure = result.failure
        if result.from_cache or (failure is not None and nothing_sent(failure.reason, failure.detail)):
            run.give_back()  # answered from research's cache, or refused before it left: nothing was sent
        if failure is not None:
            return []
        hits = [str(s.url) for s in result.sources if readable(str(s.url), self.research)]
        links = list(dict.fromkeys(sorted(hits, key=lambda url: preference(url, self.research))))  # makers first
        self.cache.put_text(key, json.dumps(links))
        return links

    def _page(self, url: str, usage: SearchUsage) -> Source | None:
        cached = self._kept_page(url)
        if cached is not None:
            return cached
        try:
            result = self.pages.fetch(url, usage)
        except Exception as exc:  # build rule 9
            log.info("enrich: a fact page failed (%s)", type(exc).__name__)
            return None
        page = result.source
        if page is None or not readable(str(page.url), self.research):  # a redirect to Reddit is never kept either
            return None
        self.cache.put_sources(("page", url), [page])
        return page

    # --- the one call -----------------------------------------------------------------------------------------------

    def _ask(
        self,
        pages: Sequence[tuple[Source, Product]],
        labelled: Sequence[tuple[EnrichTarget, Product, str]],
        targets: Sequence[tuple[EnrichTarget, Product]],
        ask: FactAsk,
        ends: float,
        search_id: str | None,
    ) -> FactsFound:
        keys = list(dict.fromkeys(t.criterion_key for t, _ in targets))[:10]
        specs = self._specs(keys)
        own = [key for key in keys if key not in specs]
        # The call needs one criterion to carry it: with only specs looked up, the category's first fixed one, whose
        # claims are left out below, as nothing asked for it.
        carried = own or [fallback_criteria(ask.category).criteria[0].key]
        criteria = allowed_criteria(
            ExtractInput(source=pages[0][0], criterion_keys=carried, category_label=ask.category[:60]),
            DataClass.PUBLIC,
            specs=SpecAsk(attributes=specs),
            settings=self.extract,
        )
        message = facts_message(
            criteria, [self._page_ask(page, product, labelled, criteria) for page, product in pages]
        )
        raw, calls = self._answer(message, ends, search_id)
        if raw is None:
            return FactsFound(claims=[], sources=[], llm_calls=calls)
        wanted = {(t.product_id, t.criterion_key): t for t, _ in targets}
        claims: list[Claim] = []
        used: dict[str, Source] = {}
        for number, answer in page_answers(raw, len(pages)).items():
            page, product = pages[number - 1]
            for claim in self._kept(answer, page, product, criteria, wanted, ask):
                claims.append(claim)
                used[page.id] = page
        return FactsFound(claims=claims, sources=list(used.values()), llm_calls=calls)

    @staticmethod
    def _page_ask(
        page: Source,
        product: Product,
        labelled: Sequence[tuple[EnrichTarget, Product, str]],
        criteria: PromptCriteria,
    ) -> PageAsk:
        """The page as the call gets it (D-101): the passages around the product's name and the words of the criteria
        looked up for it, and those criteria as the call names them. The words are public names only: the fixed key
        the call is sent and its label, never a generated key, which can hold the shopper's words (D-51)."""
        sent_as = {criteria.claim_key(key): key for key in criteria.keys}
        own = [
            (sent_as[t.criterion_key], label)
            for t, p, label in labelled
            if p.id == product.id and t.criterion_key in sent_as
        ]
        keys = tuple(dict.fromkeys(key for key, _ in own))
        words = criterion_words([text for pair in own for text in pair])
        return PageAsk(page, product.name, keys, passages(source_text(page), product.name, words))

    def _answer(self, message: str, ends: float, search_id: str | None) -> tuple[RawFacts | None, int]:
        """The call's answer, from the cache if it has it (nothing is sent), and how many calls went out."""
        digest = hashlib.sha256(f"{FACTS_SYSTEM_PROMPT}\n{message}".encode()).hexdigest()
        key = ("fact_answer", digest)
        cached = self.cache.get_text(key)
        if cached is not None:
            try:
                return RawFacts.model_validate_json(cached), 0
            except ValidationError:
                pass
        left = ends - ANSWER_MARGIN_S - monotonic()
        if left < MIN_CALL_S:
            log.info("enrich: too little time left to read the fact pages")
            return None, 0
        meter = TokenMeter(already_spent_s=max(0.0, time_limit_s(TASK) - left))  # the call ends with the look-up
        try:
            with meter.counting():
                raw = self.llm.complete(
                    LLMTask.ENRICH_EXTRACT,  # TASK, written out for the parser registry (US-48)
                    FACTS_SYSTEM_PROMPT,
                    message,
                    RawFacts,
                    data_class=DataClass.PUBLIC,
                    difficulty=Difficulty.LONG,
                    search_id=search_id,
                    max_output_tokens=ANSWER_TOKENS,
                )
        except LLMUnavailable:
            log.info("enrich: no model read the fact pages")
            return None, 1
        except Exception as exc:  # build rule 9
            log.warning("enrich: the fact call raised %s", type(exc).__name__)
            return None, 1
        self.cache.put_text(key, raw.model_dump_json())
        return raw, 1

    def _kept(
        self,
        answer: RawExtraction,
        page: Source,
        product: Product,
        criteria: PromptCriteria,
        wanted: Mapping[tuple[str, str], EnrichTarget],
        ask: FactAsk,
    ) -> list[Claim]:
        """Extraction's own checks (quote on the page word for word, product named), then only the criteria looked up
        for this page's product, each under a name verify knows the product by."""
        result = to_extraction_result(
            answer,
            page,
            criteria.keys,
            self._now(),
            self.extract.domain_tiers,
            ask.category[:60],
        )
        kept: list[Claim] = []
        for claim in _under_own_keys(result, criteria).claims:
            target = wanted.get((product.id, claim.criterion_key or ""))
            if target is None or not is_the_product(claim.product_mention, product, target):
                continue
            try:
                kept.append(Claim.model_validate({**claim.model_dump(), "product_mention": target.product_name}))
            except ValidationError:  # EnrichTarget allows an empty name, which no claim may carry: that one is left out
                continue
        return kept
