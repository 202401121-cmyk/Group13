"""The real verify and decide in the search (S3-10b, US-17, US-18): real shown, filtered-out and can't-tell products.

The Sprint 1 stubs stay only as fallbacks. Research and extract are a made-up live-shaped run (tests/fakes/live_run.py),
and enrich, explain and commerce, still stubs, are dressed as real so the sample label can be checked. Every product,
site and price here is made up.
"""

from __future__ import annotations

import app.contracts as C
from app.contracts import examples as ex
from app.decide import DecideStage, WeightedDecideStage
from app.decide.notes import NOT_RANKED_NOTE
from app.pipeline import SAMPLE_NOTE, InMemoryResultStore, PipelineRunner, SearchStages, default_search_stages
from app.verify import TrustVerifyStage, VerifyStage
from tests.fakes.live_run import CANT_TELL, FILTERED_OUT, SHOWN, LiveShapedExtract, LiveShapedResearch, claims_for
from tests.fakes.live_run import sources as live_sources
from tests.unit.test_sample_label import FakeRealStage


def _request(rubric: C.Rubric | None = None) -> C.SearchRequest:
    return C.SearchRequest(session_id=ex.SESSION_ID, raw_query="a made-up request", rubric=rubric or ex.rubric())


def live_stages() -> SearchStages:
    """The default stages, with a live-shaped research and extract, and the stages still to come dressed as real."""
    stages = default_search_stages()
    stages.research = LiveShapedResearch()
    stages.extract = LiveShapedExtract()
    stages.enrich = FakeRealStage(stages.enrich)
    stages.explain = FakeRealStage(stages.explain)
    stages.commerce = FakeRealStage(stages.commerce)
    return stages


def _run(stages: SearchStages, rubric: C.Rubric | None = None) -> C.SearchResult:
    return PipelineRunner(stages, InMemoryResultStore()).run(_request(rubric))


def _names(result: C.SearchResult) -> dict[str, str]:
    return {p.product.id: p.product.name for p in result.products}


def test_the_default_stages_verify_and_decide_for_real() -> None:
    stages = default_search_stages()
    assert isinstance(stages.verify, TrustVerifyStage)
    assert isinstance(stages.decide, WeightedDecideStage)
    assert not stages.verify.is_placeholder and not stages.decide.is_placeholder
    assert isinstance(stages.decide.baseline, DecideStage)  # the stub lives on only as the fallback


def test_a_live_run_gives_real_shown_filtered_out_and_cant_tell_products() -> None:
    result = _run(live_stages())
    assert result.decision is not None
    names = _names(result)
    assert [names[r.product_id] for r in result.decision.ranked] == SHOWN
    assert result.decision.ranked[0].label == "best_match"
    assert all(r.score is not None for r in result.decision.ranked)
    groups = {names[f.product_id]: (f.group, f.reasons) for f in result.decision.filtered}
    assert groups == {
        FILTERED_OUT: ("filtered_out", ["₹84,990 is above your ₹70,000 budget"]),
        CANT_TELL: ("cant_tell", ["We couldn't find its price"]),  # only Reddit gave one
    }
    assert result.decision.method == "weighted"


def test_a_live_run_has_no_sample_product_and_no_sample_note() -> None:
    result = _run(live_stages())
    assert sorted(_names(result).values()) == sorted([*SHOWN, FILTERED_OUT, CANT_TELL])
    assert all(p.values for p in result.products)  # each one verify resolved: none was added as a sample product
    assert result.coverage_note is None or SAMPLE_NOTE not in result.coverage_note
    assert all(r.status is C.StageStatus.OK for r in result.stage_reports)


def test_verify_is_told_the_category_and_that_the_price_is_a_filter() -> None:
    result = _run(live_stages())
    vela = next(p for p in result.products if p.product.name == CANT_TELL)
    price = next(v for v in vela.values if v.criterion_key == "price_inr")
    assert price.state is C.ValueState.UNKNOWN  # Reddit alone never settles a price


def test_citations_cover_every_claim_a_value_cites() -> None:
    result = _run(live_stages())
    cited = {
        claim_id
        for p in result.products
        for v in p.values
        for claim_id in (
            *v.supporting_claim_ids,
            *v.conflicting_claim_ids,
            *(i for a in v.alternatives for i in a.claim_ids),
        )
    }
    assert cited
    assert {c.claim_id for c in result.citations} == cited
    claims = {c.id: c for s in live_sources() for c in claims_for(s, [w.key for w in ex.rubric().weights])}
    for citation in result.citations:
        assert citation.quote == claims[citation.claim_id].quote
        assert citation.source_id == claims[citation.claim_id].source_id


