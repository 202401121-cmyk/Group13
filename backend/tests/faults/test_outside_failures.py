"""US-31 (#54): every provider and every source, failed on purpose: a timeout, a 5xx, a 429 and a bad key or refusal.

Each ends in a result, a clean failure or a clear note, never a crash. The rule every outside call follows
(app/llm/retry.py): one retry after a timeout, a 5xx or a connection error, after a short jitter; a 429 retried only
after its Retry-After (and, for an LLM, only when no other model is waiting); never after another 4xx; and only when
a whole attempt still fits before the stage's limit.

Fake transports and fake clocks: no real API is called and nothing waits for real time. YouTube joined with its source
(S4-15); Serper shopping joins when its source does (S4-14).
"""

from __future__ import annotations

import dataclasses
import json
import time
from collections.abc import Callable
from datetime import date
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from app import contracts as C
from app.contracts import DataClass, Difficulty, LLMTask, LLMUnavailable, Provider, SourceType
from app.db.breaker import CircuitBreaker
from app.extract import GuardedExtractStage
from app.llm import InMemoryCallLog, WrapperLLMClient, load_llm_config
from app.llm.health import ProviderHealth
from app.llm.pool import ModelPool
from app.llm.providers import Adapter, OpenAICompatibleAdapter
from app.llm.retry import RetryClock
from app.pipeline import (
    LLM_UNAVAILABLE_NOTE,
    InMemoryResultStore,
    PipelineRunner,
    build_coverage_note,
    default_search_stages,
    every_llm_call_failed,
)
from app.research.cache import ResearchCache
from app.research.pages import ReviewPages
from app.research.readers import FirecrawlClient, FirecrawlGate, JinaReader, ReaderText
from app.research.reddit import IntervalLimiter
from app.research.reddit.arctic_shift import ArcticShiftSource
from app.research.reddit.cache import RedditCache
from app.research.reddit.pullpush import PullPushSource
from app.research.serper import SerperSource
from app.research.settings import ProviderCap, ResearchSettings
from app.research.shopping import SerperShopping
from app.research.sites import SKIPPED_DETAIL
from app.research.usage import SearchUsage, UsageBook
from app.research.youtube import YouTubeSource
from tests.fakes.reddit import NOW, Archive, Clock
from tests.fakes.research import FakePages, FakeReddit, Web
from tests.fakes.retry import NoJitter
from tests.faults.research import test_source_faults as research_faults
from tests.unit import test_pipeline_extract as pipeline_extract

FAILURES = ("timeout", "5xx", "429", "bad key")
Handler = Callable[[httpx.Request], httpx.Response]


def _failing(kind: str) -> Handler:
    """A reply that fails the same way every time it is asked."""

    def handle(request: httpx.Request) -> httpx.Response:
        if kind == "timeout":
            raise httpx.ReadTimeout("made up", request=request)
        if kind == "5xx":
            return httpx.Response(503)
        if kind == "429":
            return httpx.Response(429, headers={"Retry-After": "1"})
        return httpx.Response(401)

    return handle


class Counted:
    """A transport that counts what it was asked."""

    def __init__(self, handle: Handler) -> None:
        self.handle = handle
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handle(request)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))


# --- every LLM provider -----------------------------------------------------------------------------------------------

OK_REPLY = json.dumps(
    {
        "choices": [{"message": {"content": '{"product": "Nova Book 14"}'}}],
        "usage": {"prompt_tokens": 9, "completion_tokens": 9},
    }
)


class Answer(BaseModel):
    product: str


def _adapter(provider: Provider, clock: Clock, transport: Counted) -> OpenAICompatibleAdapter:
    base_url = load_llm_config().provider(provider).base_url
    return OpenAICompatibleAdapter(provider, base_url, "made-up-key", transport.client(), clock=clock)


def _llm(adapters: dict[Provider, Adapter], clock: Clock) -> WrapperLLMClient:
    config = load_llm_config()
    pool = ModelPool(config.pool, clock=clock, sleep=clock.sleep, today=lambda: date(2026, 11, 15))
    health = ProviderHealth(clock)
    return WrapperLLMClient(config, adapters, InMemoryCallLog(), health=health, limiters={}, pool=pool, rng=NoJitter())


