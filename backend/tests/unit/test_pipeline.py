"""The whole stub pipeline, end to end (US-52)."""

from __future__ import annotations

import app.contracts as C
from app.contracts import examples as ex
from app.pipeline import SAMPLE_NOTE, InMemoryResultStore, PipelineRunner, build_coverage_note, default_search_stages

ORDER = [
    C.StageName.RESEARCH,
    C.StageName.EXTRACT,
    C.StageName.MATCH,
    C.StageName.VERIFY,
    C.StageName.ENRICH,
    C.StageName.DECIDE,
    C.StageName.EXPLAIN,
    C.StageName.COMMERCE,
]


def _request() -> C.SearchRequest:
    return C.SearchRequest(session_id=ex.SESSION_ID, raw_query=ex.QUERY, rubric=ex.rubric())


def test_stub_pipeline_runs_end_to_end() -> None:
    store = InMemoryResultStore()
    result = PipelineRunner(default_search_stages(), store).run(_request())

    assert [r.stage for r in result.stage_reports] == ORDER
    assert all(r.status is C.StageStatus.OK for r in result.stage_reports)
    assert not result.partial
    # Nothing went wrong, so the note only says these are samples.
    assert result.coverage_note == SAMPLE_NOTE

    assert C.SearchResult.model_validate_json(result.model_dump_json()) == result
    assert result.decision is not None
    shown = {p.product.id for p in result.products}
    assert {r.product_id for r in result.decision.ranked} <= shown
    assert result.search_id.startswith("srch_")
    assert result.finished_at >= result.started_at
    assert result.sources == ex.sources()
    assert result.commerce == ex.commerce()

    assert result.saved is True
    assert store.results == [result]


def test_products_carry_their_values_and_community_signal() -> None:
    result = PipelineRunner(default_search_stages(), InMemoryResultStore()).run(_request())
    nova = next(p for p in result.products if p.product.id == "prd_nova_book_14")
    assert nova.values
    assert all(v.product_id == "prd_nova_book_14" for v in nova.values)
    assert nova.community is not None and nova.community.label == "consistent"


def test_each_search_gets_its_own_id() -> None:
    runner = PipelineRunner(default_search_stages(), InMemoryResultStore())
    assert runner.run(_request()).search_id != runner.run(_request()).search_id


def test_coverage_note_is_none_when_nothing_went_wrong() -> None:
    ok = C.StageReport(stage=C.StageName.MATCH, status=C.StageStatus.OK, duration_ms=1)
    assert build_coverage_note([ok], [], partial=False) is None


def test_coverage_note_is_capped_at_400_characters() -> None:
    long = [
        C.StageReport(stage=name, status=C.StageStatus.FALLBACK, duration_ms=1, fallback_reason="x" * 300)
        for name in ORDER
    ]
    note = build_coverage_note(long, [], partial=True)
    assert note is not None
    assert len(note) <= 400
    assert note.endswith("...")
