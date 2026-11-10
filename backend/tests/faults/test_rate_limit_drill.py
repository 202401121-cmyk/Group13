"""P18's rate-limit drill as a test (US-46, F-6): Gemini answers every call with HTTP 429.

The search reads three web pages, and the real adapters run over a fake transport, as in P18's live drill. Every
provider is paced at its real limits from models.yaml, on a fake clock, so Mistral takes 2 requests a minute. Gemini's
host answers 429, so Mistral reads two pages and the third goes to OpenRouter, the fallback after both.
- When OpenRouter answers, every page is read: the Extract stage is OK and the coverage note has no extraction message.
- When OpenRouter keeps sending keep-alive bytes for 40 s, as it did in P18, its attempt is cut off at its time limit.
  The third page then honestly "could not be read": Gemini said 429, Mistral had no room in its minute, and
  OpenRouter was cut off. "took too long" never appears: the stage keeps to its limit.
- When Mistral answers 429 too, no allowed provider has room for any page, and the note says so.
No real API is called and nothing waits for real time.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Iterator
from datetime import date

import httpx

from app import contracts as C
from app.contracts import Provider, Source
from app.extract import GuardedExtractStage
from app.llm import InMemoryCallLog, WrapperLLMClient, load_llm_config
from app.llm.config import LLMConfig
from app.llm.health import ProviderHealth
from app.llm.limits import RateLimiter
from app.llm.pool import ModelPool
from app.llm.providers import OpenAICompatibleAdapter
from app.pipeline import InMemoryResultStore, PipelineRunner, default_search_stages
from tests.fakes.reddit import NOW
from tests.fakes.research import review_source, rubric

QUOTE = "Nova Book 14 lasts nine hours."
EXTRACTION = {
    "mentions": [{"raw_name": "Nova Book 14", "is_product": True}],
    "claims": [
        {
            "product_mention": "Nova Book 14",
            "criterion_key": "value_for_money",
            "score": 8,
            "quote": QUOTE,
            "polarity": "positive",
        }
    ],
}
EXTRACTION_MESSAGES = ("Extract took too long", "could not be read", "were not read", "was not read")


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class Research:
    name = C.StageName.RESEARCH
    is_placeholder = False

    def __init__(self, *sources: Source) -> None:
        self.sources = sources

    def run(self, inp: C.ResearchInput, ctx: C.StageContext) -> C.ResearchResult:
        return C.ResearchResult(queries=["made up"], sources=list(self.sources))

    def fallback(self, inp: C.ResearchInput, ctx: C.StageContext, error: Exception) -> C.ResearchResult:
        return C.ResearchResult(queries=["made up"], sources=[])


BODY = json.dumps(
    {
        "choices": [{"message": {"content": json.dumps(EXTRACTION)}}],
        "usage": {"prompt_tokens": 300, "completion_tokens": 60},
    }
).encode()
LIMITED = {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED"}}


class KeepAlive(httpx.SyncByteStream):
    """One keep-alive byte every 2 s on the fake clock, 20 times, then the answer: 40 s in all."""

    def __init__(self, clock: Clock) -> None:
        self.clock = clock

    def __iter__(self) -> Iterator[bytes]:
        for _ in range(20):
            self.clock.now += 2
            yield b" "
        yield BODY


def _transport(
    trickle_clock: Clock, hosts: list[str], mistral_limited: bool, openrouter_trickles: bool
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        if request.url.host == "generativelanguage.googleapis.com":
            return httpx.Response(429, json=LIMITED)
        if request.url.host == "api.mistral.ai" and mistral_limited:
            return httpx.Response(429, json=LIMITED)
        if request.url.host == "openrouter.ai" and openrouter_trickles:
            stream = KeepAlive(trickle_clock)
            return httpx.Response(200, headers={"content-type": "application/json"}, stream=stream)
        return httpx.Response(200, content=BODY, headers={"content-type": "application/json"})

    return httpx.MockTransport(handler)


def _real_limits(config: LLMConfig, clock: Clock) -> dict[Provider, RateLimiter]:
    """Each provider's own limits from models.yaml, as the app builds them, but on the fake clock."""
    return {
        p.name: RateLimiter(
            p.requests_per_second,
            p.tokens_per_minute,
            requests_per_minute=p.requests_per_minute,
            clock=clock,
            sleep=clock.sleep,
        )
        for p in config.providers
        if p.requests_per_second is not None or p.tokens_per_minute is not None or p.requests_per_minute is not None
    }


