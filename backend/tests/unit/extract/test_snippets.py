"""A search snippet stays a source and a link, but costs no extraction call (US-14). All text here is made up.

P10's live runs sent 20 snippets a search to a model, and got no claims from them.
"""

from __future__ import annotations

import dataclasses

from app import contracts as C
from app.contracts import ExtractInput, LLMTask, Source, StageContext
from app.extract import GuardedExtractStage
from app.extract.parsing import RawExtraction
from app.extract.settings import ExtractSettings
from app.pipeline import InMemoryResultStore, PipelineRunner, default_search_stages
from tests.fakes.llm import FakeLLMClient
from tests.fakes.research import review_source, rubric, web_source

CTX = StageContext(search_id="srch_a")
SNIPPET = web_source("https://a.example/", text="Nova Book 14 lasts nine hours.")


def _inp(source: Source) -> ExtractInput:
    return ExtractInput(source=source, criterion_keys=["battery_life"], category_label="laptop")


def _llm() -> FakeLLMClient:
    return FakeLLMClient({LLMTask.EXTRACT_CLAIMS: RawExtraction(mentions=[], claims=[])})


def test_a_snippet_only_source_makes_no_extraction_call() -> None:
    assert SNIPPET.via == "search_snippet"
    llm = _llm()
    result = GuardedExtractStage(llm).run(_inp(SNIPPET), CTX)
    assert llm.calls == []
    assert (result.source_id, result.claims, result.mentions) == (SNIPPET.id, [], [])


def test_a_snippet_uses_none_of_the_source_cap() -> None:
    llm = _llm()
    stage = GuardedExtractStage(llm, ExtractSettings(max_sources_per_search=1))
    stage.run(_inp(SNIPPET), CTX)
    stage.run(_inp(review_source("https://b.example/")), CTX)
    assert len(llm.calls) == 1
    assert stage.coverage_note("srch_a") is None


def test_a_snippet_adds_nothing_to_the_coverage_note() -> None:
    stage = GuardedExtractStage(FakeLLMClient())  # no answer for anything: a source sent to it would be noted
    stage.run(_inp(SNIPPET), CTX)
    assert stage.coverage_note("srch_a") is None


def test_a_fetched_web_page_is_still_read() -> None:
    fetched = Source.model_validate({**SNIPPET.model_dump(), "via": "direct"})
    llm = _llm()
    GuardedExtractStage(llm).run(_inp(fetched), CTX)
    assert len(llm.calls) == 1


def test_a_review_page_is_still_read() -> None:
    llm = _llm()
    GuardedExtractStage(llm).run(_inp(review_source("https://b.example/")), CTX)
    assert len(llm.calls) == 1


class _FixedResearch:
    name = C.StageName.RESEARCH
    is_placeholder = False

    def __init__(self, *sources: Source) -> None:
        self.sources = sources

    def run(self, inp: C.ResearchInput, ctx: StageContext) -> C.ResearchResult:
        return C.ResearchResult(queries=["made up"], sources=list(self.sources))

    def fallback(self, inp: C.ResearchInput, ctx: StageContext, error: Exception) -> C.ResearchResult:
        return C.ResearchResult(queries=["made up"], sources=[])


def _request() -> C.SearchRequest:
    return C.SearchRequest(session_id="sess_aaaaaaaaaaaaaaaaaaaaaaaa", raw_query="a made-up request", rubric=rubric())


def test_a_snippet_stays_a_source_and_a_link_in_the_result() -> None:
    llm = _llm()
    stages = dataclasses.replace(
        default_search_stages(), research=_FixedResearch(SNIPPET), extract=GuardedExtractStage(llm)
    )
    result = PipelineRunner(stages, InMemoryResultStore()).run(_request())
    assert llm.calls == []
    assert [(s.id, s.url) for s in result.sources] == [(SNIPPET.id, SNIPPET.url)]
    extract = next(r for r in result.stage_reports if r.stage is C.StageName.EXTRACT)
    assert extract.status is C.StageStatus.OK
    assert "could not be read" not in (result.coverage_note or "")


def test_progress_counts_still_include_snippets() -> None:
    seen: list[C.ProgressEvent] = []
    stages = dataclasses.replace(
        default_search_stages(), research=_FixedResearch(SNIPPET), extract=GuardedExtractStage(_llm())
    )
    PipelineRunner(stages, InMemoryResultStore()).run(_request(), progress=seen.append)
    done = next(e for e in seen if e.stage is C.StageName.EXTRACT and e.status == "done")
    assert done.sources_found[C.SourceType.WEB_SEARCH] == 1
