"""The decide stage's fallback (build rule 9, D-20): an error hands over to the stub, so the result says "sample"."""

from __future__ import annotations

import logging

import pytest

from app.contracts import DecideInput, DecideResult, StageContext, StageStatus, examples
from app.decide import DecideStage, WeightedDecideStage
from app.pipeline import made_by_placeholder, run_stage


def _ctx() -> StageContext:
    """A fresh search context each time: one made at import would run out of its 150 seconds during a long
    test run, and run_stage would then report a timeout that never happened."""
    return StageContext(search_id="srch_decide0002")


class _Broken(WeightedDecideStage):
    def run(self, inp: DecideInput, ctx: StageContext) -> DecideResult:
        raise ValueError("secret shopper text that must not reach the log")


def _input() -> DecideInput:
    return DecideInput(rubric=examples.rubric(), products=examples.match().products, values=examples.verify().values)


def test_an_error_hands_over_to_the_stub_and_says_sample(caplog: pytest.LogCaptureFixture) -> None:
    stage = _Broken()
    with caplog.at_level(logging.WARNING):
        out, report = run_stage(stage, _input(), _ctx(), 2.0)
    assert report.status is StageStatus.FALLBACK
    assert out == examples.decision()
    assert isinstance(stage.baseline, DecideStage)
    assert made_by_placeholder(stage, report)  # so the runner adds the sample note
    assert "ValueError" in caplog.text
    assert "secret shopper text" not in caplog.text


def test_a_run_that_works_is_real_and_not_a_sample() -> None:
    stage = WeightedDecideStage()
    out, report = run_stage(stage, _input(), _ctx(), 2.0)
    assert report.status is StageStatus.OK
    assert out is not None and out.method == "weighted"
    assert not made_by_placeholder(stage, report)
