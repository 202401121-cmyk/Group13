"""The verify stage's fallbacks (build rule 9): the learned method falls back to tier order and says so; if tier order
fails too, every value is Unknown, so the search still goes on."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from app.contracts import StageContext, StageStatus, ValueState, VerifyInput, examples
from app.pipeline import made_by_placeholder, run_stage
from app.verify import TrustVerifyStage, combine


def _ctx() -> StageContext:
    """A fresh search context each time: one made at import would run out of its 150 seconds during a long
    test run, and run_stage would then report a timeout that never happened."""
    return StageContext(search_id="srch_verify0002")


def _input() -> VerifyInput:
    return VerifyInput(
        match=examples.match(),
        claims=examples.claims(),
        sources=examples.sources(),
        criterion_keys=["battery_life", "ram_gb"],
    )


def _broken(*args: Any, **kwargs: Any) -> Any:
    raise RuntimeError("secret claim text that must not reach the log")


def test_an_error_in_the_learned_method_falls_back_to_tier_order_and_says_so(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(combine, "learn_weights", _broken)
    stage = TrustVerifyStage()
    with caplog.at_level(logging.WARNING):
        out, report = run_stage(stage, _input(), _ctx(), 2.0)
    assert report.status is StageStatus.FALLBACK
    assert report.fallback_reason
    assert out is not None and out.trust_method == "tier_order"
    battery = next(v for v in out.values if v.criterion_key == "battery_life" and v.product_id == "prd_kestrel_slim_15")
    assert battery.state is ValueState.DISPUTED  # tier order still settles it, with both sides
    assert not made_by_placeholder(stage, report)  # tier order is real, not a sample
    assert "tier order" in caplog.text
    assert "secret claim text" not in caplog.text


def test_if_tier_order_fails_too_every_value_is_unknown(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(combine, "resolve_all", _broken)
    with caplog.at_level(logging.WARNING):
        out, report = run_stage(TrustVerifyStage(), _input(), _ctx(), 2.0)
    assert report.status is StageStatus.FALLBACK
    assert out is not None and out.trust_method == "tier_order"
    # Every product a source names, by both keys: the two no example source names are still dropped (US-40).
    assert out.dropped_unsourced == ["prd_aurora_air_13", "prd_vela_pro_14"]
    assert len(out.values) == (len(examples.match().products) - 2) * 2
    assert {v.state for v in out.values} == {ValueState.UNKNOWN}
    assert all(v.score is None for v in out.values)
    assert "secret claim text" not in caplog.text
