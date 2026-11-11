"""US-18: shown products are ranked on the shopper's own weights, with coverage (DR-09, DR-10).

Every product here is made up.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from app.contracts import (
    DecideResult,
    Exclusion,
    HardRule,
    StageContext,
    StageName,
    StageStatus,
    ValueState,
)
from app.contracts.common import STAGE_TIME_LIMITS_S
from app.decide import WeightedDecideStage, decide
from app.decide.notes import left_out, left_out_note
from app.pipeline import run_stage
from tests.unit.decide.build import (
    RULE_RAM,
    decide_input,
    disputed,
    estimated,
    known,
    product,
    rubric,
    shown,
    unknown,
)

A, B, C, D = "prd_alpha_one", "prd_bravo_two", "prd_charlie_3", "prd_delta_four"
TWO = (("battery_life", 6.0), ("cpu_performance", 4.0))


def _scored(pid: str, battery: float | None, cpu: float | None) -> list[Any]:
    out = []
    for key, score in (("battery_life", battery), ("cpu_performance", cpu)):
        out.append(unknown(pid, key) if score is None else known(pid, key, score=score))
    return out


def _ranked(result: DecideResult, pid: str) -> Any:
    return next(r for r in result.ranked if r.product_id == pid)


def _contribution(result: DecideResult, pid: str, key: str) -> Any:
    return next(c for c in _ranked(result, pid).contributions if c.criterion_key == key)


def test_the_score_is_the_weighted_sum_times_ten() -> None:
    result = decide(decide_input(rubric(weights=TWO), [product(A)], _scored(A, 8.0, 5.0)))
    ranked = _ranked(result, A)
    assert ranked.score == pytest.approx(68.0)
    assert [(c.criterion_key, c.normalized_weight, c.contribution) for c in ranked.contributions] == [
        ("battery_life", pytest.approx(0.6), pytest.approx(4.8)),
        ("cpu_performance", pytest.approx(0.4), pytest.approx(2.0)),
    ]
    assert result.method == "weighted"


def test_every_criterion_has_a_contribution_in_the_rubrics_order() -> None:
    weights = (("weight", 2.0), ("battery_life", 6.0), ("cpu_performance", 4.0))
    values = [*_scored(A, 8.0, None), *_scored(B, 6.0, 7.0)]
    result = decide(decide_input(rubric(weights=weights), [product(A), product(B)], values))
    for ranked in result.ranked:
        assert [c.criterion_key for c in ranked.contributions] == ["weight", "battery_life", "cpu_performance"]


def test_the_contributions_add_up_to_the_score() -> None:
    weights = (("battery_life", 7.0), ("cpu_performance", 3.0), ("weight", 3.0))
    values = [*_scored(A, 7.3, 9.1), known(A, "weight", score=4.4), *_scored(B, 5.5, None), unknown(B, "weight")]
    result = decide(decide_input(rubric(weights=weights), [product(A), product(B)], values))
    for ranked in result.ranked:
        assert sum(c.contribution for c in ranked.contributions) * 10 == pytest.approx(ranked.score, abs=0.01)
        assert sum(c.normalized_weight for c in ranked.contributions) == pytest.approx(1.0)


def test_the_most_mentioned_product_does_not_win_when_it_fits_worst() -> None:
    """Task #39's done line: popularity shows how strong the evidence is, never the order (DR-10)."""
    popular = product(A, "Alpha One", recommender_count=120, weighted_mentions=96.5)
    quiet = product(B, "Bravo Two", recommender_count=1, weighted_mentions=0.4)
    middle = product(C, "Charlie 3", recommender_count=10, weighted_mentions=8.0)
    values = [*_scored(A, 3.0, 4.0), *_scored(B, 9.0, 8.5), *_scored(C, 6.0, 6.0)]
    result = decide(decide_input(rubric(weights=TWO), [popular, quiet, middle], values))
    assert shown(result) == [B, C, A]
    assert result.ranked[0].label == "best_match"


