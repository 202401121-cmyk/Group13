"""Web search results through Serper (US-12).

Each result becomes a web_search Source holding its title and snippet, unless:
- it points at Reddit (D-53): only its title is kept, for counting candidates, and its snippet goes nowhere;
- it points at YouTube: its title stands in for the text, because search results that link to videos are titles and
  links only (docs/api-setup.md).

Results are cached for 24 hours by query and region (P-12), so a repeat costs nothing, even when Serper is capped. The
key travels only in a header, and failures carry a fixed reason, never an error's text.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from typing import Any

import httpx
from pydantic import ValidationError

from app.contracts import Source, SourceFailure, SourceType
from app.contracts.research import DEFAULT_TIER, MAX_SOURCE_CHARS

from .cache import ResearchCache
from .reddit.base import FailureReason, IntervalLimiter, clean_api_text
from .usage import SearchUsage

log = logging.getLogger(__name__)

SERPER_URL = "https://google.serper.dev/search"
RESULTS_PER_QUERY = 10  # 10 is one credit; more costs two
TIMEOUT_S = 10.0
_REDDIT_HOSTS = ("reddit.com", "redd.it")
_VIDEO_HOSTS = ("youtube.com", "youtu.be")
_REDDIT_TITLES_SEPARATOR = "\n"
_REDDIT_SUFFIX = re.compile(r"(?:\s*[:|-]\s*(?:r/\w+|reddit))+\s*$", re.IGNORECASE)  # "A title : r/laptops - Reddit"


@cache
def shared_limiter(interval_s: float) -> IntervalLimiter:
    """One limiter for the whole process, however many sources are built: research.yaml's serper_interval_s."""
    return IntervalLimiter(interval_s)


SHARED_LIMITER = shared_limiter(0.2)


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class SerperResult:
    sources: tuple[Source, ...] = ()
    reddit_titles: tuple[str, ...] = ()
    failure: SourceFailure | None = None
    from_cache: bool = False


def _failure(reason: FailureReason, detail: str) -> SerperResult:
    return SerperResult(failure=SourceFailure(source_type=SourceType.WEB_SEARCH, reason=reason, detail=detail))


def host_is_under(host: str, domain: str) -> bool:
    """True for the domain itself and anything under it: www.a.example is under a.example, nota.example is not."""
    host = host.lower().rstrip(".")
    return host == domain or host.endswith(f".{domain}")


def host_of(url: str) -> str | None:
    try:
        parts = httpx.URL(url)
        host = parts.host  # read here: it decodes the name, and an invalid one raises a ValueError
    except (httpx.InvalidURL, TypeError, ValueError):
        return None
    return host.lower() if parts.scheme in ("http", "https") and host else None


def url_key(url: str) -> str:
    """One page however it was linked: any scheme, no fragment, no trailing slash, the host in lower case."""
    try:
        parts = httpx.URL(url)
        host = parts.host.lower()
    except (httpx.InvalidURL, TypeError, ValueError):
        return url
    path, _, query = parts.raw_path.decode("ascii", errors="replace").partition("?")  # as written: %2F is not a /
    port = f":{parts.port}" if parts.port else ""
    path = path.rstrip("/")
    return f"{host}{port}{path}" + (f"?{query}" if query else "")