def _read_a_page(client: WrapperLLMClient) -> Answer:
    return client.complete(
        LLMTask.EXTRACT_CLAIMS,
        "Read the page.",
        "A made-up page.",
        Answer,
        data_class=DataClass.PUBLIC,
        difficulty=Difficulty.LONG,
    )


# Requests the failing provider gets. Groq's 3 pool models are each a model of their own; a bad key stops all three.
SENT = {"timeout": 2, "5xx": 2, "429": 1, "bad key": 1}
GROQ_SENT = {"timeout": 6, "5xx": 6, "429": 3, "bad key": 1}


@pytest.mark.parametrize("kind", FAILURES)
@pytest.mark.parametrize("provider", [Provider.GROQ, Provider.GEMINI, Provider.MISTRAL, Provider.DEEPSEEK])
def test_a_failing_provider_gets_its_one_retry_then_the_next_answers(provider: Provider, kind: str) -> None:
    """Public page extraction: the failing provider, then OpenRouter, the last model on the route, which answers."""
    clock = Clock()
    failing = Counted(_failing(kind))
    backup = Counted(lambda _: httpx.Response(200, text=OK_REPLY, headers={"content-type": "application/json"}))
    client = _llm(
        {
            provider: _adapter(provider, clock, failing),
            Provider.OPENROUTER: _adapter(Provider.OPENROUTER, clock, backup),
        },
        clock,
    )
    assert _read_a_page(client).product == "Nova Book 14"
    assert len(failing.requests) == (GROQ_SENT if provider is Provider.GROQ else SENT)[kind]
    assert len(backup.requests) == 1
    if kind == "bad key":
        assert client.health.state(provider) == "dead"
    elif kind == "5xx" and provider is not Provider.GROQ:
        assert client.health.state(provider) == "cooling"  # it failed again on its retry


@pytest.mark.parametrize(("kind", "sent"), [("timeout", 2), ("5xx", 2), ("429", 2), ("bad key", 1)])
def test_the_last_provider_failing_ends_in_llm_unavailable_never_a_crash(kind: str, sent: int) -> None:
    """OpenRouter alone: as the route's last model, even its 429 is waited out once (Retry-After: 1)."""
    clock = Clock()
    failing = Counted(_failing(kind))
    client = _llm({Provider.OPENROUTER: _adapter(Provider.OPENROUTER, clock, failing)}, clock)
    started = clock.now
    with pytest.raises(LLMUnavailable):
        _read_a_page(client)
    assert len(failing.requests) == sent
    assert clock.now - started < 15  # inside the extract stage's limit
    if kind == "429":
        assert 1.0 in clock.slept  # its Retry-After, with the jitter at its shortest


# --- every research source --------------------------------------------------------------------------------------------

# A refused or bad key is never retried: the same request would be refused the same way.
SOURCE_SENT = {"timeout": 2, "5xx": 2, "429": 2, "bad key": 1}
REASONS = {"timeout": "timeout", "5xx": "error", "429": "rate_limited", "bad key": "blocked"}
# Firecrawl's 429 pauses it for the whole process for a minute, so it is not retried (the rule allows, never asks).
FIRECRAWL_SENT = {**SOURCE_SENT, "429": 1}


def _usage() -> SearchUsage:
    caps = {
        name: ProviderCap(per_search=20, per_day=200)
        for name in (
            "serper",
            "firecrawl",
            "firecrawl_pages",
            "jina",
            "pages",
            "reddit_archive",
            "youtube_search",
            "youtube",
        )
    }
    return UsageBook(caps, path=None).search()


def _retry(clock: Clock) -> RetryClock:
    return RetryClock(sleep=clock.sleep, rng=NoJitter())


