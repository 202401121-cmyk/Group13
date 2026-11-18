"""US-24 (S4-26): a search where some price look-ups failed says how many products couldn't be priced. The pipeline
reads the commerce stage's note into the coverage note, as it does decide's. Every other stage is its stub, so nothing
live is ever called, and a made-up request."""

from __future__ import annotations

import dataclasses

from app import contracts as C
from app.pipeline import InMemoryResultStore, PipelineRunner
from tests.unit.test_sample_label import stub_stages

NOTE = "We couldn't check prices for 1 of the top 3 products, because the price search failed."


class NotedCommerce:
    """The stub's offers, and the note a partial failure leaves."""

    name = C.StageName.COMMERCE
    is_placeholder = False

    def __init__(self) -> None:
        self.read: list[str] = []

    def run(self, inp: C.CommerceInput, ctx: C.StageContext) -> C.CommerceResult:
        return C.CommerceResult.model_validate(
            {"no_offer": {p.id: "https://www.google.com/search?q=x" for p in inp.products}}
        )

    def fallback(self, inp: C.CommerceInput, ctx: C.StageContext, error: Exception) -> C.CommerceResult:
        return self.run(inp, ctx)

    def coverage_note(self, search_id: str) -> str | None:
        self.read.append(search_id)
        return NOTE


def test_the_pipeline_puts_the_commerce_note_in_the_coverage_note() -> None:
    commerce = NotedCommerce()
    stages = dataclasses.replace(stub_stages(), commerce=commerce)
    request = C.SearchRequest(
        session_id="sess_aaaaaaaaaaaaaaaaaaaaaaaa", raw_query="a made-up request", rubric=C.examples.rubric()
    )
    result = PipelineRunner(stages, InMemoryResultStore()).run(request)
    assert commerce.read == [result.search_id]
    assert NOTE in (result.coverage_note or "")
