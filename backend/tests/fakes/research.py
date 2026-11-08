"""Test doubles for the research stage (US-12, US-27). Every site and page here is made up; nothing is ever called."""

from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

from app.contracts import Budget, Money, Rubric, Source, SourceFailure, SourceTier, SourceType, WeightedCriterion
from app.contracts.research import DEFAULT_TIER
from app.research.reddit import RedditResult, RequestBudget
from app.research.reddit.base import failure as reddit_failure
from app.research.reddit.base import make_source, newest
from app.research.serper import SerperResult
from app.research.usage import SearchUsage
from tests.fakes.reddit import NOW

SERPER_PATH = "/search"


def rubric(
    category: str = "laptop",
    use: str | None = "coding",
    at_most: float | None = 70000,
    at_least: float | None = None,
    currency: str = "INR",
    region: str = "IN",
) -> Rubric:
    budget = None
    if at_most is not None or at_least is not None:
        budget = Budget(
            min=None if at_least is None else Money(amount=at_least, currency=currency),
            max=None if at_most is None else Money(amount=at_most, currency=currency),
        )
    return Rubric(
        category=category,
        use_case=use,
        region=region,
        budget=budget,
        weights=[WeightedCriterion(key="value_for_money", weight=5, normalized=1.0, origin="default")],
    )


def web_source(url: str, title: str = "A made-up page", text: str = "Made-up text.", site: str | None = None) -> Source:
    host = site or (httpx.URL(url).host.removeprefix("www."))
    return Source(
        id=f"src_{hashlib.sha256(url.encode()).hexdigest()[:16]}",
        url=url,
        type=SourceType.WEB_SEARCH,
        tier=DEFAULT_TIER[SourceType.WEB_SEARCH],
        title=title,
        text=text,
        text_hash=hashlib.sha256(text.encode()).hexdigest(),
        fetched_at=NOW,
        site=host,
        via="search_snippet",
    )


def review_source(url: str, title: str = "A made-up review", text: str = "A made-up review's text.") -> Source:
    host = httpx.URL(url).host.removeprefix("www.")
    return Source(
        id=f"src_{hashlib.sha256(url.encode()).hexdigest()[:16]}",
        url=url,
        type=SourceType.REVIEW_SITE,
        tier=SourceTier.EDITORIAL,
        title=title,
        text=text,
        text_hash=hashlib.sha256(text.encode()).hexdigest(),
        fetched_at=NOW,
        site=host,
        via="direct",
    )


def thread(
    thread_id: str,
    title: str = "Laptop for coding",
    upvotes: int | None = None,
    comment_count: int | None = None,
    text: str | None = None,
    subreddit: str = "laptops",
    fetched_at: datetime = NOW,
    author_digest: str | None = None,
) -> Source:
    source = make_source(
        origin="arctic_shift",
        subreddit=subreddit,
        thread_id=thread_id,
        title=title,
        text=text or title,
        published_at=NOW,
        author_digest=author_digest,
        fetched_at=fetched_at,
        upvotes=upvotes,
        comment_count=comment_count,
    )
    assert source is not None
    return source


def serper_reply(*organic: dict[str, str]) -> dict[str, Any]:
    return {"searchParameters": {"q": "made up"}, "organic": list(organic)}


def result(link: str, title: str = "A made-up result", snippet: str = "A made-up snippet.") -> dict[str, str]:
    return {"title": title, "link": link, "snippet": snippet}


# --- fake sources for the stage's own tests -------------------------------------------------------


@dataclass
class FakeSerper:
    """Answers every query the same way, unless `answers` names the query. Spends one Serper unit per call."""

    answers: dict[str, SerperResult] = field(default_factory=dict)
    default: SerperResult = field(default_factory=SerperResult)
    calls: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)
    delay_s: float = 0.0
    error: Exception | None = None
    charge: bool = True
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def search(self, query: str, region: str, usage: SearchUsage, sites: Sequence[str] = ()) -> SerperResult:
        with self._lock:
            self.calls.append((query, tuple(sites)))
        if self.charge and not usage.take("serper"):
            return SerperResult(failure=SourceFailure(source_type=SourceType.WEB_SEARCH, reason="quota_exhausted"))
        if self.delay_s:
            time.sleep(self.delay_s)
        if self.error is not None:
            raise self.error
        return self.answers.get(query, self.default)

    @property
    def queries(self) -> list[str]:
        return [query for query, _ in self.calls]