def _drill(
    mistral_limited: bool = False, openrouter_trickles: bool = False
) -> tuple[C.SearchResult, WrapperLLMClient, list[str]]:
    config = load_llm_config()
    clock = Clock()
    # The trickle moves OpenRouter's own clock only. Pages run in parallel, so on a shared clock it could push another
    # page's Mistral reply past its deadline while that reply is being read.
    trickle_clock = Clock()
    hosts: list[str] = []
    http = httpx.Client(transport=_transport(trickle_clock, hosts, mistral_limited, openrouter_trickles))
    adapters = {
        p.name: OpenAICompatibleAdapter(
            p.name,
            p.base_url,
            "made-up",
            http,
            json_mode=p.json_mode,
            clock=trickle_clock if p.name is Provider.OPENROUTER else clock,
        )
        for p in config.providers
        if p.name in (Provider.GEMINI, Provider.MISTRAL, Provider.OPENROUTER)
    }
    llm = WrapperLLMClient(
        config,
        adapters,
        InMemoryCallLog(),
        health=ProviderHealth(clock),
        limiters=_real_limits(config, clock),
        pool=ModelPool(config.pool, clock=clock, sleep=clock.sleep, today=lambda: date(2026, 11, 10)),
    )
    pages = [review_source(f"https://page-{n}.example/", text=f"{QUOTE} Page {n}.") for n in "abc"]
    defaults = default_search_stages()
    stages = dataclasses.replace(defaults, research=Research(*pages), extract=GuardedExtractStage(llm, now=lambda: NOW))
    request = C.SearchRequest(
        session_id="sess_aaaaaaaaaaaaaaaaaaaaaaaa", raw_query="a made-up request", rubric=rubric()
    )
    return PipelineRunner(stages, InMemoryResultStore()).run(request), llm, hosts


def _extract_status(result: C.SearchResult) -> C.StageStatus:
    return next(r.status for r in result.stage_reports if r.stage is C.StageName.EXTRACT)


def _records(llm: WrapperLLMClient) -> list[tuple[Provider, str]]:
    assert isinstance(llm.call_log, InMemoryCallLog)
    return [(r.provider, r.outcome) for r in llm.call_log.records]


def test_the_p18_drill_finishes_with_no_extraction_note() -> None:
    result, llm, hosts = _drill()
    assert _extract_status(result) is C.StageStatus.OK
    note = result.coverage_note or ""
    assert not any(message in note for message in EXTRACTION_MESSAGES), note
    assert "generativelanguage.googleapis.com" in hosts  # Gemini was asked, and said 429
    assert hosts.count("api.mistral.ai") == 2  # Mistral's 2 a minute, and no request past them
    answered = sorted(provider.value for provider, outcome in _records(llm) if outcome == "ok")
    assert answered == ["mistral", "mistral", "openrouter"]  # every page was read; the fallback took the third


def test_when_mistrals_minute_is_full_and_openrouter_trickles_one_page_is_honestly_unread() -> None:
    result, llm, hosts = _drill(openrouter_trickles=True)
    assert _extract_status(result) is C.StageStatus.OK  # every source's call ended inside its time limit
    note = result.coverage_note or ""
    assert "took too long" not in note
    assert "1 source could not be read" in note
    assert hosts.count("api.mistral.ai") == 2
    records = _records(llm)
    assert [p for p, outcome in records if outcome == "ok"] == [Provider.MISTRAL, Provider.MISTRAL]
    assert (Provider.GEMINI, "rate_limited") in records
    assert (Provider.MISTRAL, "rate_limited") in records  # no room in its minute: held back, never sent
    assert (Provider.OPENROUTER, "timeout") in records  # its trickling reply was cut off in time


def test_with_no_allowed_provider_free_the_note_says_so_and_nothing_takes_too_long() -> None:
    result, llm, hosts = _drill(mistral_limited=True, openrouter_trickles=True)
    assert _extract_status(result) is C.StageStatus.OK
    note = result.coverage_note or ""
    assert "took too long" not in note
    assert "could not be read" in note
    assert "openrouter.ai" in hosts  # the fallback was asked only once Gemini and Mistral had no room
    records = _records(llm)
    assert (Provider.GEMINI, "rate_limited") in records
    assert (Provider.MISTRAL, "rate_limited") in records
    assert (Provider.OPENROUTER, "timeout") in records  # tried last, and its trickling reply cut off in time
    assert not any(outcome == "ok" for _, outcome in records)
