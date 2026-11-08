"""The research stage (US-12, US-27): rubric in, sources from Reddit, web search and review sites out.

ResearchStage is the US-52 stub. It stays as the baseline: what the live stage's fallback hands over to, and what tests
use for default stages. LiveResearchStage does the real work, with no LLM call:

1. Queries come from the rubric in code (queries.py).
2. Reddit and up to 5 Serper queries start at once, on one fixed-size pool (fetching.py).
3. When all the first round's replies are in, their results on review sites are queued as pages, best-ranked first, so
   the same answers read the same pages whichever reply arrived first. Then the held Serper slot runs: "X vs Y" if two
   candidates are known, otherwise the sixth query. It waits for Reddit's listing, because those titles name products
   far more often than search results do, but only for half the deadline: a stuck archive must not cost the query.
4. At the deadline, whatever finished is returned, and each source type that failed or did not finish is named as a
   SourceFailure, which the pipeline turns into the coverage note.

One source failing never stops another (build rule 9). No shopper text, URL or error message reaches a log.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from time import monotonic
from typing import Protocol

import httpx
from dotenv import load_dotenv

from app.contracts import (
    ResearchInput,
    ResearchResult,
    Source,
    SourceFailure,
    SourceType,
    StageContext,
    StageName,
    examples,
)

from .cache import ResearchCache, sweep_expired_files
from .client import ClientUnavailable, build_research_client
from .fetching import FetchRun, shared_pool
from .pages import PageResult, ReviewPages
from .queries import SearchQuery, build_queries, comparison_query, first_round, held_query, reddit_query
from .reddit.base import FailureReason, RedditSource
from .reddit.factory import build_reddit_source
from .reddit.settings import load_reddit_settings
from .serper import SerperResult, SerperSource, host_is_under, host_of, shared_limiter, url_key
from .settings import ResearchSettings, load_research_settings
from .threads import CountingBudget, ThreadProgress, find_threads, read_comments
from .usage import SearchUsage, UsageBook

log = logging.getLogger(__name__)

DEADLINE_MARGIN_S = 3.0  # the search's own limit may be nearer than ours; returning late would lose everything
HELD_SLOT_WAIT_SHARE = 0.5  # of the deadline: long enough for a normal listing, with time left for the query and pages
HELD_SLOT_MIN_SHARE = 0.1  # of the deadline: with less left, a reply would not arrive and the credit would be wasted
REDDIT, WEB, REVIEW = SourceType.REDDIT, SourceType.WEB_SEARCH, SourceType.REVIEW_SITE
LIVE_TYPES = (REDDIT, WEB, REVIEW)


class ResearchStage:
    """Returns the three example sources.

    `fail=True` makes run() raise, so fault tests can prove the pipeline survives a research failure.
    """

    name = StageName.RESEARCH
    is_placeholder = True

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    def run(self, inp: ResearchInput, ctx: StageContext) -> ResearchResult:
        if self.fail:
            raise RuntimeError("simulated research failure")
        # The example shows what a timeout looks like, but no search ran here: a shopper must not be told one failed.
        return ResearchResult.model_validate({**examples.research().model_dump(), "failures": []})

    def fallback(self, inp: ResearchInput, ctx: StageContext, error: Exception) -> ResearchResult:
        # One query and no sources: the search goes on, and the coverage note says research fell back.
        query = " ".join(part for part in ("best", inp.rubric.category, inp.rubric.use_case) if part)
        return ResearchResult(queries=[query], sources=[])


class SerperLike(Protocol):
    def search(self, query: str, region: str, usage: SearchUsage, sites: Sequence[str] = ()) -> SerperResult: ...


class PagesLike(Protocol):
    def fetch(self, url: str, usage: SearchUsage) -> PageResult: ...


class _Unset:
    """Not given: build the real one. None is a real answer for Reddit (REDDIT_SOURCE=off)."""


_UNSET = _Unset()
_shared_books: dict[str, UsageBook] = {}
_shared_books_lock = threading.Lock()


def _shared_book(settings: ResearchSettings) -> UsageBook:
    """One book per process, because a stage is built per search and counts must add up across them."""
    with _shared_books_lock:
        return _shared_books.setdefault("book", UsageBook(settings.caps))


@dataclass(frozen=True)
class ResearchReport:
    """How a run went, for the CLI and the log: times and counts only. No query text and no URL."""

    seconds: dict[SourceType, float]
    total_seconds: float
    counts: dict[SourceType, int]
    used: dict[str, int]
    today: dict[str, int]
    caps: dict[str, int]
    comparison: str | None = None  # "X vs Y": product names from titles or known candidates, never the shopper's words


class LiveResearchStage:
    name = StageName.RESEARCH
    is_placeholder = False
    baseline = ResearchStage()

    def __init__(
        self,
        *,
        settings: ResearchSettings | None = None,
        reddit: RedditSource | _Unset | None = _UNSET,
        serper: SerperLike | None = None,
        pages: PagesLike | None = None,
        usage: UsageBook | None = None,
        pool: ThreadPoolExecutor | None = None,
    ) -> None:
        self._settings = settings
        self.reddit = reddit
        self.serper = serper
        self.pages = pages
        self._usage = usage
        self._pool = pool
        self.last_report: ResearchReport | None = None

    def fallback(self, inp: ResearchInput, ctx: StageContext, error: Exception) -> ResearchResult:
        return self.baseline.fallback(inp, ctx, error)

    def run(self, inp: ResearchInput, ctx: StageContext) -> ResearchResult:
        settings = self._settings if self._settings is not None else load_research_settings()
        book = self._usage if self._usage is not None else _shared_book(settings)
        pool = self._pool if self._pool is not None else shared_pool(settings.pool_size)
        wiring = self._wire(settings)
        job = _Job(inp, ctx, settings, book.search(), pool, wiring)
        result = job.run()
        self.last_report = job.report(book)
        _log_report(self.last_report)
        if wiring.client is not None and not job.unfinished:
            wiring.client.close()  # one that a worker still holds at the deadline is left for it to finish with
        return result

    def _wire(self, settings: ResearchSettings) -> _Wiring:
        """The real sources, unless a test gave its own. What can't be built is skipped, with a reason."""
        if self.serper is None or self.pages is None or isinstance(self.reddit, _Unset):
            sweep_expired_files()
        wiring = _Wiring(serper=self.serper, pages=self.pages)
        client: httpx.Client | None = None
        if self.serper is None or self.pages is None:
            try:
                client = build_research_client()
            except ClientUnavailable:
                wiring.serper_problem = wiring.pages_problem = "error"
        wiring.client = client
        if client is not None:
            cache = ResearchCache(enabled=settings.cache_on)
            if self.serper is None:
                load_dotenv()
                key = os.environ.get("SERPER_API_KEY", "").strip() or None
                wiring.serper = SerperSource(
                    client, key, cache=cache, limiter=shared_limiter(settings.serper_interval_s)
                )
            if self.pages is None:
                wiring.pages = ReviewPages(client, settings, cache=cache)
        if isinstance(self.reddit, _Unset):
            try:
                wiring.reddit = build_reddit_source(load_reddit_settings(), http=client)
                if wiring.reddit is None:
                    wiring.reddit_problem = "blocked"  # REDDIT_SOURCE=off
            except Exception as exc:  # no client, or PRAW refusing its settings: only Reddit is lost (rule 9)
                log.warning("research: no Reddit source could be built (%s)", type(exc).__name__)
                wiring.reddit_problem = "error"
        else:
            wiring.reddit = self.reddit
            if self.reddit is None:
                wiring.reddit_problem = "blocked"
        return wiring