@pytest.mark.parametrize("kind", FAILURES)
def test_serper(kind: str) -> None:
    clock = Clock()
    web = Counted(_failing(kind))
    serper = SerperSource(
        web.client(),
        "made-up-key",
        cache=ResearchCache(folder=None, now=lambda: NOW),
        limiter=IntervalLimiter(0.0, clock=clock, sleep=clock.sleep),
        now=lambda: NOW,
        retry=_retry(clock),
    )
    usage = _usage()
    found = serper.search("laptop for coding", "IN", usage)
    assert found.failure is not None and found.failure.reason == REASONS[kind]
    assert len(web.requests) == SOURCE_SENT[kind]
    assert usage.used["serper"] == SOURCE_SENT[kind]  # a retry is a query like any other: it is counted


@pytest.mark.parametrize("kind", FAILURES)
def test_serper_shopping(kind: str) -> None:
    """S4-26: Serper shopping keeps the same one-retry rule as web search."""
    clock = Clock()
    shop = Counted(_failing(kind))
    shopping = SerperShopping(
        shop.client(),
        "made-up-key",
        cache=ResearchCache(folder=None, now=lambda: NOW),
        limiter=IntervalLimiter(0.0, clock=clock, sleep=clock.sleep),
        now=lambda: NOW,
        retry=_retry(clock),
    )
    usage = _usage()
    found = shopping.search("Acer Swift Go 14", usage)
    assert found.failure == REASONS[kind]
    assert len(shop.requests) == SOURCE_SENT[kind]
    assert usage.used["serper"] == SOURCE_SENT[kind]  # a retry is a request like any other: it is counted


@pytest.mark.parametrize("kind", FAILURES)
def test_youtube(kind: str) -> None:
    clock = Clock()
    web = Counted(_failing(kind))
    youtube = YouTubeSource(
        web.client(),
        "made-up-key",
        cache=ResearchCache(folder=None, now=lambda: NOW),
        now=lambda: NOW,
        retry=_retry(clock),
    )
    usage = _usage()
    found = youtube.search("best earbuds review", usage)
    assert found.failure is not None and found.failure.reason == REASONS[kind]
    assert found.sources == ()
    assert len(web.requests) == SOURCE_SENT[kind]  # search.list failed, so nothing else was asked
    assert usage.used["youtube_search"] == SOURCE_SENT[kind]  # a retry is a call like any other: it is counted


def _firecrawl(web: Counted, clock: Clock) -> FirecrawlClient:
    return FirecrawlClient(
        web.client(),
        "made-up-key",
        cache=ResearchCache(folder=None, now=lambda: NOW),
        gate=FirecrawlGate(clock),
        now=lambda: NOW,
        retry=_retry(clock),
    )


@pytest.mark.parametrize("kind", FAILURES)
def test_firecrawl_search(kind: str) -> None:
    clock = Clock()
    web = Counted(_failing(kind))
    found = _firecrawl(web, clock).search("laptop for coding", "IN", _usage())
    assert found.failure is not None and found.failure.reason == REASONS[kind]
    assert found.failure.detail.startswith("Firecrawl: search")
    assert len(web.requests) == FIRECRAWL_SENT[kind]


@pytest.mark.parametrize("kind", FAILURES)
def test_firecrawl_pages(kind: str) -> None:
    clock = Clock()
    web = Counted(_failing(kind))
    read = _firecrawl(web, clock).scrape("https://www.reviews.example/nova", _usage())
    assert not isinstance(read, ReaderText) and read[0] == REASONS[kind]
    assert len(web.requests) == FIRECRAWL_SENT[kind]


def test_a_firecrawl_retry_needs_its_own_place_in_the_minute() -> None:
    clock = Clock()
    web = Counted(_failing("5xx"))
    client = FirecrawlClient(
        web.client(),
        "made-up-key",
        cache=ResearchCache(folder=None, now=lambda: NOW),
        gate=FirecrawlGate(clock, per_minute=1),
        now=lambda: NOW,
        retry=_retry(clock),
    )
    client.scrape("https://www.reviews.example/nova", _usage())
    assert len(web.requests) == 1  # the minute's one place went to the first request


