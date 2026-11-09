"""The runner reads sources in parallel, and no one source can stop a search (US-14)."""

from __future__ import annotations

import dataclasses
import threading
import time
from typing import Any

import pytest

from app import contracts as C
from app.contracts import ExtractionResult, Provider, Source
from app.db import InMemoryRequestStore
from app.extract import ExtractStage, GuardedExtractStage, default_extract_stage
from app.extract.parsing import RawExtraction
from app.extract.settings import ExtractSettings
from app.llm import InMemoryCallLog, WrapperLLMClient, load_llm_config
from app.llm.errors import ProviderError
from app.pipeline import (
    STAGE_TIME_LIMITS_S,
    CancelFlag,
    DatabaseResultStore,
    InMemoryResultStore,
    PipelineRunner,
    SearchCancelled,
    _Search,
    build_coverage_note,
    default_search_stages,
    new_search_id,
)
from tests.fakes.llm import FakeLLMClient
from tests.fakes.reddit import NOW
from tests.fakes.research import rubric, thread, web_source

QUOTE = "Nova Book 14 lasts nine hours."
SECRET_COMMENT = "A made-up Reddit comment that must not be saved."


def _web(name: str) -> Source:
    return web_source(f"https://{name}.example/", text=f"{QUOTE} Page {name}.")


def _claim(source: Source) -> C.Claim:
    return C.Claim(
        id=f"clm_{source.id[4:12]}",
        source_id=source.id,
        source_url=source.url,
        product_mention="Nova Book 14",
        criterion_key="battery_life",
        score=8.0,
        quote=QUOTE,
        claim_type=C.ClaimType.EXPERIENCE,
        polarity="positive",
        retrieved_at=source.fetched_at,
        extracted_at=NOW,
    )


class FixedResearch:
    name = C.StageName.RESEARCH
    is_placeholder = False

    def __init__(self, *sources: Source) -> None:
        self.sources = sources

    def run(self, inp: C.ResearchInput, ctx: C.StageContext) -> C.ResearchResult:
        return C.ResearchResult(queries=["made up"], sources=list(self.sources))

    def fallback(self, inp: C.ResearchInput, ctx: C.StageContext, error: Exception) -> C.ResearchResult:
        return C.ResearchResult(queries=["made up"], sources=[])


class SpyVerify:
    """A real verify step's place: it keeps the claims the runner passed on, so the test can read them."""

    name = C.StageName.VERIFY
    is_placeholder = True

    def __init__(self, real: Any) -> None:
        self.real = real
        self.claims: list[C.Claim] = []

    def run(self, inp: C.VerifyInput, ctx: C.StageContext) -> C.VerifyResult:
        self.claims = list(inp.claims)
        result: C.VerifyResult = self.real.run(inp, ctx)
        return result

    def fallback(self, inp: C.VerifyInput, ctx: C.StageContext, error: Exception) -> C.VerifyResult:
        result: C.VerifyResult = self.real.fallback(inp, ctx, error)
        return result


class ClaimsPerSource:
    """An extract stage giving one claim per source. Tests change what one source does through `behave`."""

    name = C.StageName.EXTRACT
    is_placeholder = False

    def __init__(self, behave: Any = None) -> None:
        self.behave = behave
        self.read = 0
        self._lock = threading.Lock()

    def run(self, inp: C.ExtractInput, ctx: C.StageContext) -> ExtractionResult:
        with self._lock:
            self.read += 1
        if self.behave is not None:
            self.behave(inp.source)
        return ExtractionResult(source_id=inp.source.id, claims=[_claim(inp.source)])

    def fallback(self, inp: C.ExtractInput, ctx: C.StageContext, error: Exception) -> ExtractionResult:
        return ExtractionResult(source_id=inp.source.id)


def _request() -> C.SearchRequest:
    return C.SearchRequest(session_id="sess_aaaaaaaaaaaaaaaaaaaaaaaa", raw_query="a made-up request", rubric=rubric())