def test_citations_are_left_empty_when_a_cited_claim_is_not_the_runs() -> None:
    """All or none (US-21): the verify stub cites the example claims, which a live run never read."""
    stages = live_stages()
    stages.verify = FakeRealStage(VerifyStage())
    result = _run(stages)
    assert result.citations == []


class _DropsOne:
    """The real verify, then one product dropped as unsourced, as S3-7's check will drop them."""

    name = C.StageName.VERIFY
    is_placeholder = False

    def __init__(self, drop: str) -> None:
        self.inner = TrustVerifyStage()
        self.drop = drop

    def run(self, inp: C.VerifyInput, ctx: C.StageContext) -> C.VerifyResult:
        out = self.inner.run(inp, ctx)
        dropped = next(p.id for p in inp.match.products if p.name == self.drop)
        return C.VerifyResult.model_validate(
            {
                **out.model_dump(),
                "values": [v.model_dump() for v in out.values if v.product_id != dropped],
                "dropped_unsourced": [dropped],
            }
        )

    def fallback(self, inp: C.VerifyInput, ctx: C.StageContext, error: Exception) -> C.VerifyResult:
        return self.inner.fallback(inp, ctx, error)


def test_a_dropped_product_appears_nowhere() -> None:
    stages = live_stages()
    stages.verify = _DropsOne(FILTERED_OUT)
    result = _run(stages)
    dropped = next(p.product.id for p in _run(live_stages()).products if p.product.name == FILTERED_OUT)
    assert FILTERED_OUT not in _names(result).values()
    assert result.decision is not None
    shown_or_held = {r.product_id for r in result.decision.ranked} | {f.product_id for f in result.decision.filtered}
    assert shown_or_held == set(_names(result))
    assert dropped not in result.model_dump_json()  # not in the products, the decision or the citations


class _Raises(WeightedDecideStage):
    def run(self, inp: C.DecideInput, ctx: C.StageContext) -> C.DecideResult:
        raise RuntimeError("made-up failure")


def test_decide_failing_falls_back_to_the_stub_labelled_a_sample() -> None:
    stages = live_stages()
    stages.decide = _Raises()
    result = _run(stages)
    assert result.decision == ex.decision()
    decide = next(r for r in result.stage_reports if r.stage is C.StageName.DECIDE)
    assert decide.status is C.StageStatus.FALLBACK
    assert result.coverage_note is not None
    assert "Decide used a simpler fallback: something went wrong." in result.coverage_note
    assert result.coverage_note.endswith(SAMPLE_NOTE)
    assert {"Aurora Air 13", "Vela Pro 14"} <= set(_names(result).values())  # the sample's own products, named


def test_nothing_scored_says_not_ranked_in_the_note() -> None:
    rubric = C.Rubric.model_validate({**ex.rubric().model_dump(), "budget": None, "hard_rules": []})
    stages = live_stages()
    stages.extract = _OnlyRam()
    result = _run(stages, rubric)
    assert result.decision is not None and result.decision.ranked
    assert all(r.score is None and r.label is None for r in result.decision.ranked)
    names = _names(result)
    listed = [names[r.product_id] for r in result.decision.ranked]
    assert listed == sorted(listed)
    assert result.coverage_note is not None and NOT_RANKED_NOTE in result.coverage_note


class _OnlyRam(LiveShapedExtract):
    """Reads only RAM and prices: no criterion has a score for any product."""

    def run(self, inp: C.ExtractInput, ctx: C.StageContext) -> C.ExtractionResult:
        out = super().run(inp, ctx)
        kept = [c for c in out.claims if c.score is None]
        return C.ExtractionResult.model_validate({**out.model_dump(), "claims": [c.model_dump() for c in kept]})


def test_the_decide_note_comes_before_the_extract_note_and_the_note_still_fits() -> None:
    rubric = C.Rubric.model_validate({**ex.rubric().model_dump(), "budget": None, "hard_rules": []})
    stages = live_stages()
    stages.extract = _OnlyRamWithNote()
    note = _run(stages, rubric).coverage_note
    assert note is not None and len(note) <= 400
    assert note.startswith(NOT_RANKED_NOTE)
    assert note.endswith("...")  # the extract note, last, is the one cut to fit


class _OnlyRamWithNote(_OnlyRam):
    """Also leaves a long note of its own for the search, as the real extract stage does."""

    def coverage_note(self, search_id: str) -> str | None:
        return "x" * 600