@pytest.mark.parametrize("kind", FAILURES)
def test_jina(kind: str) -> None:
    clock = Clock()
    web = Counted(_failing(kind))
    usage = _usage()
    read = JinaReader(web.client(), None, retry=_retry(clock)).read("https://www.reviews.example/nova", usage)
    assert not isinstance(read, ReaderText) and read[0] == REASONS[kind]
    assert len(web.requests) == SOURCE_SENT[kind]
    assert usage.used["jina"] == SOURCE_SENT[kind]


PAGE = "https://www.reviews.example/nova-book-14"


def _pages(web: Web, clock: Clock, cache: ResearchCache | None = None) -> ReviewPages:
    return ReviewPages(
        web.client(),
        ResearchSettings(review_domains=("reviews.example",)),
        cache=cache or ResearchCache(folder=None, now=lambda: NOW),
        now=lambda: NOW,
        clock=clock,
        sleep=clock.sleep,
        retry=_retry(clock),
    )


@pytest.mark.parametrize(
    ("kind", "sent", "reason"),
    [("timeout", 2, "timeout"), ("5xx", 2, "error"), ("429", 2, "rate_limited"), ("bad key", 1, "blocked")],
)
def test_plain_pages(kind: str, sent: int, reason: str) -> None:
    """With no reader to hand the page to, the plain fetch's own reason stands. A 403 is a refusal like a bad key."""
    clock = Clock()
    failing = _failing(kind) if kind != "bad key" else (lambda _: httpx.Response(403))
    web = Web().on(PAGE, failing)  # robots.txt is a 404: everything is allowed
    found = _pages(web, clock).fetch(PAGE, _usage())
    assert found.source is None and found.reason == reason
    assert web.asked(PAGE) == sent


ARCHIVES = {
    "arctic_shift": (ArcticShiftSource, "https://arctic.example.test", "/api/posts/search"),
    "pullpush": (PullPushSource, "https://pullpush.example.test", "/reddit/search/submission/"),
}


@pytest.mark.parametrize("kind", FAILURES)
@pytest.mark.parametrize("name", ARCHIVES)
def test_reddit_archives(name: str, kind: str) -> None:
    clock = Clock()
    kind_of, base_url, path = ARCHIVES[name]
    answer: Any
    if kind == "timeout":
        answer = httpx.ReadTimeout("made up")
    elif kind == "5xx":
        answer = httpx.Response(503)
    elif kind == "429":
        answer = httpx.Response(429, headers={"Retry-After": "1"})
    else:
        answer = httpx.Response(403)
    archive = Archive().on(path, answer)
    source = kind_of(
        archive.client(),
        base_url=base_url,
        salt="made-up-salt",
        cache=RedditCache(folder=None, now=lambda: NOW),
        limiter=IntervalLimiter(0.0, clock=clock, sleep=clock.sleep),
        breaker=CircuitBreaker(3, 600, clock=clock),
        now=lambda: NOW,
        sleep=clock.sleep,
        rng=NoJitter(),
    )
    found = source.search_threads("laptop", ["laptops"])
    assert found.failure is not None and found.failure.reason == REASONS[kind]
    assert len(archive.requests) == SOURCE_SENT[kind]


# --- the edge cases ---------------------------------------------------------------------------------------------------


def test_one_retry_only_however_often_it_fails() -> None:
    clock = Clock()
    web = Counted(_failing("timeout"))
    JinaReader(web.client(), None, retry=_retry(clock)).read("https://www.reviews.example/nova", _usage())
    assert len(web.requests) == 2
    assert clock.slept == [0.2]  # one jitter, at its shortest


def test_a_retry_that_would_outlast_the_stage_is_not_made() -> None:
    clock = Clock()
    web = Counted(_failing("5xx"))
    retry = RetryClock(
        time_left=lambda: 12.0, sleep=clock.sleep, rng=NoJitter()
    )  # Jina's 12 s and the jitter don't fit
    JinaReader(web.client(), None, retry=retry).read("https://www.reviews.example/nova", _usage())
    assert len(web.requests) == 1


