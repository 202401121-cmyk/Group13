"""Live smoke test for the Reddit archives (US-12). Calls real APIs: never run it in CI or pytest.

Run from backend/:
    python -m scripts.smoke_sources                      one request per archive: does it answer, how fresh is it
    python -m scripts.smoke_sources --sample "laptop for coding"   also a real search (4 requests per archive)

For each archive (Arctic Shift, PullPush) it asks for the newest post in r/laptops, through the same HTTP layer
the app uses (our User-Agent, 10 s timeout, the rate limits), and prints: works, latency, and the date of the
newest post, which shows how far behind the archive is. With --sample it also runs the app's own thread search
and comment read for the laptop subreddits and prints counts, dates and permalinks.
No username is ever printed. It is a report, not a gate: it exits 0 even when an archive is down.
"""

from __future__ import annotations

import argparse
import io
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic

import httpx

from app.db.breaker import CircuitBreaker
from app.research.client import ClientUnavailable, build_research_client
from app.research.reddit import RequestBudget, load_reddit_settings, subreddits_for
from app.research.reddit.archive import ArchiveSource
from app.research.reddit.arctic_shift import ArcticShiftSource
from app.research.reddit.base import IntervalLimiter, utc_from_epoch
from app.research.reddit.cache import RedditCache
from app.research.reddit.http import ArchiveHttp
from app.research.reddit.parsing import RawPost, parse_items
from app.research.reddit.pullpush import PullPushSource

SUBREDDIT = "laptops"


@dataclass
class Row:
    archive: str
    works: str
    latency: str
    newest: str = "-"
    note: str = ""


def check(source: ArchiveSource, http: httpx.Client) -> Row:
    """One request: the newest post in r/laptops."""
    params: dict[str, str | int] = {"subreddit": SUBREDDIT, "sort": "desc"}
    params.update({"size": 1, "sort_type": "created_utc"} if source.name == "pullpush" else {"limit": 1})
    fetch = ArchiveHttp(source.label, source.http.base_url, http, IntervalLimiter(0), CircuitBreaker())
    started = monotonic()
    fetched = fetch.get(source.listing_path, params, budget=None)
    latency = f"{(monotonic() - started) * 1000:.0f} ms"
    if fetched.failure is not None:
        return Row(source.label, "no", latency, note=fetched.failure.detail)
    posts = parse_items(fetched.items, RawPost)
    when = utc_from_epoch(posts[0].created_utc) if posts else None
    return Row(source.label, "yes", latency, f"{when:%Y-%m-%d %H:%M} UTC" if when else "no posts")


def sample(source: ArchiveSource, query: str) -> list[str]:
    """The app's own search and comment read, for the laptop subreddits. Permalinks and dates only."""
    budget = RequestBudget(4)
    started = monotonic()
    found = source.search_threads(query, subreddits_for("laptop")[:2], limit=25, budget=budget)
    problem = found.failure.detail if found.failure else "no failure"
    head = f"{source.label}: {len(found.sources)} threads in {monotonic() - started:.1f}s"
    if found.newest_at is not None:
        head += f", newest {found.newest_at:%Y-%m-%d}"
    lines = [f"{head} | {found.coverage_note or 'no coverage note'} | {problem}"]
    lines += [f"  {s.published_at:%Y-%m-%d} {s.url}" for s in found.sources[:5] if s.published_at]
    oldest = sorted(found.sources, key=lambda s: s.published_at or datetime.max.replace(tzinfo=UTC))[:2]
    for thread in oldest:  # the archive's comments lag a day or two, so read the oldest matches
        started = monotonic()
        comments = source.get_comments(str(thread.url).rstrip("/").split("/")[-1], limit=50, budget=budget)
        detail = comments.failure.detail if comments.failure else "no failure"
        lines.append(f"  comments of {thread.url}: {len(comments.sources)} in {monotonic() - started:.1f}s ({detail})")
    lines.append(f"  requests used: {budget.used} of {budget.max_requests}")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sample", metavar="QUERY", help="also run a real search for QUERY in the laptop subreddits")
    args = parser.parse_args(argv)
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    settings = load_reddit_settings()
    try:
        client = build_research_client()
    except ClientUnavailable as exc:  # its text is the error's type only, never the proxy's URL
        print(f"could not build an HTTP client ({exc}): the archives were not checked")
        return 0
    with client as http:
        cache = RedditCache(folder=None)  # a smoke test must not fill or read the app's cache
        sources: list[ArchiveSource] = [
            ArcticShiftSource(http, base_url=settings.arctic_shift_base_url, cache=cache),
            PullPushSource(http, base_url=settings.pullpush_base_url, cache=cache),
        ]
        rows = [check(source, http) for source in sources]
        print(f"{'archive':<13} {'works':<6} {'latency':<9} {'newest post in r/' + SUBREDDIT:<26} note")
        for r in rows:
            print(f"{r.archive:<13} {r.works:<6} {r.latency:<9} {r.newest:<26} {r.note}")
        if args.sample:
            for source in sources:
                print()
                print("\n".join(sample(source, args.sample)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
