"""Sites that keep failing are skipped for 24 hours, then tried again (US-31).

Each site (a page's host) keeps a score for 24 hours, in the research cache:

- a block counts 1: HTTP 401 or 403, robots.txt saying no, or a crawl delay longer than we wait;
- a failure that repeats counts 1: a 5xx, a timeout or a broken connection that failed again on its one retry;
- a rate limit (HTTP 429) counts 0.5, as the site is busy rather than refusing us;
- a page read by any route (a plain fetch, Jina or Firecrawl) clears the score: the site works.

At 3 the site is skipped for 24 hours, and its score starts again from nothing once they are over. Only the site's own
replies count: a reader's failure (Jina's 429, say) is the reader's, never the site's. With the research cache
switched off (RESEARCH_CACHE=off) nothing is remembered, as with every other cache.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import urlsplit

from .cache import ResearchCache

log = logging.getLogger(__name__)

SiteOutcome = Literal["ok", "blocked", "failed", "rate_limited"]

WEIGHTS: dict[str, float] = {"blocked": 1.0, "failed": 1.0, "rate_limited": 0.5}
SKIP_AT = 3.0
MEMORY = timedelta(hours=24)  # how long a failure counts, and how long a site is skipped
SKIPPED_DETAIL = "skipped for 24 hours: "  # starts a SourceFailure's detail; the coverage note names the sites
NAMED_IN_NOTE = 3
# One lock for every search in the process: each search has its own cache, but they share the files, so a score is
# read and written whole, and two searches can't undo each other's counts.
_LOCK = threading.Lock()


def _utc_now() -> datetime:
    return datetime.now(UTC)


def site_of(url_or_host: str) -> str | None:
    """A page's site: its host in lower case, without a leading "www.". None if there is no host."""
    text = url_or_host.strip()
    host = urlsplit(text if "//" in text else f"//{text}").hostname
    if not host:
        return None
    host = host.lower().rstrip(".")
    return host.removeprefix("www.") or None


def skipped_detail(sites: Sequence[str], limit: int = 300) -> str:
    """The detail that names the skipped sites, sorted, as many as fit in `limit` characters."""
    text = SKIPPED_DETAIL
    for i, site in enumerate(sorted(set(sites))):
        part = site if i == 0 else f", {site}"
        if len(text) + len(part) > limit:
            break
        text += part
    return text


def skipped_sentence(detail: str) -> str:
    """The coverage note's sentence for a skipped-sites detail: at most NAMED_IN_NOTE sites by name."""
    sites = [s for s in detail.removeprefix(SKIPPED_DETAIL).split(", ") if s]
    named = sites[:NAMED_IN_NOTE]
    if len(sites) > NAMED_IN_NOTE:
        listed = f"{', '.join(named)} and {len(sites) - NAMED_IN_NOTE} more"
    elif len(named) > 1:
        listed = f"{', '.join(named[:-1])} and {named[-1]}"
    else:
        listed = named[0] if named else "some sites"
    return f"Skipped {listed} for 24 hours, because they kept failing."


@dataclass
class _State:
    events: list[tuple[datetime, float]] = field(default_factory=list)
    skip_until: datetime | None = None


class SiteMemory:
    """Each site's score, kept 24 hours in the research cache as text under ("site", host)."""

    def __init__(self, cache: ResearchCache, now: Callable[[], datetime] = _utc_now) -> None:
        self._cache = cache
        self._now = now

    def skipped(self, url_or_host: str) -> bool:
        """True while this page's site is being skipped. Never raises: in doubt, the site is tried."""
        try:
            site = site_of(url_or_host)
            if site is None:
                return False
            with _LOCK:
                state = self._read(site)
            return state.skip_until is not None and self._now() < state.skip_until
        except Exception as exc:  # a page is never lost to the memory itself (build rule 9)
            log.warning("research: a site's memory could not be read (%s)", type(exc).__name__)
            return False

    def record(self, url_or_host: str, outcome: SiteOutcome) -> None:
        """What a page's site did. A read page clears its score; a failure adds its weight; 3 means skip 24 hours."""
        try:
            self._record(url_or_host, outcome)
        except Exception as exc:  # never stops a search (build rule 9)
            log.warning("research: a site's memory could not be kept (%s)", type(exc).__name__)

    def _record(self, url_or_host: str, outcome: SiteOutcome) -> None:
        site = site_of(url_or_host)
        if site is None:
            return
        with _LOCK:
            state = self._read(site)
            now = self._now()
            if state.skip_until is not None and now < state.skip_until:
                return  # already skipped: its 24 hours are not stretched
            if outcome == "ok":
                if state.events or state.skip_until is not None:
                    self._write(site, _State())
                return
            events = [(at, weight) for at, weight in state.events if now - at < MEMORY]
            events.append((now, WEIGHTS[outcome]))
            if sum(weight for _, weight in events) >= SKIP_AT:
                self._write(site, _State(skip_until=now + MEMORY))
            else:
                self._write(site, _State(events=events))

    def score(self, url_or_host: str) -> float:
        """The site's score now: what counts towards SKIP_AT."""
        site = site_of(url_or_host)
        if site is None:
            return 0.0
        now = self._now()
        with _LOCK:
            events = self._read(site).events
        return sum(weight for at, weight in events if now - at < MEMORY)

    def _read(self, site: str) -> _State:
        text = self._cache.get_text(("site", site))
        if not text:
            return _State()
        try:
            data = json.loads(text)
            events = [(datetime.fromisoformat(at), float(weight)) for at, weight in data.get("events", [])]
            until = data.get("skip_until")
            skip_until = datetime.fromisoformat(until) if isinstance(until, str) else None
        except (ValueError, TypeError, AttributeError):
            return _State()  # a damaged entry is forgotten, never a reason to skip a site
        times = [at for at, _ in events] + ([skip_until] if skip_until is not None else [])
        if any(at.tzinfo is None for at in times):
            return _State()  # a time with no zone can't be compared: forgotten too
        return _State(events, skip_until)

    def _write(self, site: str, state: _State) -> None:
        body = {
            "events": [[at.isoformat(), weight] for at, weight in state.events],
            "skip_until": state.skip_until.isoformat() if state.skip_until is not None else None,
        }
        self._cache.put_text(("site", site), json.dumps(body))