@dataclass
class FakePages:
    """A page per URL, or a failure reason. Unknown URLs are "not_found"."""

    pages: dict[str, Source] = field(default_factory=dict)
    reasons: dict[str, str] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)
    delay_s: float = 0.0
    error: Exception | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def fetch(self, url: str, usage: SearchUsage) -> Any:
        from app.research.pages import PageResult

        with self._lock:
            self.calls.append(url)
        if self.delay_s:
            time.sleep(self.delay_s)
        if self.error is not None:
            raise self.error
        if url in self.pages:
            return PageResult(source=self.pages[url])
        return PageResult(reason=self.reasons.get(url, "not_found"))  # type: ignore[arg-type]


@dataclass
class FakeReddit:
    """A RedditSource: the threads it was given, and comments only if `comments` names the thread's URL."""

    threads: Sequence[Source] = ()
    with_comment_counts: dict[str, int] = field(default_factory=dict)
    failure_reason: str | None = None
    delay_s: float = 0.0
    wait_for: threading.Event | None = None
    error: Exception | None = None
    comment_error: Exception | None = None
    comment_calls: list[str] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    subreddits: list[Sequence[str]] = field(default_factory=list)
    comment_delay_s: float = 0.0
    comment_failure_reason: str | None = None
    name: str = "fake"

    def search_threads(
        self,
        query: str,
        subreddits: Sequence[str],
        after: datetime | None = None,
        limit: int = 25,
        budget: RequestBudget | None = None,
    ) -> RedditResult:
        self.queries.append(query)
        self.subreddits.append(subreddits)
        if self.delay_s:
            time.sleep(self.delay_s)
        if self.wait_for is not None:
            self.wait_for.wait(10)
        if self.error is not None:
            raise self.error
        if self.failure_reason is not None:
            return RedditResult(failure=reddit_failure(self.failure_reason, "made up"))  # type: ignore[arg-type]
        kept = tuple(self.threads[:limit])
        return RedditResult(kept, newest(kept), origin="arctic_shift")

    def get_comments(
        self, thread_id: str, depth: int = 3, limit: int = 100, budget: RequestBudget | None = None
    ) -> RedditResult:
        return RedditResult(origin="arctic_shift")

    def with_comments(
        self, thread: Source, depth: int = 3, limit: int = 100, budget: RequestBudget | None = None
    ) -> RedditResult:
        self.comment_calls.append(str(thread.url))
        if self.comment_delay_s:
            time.sleep(self.comment_delay_s)
        if self.comment_error is not None:
            raise self.comment_error
        if self.comment_failure_reason is not None:
            return RedditResult(failure=reddit_failure(self.comment_failure_reason, "made up"))  # type: ignore[arg-type]
        return RedditResult((thread,), origin="arctic_shift")


# --- a made-up web server for the page fetcher ---------------------------------------------------


Answer = httpx.Response | Exception | Callable[[httpx.Request], httpx.Response]


@dataclass
class Web:
    """Answers by full URL. robots.txt defaults to 404 (which allows everything), and anything else unknown too."""

    routes: dict[str, Answer] = field(default_factory=dict)
    requests: list[httpx.Request] = field(default_factory=list)

    def on(self, url: str, answer: Answer) -> Web:
        self.routes[url] = answer
        return self

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self._handle))

    def asked(self, url: str) -> int:
        return sum(str(r.url) == url for r in self.requests)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.routes.get(str(request.url))
        if answer is None:
            return httpx.Response(404)
        if isinstance(answer, Exception):
            raise answer
        if callable(answer) and not isinstance(answer, httpx.Response):
            return answer(request)
        assert isinstance(answer, httpx.Response)
        return answer


def html_page(body: str = "", title: str = "A made-up review", head: str = "") -> httpx.Response:
    words = body or " ".join(f"word{i}" for i in range(200))
    page = f"<!doctype html><html><head><title>{title}</title>{head}</head><body>{words}</body></html>"
    return httpx.Response(200, text=page, headers={"content-type": "text/html; charset=utf-8"})