def test_a_429_counts_half_against_its_site(tmp_path: Any) -> None:
    clock = Clock()
    web = Web().on(PAGE, lambda _: httpx.Response(429))
    pages = _pages(web, clock, ResearchCache(folder=tmp_path, now=lambda: NOW))
    pages.sites = type(pages.sites)(pages.cache, now=lambda: NOW)
    for _ in range(2):
        pages.fetch(PAGE, _usage())
    assert pages.sites.score(PAGE) == 1.0  # two rate limits weigh what one block does
    assert pages.skipped_site(PAGE) is None


def test_a_site_that_keeps_refusing_is_skipped_and_named(tmp_path: Any) -> None:
    clock = Clock()
    web = Web().on(PAGE, lambda _: httpx.Response(403))
    pages = _pages(web, clock, ResearchCache(folder=tmp_path, now=lambda: NOW))
    pages.sites = type(pages.sites)(pages.cache, now=lambda: NOW)
    for _ in range(3):
        pages.fetch(PAGE, _usage())
    assert pages.skipped_site(PAGE) == "reviews.example"


class SkippingPages(FakePages):
    """Review pages whose one site has kept failing: it is skipped for 24 hours."""

    def skipped_site(self, url: str) -> str | None:
        return "reviews.example" if "reviews.example" in url else None


def test_a_skipped_site_costs_no_request_and_the_note_names_it() -> None:
    pages = SkippingPages()
    result = research_faults._run(research_faults._stage(pages=pages))
    assert pages.calls == []  # no request, no cap and no thread for it
    skipped = [f for f in result.failures if f.detail.startswith(SKIPPED_DETAIL)]
    assert [(f.source_type, f.reason, f.detail) for f in skipped] == [
        (SourceType.REVIEW_SITE, "blocked", f"{SKIPPED_DETAIL}reviews.example")
    ]
    note = build_coverage_note([], result.failures, False)
    assert note is not None and "Skipped reviews.example for 24 hours, because they kept failing." in note
    assert "Review sites found nothing" not in note
    assert SourceType.REDDIT in research_faults._types(result) and SourceType.WEB_SEARCH in research_faults._types(
        result
    )


def test_one_source_type_down_still_gives_results_with_a_note() -> None:
    result = research_faults._run(research_faults._stage(reddit=FakeReddit(failure_reason="timeout")))
    assert {SourceType.WEB_SEARCH, SourceType.REVIEW_SITE} <= research_faults._types(result)
    assert build_coverage_note([], result.failures, False) == "Reddit timed out."


def test_a_failed_firecrawl_search_is_named_as_a_search() -> None:
    failure = C.SourceFailure(
        source_type=SourceType.WEB_SEARCH, reason="rate_limited", detail="Firecrawl: search HTTP 429"
    )
    assert build_coverage_note([], [failure], False) == "Firecrawl search hit a rate limit."


# --- every LLM provider failing in a search ---------------------------------------------------------------------------


def test_every_provider_failing_sets_llm_unavailable_and_says_so_first() -> None:
    sources = [pipeline_extract._page("a"), pipeline_extract._page("b")]
    extract = GuardedExtractStage(pipeline_extract._failing_client(), now=lambda: NOW)
    result, _ = pipeline_extract._search(extract, *sources)
    assert result.llm_unavailable is True
    assert result.coverage_note is not None and result.coverage_note.startswith(LLM_UNAVAILABLE_NOTE)
    assert result.decision is not None  # the search still finished
    assert C.SearchResult.model_validate_json(result.model_dump_json()) == result


def test_a_search_whose_llm_answered_is_not_marked_unavailable() -> None:
    result, _ = pipeline_extract._search(pipeline_extract.ClaimsPerSource(), pipeline_extract._web("a"))
    assert result.llm_unavailable is False
    assert result.coverage_note is None or LLM_UNAVAILABLE_NOTE not in result.coverage_note


