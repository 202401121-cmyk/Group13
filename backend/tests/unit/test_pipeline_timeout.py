"""Time limits (US-52): a slow stage times out, and a search out of time skips what is left.

The slow stages sleep in a background thread; the test only waits for the (tiny) limits.
"""

from __future__ import annotations

import time
from concurrent.futures import ALL_COMPLETED
from typing import Any

import pytest

import app.contracts as C
from app import pipeline
from app.contracts import examples as ex
from app.pipeline import InMemoryResultStore, PipelineRunner, default_search_stages


def _request() -> C.SearchRequest:
    return C.SearchRequest(session_id=ex.SESSION_ID, raw_query=ex.QUERY, rubric=ex.rubric())


class _SlowVerify:
    name = C.StageName.VERIFY
    fallback_output = C.VerifyResult.model_validate({**ex.verify().model_dump(), "trust_method": "tier_order"})

    def run(self, inp: C.VerifyInput, ctx: C.StageContext) -> C.VerifyResult:
        time.sleep(0.5)
        return ex.verify()

    def fallback(self, inp: C.VerifyInput, ctx: C.StageContext, error: Exception) -> C.VerifyResult:
        return self.fallback_output


class _SlowResearch:
    name = C.StageName.RESEARCH

    def run(self, inp: C.ResearchInput, ctx: C.StageContext) -> C.ResearchResult:
        time.sleep(0.3)
        return ex.research()

    def fallback(self, inp: C.ResearchInput, ctx: C.StageContext, error: Exception) -> C.ResearchResult:
        return C.ResearchResult(queries=["best laptop"], sources=[])


def test_a_slow_stage_times_out_and_its_fallback_is_used() -> None:
    stages = default_search_stages()
    stages.verify = _SlowVerify()
    runner = PipelineRunner(stages, InMemoryResultStore(), time_limits={C.StageName.VERIFY: 0.05})

    started = time.monotonic()
    result = runner.run(_request())
    assert time.monotonic() - started < 0.4  # did not wait for the 0.5 s sleep

    verify = next(r for r in result.stage_reports if r.stage is C.StageName.VERIFY)
    assert verify.status is C.StageStatus.TIMEOUT
    assert verify.fallback_reason == "took too long"
    # The fallback's output is what the rest of the pipeline used: its values reached the products.
    fallback_values = {(v.product_id, v.criterion_key) for v in _SlowVerify.fallback_output.values}
    used_values = {(v.product_id, v.criterion_key) for p in result.products for v in p.values}
    assert used_values == fallback_values
    assert result.stage_reports[-1].stage is C.StageName.COMMERCE
    assert not result.partial
    assert result.coverage_note is not None
    assert "Verify took too long, so a simpler fallback was used." in result.coverage_note


def test_running_out_of_search_time_skips_the_remaining_stages() -> None:
    stages = default_search_stages()
    stages.research = _SlowResearch()
    runner = PipelineRunner(stages, InMemoryResultStore(), search_time_limit_s=0.1)

    result = runner.run(_request())

    statuses = {r.stage: r.status for r in result.stage_reports}
    assert statuses[C.StageName.RESEARCH] is C.StageStatus.TIMEOUT
    later = [C.StageName.EXTRACT, C.StageName.MATCH, C.StageName.DECIDE, C.StageName.COMMERCE]
    assert all(statuses[s] is C.StageStatus.SKIPPED for s in later)
    assert len(result.stage_reports) == 8
    assert result.partial
    assert result.decision is None
    assert result.coverage_note is not None
    assert "ran out of time" in result.coverage_note
    assert C.SearchResult.model_validate_json(result.model_dump_json()) == result


def test_a_stage_cut_off_by_the_search_deadline_ends_the_search(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: on GitHub's Windows runners a timed wait can wake a moment before the deadline.

    The stage still times out, but a sliver of search time seems to be left, so the runner used to start the next
    stages instead of skipping them. Here every wait wakes halfway to its deadline, so the race happens on every
    machine, every time.
    """
    real_wait = pipeline.wait

    def early_wait(fs: Any, timeout: float | None = None, return_when: str = ALL_COMPLETED) -> Any:
        early = None if timeout is None else timeout / 2
        return real_wait(fs, timeout=early, return_when=return_when)

    monkeypatch.setattr(pipeline, "wait", early_wait)
    stages = default_search_stages()
    stages.research = _SlowResearch()

    result = PipelineRunner(stages, InMemoryResultStore(), search_time_limit_s=0.1).run(_request())

    statuses = [(r.stage, r.status) for r in result.stage_reports]
    assert statuses[0] == (C.StageName.RESEARCH, C.StageStatus.TIMEOUT)
    assert all(status is C.StageStatus.SKIPPED for _, status in statuses[1:]), statuses
    assert len(statuses) == 8
    assert result.partial
    assert result.coverage_note is not None and "ran out of time" in result.coverage_note
