"""US-38 (D-100 item 1): the pipeline reads research's note on the readers that read hard pages into the search's
coverage note. Every other stage is its stub, so nothing live is called, and the request is made up."""

from __future__ import annotations

import dataclasses

from app import contracts as C
from app.pipeline import InMemoryResultStore, PipelineRunner
from tests.unit.test_sample_label import stub_stages

NOTE = "2 hard-to-read pages were read, 2 by Firecrawl (1 after the page's scripts ran)."


class NotedResearch:
    """The stub's sources, and the readers' note."""

    name = C.StageName.RESEARCH
    is_placeholder = False

    def __init__(self, stub: object) -> None:
        self.stub = stub
        self.read: list[str] = []

    def run(self, inp: C.ResearchInput, ctx: C.StageContext) -> C.ResearchResult:
        return self.stub.run(inp, ctx)  # type: ignore[attr-defined, no-any-return]

    def fallback(self, inp: C.ResearchInput, ctx: C.StageContext, error: Exception) -> C.ResearchResult:
        return self.run(inp, ctx)

    def coverage_note(self, search_id: str) -> str | None:
        self.read.append(search_id)
        return NOTE


def test_the_pipeline_puts_the_readers_note_in_the_coverage_note() -> None:
    stages = stub_stages()
    research = NotedResearch(stages.research)
    request = C.SearchRequest(
        session_id="sess_aaaaaaaaaaaaaaaaaaaaaaaa", raw_query="a made-up request", rubric=C.examples.rubric()
    )
    result = PipelineRunner(dataclasses.replace(stages, research=research), InMemoryResultStore()).run(request)
    assert research.read == [result.search_id]
    assert NOTE in (result.coverage_note or "")