@pytest.mark.parametrize(
    ("counts", "unavailable"),
    [
        ({"sent": 3, "unreadable": 3}, True),
        ({"sent": 3, "unreadable": 2}, False),  # one answered: the providers were not all down
        ({"sent": 0, "unreadable": 0}, False),  # nothing needed the LLM
        (None, False),
        ("not counts", False),
    ],
)
def test_every_llm_call_failed(counts: object, unavailable: bool) -> None:
    assert every_llm_call_failed(counts) is unavailable


def test_the_note_puts_the_llm_first_and_keeps_its_limit() -> None:
    note = build_coverage_note([], [], True, extract_note="A made-up note. " * 40, llm_unavailable=True)
    assert note is not None and note.startswith(LLM_UNAVAILABLE_NOTE) and len(note) <= 400


# --- a stage's helper failing never crashes the search ----------------------------------------------------------------


class BrokenHelpers(pipeline_extract.ClaimsPerSource):
    """An extract stage whose helpers outside run_stage all raise."""

    def ask_for_specs(self, *args: object, **kwargs: object) -> None:
        raise RuntimeError("a made-up crash in ask_for_specs")

    def counts_for(self, search_id: str) -> dict[str, int]:
        raise RuntimeError("a made-up crash in counts_for")

    def coverage_note(self, search_id: str) -> str:
        raise RuntimeError("a made-up crash in coverage_note")


class BrokenFilterKeys(pipeline_extract.SpyVerify):
    def use_filter_keys(self, search_id: str, keys: list[str]) -> None:
        raise RuntimeError("a made-up crash in use_filter_keys")


def test_a_stage_helper_that_raises_never_stops_the_search() -> None:
    defaults = default_search_stages()
    stages = dataclasses.replace(
        defaults,
        research=pipeline_extract.FixedResearch(pipeline_extract._web("a")),
        extract=BrokenHelpers(),
        verify=BrokenFilterKeys(defaults.verify),
    )
    result = PipelineRunner(stages, InMemoryResultStore()).run(pipeline_extract._request())
    assert result.decision is not None
    assert result.llm_unavailable is False
    assert all(r.status is not C.StageStatus.FAILED for r in result.stage_reports)


# --- the site memory counts what the site itself did, once a search for robots.txt ------------------------------------


def _remembering(web: Web, clock: Clock, tmp_path: Any, retry: RetryClock | None = None) -> ReviewPages:
    cache = ResearchCache(folder=tmp_path, now=lambda: NOW)
    pages = ReviewPages(
        web.client(),
        ResearchSettings(review_domains=("reviews.example",)),
        cache=cache,
        now=lambda: NOW,
        clock=clock,
        sleep=clock.sleep,
        retry=retry or _retry(clock),
    )
    pages.sites = type(pages.sites)(cache, now=lambda: NOW)
    return pages


THREE_PAGES = [PAGE, "https://www.reviews.example/b", "https://www.reviews.example/c"]
ROBOTS = "https://www.reviews.example/robots.txt"


def test_a_dead_robots_file_counts_once_a_search_however_many_pages_it_stops(tmp_path: Any) -> None:
    web = Web().on(ROBOTS, httpx.ConnectError("no route"))
    pages = _remembering(web, Clock(), tmp_path)
    usage = _usage()
    for url in THREE_PAGES:
        pages.fetch(url, usage)
    assert pages.sites.score(PAGE) == 1.0  # it failed again on its retry: one repeated failure for the site
    assert pages.skipped_site(PAGE) is None


def test_a_robots_no_counts_once_a_search(tmp_path: Any) -> None:
    web = Web().on(ROBOTS, httpx.Response(200, text="User-agent: *\nDisallow: /"))
    pages = _remembering(web, Clock(), tmp_path)
    usage = _usage()
    for url in THREE_PAGES:
        pages.fetch(url, usage)
    assert pages.sites.score(PAGE) == 1.0