@dataclass
class _Wiring:
    serper: SerperLike | None = None
    pages: PagesLike | None = None
    reddit: RedditSource | None = None
    serper_problem: FailureReason | None = None
    pages_problem: FailureReason | None = None
    reddit_problem: FailureReason | None = None
    client: httpx.Client | None = None  # built for this search, so closed after it


@dataclass
class _Timing:
    """When each source type started and last finished."""

    clock: Callable[[], float] = monotonic
    started_at: float = field(default_factory=monotonic)
    finished: dict[SourceType, float] = field(default_factory=dict)

    def mark(self, kind: SourceType) -> None:
        self.finished[kind] = self.clock() - self.started_at


class _Job:
    """One search's research. Its callbacks all run in the stage's own thread (FetchRun.wait), so it needs no locks."""

    def __init__(
        self,
        inp: ResearchInput,
        ctx: StageContext,
        settings: ResearchSettings,
        usage: SearchUsage,
        pool: ThreadPoolExecutor,
        wiring: _Wiring,
    ) -> None:
        self.inp = inp
        self.rubric = inp.rubric
        self.settings = settings
        self.usage = usage
        self.wiring = wiring
        self.queries = build_queries(self.rubric)
        self.comparison: SearchQuery | None = None
        self.deadline_s = min(settings.deadline_s, max(0.0, ctx.seconds_left - DEADLINE_MARGIN_S))
        self.fetches = FetchRun(pool, self.deadline_s)
        self.threads = ThreadProgress()
        self.web: dict[str, tuple[int, int, Source]] = {}  # url_key -> (query number, rank in its reply, source)
        self.review: dict[str, tuple[int, int, Source]] = {}
        self.landed_on: dict[str, str] = {}  # url_key of a link read -> url_key of where it redirected
        self.reddit_titles: list[str] = []
        self.pages_queued: dict[str, int] = {}  # domain -> pages queued
        self.first_round_left = 0
        self.reddit_listing_done = False
        self.slot_wait_over = False
        self.held_slot_used = False
        self.unfinished: list[str] = []
        self.problems: dict[SourceType, list[FailureReason]] = {}
        self.page_failures: list[FailureReason] = []
        self.links_tried: set[str] = set()
        self.timing = _Timing()
        self.started = monotonic()
        self.total_seconds = 0.0

    def run(self) -> ResearchResult:
        self._start_reddit()
        self._start_serper()
        self.fetches.after(self.deadline_s * HELD_SLOT_WAIT_SHARE, self._stop_waiting)
        self.unfinished = self.fetches.wait()
        self.total_seconds = monotonic() - self.started
        for label in self.unfinished:
            self._problem(SourceType(label), "timeout")
        return self._result()

    # --- Reddit ---------------------------------------------------------------------------------

    def _start_reddit(self) -> None:
        reddit = self.wiring.reddit
        if reddit is None:
            self._problem(REDDIT, self.wiring.reddit_problem or "blocked")
            self.reddit_listing_done = True
            return
        budget = CountingBudget(self.usage)
        query = reddit_query(self.rubric)

        def find() -> None:
            find_threads(reddit, query, self.rubric.category, budget, self.settings, self.threads)

        def read() -> None:
            read_comments(reddit, budget, self.settings, self.threads, self.fetches.time_left)

        def listing_done(_: object) -> None:
            self.timing.mark(REDDIT)
            self.reddit_listing_done = True
            self._run_held_slot()
            if not self.threads.finished:
                self.fetches.submit(
                    REDDIT.value, read, on_done=lambda _: self._comments_done(), on_error=comments_failed
                )

        def listing_failed(_: Exception) -> None:
            self.timing.mark(REDDIT)
            self._problem(REDDIT, "error")
            self.reddit_listing_done = True
            self._run_held_slot()

        def comments_failed(_: Exception) -> None:
            self.timing.mark(REDDIT)
            self._problem(REDDIT, "error")

        self.fetches.submit(REDDIT.value, find, on_done=listing_done, on_error=listing_failed)

    def _comments_done(self) -> None:
        self.timing.mark(REDDIT)
        if not self.threads.finished:
            self._problem(REDDIT, "timeout")

    # --- Serper ---------------------------------------------------------------------------------

    def _start_serper(self) -> None:
        serper = self.wiring.serper
        if serper is None:
            reason = self.wiring.serper_problem or "blocked"
            self._problem(WEB, reason)
            self._problem(REVIEW, reason)
            return
        round_one = first_round(self.queries)
        self.first_round_left = len(round_one)
        for number, query in enumerate(round_one):
            self._search(query, number, is_first_round=True)

    def _search(self, query: SearchQuery, number: int, *, is_first_round: bool) -> None:
        serper = self.wiring.serper
        assert serper is not None  # only called once _start_serper has seen it
        sites = self.settings.review_domains if query.goes_to == "review" else ()

        def done(found: SerperResult) -> None:
            self.timing.mark(WEB)
            self._read_serper(found, number)
            if is_first_round:
                self._first_round_step()
            else:
                self._queue_pages()

        def failed(_: Exception) -> None:
            self.timing.mark(WEB)
            self._problem(WEB, "error")
            if is_first_round:
                self._first_round_step()

        self.fetches.submit(
            WEB.value, lambda: serper.search(query.text, self.rubric.region, self.usage, sites), done, failed
        )

    def _first_round_step(self) -> None:
        self.first_round_left -= 1
        if self.first_round_left == 0 or self.slot_wait_over:  # past halfway, nothing waits for a stuck query
            self._queue_pages()
        self._run_held_slot()

    def _stop_waiting(self) -> None:
        """Half the deadline has gone: read what we have, and stop waiting for whatever is stuck."""
        self.slot_wait_over = True
        self._queue_pages()
        self._run_held_slot()

    def _run_held_slot(self) -> None:
        """Once: when the first round is in, and so is Reddit's listing or the wait for it is over."""
        if self.held_slot_used or self.first_round_left > 0 or self.wiring.serper is None:
            return
        if not self.reddit_listing_done and not self.slot_wait_over:
            return
        self.held_slot_used = True
        if self.fetches.time_left() < self.deadline_s * HELD_SLOT_MIN_SHARE:
            return
        self.comparison = comparison_query(self.inp.known_candidates, self._titles(), self.rubric.category)
        held = self.comparison or held_query(self.queries)
        if held is not None:
            self._search(held, len(self.queries), is_first_round=False)

    def _titles(self) -> list[tuple[str, str]]:
        titles = [(str(s.site), s.title) for _, _, s in (*self.web.values(), *self.review.values())]
        reddit = [title for _, title in self.threads.titles] + self.reddit_titles
        # By its title, so one thread seen by the listing and by two searches is still one source.
        return titles + [(f"reddit:{title.casefold()}", title) for title in reddit]

    def _read_serper(self, found: SerperResult, number: int) -> None:
        if found.failure is not None:
            self._problem(WEB, found.failure.reason)
        self.reddit_titles += found.reddit_titles
        for rank, source in enumerate(found.sources):
            url = str(source.url)
            key = url_key(url)
            key = self.landed_on.get(key, key)  # a link we already followed is the page it led to
            place = (number, rank)
            page = self.review.get(key)
            snippet = self.web.get(key)
            if page is not None:
                if place < page[:2]:  # the same order, whichever query answered first
                    self.review[key] = (number, rank, page[2])
            elif snippet is None or place < snippet[:2]:
                self.web[key] = (number, rank, source)

    # --- review pages ---------------------------------------------------------------------------

    def _queue_pages(self) -> None:
        """Best-ranked first, so the caps pick the same pages from the same answers, whichever arrived first."""
        ranked = sorted(self.web.items(), key=lambda pair: (pair[1][0], pair[1][1], pair[0]))
        for _, (_, _, source) in ranked:
            self._queue_page(str(source.url))

    def _queue_page(self, url: str) -> None:
        pages = self.wiring.pages
        domain = self._review_domain(url)
        key = url_key(url)
        if pages is None or domain is None or key in self.links_tried or not self.settings.pages_on:
            return
        if len(self.links_tried) >= self.settings.max_review_pages:
            return
        if self.pages_queued.get(domain, 0) >= self.settings.max_pages_per_domain:
            return
        self.pages_queued[domain] = self.pages_queued.get(domain, 0) + 1
        self.links_tried.add(key)

        def done(found: PageResult) -> None:
            self.timing.mark(REVIEW)
            self._read_page(url, found)

        def failed(_: Exception) -> None:
            self.timing.mark(REVIEW)
            self.page_failures.append("error")

        self.fetches.submit(REVIEW.value, lambda: pages.fetch(url, self.usage), done, failed)

    def _review_domain(self, url: str) -> str | None:
        host = host_of(url)
        if host is None:
            return None
        return next((d for d in self.settings.review_domains if host_is_under(host, d)), None)

    def _read_page(self, url: str, found: PageResult) -> None:
        if found.source is None:
            self.page_failures.append(found.reason or "error")
            return
        link = url_key(url)
        final = url_key(str(found.source.url))  # a redirect can land on a page another result links to
        self.landed_on[link] = final
        known = (self.web.pop(link, None), self.web.pop(final, None), self.review.get(final))
        places = [(entry[0], entry[1]) for entry in known if entry is not None]
        number, rank = min(places, default=(len(self.queries), len(self.review)))
        self.review[final] = (number, rank, found.source)

    # --- the result -----------------------------------------------------------------------------

    def _problem(self, kind: SourceType, reason: FailureReason) -> None:
        self.problems.setdefault(kind, []).append(reason)

    def _review_problems(self) -> list[FailureReason]:
        """Review sites failed if pages were tried and none came back, or none could even be looked for."""
        problems = list(self.problems.get(REVIEW, ()))
        if self.review:  # one page failing while others are read is ordinary, but a cap and running out of time are not
            return [*problems, *(reason for reason in self.page_failures if reason == "quota_exhausted")]
        if problems or self.page_failures:
            return [*problems, *self.page_failures]
        if not self.settings.pages_on:
            return ["blocked"]
        if not self.links_tried and WEB not in self.problems and self.wiring.serper is not None:
            return ["not_found"]
        return self.problems.get(WEB, [])

    def _failures(self) -> list[SourceFailure]:
        """Every different thing that went wrong, by source type. One reason must not hide another."""
        found: dict[SourceType, list[FailureReason]] = {
            REDDIT: [*self.problems.get(REDDIT, ()), *self.threads.failures],
            WEB: self.problems.get(WEB, []),
            REVIEW: self._review_problems(),
        }
        return [
            SourceFailure(source_type=kind, reason=reason)
            for kind in LIVE_TYPES
            for reason in dict.fromkeys(found[kind])
        ]

    def _ordered(self, found: dict[str, tuple[int, int, Source]], limit: int) -> list[Source]:
        """By query, then rank in its reply, so the same answers give the same order however the threads finished."""
        ranked = sorted(found.items(), key=lambda pair: (pair[1][0], pair[1][1], pair[0]))
        return [source for _, (_, _, source) in ranked[:limit]]

    def _result(self) -> ResearchResult:
        threads = self.threads.snapshot()[: self.settings.max_reddit_threads]
        sources = (
            threads
            + self._ordered(self.web, self.settings.max_web_results)
            + self._ordered(self.review, self.settings.max_review_pages)
        )
        texts = [q.text for q in self.queries]
        if self.comparison is not None:
            texts.append(self.comparison.text)
        return ResearchResult(queries=texts, sources=sources, failures=self._failures())

    def report(self, book: UsageBook) -> ResearchReport:
        threads = len(self.threads.snapshot())
        counts = {
            REDDIT: min(threads, self.settings.max_reddit_threads),
            WEB: min(len(self.web), self.settings.max_web_results),
            REVIEW: min(len(self.review), self.settings.max_review_pages),
        }
        seconds = {kind: self.timing.finished.get(kind, 0.0) for kind in LIVE_TYPES}
        return ResearchReport(
            seconds=seconds,
            total_seconds=max(self.total_seconds, *seconds.values()),
            counts=counts,
            used=dict(self.usage.used),
            today=book.today(),
            caps={name: cap.per_day for name, cap in book.caps.items()},
            comparison=self.comparison.text if self.comparison is not None else None,
        )


def _log_report(report: ResearchReport) -> None:
    per_source = ", ".join(f"{kind.value} {report.seconds[kind]:.1f}s ({report.counts[kind]})" for kind in LIVE_TYPES)
    used = ", ".join(f"{name} {count}" for name, count in report.used.items())
    log.info("research: %s; total %.1fs; used %s", per_source, report.total_seconds, used)
