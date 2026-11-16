"""US-19: honest confidence and ties from 1,000 simulations (S4-7).

After the filter, 1,000 seeded simulations vary each weight by its uncertainty and each score by its value's: opinion
and compared scores by 1 - confidence, estimated scores widest, and a Disputed value draws from its sides. A product's
chance of being first is its share of the simulations it tops; its confidence comes from that chance and its coverage;
products whose chance of beating each other lies between 0.4 and 0.6 are "too close to call", with the criterion that
separates them. With nothing ranked it stays weighted, and on any error the plain weighted result stands. Every
product and request here is made up.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import numpy as np
import pytest

from app.contracts import (
    STAGE_TIME_LIMITS_S,
    AlternativeValue,
    DecideInput,
    DecideResult,
    ResolvedValue,
    Rubric,
    StageName,
    ValueState,
    WeightedCriterion,
    normalize,
)
from app.decide import decide, decision
from app.decide.directions import directions_for
from app.decide.rules import index_values
from app.decide.scoring import evidence
from app.decide.simulation import (
    SIMULATIONS,
    SPREAD,
    TIME_LIMIT_S,
    SimulationTimeout,
    confidence_for,
    draw,
    simulate,
)
from tests.unit.decide.build import claim_id, decide_input, disputed, estimated, known, product, rubric, shown

A, B, C, D = "prd_alpha_one", "prd_bravo_two", "prd_charlie_3", "prd_delta_four"
TWO = (("battery_life", 6.0), ("cpu_performance", 4.0))
THREE = (("battery_life", 5.0), ("cpu_performance", 3.0), ("build_quality", 2.0))


def _scored(pid: str, *scores: float | None, keys: tuple[tuple[str, float], ...] = TWO) -> list[Any]:
    return [known(pid, key, score=s) for (key, _), s in zip(keys, scores, strict=True) if s is not None]


def _with_uncertainty(rb: Rubric, uncertainty: float) -> Rubric:
    weights = [{**w.model_dump(), "uncertainty": uncertainty} for w in rb.weights]
    return Rubric.model_validate({**rb.model_dump(), "weights": weights})


def _chances(result: DecideResult) -> dict[str, float]:
    return {r.product_id: r.chance_first or 0.0 for r in result.ranked}


def _ranked(result: DecideResult, pid: str) -> Any:
    return next(r for r in result.ranked if r.product_id == pid)


# --- the method, the seed, the validators -----------------------------------------------------------------------


def test_a_ranked_result_comes_from_the_simulations() -> None:
    values = [*_scored(A, 9.0, 8.0), *_scored(B, 6.0, 6.0), *_scored(C, 4.0, 5.0)]
    result = decide(decide_input(rubric(weights=TWO), [product(A), product(B), product(C)], values))
    assert result.method == "monte_carlo"
    assert all(r.chance_first is not None for r in result.ranked)
    assert sum(_chances(result).values()) == pytest.approx(1.0, abs=0.002)


def test_the_same_inputs_and_seed_give_the_same_output() -> None:
    values = [*_scored(A, 7.0, 6.0), *_scored(B, 6.5, 6.8), *_scored(C, 6.9, 6.1)]
    inp = decide_input(rubric(weights=TWO), [product(A), product(B), product(C)], values)
    assert decide(inp).model_dump() == decide(inp).model_dump()
    other = decide(DecideInput.model_validate({**inp.model_dump(), "seed": 7}))
    assert _chances(other) != _chances(decide(inp))  # the seed is what draws the simulations


def test_ranks_follow_the_chance_of_being_first_and_the_result_validates() -> None:
    values = [*_scored(A, 7.0, 6.0), *_scored(B, 6.5, 6.8), *_scored(C, 6.9, 6.1), *_scored(D, 3.0, 3.0)]
    result = decide(decide_input(rubric(weights=TWO), [product(p) for p in (D, C, B, A)], values))
    chances = [r.chance_first or 0.0 for r in result.ranked]
    assert chances == sorted(chances, reverse=True)
    assert [r.rank for r in result.ranked] == [1, 2, 3, 4]
    assert [r.label for r in result.ranked][:2] == ["best_match", "runner_up"]
    assert DecideResult.model_validate(result.model_dump()) == result
    leader = result.ranked[0].score or 0.0
    assert all(r.gap_to_leader == pytest.approx(max(0.0, leader - (r.score or 0.0)), abs=0.01) for r in result.ranked)


def test_the_score_and_contributions_stay_the_weighted_ones() -> None:
    values = _scored(A, 8.0, 5.0)
    result = decide(decide_input(rubric(weights=TWO), [product(A)], values))
    assert _ranked(result, A).score == pytest.approx(68.0)
    assert [c.contribution for c in _ranked(result, A).contributions] == [pytest.approx(4.8), pytest.approx(2.0)]


# --- one product, all tied, a clear leader ----------------------------------------------------------------------


def test_one_product_is_first_every_time() -> None:
    result = decide(decide_input(rubric(weights=TWO), [product(A)], _scored(A, 8.0, 5.0)))
    assert (_ranked(result, A).chance_first, _ranked(result, A).confidence) == (1.0, "high")  # coverage 1.0
    assert _ranked(result, A).tied_with == []


def test_one_product_with_little_evidence_is_still_first_but_not_confidently() -> None:
    values = [estimated(A, "battery_life", 8.0), estimated(A, "cpu_performance", 5.0)]
    ranked = _ranked(decide(decide_input(rubric(weights=TWO), [product(A)], values)), A)
    assert (ranked.chance_first, ranked.coverage, ranked.confidence) == (1.0, 0.0, "low")


def test_identical_products_share_the_first_place_and_are_all_too_close_to_call() -> None:
    values = [*_scored(A, 7.0, 6.0), *_scored(B, 7.0, 6.0), *_scored(C, 7.0, 6.0)]
    result = decide(decide_input(rubric(weights=TWO), [product(A), product(B), product(C)], values))
    for pid in (A, B, C):
        ranked = _ranked(result, pid)
        assert ranked.chance_first == pytest.approx(1 / 3, abs=0.06)
        assert sorted(ranked.tied_with) == sorted({A, B, C} - {pid})
        assert ranked.separating_criterion is None  # every contribution is the same: nothing separates them
        assert ranked.confidence == "medium"  # chance between 0.3 and 0.6, full coverage


def test_two_close_products_name_the_criterion_that_separates_them_most() -> None:
    # equal totals (65), but Alpha gets 1.0 more of its score from battery life: 0.5 × 8 against 0.5 × 6
    values = [*_scored(A, 8.0, 5.0, 5.0, keys=THREE), *_scored(B, 6.0, 7.0, 7.0, keys=THREE)]
    result = decide(decide_input(rubric(weights=THREE), [product(A), product(B)], values))
    assert _ranked(result, A).tied_with == [B]
    assert _ranked(result, B).tied_with == [A]
    assert _ranked(result, A).separating_criterion == "battery_life"
    assert _ranked(result, B).separating_criterion == "battery_life"


def test_a_clear_leader_is_confidently_first_and_tied_with_no_one() -> None:
    values = [*_scored(A, 9.0, 9.0), *_scored(B, 5.0, 5.0), *_scored(C, 4.0, 4.0)]
    result = decide(decide_input(rubric(weights=TWO), [product(C), product(B), product(A)], values))
    assert shown(result) == [A, B, C]
    assert _ranked(result, A).chance_first is not None and _ranked(result, A).chance_first >= 0.6
    assert [r.confidence for r in result.ranked] == ["high", "low", "low"]
    assert all(r.tied_with == [] and r.separating_criterion is None for r in result.ranked)


# --- the confidence rule (DECIDED 2) ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("chance", "coverage", "expected"),
    [
        pytest.param(0.6, 0.8, "high", id="both-at-their-line"),
        pytest.param(0.95, 1.0, "high", id="well-above"),
        pytest.param(0.59, 0.9, "medium", id="chance-just-under-0.6"),
        pytest.param(0.9, 0.79, "medium", id="coverage-just-under-0.8"),
        pytest.param(0.3, 0.5, "medium", id="both-at-the-low-lines"),
        pytest.param(0.29, 1.0, "low", id="chance-under-0.3"),
        pytest.param(0.9, 0.49, "low", id="coverage-under-0.5"),
        pytest.param(0.0, 0.0, "low", id="nothing"),
    ],
)
def test_confidence_is_high_medium_or_low(chance: float, coverage: float, expected: str) -> None:
    assert confidence_for(chance, coverage) == expected


# --- what the simulations draw ----------------------------------------------------------------------------------


def _drawn(rb: Rubric, products: list[Any], values: list[Any], seed: int = 42) -> Any:
    keys = [w.key for w in rb.weights]
    indexed = index_values(values, {p.id for p in products})
    return draw(products, rb, indexed, directions_for(keys, rb.category), seed)


def test_weights_stay_at_zero_or_more_and_scores_within_0_and_10() -> None:
    rb = _with_uncertainty(rubric(weights=TWO), 1.0)  # the widest weight spread there is
    values = [*_scored(A, 9.9, 0.1), estimated(B, "battery_life", 0.2), estimated(B, "cpu_performance", 9.8)]
    drawn = _drawn(rb, [product(A), product(B)], values)
    assert drawn.weights.shape == (SIMULATIONS, 2)
    assert (drawn.weights >= 0).all()
    assert np.allclose(drawn.weights.sum(axis=1), 1.0)
    assert drawn.scores.shape == (SIMULATIONS, 2, 2)
    assert ((drawn.scores >= 0) & (drawn.scores <= 10)).all()
    assert ((drawn.totals >= 0) & (drawn.totals <= 100)).all()


def test_a_weight_with_no_uncertainty_never_moves() -> None:
    drawn = _drawn(_with_uncertainty(rubric(weights=TWO), 0.0), [product(A)], _scored(A, 8.0, 5.0))
    assert np.allclose(drawn.weights, [0.6, 0.4])


def test_an_estimated_score_varies_widest_and_a_confident_one_least() -> None:
    values = [known(A, "battery_life", score=7.0), estimated(A, "cpu_performance", 7.0)]
    drawn = _drawn(rubric(weights=TWO), [product(A)], values)
    known_spread, estimated_spread = drawn.scores[:, 0, 0].std(), drawn.scores[:, 0, 1].std()
    assert known_spread == pytest.approx(SPREAD * (1 - 0.8), rel=0.15)  # known() carries confidence 0.8
    assert estimated_spread == pytest.approx(SPREAD, rel=0.15)


def test_a_disputed_score_draws_from_both_sides() -> None:
    sure = _drawn(rubric(weights=TWO), [product(A)], _scored(A, 7.0, 5.0)).scores[:, 0, 0]
    # sources split on the score itself: 7 (verify's side, confidence 0.5) or 3
    split = ResolvedValue(
        product_id=A,
        criterion_key="battery_life",
        state=ValueState.DISPUTED,
        score=7.0,
        confidence=0.5,
        supporting_claim_ids=[claim_id(A, "battery_life", "a")],
        conflicting_claim_ids=[claim_id(A, "battery_life", "b")],
        alternatives=[AlternativeValue(score=3.0, claim_ids=[claim_id(A, "battery_life", "b")])],
    )
    both = _drawn(rubric(weights=TWO), [product(A)], [split, known(A, "cpu_performance", score=5.0)]).scores[:, 0, 0]
    assert both.std() > 3 * sure.std()  # 7 or 3, against 7 alone
    assert ((both > 5).mean(), (both < 5).mean()) == (pytest.approx(0.5, abs=0.06), pytest.approx(0.5, abs=0.06))


BATTERY = (("battery_life", 10.0),)


def _hours(pid: str, *sides: float) -> Any:
    """A stated battery life: Known, or Disputed between its sides (verify's first, at confidence 0.5)."""
    if len(sides) == 1:
        return known(pid, "battery_life", value=sides[0])
    return disputed(pid, "battery_life", value=sides[0], others=[(h, None) for h in sides[1:]])


def test_a_disputed_stated_value_draws_both_sides_of_its_comparison() -> None:
    # battery life is compared from stated hours: Alpha's sources say 10 h or 6 h, Bravo's 8 h
    values = [_hours(A, 10.0, 6.0), _hours(B, 8.0)]
    scores = _drawn(rubric(weights=BATTERY), [product(A), product(B)], values).scores
    alpha, bravo = scores[:, 0, 0], scores[:, 1, 0]
    assert (alpha > 7).mean() == pytest.approx(0.5, abs=0.06)  # its own side compares highest (9)
    assert (alpha < 3).mean() == pytest.approx(0.5, abs=0.06)  # the other side compares lowest (1)
    # the comparison is scored again with Alpha's side in place, so Bravo moves with it: 1 against 10 h, 9 against 6 h
    assert ((alpha > 5) == (bravo < 5)).all()  # 9 or 1 each, 4 points either way: far beyond their noise
    assert (bravo > 7).mean() == pytest.approx(0.5, abs=0.06)


def test_a_coin_flip_dispute_is_too_close_to_call() -> None:
    values = [_hours(A, 10.0, 6.0), _hours(B, 8.0)]
    result = decide(decide_input(rubric(weights=BATTERY), [product(A), product(B)], values))
    assert result.method == "monte_carlo"
    for pid, other in ((A, B), (B, A)):
        assert _ranked(result, pid).chance_first == pytest.approx(0.5, abs=0.06)
        assert _ranked(result, pid).tied_with == [other]
        assert _ranked(result, pid).separating_criterion == "battery_life"


def test_two_disputes_on_one_criterion_score_every_set_of_sides() -> None:
    # Alpha 10 h or 6 h, Bravo 9 h or 5 h, Charlie 8 h. Charlie's score depends on both: 1 (10 and 9), 5.8 (10 and 5),
    # 6.33 (6 and 9) and 9 (6 and 5), a quarter of the simulations each
    values = [_hours(A, 10.0, 6.0), _hours(B, 9.0, 5.0), _hours(C, 8.0)]
    charlie = _drawn(rubric(weights=BATTERY), [product(A), product(B), product(C)], values).scores[:, 2, 0]
    assert (charlie < 2.5).mean() == pytest.approx(0.25, abs=0.05)
    assert ((charlie > 4.5) & (charlie < 7.5)).mean() == pytest.approx(0.5, abs=0.06)
    assert (charlie > 7.5).mean() == pytest.approx(0.25, abs=0.05)


def test_a_product_with_no_stated_value_takes_the_average_of_each_draw() -> None:
    # Delta states no battery life, so it takes its peers' average (DR-09), which moves with Alpha's side:
    # Alpha 10 h scores 9, Bravo (8 h) 1.38, Charlie (7.9 h) 1, an average of 3.79; Alpha 2 h scores 1, Bravo 9 and
    # Charlie 8.87, an average of 6.29
    values = [_hours(A, 10.0, 2.0), _hours(B, 8.0), _hours(C, 7.9), known(D, "cpu_performance", score=5.0)]
    rb = rubric(weights=(("battery_life", 5.0), ("cpu_performance", 5.0)))
    drawn = _drawn(rb, [product(A), product(B), product(C), product(D)], values)
    alpha, delta = drawn.scores[:, drawn.product_ids.index(A), 0], drawn.scores[:, drawn.product_ids.index(D), 0]
    assert delta[alpha > 5].mean() == pytest.approx(3.79, abs=0.3)
    assert delta[alpha < 5].mean() == pytest.approx(6.29, abs=0.3)


def _sure(value: ResolvedValue) -> ResolvedValue:
    return ResolvedValue.model_validate({**value.model_dump(), "confidence": 1.0})  # sure: drawn with no noise


@pytest.mark.parametrize(
    ("key", "own", "other", "rest"),
    [
        pytest.param("battery_life", "10 h", "300 min", ["8 h", "540 min", "5 h", "7.5 h"], id="hours-and-minutes"),
        pytest.param(
            "size_and_weight", "1.2 kg", "2900 g", ["1.9 kg", "1500 g", "2.4 kg", "1.6 kg"], id="lower-better"
        ),
        pytest.param(
            "battery_life", "20 h", "9.98 h", ["9.97 h", "9.99 h", "9.975 h", "9.985 h"], id="within-1-percent"
        ),
        pytest.param(
            "battery_life", "10 h", "7", ["8 h", "540 min", "5 h", "7.5 h"], id="a-side-left-out-for-its-unit"
        ),
        pytest.param("water_resistance", "yes", "no", ["yes", "no", "no", "yes"], id="yes-or-no"),
    ],
)
def test_a_drawn_side_is_compared_exactly_as_scoring_compares_it(
    key: str, own: str, other: str, rest: list[str]
) -> None:
    # Alpha's sources split between two values, and Alpha always takes the other side (confidence 0): every other
    # product, sure of its own value, must then score exactly as scoring.py scores the world with that side in place
    split = ResolvedValue.model_validate(
        {**disputed(A, key, value=own, others=[(other, None)]).model_dump(), "confidence": 0.0}
    )
    pids = [A, B, C, D, "prd_echo_five"]
    others = [_sure(known(pid, key, value=v)) for pid, v in zip(pids[1:], rest, strict=True)]
    rb = rubric(weights=((key, 10.0),))
    drawn = _drawn(rb, [product(p) for p in pids], [split, *others])
    swapped = ResolvedValue.model_validate({**split.model_dump(), "value": other})
    expected = evidence(
        [product(p) for p in pids],
        [key],
        index_values([swapped, *others], set(pids)),
        directions_for([key], "laptop"),
    )
    for pid in pids[1:]:
        assert np.allclose(drawn.scores[:, drawn.product_ids.index(pid), 0], expected[pid][key].score, atol=0.011)

    # and sure of its own side (confidence 1), every product, Alpha too, keeps the score the weighted ranking gave it
    kept = _sure(split)
    drawn = _drawn(rb, [product(p) for p in pids], [kept, *others])
    as_given = evidence(
        [product(p) for p in pids], [key], index_values([kept, *others], set(pids)), directions_for([key], "laptop")
    )
    for pid in pids:
        assert np.allclose(drawn.scores[:, drawn.product_ids.index(pid), 0], as_given[pid][key].score, atol=0.011)


def test_an_opinion_wins_over_a_disputed_stated_value() -> None:
    # Alpha's source scored its battery 7, so its disputed hours are never drawn: only noise around 7
    rated = disputed(A, "battery_life", score=7.0, value=10.0, others=[(2.0, None)])
    drawn = _drawn(rubric(weights=BATTERY), [product(A), product(B)], [rated, _hours(B, 8.0)])
    alpha = drawn.scores[:, drawn.product_ids.index(A), 0]
    assert alpha.mean() == pytest.approx(7.0, abs=0.15)
    assert alpha.std() == pytest.approx(SPREAD * (1 - 0.5), rel=0.15)


def test_a_disputed_price_within_the_budget_on_both_sides_is_shown_and_simulated() -> None:
    rb = rubric(weights=TWO, budget_max=70000)
    values = [
        *_scored(A, 8.0, 7.0),
        disputed(A, "price_inr", value=60000, others=[(65000, "INR")]),
        *_scored(B, 6.0, 6.0),
        known(B, "price_inr", value=62000),
    ]
    result = decide(decide_input(rb, [product(A), product(B)], values))
    assert result.method == "monte_carlo"
    assert shown(result) == [A, B]
    assert result.filtered == []


# --- not ranked, errors, time -----------------------------------------------------------------------------------


def test_with_nothing_to_score_it_stays_weighted() -> None:
    result = decide(decide_input(rubric(weights=TWO), [product(A), product(B)], []))
    assert result.method == "weighted"
    assert all(r.chance_first is None and r.score is None for r in result.ranked)


def test_an_error_in_the_simulations_leaves_the_weighted_result(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("secret shopper text")

    monkeypatch.setattr(decision, "simulate", broken)
    values = [*_scored(A, 9.0, 8.0), *_scored(B, 6.0, 6.0)]
    with caplog.at_level(logging.WARNING):
        result = decide(decide_input(rubric(weights=TWO), [product(A), product(B)], values))
    assert result.method == "weighted"
    assert shown(result) == [A, B]
    assert all(r.chance_first is None for r in result.ranked)
    assert [r.message for r in caplog.records if r.name == "app.decide.decision"] == [
        "decide: the simulations failed (RuntimeError); the plain weighted ranking stands"
    ]
    assert "secret shopper text" not in caplog.text


def test_past_the_deadline_the_simulations_stop() -> None:
    values = index_values([*_scored(A, 9.0, 8.0), *_scored(B, 6.0, 6.0)], {A, B})
    rb = rubric(weights=TWO)
    with pytest.raises(SimulationTimeout):
        simulate([product(A), product(B)], rb, values, directions_for(["battery_life"], "laptop"), 42, deadline=0.0)


def test_past_the_time_limit_the_weighted_result_stands(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    decide_limit = STAGE_TIME_LIMITS_S[StageName.DECIDE]
    assert 0 < TIME_LIMIT_S < decide_limit  # the simulations stop inside decide's own limit
    monkeypatch.setattr(decision, "TIME_LIMIT_S", -1.0)  # a deadline already past
    values = [*_scored(A, 9.0, 8.0), *_scored(B, 6.0, 6.0)]
    with caplog.at_level(logging.WARNING):
        result = decide(decide_input(rubric(weights=TWO), [product(A), product(B)], values))
    assert result.method == "weighted"
    assert shown(result) == [A, B]
    assert [r.message for r in caplog.records if r.name == "app.decide.decision"] == [
        "decide: the simulations failed (SimulationTimeout); the plain weighted ranking stands"
    ]


def test_a_negative_seed_still_simulates_the_same_way_each_time() -> None:
    values = [*_scored(A, 7.0, 6.0), *_scored(B, 6.5, 6.8)]
    inp = decide_input(rubric(weights=TWO), [product(A), product(B)], values)
    negative = DecideInput.model_validate({**inp.model_dump(), "seed": -1})
    assert decide(negative).method == "monte_carlo"
    assert decide(negative).model_dump() == decide(negative).model_dump()


def _many(keys: list[str]) -> Rubric:
    weights = [(key, float(1 + i % 5)) for i, key in enumerate(keys)]
    shares = normalize(dict(weights))
    return Rubric.model_validate(
        {
            **rubric(weights=TWO).model_dump(),
            "weights": [
                WeightedCriterion(key=k, weight=w, normalized=shares[k], origin="default").model_dump()
                for k, w in weights
            ],
        }
    )


def _timed(inp: DecideInput) -> tuple[DecideResult, float]:
    started = time.perf_counter()
    result = decide(inp)
    return result, time.perf_counter() - started


def test_200_products_with_10_criteria_take_under_2_seconds() -> None:
    keys = [f"criterion_{i:02d}" for i in range(10)]
    pids = [f"prd_item_{n:03d}" for n in range(200)]
    rng = np.random.default_rng(0)
    values = [known(pid, key, score=round(float(rng.uniform(1, 9)), 1)) for pid in pids for key in keys]
    result, elapsed = _timed(decide_input(_many(keys), [product(pid) for pid in pids], values))
    assert result.method == "monte_carlo"
    assert len(result.ranked) == 50  # DecideResult holds at most 50
    assert elapsed < 2.0, f"{elapsed:.2f} s"


def test_200_products_with_disputed_stated_values_take_under_2_seconds() -> None:
    # the costliest case: every product's battery life and weight are stated and disputed, so each simulation's
    # comparison is scored again, alongside 8 criteria with opinion scores
    keys = ["battery_life", "size_and_weight", *(f"criterion_{i:02d}" for i in range(8))]
    pids = [f"prd_item_{n:03d}" for n in range(200)]
    rng = np.random.default_rng(1)
    values = []
    for pid in pids:
        hours, kg = round(float(rng.uniform(4, 14)), 1), round(float(rng.uniform(1.0, 2.5)), 2)
        values.append(disputed(pid, "battery_life", value=hours, others=[(hours - 2, None), (hours + 1, None)]))
        values.append(disputed(pid, "size_and_weight", value=kg, others=[(kg + 0.3, None)]))
        values += [known(pid, key, score=round(float(rng.uniform(1, 9)), 1)) for key in keys[2:]]
    result, elapsed = _timed(decide_input(_many(keys), [product(pid) for pid in pids], values))
    assert result.method == "monte_carlo"
    assert len(result.ranked) == 50
    assert elapsed < 2.0, f"{elapsed:.2f} s"