def test_a_robots_file_that_could_not_be_read_says_nothing_about_the_site(tmp_path: Any) -> None:
    moved = httpx.Response(301, headers={"location": "https://elsewhere.example/robots.txt"})  # off our sites
    pages = _remembering(Web().on(ROBOTS, moved), Clock(), tmp_path)
    usage = _usage()
    for url in THREE_PAGES:
        assert pages.fetch(url, usage).reason == "blocked"
    assert pages.sites.score(PAGE) == 0


def test_a_failure_with_no_time_for_its_retry_does_not_count(tmp_path: Any) -> None:
    clock = Clock()
    hurried = RetryClock(time_left=lambda: 5.0, sleep=clock.sleep, rng=NoJitter())  # a page's 15 s won't fit
    web = Web().on(PAGE, lambda _: httpx.Response(503))
    pages = _remembering(web, clock, tmp_path, hurried)
    assert pages.fetch(PAGE, _usage()).reason == "error"
    assert web.asked(PAGE) == 1
    assert pages.sites.score(PAGE) == 0  # not repeated, so not counted


def test_a_failure_that_repeats_on_its_retry_counts(tmp_path: Any) -> None:
    web = Web().on(PAGE, lambda _: httpx.Response(503))
    pages = _remembering(web, Clock(), tmp_path)
    pages.fetch(PAGE, _usage())
    assert web.asked(PAGE) == 2
    assert pages.sites.score(PAGE) == 1.0


def test_a_page_read_from_the_cache_is_served_from_a_skipped_site(tmp_path: Any) -> None:
    web = Web().on(PAGE, lambda _: httpx.Response(403))
    pages = _remembering(web, Clock(), tmp_path)
    for _ in range(3):
        pages.fetch(PAGE, _usage())
    assert pages.skipped_site(PAGE) == "reviews.example"
    pages.cache.put_sources(("page", PAGE), [pipeline_extract._page("cached")])
    assert pages.skipped_site(PAGE) is None


# --- every provider hanging -------------------------------------------------------------------------------------------


class Hanging(pipeline_extract.ClaimsPerSource):
    """Every provider hangs: each source's read outlasts its time limit, and its counts say it was sent."""

    def __init__(self, sources: int) -> None:
        super().__init__(lambda _: time.sleep(0.6))
        self.sources = sources

    def counts_for(self, search_id: str) -> dict[str, int]:
        return {"sent": self.sources, "unreadable": 0}


def test_every_provider_hanging_also_sets_llm_unavailable() -> None:
    stages = dataclasses.replace(
        default_search_stages(),
        research=pipeline_extract.FixedResearch(pipeline_extract._web("a"), pipeline_extract._web("b")),
        extract=Hanging(2),
    )
    runner = PipelineRunner(stages, InMemoryResultStore(), time_limits={C.StageName.EXTRACT: 0.2})
    result = runner.run(pipeline_extract._request())
    assert result.llm_unavailable is True
    assert result.coverage_note is not None and result.coverage_note.startswith(LLM_UNAVAILABLE_NOTE)


class AnsweredButCountedUnreadable(pipeline_extract.ClaimsPerSource):
    def counts_for(self, search_id: str) -> dict[str, int]:
        return {"sent": 1, "unreadable": 1}


def test_a_search_with_any_claim_was_answered() -> None:
    stages = dataclasses.replace(
        default_search_stages(),
        research=pipeline_extract.FixedResearch(pipeline_extract._web("a")),
        extract=AnsweredButCountedUnreadable(),
    )
    result = PipelineRunner(stages, InMemoryResultStore()).run(pipeline_extract._request())
    assert result.llm_unavailable is False


@pytest.mark.parametrize(
    ("counts", "timed_out", "unavailable"),
    [
        ({"sent": 3, "unreadable": 1}, 2, True),  # one failed, two hung
        ({"sent": 3, "unreadable": 0}, 2, False),  # one answered
        ({"sent": 2, "unreadable": 0}, 2, True),
    ],
)
def test_a_source_that_ran_out_of_time_got_no_answer(counts: object, timed_out: int, unavailable: bool) -> None:
    assert every_llm_call_failed(counts, timed_out) is unavailable