class SerperSource:
    def __init__(
        self,
        http: httpx.Client,
        api_key: str | None,
        *,
        cache: ResearchCache,
        limiter: IntervalLimiter = SHARED_LIMITER,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.http = http
        self._api_key = api_key
        self.cache = cache
        self.limiter = limiter
        self._now = now

    def __repr__(self) -> str:
        return f"SerperSource(key_set={self._api_key is not None})"  # never the key, not even in a traceback

    def search(self, query: str, region: str, usage: SearchUsage, sites: Sequence[str] = ()) -> SerperResult:
        text = f"{query} ({' OR '.join(f'site:{site}' for site in sites)})" if sites else query
        country = region.lower() if len(region) == 2 else ""
        key = ("serper", text, country)
        cached = self.cache.get_sources(key)
        if cached is not None:
            titles = self.cache.get_text((*key, "reddit_titles")) or ""
            return SerperResult(cached, _split_titles(titles), from_cache=True)
        if not self._api_key:
            return _failure("blocked", "web search: SERPER_API_KEY is not set")
        if not usage.take("serper"):
            return _failure("quota_exhausted", "web search: a Serper cap is reached")
        result = self._send(text, country)
        if result.failure is None:
            self.cache.put_sources(key, result.sources)
            self.cache.put_text((*key, "reddit_titles"), _REDDIT_TITLES_SEPARATOR.join(result.reddit_titles))
        return result

    def _send(self, text: str, country: str) -> SerperResult:
        body: dict[str, str | int] = {"q": text, "hl": "en", "num": RESULTS_PER_QUERY}
        if country:
            body["gl"] = country
        self.limiter.wait()
        try:
            response = self.http.post(
                SERPER_URL, json=body, headers={"X-API-KEY": self._api_key or ""}, timeout=TIMEOUT_S
            )
        except httpx.TimeoutException:
            return _failure("timeout", f"web search: no reply within {TIMEOUT_S:g}s")
        except httpx.HTTPError as exc:
            return _failure("error", f"web search: request failed ({type(exc).__name__})")
        status = response.status_code
        if status in (401, 403):
            return _failure("blocked", f"web search: HTTP {status} (refused)")
        if status == 402:
            return _failure("quota_exhausted", "web search: HTTP 402 (no credits left)")
        if status == 429:
            return _failure("rate_limited", "web search: HTTP 429 (rate limited)")
        if not 200 <= status < 300:
            return _failure("error", f"web search: HTTP {status}")
        try:
            reply = response.json()
        except ValueError:
            return _failure("error", "web search: the reply was not JSON")
        organic = reply.get("organic") if isinstance(reply, dict) else None
        if not isinstance(organic, list):
            return _failure("error", "web search: the reply had no results list")
        return self._read(organic)

    def _read(self, organic: list[Any]) -> SerperResult:
        sources: list[Source] = []
        titles: list[str] = []
        seen: set[str] = set()
        for item in organic:
            if not isinstance(item, dict):
                continue
            # A link is an address, so it is never decoded as text: "&region=" must not become a registered sign.
            raw_link = item.get("link")
            link = raw_link.strip() if isinstance(raw_link, str) else ""
            title, snippet = _text(item, "title"), _text(item, "snippet")
            host = host_of(link)
            if host is None or link in seen:
                continue
            seen.add(link)
            if any(host_is_under(host, domain) for domain in _REDDIT_HOSTS):
                if bare_title := _REDDIT_SUFFIX.sub("", title):  # one thread reads the same in every reply
                    titles.append(bare_title)
                continue
            if any(host_is_under(host, domain) for domain in _VIDEO_HOSTS):
                snippet = title
            source = self._source(link, host, title, snippet or title)
            if source is not None:
                sources.append(source)
        return SerperResult(tuple(sources), tuple(titles))

    def _source(self, link: str, host: str, title: str, text: str) -> Source | None:
        body = text[:MAX_SOURCE_CHARS].strip()
        if not body:
            return None
        try:
            return Source(
                id=f"src_{hashlib.sha256(link.encode()).hexdigest()[:16]}",
                url=link,
                type=SourceType.WEB_SEARCH,
                tier=DEFAULT_TIER[SourceType.WEB_SEARCH],
                title=title[:300],
                text=body,
                text_hash=hashlib.sha256(body.encode()).hexdigest(),
                fetched_at=self._now(),
                site=host.removeprefix("www.")[:120],
                via="search_snippet",
            )
        except (ValidationError, ValueError):  # skip the result, never crash the search
            return None


def _text(item: dict[str, Any], name: str) -> str:
    value = item.get(name)
    return " ".join(clean_api_text(value).split()) if isinstance(value, str) else ""


def _split_titles(text: str) -> tuple[str, ...]:
    return tuple(line for line in text.split(_REDDIT_TITLES_SEPARATOR) if line)
