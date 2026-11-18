"""US-41 (S4-36, P-34): enrich's store prices reach verify, decide, the result and the source drawer.

Research and extract are the made-up live-shaped run (tests/fakes/live_run.py): Vela Pro 14 is held as "can't tell",
as only Reddit gives its price. Enrich's shopping client is a fake: no real API is called. Every product, store and
price here is made up.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from typing import Any

import pytest

import app.contracts as C
from app.api.evidence import ClaimRecorder, cited_claims, evidence_for
from app.contracts import examples as ex
from app.decide.rules import PRICE_KEY
from app.enrich import EnrichStage, PriceLookupStage, default_enrich_stage
from app.llm.client import LLMClient
from app.pipeline import InMemoryResultStore, PipelineRunner, SearchStages, build_coverage_note
from app.research.settings import ProviderCap
from app.research.shopping import Listing, ShoppingResult
from app.research.usage import SearchUsage, UsageBook
from app.verify import TrustVerifyStage
from tests.fakes.live_run import CANT_TELL, FILTERED_OUT, SHOWN
from tests.unit.test_pipeline_live_decide import live_stages

FETCHED = datetime(2026, 11, 18, 9, 30, tzinfo=UTC)
VELA_QUERY = "Vela Pro 14"
VELA_TITLE = "Vela Pro 14 Laptop (16GB RAM, 512GB SSD)"
VELA_LINK = "https://madeupstore.example/vela-pro-14"


class FakeShopping:
    """Answers by query, taking a Serper unit for each, as the real client does. Records what it was asked."""

    def __init__(self, answers: dict[str, ShoppingResult] | None = None, fail: bool = False) -> None:
        self.answers = answers or {}
        self.fail = fail
        self.asked: list[str] = []
        self._lock = threading.Lock()

    def search(self, query: str, usage: SearchUsage) -> ShoppingResult:
        with self._lock:
            self.asked.append(query)
        if self.fail:
            return ShoppingResult(failure="error", detail="shopping: request failed")
        if not usage.take("serper"):
            return ShoppingResult(failure="quota_exhausted")
        return self.answers.get(query, ShoppingResult(fetched_at=FETCHED))


def _vela_at(price: str) -> FakeShopping:
    listing = Listing(title=VELA_TITLE, link=VELA_LINK, store="Made-up Store", price=price, position=1)
    return FakeShopping({VELA_QUERY: ShoppingResult(listings=(listing,), fetched_at=FETCHED)})


def _stages(shopping: Any, verify: Any = None) -> SearchStages:
    stages = live_stages()
    usage = UsageBook({"serper": ProviderCap(per_search=6, per_day=60)}, path=None)
    stages.enrich = PriceLookupStage(shopping, usage=usage)
    if verify is not None:
        stages.verify = verify
    return stages


def _run(stages: SearchStages) -> C.SearchResult:
    request = C.SearchRequest(session_id=ex.SESSION_ID, raw_query="a made-up request", rubric=ex.rubric())
    return PipelineRunner(stages, InMemoryResultStore()).run(request)


def _names(result: C.SearchResult) -> dict[str, str]:
    return {p.product.id: p.product.name for p in result.products}


def _groups(result: C.SearchResult) -> dict[str, tuple[str, list[str]]]:
    assert result.decision is not None
    names = _names(result)
    return {names[f.product_id]: (f.group, f.reasons) for f in result.decision.filtered}


def _shown(result: C.SearchResult) -> list[str]:
    assert result.decision is not None
    names = _names(result)
    return [names[r.product_id] for r in result.decision.ranked]


def _vela_price(result: C.SearchResult) -> C.ResolvedValue:
    vela = next(p for p in result.products if p.product.name == CANT_TELL)
    return next(v for v in vela.values if v.criterion_key == PRICE_KEY)


def test_the_default_enrich_stage_is_the_price_lookup() -> None:
    stage = default_enrich_stage()
    assert isinstance(stage, PriceLookupStage) and not stage.is_placeholder


def test_only_the_cant_tell_product_is_looked_up() -> None:
    shopping = FakeShopping()
    _run(_stages(shopping))
    assert shopping.asked == [VELA_QUERY]  # the others have a known price from two review sites


def test_a_cant_tell_product_becomes_shown_with_its_store_price() -> None:
    result = _run(_stages(_vela_at("₹62,490")))
    assert CANT_TELL in _shown(result) and CANT_TELL not in _groups(result)
    assert set(SHOWN) <= set(_shown(result))
    price = _vela_price(result)
    assert price.state is C.ValueState.KNOWN and price.value == 62490.0
    assert result.decision is not None and all(r.score is not None for r in result.decision.ranked)
    assert result.coverage_note is not None and "1 price came from a store listing." in result.coverage_note


def test_an_over_budget_price_filters_the_product_out_with_its_reason() -> None:
    result = _run(_stages(_vela_at("₹79,990")))
    groups = _groups(result)
    assert groups[CANT_TELL] == ("filtered_out", ["₹79,990 is above your ₹70,000 budget"])
    assert groups[FILTERED_OUT][0] == "filtered_out"


def test_the_new_source_and_claim_reach_the_result_and_its_citations() -> None:
    result = _run(_stages(_vela_at("₹62,490")))
    (store,) = [s for s in result.sources if s.type is C.SourceType.RETAILER]
    assert str(store.url) == VELA_LINK and store.fetched_at == FETCHED
    price = _vela_price(result)
    cited = {c.claim_id: c for c in result.citations}
    assert set(price.supporting_claim_ids) <= set(cited)
    (claim_id,) = price.supporting_claim_ids
    assert cited[claim_id].source_id == store.id
    assert cited[claim_id].quote == f"{VELA_TITLE}, ₹62,490, at Made-up Store"


def test_the_new_price_opens_in_the_source_drawer_with_its_date() -> None:
    stages = _stages(_vela_at("₹62,490"))
    recorder = ClaimRecorder(stages.extract)
    stages.extract = recorder
    result = _run(stages)
    vela = next(p.product.id for p in result.products if p.product.name == CANT_TELL)
    answer = evidence_for(result, cited_claims(result, recorder.claims()), vela, PRICE_KEY)
    (shown,) = answer.claims
    assert answer.state is C.ValueState.KNOWN and answer.winning_claim_ids == [shown.claim_id]
    assert shown.retrieved_at == FETCHED and shown.value == 62490.0
    assert str(shown.source.url) == VELA_LINK


class _SecondRunFails:
    """The real verify the first time; the second time (with enrich's prices) it raises."""

    name = C.StageName.VERIFY
    is_placeholder = False

    def __init__(self) -> None:
        self.inner = TrustVerifyStage()
        self.runs = 0

    def run(self, inp: C.VerifyInput, ctx: C.StageContext) -> C.VerifyResult:
        self.runs += 1
        if self.runs > 1:
            raise RuntimeError("made up")
        return self.inner.run(inp, ctx)

    def fallback(self, inp: C.VerifyInput, ctx: C.StageContext, error: Exception) -> C.VerifyResult:
        return self.inner.fallback(inp, ctx, error)


def test_a_failed_second_verify_keeps_the_first_result() -> None:
    verify = _SecondRunFails()
    result = _run(_stages(_vela_at("₹62,490"), verify))
    assert verify.runs == 2
    assert _groups(result)[CANT_TELL] == ("cant_tell", ["We couldn't find its price"])
    assert not any(s.type is C.SourceType.RETAILER for s in result.sources)
    assert result.coverage_note is None or "store listing" not in result.coverage_note
    assert [r.stage for r in result.stage_reports].count(C.StageName.VERIFY) == 1  # its report is never added
    assert all(r.status is C.StageStatus.OK for r in result.stage_reports)


def _baseline() -> C.SearchResult:
    stages = live_stages()
    stages.enrich = EnrichStage()
    return _run(stages)


@pytest.mark.parametrize("fail", ["serper_down", "raises"])
def test_enrich_failing_changes_nothing(fail: str) -> None:
    class Raises:
        def search(self, query: str, usage: SearchUsage) -> ShoppingResult:
            raise RuntimeError("made up")

    shopping = FakeShopping(fail=True) if fail == "serper_down" else Raises()
    result = _run(_stages(shopping))
    before = _baseline()
    assert _shown(result) == _shown(before) and _groups(result) == _groups(before)
    assert [s.id for s in result.sources] == [s.id for s in before.sources]
    assert result.citations == before.citations
    enrich = next(r for r in result.stage_reports if r.stage is C.StageName.ENRICH)
    assert enrich.status is (C.StageStatus.OK if fail == "serper_down" else C.StageStatus.FALLBACK)


def test_no_llm_call_is_made(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []

    def complete(self: LLMClient, task: object, *args: object, **kwargs: object) -> object:
        calls.append(task)
        raise AssertionError("no LLM call is expected here")

    monkeypatch.setattr(LLMClient, "complete", complete)
    stages = _stages(_vela_at("₹62,490"))
    result = _run(stages)
    assert calls == []
    assert CANT_TELL in _shown(result)


def test_without_a_budget_nothing_is_looked_up() -> None:
    shopping = FakeShopping()
    rubric = C.Rubric.model_validate({**ex.rubric().model_dump(), "budget": None})
    request = C.SearchRequest(session_id=ex.SESSION_ID, raw_query="a made-up request", rubric=rubric)
    PipelineRunner(_stages(shopping), InMemoryResultStore()).run(request)
    assert shopping.asked == []


def test_the_note_has_enrichs_sentence() -> None:
    note = build_coverage_note([], [], False, enrich_note="2 prices came from store listings.")
    assert note == "2 prices came from store listings."


class _SecondRun:
    """The real verify; its second run (with enrich's prices) does what `second` says."""

    name = C.StageName.VERIFY
    is_placeholder = False

    def __init__(self, second: str, first: str = "real", drop: str | None = None) -> None:
        self.inner = TrustVerifyStage()
        self.second, self.first, self.drop = second, first, drop
        self.runs = 0
        self.kept: C.VerifyResult | None = None

    def run(self, inp: C.VerifyInput, ctx: C.StageContext) -> C.VerifyResult:
        self.runs += 1
        how = self.first if self.runs == 1 else self.second
        if how == "raises":
            raise RuntimeError("made up")
        if how == "slow":
            time.sleep(1.5)
        if how == "same_as_first" and self.kept is not None:
            return self.kept
        out = self.inner.run(inp, ctx)
        if how == "drops" and self.drop is not None:
            dropped = next(p.id for p in inp.match.products if p.name == self.drop)
            out = C.VerifyResult.model_validate(
                {
                    **out.model_dump(),
                    "values": [v.model_dump() for v in out.values if v.product_id != dropped],
                    "dropped_unsourced": [dropped],
                }
            )
        self.kept = out
        return out

    def fallback(self, inp: C.VerifyInput, ctx: C.StageContext, error: Exception) -> C.VerifyResult:
        return self.inner.fallback(inp, ctx, error)


def test_the_note_counts_only_the_prices_verify_took() -> None:
    """A price verify doesn't take (a heavier source says otherwise) is never said to have come from a listing."""
    verify = _SecondRun("same_as_first")
    result = _run(_stages(_vela_at("₹62,490"), verify))
    assert verify.runs == 2
    assert _groups(result)[CANT_TELL][0] == "cant_tell"
    assert result.coverage_note is None or "store listing" not in result.coverage_note
    assert not any(s.type is C.SourceType.RETAILER for s in result.sources)


def test_a_second_verify_that_takes_too_long_keeps_the_first_result() -> None:
    verify = _SecondRun("slow")
    stages = _stages(_vela_at("₹62,490"), verify)
    request = C.SearchRequest(session_id=ex.SESSION_ID, raw_query="a made-up request", rubric=ex.rubric())
    runner = PipelineRunner(stages, InMemoryResultStore(), time_limits={C.StageName.VERIFY: 1.0})
    result = runner.run(request)
    assert verify.runs == 2
    assert _groups(result)[CANT_TELL][0] == "cant_tell"
    assert not any(s.type is C.SourceType.RETAILER for s in result.sources)
    assert all(r.status is C.StageStatus.OK for r in result.stage_reports)


def test_a_product_dropped_as_unsourced_stays_dropped() -> None:
    """The listing names products too; a product the first verify dropped must not come back on another's listing."""
    verify = _SecondRun("real", first="drops", drop=FILTERED_OUT)
    result = _run(_stages(_vela_at("₹62,490"), verify))
    assert verify.runs == 2
    assert FILTERED_OUT not in _names(result).values()
    assert CANT_TELL in _shown(result)


def test_after_a_verify_fallback_nothing_is_looked_up() -> None:
    """Verify run again would give values its report says it didn't, so enrich leaves them as they are."""
    shopping = _vela_at("₹62,490")
    result = _run(_stages(shopping, _SecondRun("real", first="raises")))
    assert shopping.asked == []
    verify = next(r for r in result.stage_reports if r.stage is C.StageName.VERIFY)
    assert verify.status is C.StageStatus.FALLBACK


def test_the_search_runs_recorder_keeps_enrichs_claims() -> None:
    """The real route's wrapper (search_runs._recording) gets the new price, so its drawer can open it."""
    from app.api.search_runs import _recording

    runner = PipelineRunner(_stages(_vela_at("₹62,490")), InMemoryResultStore())
    recording, recorder = _recording(runner)
    assert recorder is not None
    request = C.SearchRequest(session_id=ex.SESSION_ID, raw_query="a made-up request", rubric=ex.rubric())
    result = recording.run(request)
    (claim_id,) = _vela_price(result).supporting_claim_ids
    assert claim_id in cited_claims(result, recorder.claims())