def test_mention_counts_never_change_the_score() -> None:
    values = [*_scored(A, 7.0, 6.0), *_scored(B, 6.5, 7.0)]
    plain = decide(decide_input(rubric(weights=TWO), [product(A), product(B)], values))
    loud = [product(A, recommender_count=500, weighted_mentions=400.0), product(B, recommender_count=0)]
    assert decide(decide_input(rubric(weights=TWO), loud, values)).ranked == plain.ranked


def test_an_unknown_criterion_takes_the_peer_average_and_is_marked_estimated() -> None:
    values = [
        *_scored(A, 8.0, 7.0),
        *_scored(B, 6.0, 7.0),
        unknown(C, "battery_life"),  # Unknown
        known(C, "cpu_performance", score=7.0),
        known(D, "battery_life", value="10 hours"),  # Known, but with no score to rank on
        known(D, "cpu_performance", score=7.0),
    ]
    products = [product(A), product(B), product(C), product(D)]
    result = decide(decide_input(rubric(weights=TWO), products, values))
    for pid in (C, D):
        battery = _contribution(result, pid, "battery_life")
        assert battery.state is ValueState.ESTIMATED
        assert battery.score == pytest.approx(7.0)  # the average of the others that have it: 8 and 6
        assert battery.contribution > 0  # never zero (DR-09)
    missing = decide(decide_input(rubric(weights=TWO), products, [v for v in values if v.product_id != C]))
    assert _contribution(missing, C, "battery_life").state is ValueState.ESTIMATED  # no value at all: the same


def test_an_estimate_from_verify_keeps_its_score_and_stays_estimated() -> None:
    values = [*_scored(A, 8.0, 7.0), estimated(B, "battery_life", 5.5), known(B, "cpu_performance", score=7.0)]
    result = decide(decide_input(rubric(weights=TWO), [product(A), product(B)], values))
    battery = _contribution(result, B, "battery_life")
    assert (battery.state, battery.score) == (ValueState.ESTIMATED, pytest.approx(5.5))


def test_a_criterion_no_product_has_is_left_out_and_named() -> None:
    values = [*_scored(A, 8.0, None), *_scored(B, 6.0, None)]
    result = decide(decide_input(rubric(weights=TWO), [product(A), product(B)], values))
    cpu = _contribution(result, A, "cpu_performance")
    assert (cpu.state, cpu.score, cpu.normalized_weight, cpu.contribution) == (ValueState.UNKNOWN, None, 0.0, 0.0)
    assert _contribution(result, A, "battery_life").normalized_weight == pytest.approx(1.0)  # renormalised
    assert _ranked(result, A).score == pytest.approx(80.0)
    assert left_out(result) == ["cpu_performance"]
    assert left_out_note(result) == (
        "We found no information on CPU performance for any product, so it was left out of the scores."
    )


def test_two_left_out_criteria_are_named_together() -> None:
    weights = (("battery_life", 6.0), ("cpu_performance", 4.0), ("max_temperature", 2.0))
    values = [*_scored(A, 8.0, None), unknown(A, "max_temperature")]
    result = decide(decide_input(rubric(weights=weights), [product(A)], values))
    assert left_out(result) == ["cpu_performance", "max_temperature"]
    assert left_out_note(result) == (
        "We found no information on CPU performance and max temperature for any product, so they were left out of "
        "the scores."
    )
    full = decide(decide_input(rubric(weights=TWO), [product(A)], _scored(A, 8.0, 6.0)))
    assert (left_out(full), left_out_note(full)) == ([], None)


def test_when_the_remaining_weights_are_all_zero_they_share_equally() -> None:
    weights = (("battery_life", 0.0), ("cpu_performance", 0.0), ("weight", 5.0))
    values = [*_scored(A, 8.0, 6.0), unknown(A, "weight")]
    result = decide(decide_input(rubric(weights=weights), [product(A)], values))
    assert _ranked(result, A).score == pytest.approx(70.0)


