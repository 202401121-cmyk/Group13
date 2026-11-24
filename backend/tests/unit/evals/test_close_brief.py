"""US-19 criterion 4 (S4-28, D-74): a made-up brief with close products, so the stability check can fail, and a floor.

The brief (tests/fixtures/decide/stability/close_earbuds.json) is reported beside the five decide briefs, with the
same seed and method. Its median Spearman has a floor in evals/thresholds.yaml (the measured value less 0.05); US-19's
target of 0.9 stays beside it, reported met or not met, never lowered. Every product and value in it is made up.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from app.contracts import DecideInput, ValueState
from app.decide import decide
from evals import stability, thresholds

MEASURE = "decide.stability.close_median"


def _category(fallback: Any, name: str) -> dict[str, Any]:
    """One category's entry in criteria_fallback.yaml, wherever the file nests it."""
    if isinstance(fallback, dict):
        if name in fallback and isinstance(fallback[name], dict) and "criteria" in fallback[name]:
            found: dict[str, Any] = fallback[name]
            return found
        for value in fallback.values():
            try:
                return _category(value, name)
            except KeyError:
                continue
    raise KeyError(name)


def _brief() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(stability.CLOSE_BRIEF.read_text(encoding="utf-8"))
    return data


def _input() -> DecideInput:
    return DecideInput.model_validate(_brief()["input"])


@pytest.fixture(scope="module")
def measured() -> dict[str, Any]:
    """One measurement for the tests that need one: each costs 600 decide runs."""
    return stability.measure()


def _record() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(stability.STABILITY_OUT.read_text(encoding="utf-8"))
    return data


def test_the_brief_loads_validates_and_is_marked_made_up() -> None:
    brief = _brief()
    assert "made-up" in brief["about"].lower() and "not real facts" in brief["about"].lower()
    inp = _input()
    assert 6 <= len(inp.products) <= 8
    assert inp.rubric.category == "earbuds"


def test_its_criteria_are_real_earbuds_keys() -> None:
    import yaml  # type: ignore[import-untyped]

    from evals.run_baselines import BACKEND

    fallback = yaml.safe_load((BACKEND / "config" / "criteria_fallback.yaml").read_text(encoding="utf-8"))
    earbuds = {c["key"]: c["default_weight"] for c in _category(fallback, "earbuds")["criteria"]}
    for weight in _input().rubric.weights:
        assert weight.key in earbuds  # an earbuds key, never one from another category
        assert weight.weight == earbuds[weight.key]  # at the category's default weight, as its origin says
        assert weight.origin == "default"


def test_every_value_is_sourced_in_the_briefs_own_format() -> None:
    for value in _input().values:
        assert value.state is ValueState.KNOWN
        assert value.supporting_claim_ids and all(c.startswith("clm_") for c in value.supporting_claim_ids)


def test_at_least_three_shown_products_are_within_a_few_points() -> None:
    scores = sorted((r.score for r in decide(_input()).ranked if r.score is not None), reverse=True)
    assert len(scores) >= 3
    assert abs(scores[0] - scores[2]) <= 3.0  # the top three, on decide's 0 to 100 scale


def test_no_product_leads_on_every_criterion() -> None:
    inp = _input()
    keys = [w.key for w in inp.rubric.weights]
    best = {k: max(v.score or 0.0 for v in inp.values if v.criterion_key == k) for k in keys}
    for p in inp.products:
        mine = {v.criterion_key: v.score for v in inp.values if v.product_id == p.id}
        assert not all(mine.get(k) == best[k] for k in keys), p.name


def test_it_gives_its_hand_written_order() -> None:
    names = {p.id: p.name for p in _input().products}
    assert [names[r.product_id] for r in decide(_input()).ranked] == _brief()["expect"]["shown"]


def test_some_weight_change_reorders_its_top_3_so_the_check_can_fail(measured: dict[str, Any]) -> None:
    close = measured["close"]
    assert close["top3_reordered"] >= 1
    assert close["lowest"] < 1.0


def test_the_same_seed_gives_the_same_numbers(measured: dict[str, Any]) -> None:
    assert stability.measure() == measured


def test_the_five_briefs_keep_their_numbers(measured: dict[str, Any]) -> None:
    record = measured
    assert record["briefs"] == "backend/tests/fixtures/decide (5 made-up briefs)"
    assert (record["median"], record["lowest"]) == (1.0, 1.0)


def test_the_record_reports_the_close_brief_beside_the_five() -> None:
    record = _record()
    close = record["close"]
    assert close["brief"] == "backend/tests/fixtures/decide/stability/close_earbuds.json"
    assert close["target_median"] == stability.TARGET == 0.9
    assert close["meets_target"] is (close["median"] >= 0.9)
    if not close["meets_target"]:
        assert "below US-19's target of 0.9" in close["note"]
    measure = next(m for m in record["measures"] if m["id"] == MEASURE)  # beside it, P-45's number (S5-15)
    assert (measure["id"], measure["value"], measure["status"]) == (MEASURE, close["median"], "measured")


def test_the_floor_is_the_measured_median_less_0_05_with_the_target_beside_it() -> None:
    floor = thresholds.load(thresholds.THRESHOLDS).measures[MEASURE]
    median = _record()["close"]["median"]
    assert floor.measured == median
    lowered = round(median - 0.05, 4)
    assert floor.floor == pytest.approx(max(lowered, 0.9) if median >= 0.9 else lowered)  # a met target stays (P-45)
    assert floor.target is not None and floor.target.value == 0.9
    assert floor.target.note == ("met" if median >= 0.9 else "not met")


def test_the_floor_check_passes_on_the_record_and_fails_01_lower() -> None:
    loaded = thresholds.load(thresholds.THRESHOLDS)
    measures = thresholds.all_measures()
    assert not [p for p in thresholds.check(loaded, measures) if MEASURE in p]
    lower = [{**m, "value": round(m["value"] - 0.1, 4)} if m["id"] == MEASURE else m for m in measures]
    assert [p for p in thresholds.check(loaded, lower) if MEASURE in p] == [
        f"{MEASURE} is below its floor: {round(_record()['close']['median'] - 0.1, 4)} < "
        f"{loaded.measures[MEASURE].floor}"
    ]


def test_the_target_can_never_be_lowered() -> None:
    content = thresholds.load(thresholds.THRESHOLDS).model_dump(mode="json")
    content["measures"][MEASURE]["target"]["value"] = 0.85
    with pytest.raises(ValidationError):
        thresholds.Thresholds.model_validate(content)