def _search(extract: Any, *sources: Source, **runner: Any) -> tuple[C.SearchResult, SpyVerify]:
    defaults = default_search_stages()
    spy = SpyVerify(defaults.verify)
    stages = dataclasses.replace(defaults, research=FixedResearch(*sources), extract=extract, verify=spy)
    return PipelineRunner(stages, InMemoryResultStore(), **runner).run(_request()), spy


def _report(result: C.SearchResult) -> C.StageReport:
    return next(r for r in result.stage_reports if r.stage is C.StageName.EXTRACT)


def test_sources_are_read_in_parallel() -> None:
    """The barrier opens only when three sources are being read at once."""
    barrier = threading.Barrier(3, timeout=5)
    result, spy = _search(ClaimsPerSource(lambda _: barrier.wait()), _web("a"), _web("b"), _web("c"))
    assert _report(result).status is C.StageStatus.OK
    assert len(spy.claims) == 3


def test_workers_are_limited() -> None:
    running = {"now": 0, "most": 0}
    lock = threading.Lock()

    def behave(_: Source) -> None:
        with lock:
            running["now"] += 1
            running["most"] = max(running["most"], running["now"])
        time.sleep(0.02)
        with lock:
            running["now"] -= 1

    _search(ClaimsPerSource(behave), *[_web(f"s{i}") for i in range(10)], extract_workers=3)
    assert running["most"] == 3


def test_claims_keep_the_sources_order() -> None:
    sources = [_web(f"s{i}") for i in range(6)]
    delays = {source.id: 0.06 - 0.01 * i for i, source in enumerate(sources)}  # the first takes longest
    _, spy = _search(ClaimsPerSource(lambda s: time.sleep(delays[s.id])), *sources)
    assert [c.source_id for c in spy.claims] == [s.id for s in sources]


def test_one_source_timing_out_lets_the_others_finish() -> None:
    slow, fine, also_fine = _web("slow"), _web("fine"), _web("also")
    release = threading.Event()

    def behave(source: Source) -> None:
        if source.id == slow.id:
            release.wait(5)

    extract = ClaimsPerSource(behave)
    try:
        result, spy = _search(extract, slow, fine, also_fine, time_limits={C.StageName.EXTRACT: 0.2})
    finally:
        release.set()
    assert [c.source_id for c in spy.claims] == [fine.id, also_fine.id]
    assert _report(result).status is C.StageStatus.TIMEOUT
    assert result.partial is False
    assert result.decision is not None  # the search went on


def test_one_source_raising_lets_the_others_finish() -> None:
    bad, fine = _web("bad"), _web("fine")

    def behave(source: Source) -> None:
        if source.id == bad.id:
            raise RuntimeError("a made-up crash")

    result, spy = _search(ClaimsPerSource(behave), bad, fine)
    assert [c.source_id for c in spy.claims] == [fine.id]
    assert _report(result).status is C.StageStatus.FALLBACK
    assert result.decision is not None


def _failing_client() -> WrapperLLMClient:
    from tests.fakes.llm import FakeAdapter

    adapters = {p: FakeAdapter(p, replies=(ProviderError(p, "made up"),)) for p in Provider}
    return WrapperLLMClient(load_llm_config(), dict(adapters), InMemoryCallLog(), limiters={})


def test_a_failed_fallback_stops_the_reading() -> None:
    class Broken(ClaimsPerSource):
        def run(self, inp: C.ExtractInput, ctx: C.StageContext) -> ExtractionResult:
            self.read += 1
            raise RuntimeError("a made-up crash")

        def fallback(self, inp: C.ExtractInput, ctx: C.StageContext, error: Exception) -> ExtractionResult:
            raise RuntimeError("a made-up crash in the fallback")

    extract = Broken()
    result, spy = _search(extract, *[_web(f"s{i}") for i in range(5)], extract_workers=1)
    assert extract.read == 1
    assert _report(result).status is C.StageStatus.FAILED
    assert result.partial is True
    assert spy.claims == []