def test_coverage_is_the_evidenced_share_of_the_score() -> None:
    values = [
        *_scored(A, 8.0, 6.0),
        *_scored(B, 8.0, None),  # its CPU score is a peer estimate
        disputed(C, "battery_life", score=8.0),
        estimated(C, "cpu_performance", 6.0),  # verify's estimate is not evidence either
    ]
    weights = (("battery_life", 5.0), ("cpu_performance", 5.0))
    result = decide(decide_input(rubric(weights=weights), [product(A), product(B), product(C)], values))
    assert _ranked(result, A).coverage == pytest.approx(1.0)
    assert _ranked(result, B).coverage == pytest.approx(round(4 / 7, 3))  # 4 of the 7 points have evidence
    assert _ranked(result, C).coverage == pytest.approx(round(4 / 7, 3))  # a Disputed value counts as evidence


def test_with_a_score_of_zero_coverage_is_the_evidenced_share_of_the_weight() -> None:
    values = [known(A, "battery_life", score=0.0), estimated(A, "cpu_performance", 0.0)]
    result = decide(decide_input(rubric(weights=TWO), [product(A)], values))
    assert _ranked(result, A).score == 0.0
    assert _ranked(result, A).coverage == pytest.approx(0.6)


def test_labels_and_the_gap_to_the_leader() -> None:
    values = [*_scored(A, 9.0, 9.0), *_scored(B, 8.0, 7.5), *_scored(C, 5.0, 5.0)]
    result = decide(decide_input(rubric(weights=TWO), [product(C), product(B), product(A)], values))
    assert shown(result) == [A, B, C]
    assert [r.rank for r in result.ranked] == [1, 2, 3]
    assert [r.label for r in result.ranked] == ["best_match", "runner_up", None]
    assert [r.gap_to_leader for r in result.ranked] == [0.0, pytest.approx(12.0), pytest.approx(40.0)]
    assert all(r.tied_with == [] and r.chance_first is None for r in result.ranked)


def _confidence(evidenced: list[bool], scores: list[float], battery: float = 8.0) -> list[str]:
    """One product per score; product i's battery is Known when evidenced[i], else a verify estimate. The battery's
    weight out of 10 is the coverage of an evidenced product."""
    weights = (("battery_life", battery), ("cpu_performance", 10.0 - battery))
    values: list[Any] = []
    pids = [f"prd_item_{i:02d}" for i in range(len(scores))]
    for pid, score, has in zip(pids, scores, evidenced, strict=True):
        values.append(known(pid, "battery_life", score=score) if has else estimated(pid, "battery_life", score))
        values.append(estimated(pid, "cpu_performance", score))
    result = decide(decide_input(rubric(weights=weights), [product(p) for p in pids], values))
    return [r.confidence for r in sorted(result.ranked, key=lambda r: pids.index(r.product_id))]


@pytest.mark.parametrize(
    ("evidenced", "scores", "expected"),
    [
        pytest.param([True, True], [8.0, 7.5], ["high", "medium"], id="coverage-0.8-and-5-points-ahead"),
        pytest.param([True, True], [8.0, 7.51], ["medium", "medium"], id="4.9-points-ahead"),
        pytest.param([False, True], [8.0, 7.0], ["low", "medium"], id="coverage-under-0.5"),
        pytest.param([True], [9.0], ["medium"], id="the-last-product-has-no-next"),
    ],
)
def test_confidence_is_high_medium_or_low(evidenced: list[bool], scores: list[float], expected: list[str]) -> None:
    assert _confidence(evidenced, scores) == expected


@pytest.mark.parametrize(
    ("battery", "expected"),
    [
        pytest.param(6.0, "medium", id="coverage-0.6-and-far-ahead"),
        pytest.param(5.0, "medium", id="coverage-exactly-0.5-is-not-low"),
        pytest.param(4.9, "low", id="coverage-0.49"),
    ],
)
def test_coverage_between_the_lines_gives_medium(battery: float, expected: str) -> None:
    assert _confidence([True, True], [9.0, 2.0], battery)[0] == expected


