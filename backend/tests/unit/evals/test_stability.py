"""US-19's stability check (S4-7, DECIDED 4): the rank order under random 10% weight changes on the five briefs.

The committed record (evals/results/us19-stability.json) must be what the check gives now, and its median must meet
US-19's target of 0.9, which is never lowered. Every brief here is made up.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from evals import stability
from tests.unit.decide.build import decide_input, known, product, rubric

A, B, C = "prd_alpha_one", "prd_bravo_two", "prd_charlie_3"


def test_the_target_is_0_9_and_never_lowered() -> None:
    assert stability.TARGET == 0.9
    assert (stability.CHANGES, stability.SHIFT) == (100, 0.10)


def test_the_committed_record_is_what_the_check_gives_now() -> None:
    committed = json.loads(stability.STABILITY_OUT.read_text(encoding="utf-8"))
    assert committed == stability.measure()


def test_the_rank_order_meets_the_target() -> None:
    record = json.loads(stability.STABILITY_OUT.read_text(encoding="utf-8"))
    assert record["meets_target"] is True
    assert record["median"] >= stability.TARGET
    counted = [name for name, row in record["per_brief"].items() if row["counted"]]
    assert len(counted) >= 4  # every brief that shows 2 or more products
    assert all(record["per_brief"][name]["shown"] >= 2 for name in counted)


@pytest.mark.parametrize(
    ("after", "rho"),
    [
        pytest.param(["a", "b", "c", "d"], 1.0, id="same-order"),
        pytest.param(["d", "c", "b", "a"], -1.0, id="reversed"),
        pytest.param(["b", "a", "c", "d"], 0.8, id="one-swap-at-the-top"),
        pytest.param(["a", "b", "d", "c"], 0.8, id="one-swap-at-the-bottom"),
    ],
)
def test_spearman_reads_an_order(after: list[str], rho: float) -> None:
    assert stability.spearman(["a", "b", "c", "d"], after) == pytest.approx(rho)


def test_spearman_needs_the_same_products_and_two_of_them() -> None:
    with pytest.raises(ValueError):
        stability.spearman(["a", "b"], ["a", "c"])
    assert stability.spearman(["a"], ["a"]) == 1.0


def test_a_change_moves_each_share_by_at_most_10_percent_before_renormalising() -> None:
    rb = rubric(weights=(("battery_life", 6.0), ("cpu_performance", 3.0), ("weight", 1.0)))
    rng = np.random.default_rng(0)
    for _ in range(50):
        moved = stability.changed(rb, rng)
        assert sum(w.normalized for w in moved.weights) == pytest.approx(1.0)
        ratios = [m.normalized / w.normalized for m, w in zip(moved.weights, rb.weights, strict=True)]
        # each share moved by up to 10% either way; renormalising scales them all by the same factor
        assert max(ratios) / min(ratios) <= 1.1 / 0.9 + 1e-9


def test_the_check_falls_below_1_when_close_products_trade_criteria(tmp_path: Path) -> None:
    """The five briefs' products are far apart, so no 10% change reorders them. This made-up brief's products are
    level and trade battery life against speed, so the same check does find changed orders: it can fail."""
    values = [
        known(A, "battery_life", score=8.0),
        known(A, "cpu_performance", score=6.0),
        known(B, "battery_life", score=6.0),
        known(B, "cpu_performance", score=8.0),
        known(C, "battery_life", score=4.0),
        known(C, "cpu_performance", score=4.0),
    ]
    rb = rubric(weights=(("battery_life", 5.0), ("cpu_performance", 5.0)))
    inp = decide_input(rb, [product(A), product(B), product(C)], values)
    (tmp_path / "close_products.json").write_text(json.dumps({"input": inp.model_dump(mode="json")}), encoding="utf-8")
    record = stability.measure(tmp_path)
    assert record["per_brief"]["close_products"]["counted"] is True
    assert record["lowest"] < 1.0
    assert record["meets_target"] is (record["median"] >= stability.TARGET)
    assert record["note"] is None  # the note on the five briefs is for an all-1.0 record only


def test_the_record_says_when_no_change_reordered_anything() -> None:
    record = json.loads(stability.STABILITY_OUT.read_text(encoding="utf-8"))
    if record["lowest"] == 1.0:
        assert record["note"] is not None and "no change reordered" in record["note"].casefold()