def test_a_long_category_does_not_stop_the_search() -> None:
    """A rubric's category may hold 64 characters, and extraction's input only 60. It must not crash the search."""
    long = C.Rubric.model_validate({**rubric().model_dump(), "category": "a" * 64})
    request = C.SearchRequest(session_id="sess_aaaaaaaaaaaaaaaaaaaaaaaa", raw_query="a made-up request", rubric=long)
    extract = ClaimsPerSource()
    defaults = default_search_stages()
    stages = dataclasses.replace(defaults, research=FixedResearch(_web("a")), extract=extract)
    result = PipelineRunner(stages, InMemoryResultStore()).run(request)
    assert extract.read == 1
    assert _report(result).status is C.StageStatus.OK


def test_a_real_claim_reaches_verify_through_the_runner() -> None:
    raw = RawExtraction.model_validate(
        {
            "mentions": [{"raw_name": "Nova Book 14", "is_product": True}],
            "claims": [
                {
                    "product_mention": "Nova Book 14",
                    "criterion_key": "value_for_money",
                    "score": 8,
                    "quote": QUOTE.upper(),
                    "polarity": "positive",
                }
            ],
        }
    )
    source = _web("a")
    stage = GuardedExtractStage(FakeLLMClient({C.LLMTask.EXTRACT_CLAIMS: raw}), now=lambda: NOW)
    _, spy = _search(stage, source)
    (claim,) = spy.claims
    assert (claim.source_id, claim.source_url, claim.quote) == (source.id, source.url, QUOTE)


def test_every_provider_failing_gives_no_claims_and_a_coverage_note() -> None:
    sources = [_web("a"), _web("b"), thread("aaa", text=QUOTE)]
    result, spy = _search(GuardedExtractStage(_failing_client(), now=lambda: NOW), *sources)
    assert spy.claims == []
    assert _report(result).status is C.StageStatus.OK
    assert result.coverage_note is not None
    assert "3 sources could not be read" in result.coverage_note
    assert result.decision is not None


def test_the_stages_note_reaches_the_coverage_note() -> None:
    settings = ExtractSettings(reddit_tokens_per_search=1)
    stage = GuardedExtractStage(FakeLLMClient(), settings, now=lambda: NOW)
    result, _ = _search(stage, thread("aaa", text=QUOTE), thread("bbb", text=QUOTE))
    assert result.coverage_note is not None
    assert "2 Reddit threads were not read" in result.coverage_note


def test_a_stage_without_a_note_is_fine() -> None:
    result, _ = _search(ClaimsPerSource(), _web("a"))
    assert result.coverage_note is None or "could not be read" not in result.coverage_note


def test_build_coverage_note_adds_the_extract_note() -> None:
    assert build_coverage_note([], [], False, extract_note="A made-up note.") == "A made-up note."
    assert build_coverage_note([], [], False) is None


def test_the_partial_sentence_outlasts_a_long_extract_note() -> None:
    note = build_coverage_note([], [], True, extract_note="A made-up note. " * 40)
    assert note is not None
    assert "These results are partial." in note
    assert len(note) <= 400


def test_the_report_gives_elapsed_time() -> None:
    """Summing three parallel reads of 0.3 s would say about 900 ms."""
    result, _ = _search(ClaimsPerSource(lambda _: time.sleep(0.3)), _web("a"), _web("b"), _web("c"))
    assert 250 <= _report(result).duration_ms < 800


def test_a_cancel_stops_sources_not_yet_started() -> None:
    cancel = CancelFlag()
    extract = ClaimsPerSource(lambda _: cancel.request())
    defaults = default_search_stages()
    stages = dataclasses.replace(defaults, research=FixedResearch(*[_web(f"s{i}") for i in range(20)]), extract=extract)
    with pytest.raises(SearchCancelled):
        PipelineRunner(stages, InMemoryResultStore(), extract_workers=2).run(
            _request(), search_id=new_search_id(), cancel=cancel
        )
    assert 1 <= extract.read <= 2