def test_only_shown_products_are_ranked_and_give_peer_averages() -> None:
    rb = rubric(weights=TWO, exclusions=[Exclusion(kind="brand", value="Zenith")])
    values = [*_scored(A, 8.0, 7.0), *_scored(B, 6.0, 7.0), *_scored(C, None, 7.0), *_scored(D, 10.0, 10.0)]
    products = [product(A), product(B), product(C), product(D, "Zenith Four")]
    result = decide(decide_input(rb, products, values))
    assert sorted(shown(result)) == sorted([A, B, C])
    assert _contribution(result, C, "battery_life").score == pytest.approx(7.0)  # not pulled up by the Zenith's 10


def test_with_nothing_to_score_products_are_listed_unlabelled() -> None:
    result = decide(decide_input(rubric(weights=TWO), [product(A), product(B)], []))
    assert shown(result) == [A, B]
    for ranked in result.ranked:
        assert (ranked.score, ranked.coverage, ranked.confidence, ranked.label) == (0.0, 0.0, "low", None)
        assert {c.state for c in ranked.contributions} == {ValueState.UNKNOWN}
    assert left_out(result) == ["battery_life", "cpu_performance"]


def test_ties_are_broken_by_coverage_then_by_id() -> None:
    values = [
        *_scored(C, 7.0, 7.0),
        *_scored(B, 7.0, 7.0),
        estimated(A, "battery_life", 7.0),
        known(A, "cpu_performance", score=7.0),
    ]
    result = decide(decide_input(rubric(weights=TWO), [product(A), product(C), product(B)], values))
    assert shown(result) == [B, C, A]


def test_same_inputs_same_output() -> None:
    rb = rubric(weights=TWO, budget_max=70000, rules=[RULE_RAM])
    values = [*_scored(A, 8.0, None), *_scored(B, 6.0, 7.0), *_scored(C, 7.0, 7.0)]
    for pid in (A, B, C):
        values += [known(pid, "price_inr", value=60000), known(pid, "ram_gb", value=16)]
    inp = decide_input(rb, [product(A), product(B), product(C)], values)
    first = decide(inp)
    assert all(decide(inp) == first for _ in range(5))
    assert decide(inp.model_validate({**inp.model_dump(), "seed": 7})) == first  # the plain score uses no seed


def test_more_than_fifty_shown_keeps_the_top_fifty() -> None:
    pids = [f"prd_item_{i:03d}" for i in range(60)]
    values = [known(pid, "battery_life", score=round(i / 6, 2)) for i, pid in enumerate(pids)]
    result = decide(decide_input(rubric(), [product(p) for p in pids], values))
    assert len(result.ranked) == 50
    assert shown(result)[0] == pids[-1]


def test_two_hundred_products_take_under_two_seconds() -> None:
    weights = tuple((f"criterion_{i}", float(i + 1)) for i in range(10))
    rules = [
        HardRule(attribute=f"spec_{i}_gb", operator=">=", value=1, unit="GB", source_text=f"rule {i}")
        for i in range(20)
    ]
    exclusions = [Exclusion(kind="brand" if i % 2 else "model", value=f"Brand {i}") for i in range(30)]
    rb = rubric(weights=weights, budget_max=100000, rules=rules, exclusions=exclusions)
    pids = [f"prd_item_{i:03d}" for i in range(200)]
    values: list[Any] = []
    for n, pid in enumerate(pids):
        values.append(known(pid, "price_inr", value=50000 + n * 300))
        values += [known(pid, f"spec_{i}_gb", value=f"{1 + n % 3} GB") for i in range(20)]
        values += [known(pid, key, score=(n * 7 + i) % 11) for i, (key, _) in enumerate(weights) if (n + i) % 4]
    inp = decide_input(rb, [product(p, f"Item {p[-3:]} Laptop") for p in pids], values)
    started = time.perf_counter()
    result = decide(inp)
    assert time.perf_counter() - started < 2.0
    assert result.ranked
    out, report = run_stage(
        WeightedDecideStage(), inp, StageContext(search_id="srch_decide0001"), STAGE_TIME_LIMITS_S[StageName.DECIDE]
    )
    assert report.status is StageStatus.OK
    assert out == result