def test_a_cancel_before_any_source_starts_reads_nothing() -> None:
    cancel = CancelFlag()
    extract = ClaimsPerSource()

    def progress(event: C.ProgressEvent) -> None:
        if event.stage is C.StageName.EXTRACT and event.status == "started":
            cancel.request()

    defaults = default_search_stages()
    stages = dataclasses.replace(defaults, research=FixedResearch(_web("a")), extract=extract)
    with pytest.raises(SearchCancelled):
        PipelineRunner(stages, InMemoryResultStore()).run(
            _request(), search_id=new_search_id(), progress=progress, cancel=cancel
        )
    assert extract.read == 0


def test_no_source_starts_with_no_time_left() -> None:
    """A read started then would be thrown away, but its model call would still go out."""
    extract = ClaimsPerSource()
    stages = dataclasses.replace(default_search_stages(), extract=extract)
    ctx = C.StageContext(search_id="srch_a", time_limit_s=0.0)
    search = _Search(stages, STAGE_TIME_LIMITS_S, _request(), ctx)
    search.research_out = C.ResearchResult(queries=["made up"], sources=[_web("a"), _web("b")])
    assert search.extract().status is C.StageStatus.SKIPPED
    assert extract.read == 0


def test_no_source_starts_once_the_search_is_marked_out_of_time() -> None:
    """A timed wait can wake a moment before its deadline, so the flag counts even with a little time left."""
    extract = ClaimsPerSource()
    stages = dataclasses.replace(default_search_stages(), extract=extract)
    search = _Search(stages, STAGE_TIME_LIMITS_S, _request(), C.StageContext(search_id="srch_a"))
    search.research_out = C.ResearchResult(queries=["made up"], sources=[_web("a")])
    search.out_of_time = True
    assert search.extract().status is C.StageStatus.SKIPPED
    assert extract.read == 0


def test_a_search_out_of_time_starts_no_more_sources() -> None:
    def behave(_: Source) -> None:
        time.sleep(0.3)

    extract = ClaimsPerSource(behave)
    result, _ = _search(extract, *[_web(f"s{i}") for i in range(6)], search_time_limit_s=0.15, extract_workers=1)
    assert extract.read == 1
    assert result.partial is True


def test_the_saved_row_keeps_no_reddit_quote() -> None:
    """Claims stay in memory for the run, and the saved result's Reddit text is stripped (D-53)."""
    comment = C.SourceComment(id="m1", depth=1, upvotes=3, text=SECRET_COMMENT)
    source = thread("aaa", text="A made-up post about the Nova Book 14.")
    source = C.Source.model_validate({**source.model_dump(), "comments": (comment,)})
    raw = RawExtraction.model_validate(
        {
            "mentions": [],
            "claims": [
                {
                    "product_mention": "Nova Book 14",
                    "criterion_key": "value_for_money",
                    "score": 8,
                    "quote": "A made-up Reddit comment",
                    "polarity": "positive",
                }
            ],
        }
    )
    llm = FakeLLMClient({C.LLMTask.EXTRACT_CLAIMS: raw})
    store = InMemoryRequestStore()
    defaults = default_search_stages()
    spy = SpyVerify(defaults.verify)
    stages = dataclasses.replace(
        defaults, research=FixedResearch(source), extract=GuardedExtractStage(llm, now=lambda: NOW), verify=spy
    )
    PipelineRunner(stages, DatabaseResultStore(store, time_limit_s=5)).run(_request())
    assert [c.quote for c in spy.claims] == ["A made-up Reddit comment"]  # so a claim really was there to leak
    (row,) = store.searches
    assert "made-up Reddit comment" not in str(row)
    assert "made-up post" not in str(row)


def test_the_real_stage_is_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.pipeline.default_extract_stage", default_extract_stage)
    stage = default_search_stages().extract
    assert isinstance(stage, GuardedExtractStage)
    assert isinstance(stage.stub, ExtractStage)


def test_the_default_stage_shares_the_client_intake_uses(monkeypatch: pytest.MonkeyPatch) -> None:
    """One client means one pool, so the shopper's reserve covers extraction and intake alike."""
    client = FakeLLMClient()
    monkeypatch.setattr("app.presearch.default_llm", lambda: client)
    assert default_extract_stage().llm is client


def test_tests_stay_offline_on_the_stub() -> None:
    assert isinstance(default_search_stages().extract, ExtractStage)
