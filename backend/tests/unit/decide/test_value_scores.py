"""Stated facts scored by comparing the shown products (US-18, P-20, DR-09, DR-10). Plain code, never a model.

A Known or Disputed value with no opinion score, on a criterion with a direction, is scored from the shown products'
values on that criterion: linear from the lowest to the highest onto 1 to 9, the direction deciding which end is 9.
Every product and request here is made up.
"""

from __future__ import annotations

import time
from typing import Any

from app.contracts import Contribution, DecideResult, HardRule, ResolvedValue, StageContext, ValueState
from app.decide import WeightedDecideStage, decide
from app.decide.directions import direction_of
from app.decide.scoring import rank
from tests.unit.decide.build import RULE_RAM, decide_input, disputed, filtered, known, product, rubric

A, B, C = "prd_alpha_one", "prd_bravo_two", "prd_charlie_three"
CTX = StageContext(search_id="srch_values01")


def _run(weights: list[tuple[str, float]], values: list[ResolvedValue], pids: tuple[str, ...] = (A, B, C)) -> Any:
    return decide(decide_input(rubric(weights=weights), [product(p) for p in pids], values))


def _of(result: DecideResult, pid: str, key: str) -> Contribution:
    ranked = next(r for r in result.ranked if r.product_id == pid)
    return next(c for c in ranked.contributions if c.criterion_key == key)


def _scores(result: DecideResult, key: str) -> dict[str, float | None]:
    return {r.product_id: _of(result, r.product_id, key).score for r in result.ranked}


def test_a_higher_is_better_number_scores_1_to_9_across_the_shown_products() -> None:
    values = [known(A, "battery_life", value="10 hours"), known(B, "battery_life", value="15 hours")]
    values += [known(C, "battery_life", value="20 hours")]
    result = _run([("battery_life", 5.0)], values)
    assert _scores(result, "battery_life") == {A: 1.0, B: 5.0, C: 9.0}
    assert [r.product_id for r in result.ranked] == [C, B, A]
    assert {_of(result, p, "battery_life").state for p in (A, B, C)} == {ValueState.KNOWN}


def test_a_lower_is_better_number_scores_the_other_way() -> None:
    values = [
        known(A, "weight", value="1.2 kg"),
        known(B, "weight", value="1.6 kg"),
        known(C, "weight", value="2.0 kg"),
    ]
    assert _scores(_run([("weight", 5.0)], values), "weight") == {A: 9.0, B: 5.0, C: 1.0}


def test_mixed_units_are_compared_after_conversion() -> None:
    values = [known(A, "weight", value="1200 g"), known(B, "weight", value="1.6 kg")]
    assert _scores(_run([("weight", 5.0)], values, (A, B)), "weight") == {A: 9.0, B: 1.0}  # 1.2 kg is the lighter
    days = [known(A, "battery_life", value="14 days"), known(B, "battery_life", value="36 hours")]
    assert _scores(_run([("battery_life", 5.0)], days, (A, B)), "battery_life") == {A: 9.0, B: 1.0}


def test_a_value_whose_unit_cant_be_converted_is_left_out() -> None:
    values = [
        known(A, "weight", value="1.2 kg"),
        known(B, "weight", value="2 kg"),
        known(C, "weight", value="5 bricks"),
    ]
    result = _run([("weight", 5.0)], values)
    assert _scores(result, "weight") == {A: 9.0, B: 1.0, C: 5.0}
    assert _of(result, C, "weight").state is ValueState.ESTIMATED  # the peers' average, not a comparison


def test_equal_values_all_score_5() -> None:
    values = [known(A, "battery_life", value="10 h"), known(B, "battery_life", value="600 min")]
    assert _scores(_run([("battery_life", 5.0)], values, (A, B)), "battery_life") == {A: 5.0, B: 5.0}


def test_one_product_with_a_value_gets_no_derived_score() -> None:
    values = [known(A, "battery_life", value="10 hours")]
    values += [known(A, "keyboard_comfort", score=6.0), known(B, "keyboard_comfort", score=7.0)]
    result = _run([("battery_life", 5.0), ("keyboard_comfort", 5.0)], values, (A, B))
    assert _of(result, A, "battery_life").score is None  # left out for every product: nothing to compare with
    assert _of(result, A, "battery_life").state is ValueState.UNKNOWN


def test_yes_and_no_score_9_and_1_as_the_direction_says() -> None:
    values = [known(A, "water_resistance", value=True), known(B, "water_resistance", value="no")]
    assert _scores(_run([("water_resistance", 5.0)], values, (A, B)), "water_resistance") == {A: 9.0, B: 1.0}
    rb = rubric(weights=[("has_fan_noise", 5.0)])
    lower = rank(
        [product(A), product(B)],
        rb,
        {
            (A, "has_fan_noise"): known(A, "has_fan_noise", value="yes"),
            (B, "has_fan_noise"): known(B, "has_fan_noise", value=False),
        },
        directions={"has_fan_noise": "lower"},
    )
    scores = {r.product_id: r.contributions[0].score for r in lower}
    assert scores == {A: 1.0, B: 9.0}


def test_an_opinion_score_beats_a_derived_one() -> None:
    values = [known(A, "battery_life", value="20 hours", score=3.0), known(B, "battery_life", value="10 hours")]
    result = _run([("battery_life", 5.0)], values, (A, B))
    assert _of(result, A, "battery_life").score == 3.0  # the source's opinion, though its hours are the most
    assert _of(result, B, "battery_life").score == 1.0  # compared with A's 20 hours


def test_text_values_get_no_derived_score() -> None:
    values = [known(A, "battery_life", value="all day"), known(B, "battery_life", value="long lasting")]
    values += [known(A, "keyboard_comfort", score=6.0), known(B, "keyboard_comfort", score=7.0)]
    result = _run([("battery_life", 5.0), ("keyboard_comfort", 5.0)], values, (A, B))
    assert _scores(result, "battery_life") == {A: None, B: None}


def test_a_criterion_with_no_direction_gets_no_derived_score() -> None:
    values = [known(A, "display_quality", value="1000 nits"), known(B, "display_quality", value="500 nits")]
    values += [known(A, "keyboard_comfort", score=6.0), known(B, "keyboard_comfort", score=7.0)]
    result = _run([("display_quality", 5.0), ("keyboard_comfort", 5.0)], values, (A, B))
    assert direction_of("display_quality", "laptop") is None
    assert _scores(result, "display_quality") == {A: None, B: None}


def test_a_disputed_value_uses_its_winning_side() -> None:
    # the winning side, 20 hours, beats B's 10: the other side, 8 hours, would lose to it
    values = [disputed(A, "battery_life", value="20 hours", others=[("8 hours", None)])]
    values += [known(B, "battery_life", value="10 hours")]
    result = _run([("battery_life", 5.0)], values, (A, B))
    assert _of(result, A, "battery_life").score == 9.0
    assert _of(result, A, "battery_life").state is ValueState.DISPUTED


def test_a_derived_score_counts_as_evidence_in_coverage() -> None:
    values = [known(A, "battery_life", value="10 hours"), known(B, "battery_life", value="20 hours")]
    result = _run([("battery_life", 5.0)], values, (A, B))
    assert {r.product_id: r.coverage for r in result.ranked} == {A: 1.0, B: 1.0}
    assert all(r.score is not None for r in result.ranked)


def test_a_product_with_neither_is_estimated_from_its_peers() -> None:
    values = [known(A, "battery_life", value="10 hours"), known(B, "battery_life", value="20 hours")]
    result = _run([("battery_life", 5.0)], values)
    third = _of(result, C, "battery_life")
    assert (third.score, third.state) == (5.0, ValueState.ESTIMATED)  # the average of 1 and 9, never zero (DR-09)


def test_the_most_mentioned_product_does_not_win_when_its_values_are_worst() -> None:
    popular = product(A, recommender_count=50, weighted_mentions=50.0)
    quiet = product(B, recommender_count=1, weighted_mentions=1.0)
    values = [known(A, "weight", value="2.5 kg"), known(B, "weight", value="1.2 kg")]
    result = decide(decide_input(rubric(weights=[("weight", 5.0)]), [popular, quiet], values))
    assert [r.product_id for r in result.ranked] == [B, A]  # mentions never set the score (DR-10)


def test_the_same_inputs_give_the_same_output() -> None:
    values = [known(A, "weight", value="1.2 kg"), known(B, "weight", value="1.6 kg"), known(C, "weight", value="2 kg")]
    values += [known(A, "battery_life", value="10 hours"), known(C, "battery_life", value="15 hours")]
    first = _run([("weight", 5.0), ("battery_life", 3.0)], values)
    again = _run([("weight", 5.0), ("battery_life", 3.0)], list(reversed(values)))
    assert first == again


def test_200_products_are_ranked_in_under_2_seconds() -> None:
    pids = [f"prd_item_{n:03d}" for n in range(200)]
    values: list[ResolvedValue] = []
    for n, pid in enumerate(pids):
        values += [known(pid, "weight", value=f"{1 + n / 100:.2f} kg"), known(pid, "battery_life", value=f"{n % 24} h")]
        values += [known(pid, "memory_and_storage", value=f"{8 * (1 + n % 4)} GB")]
    inp = decide_input(
        rubric(weights=[("weight", 5.0), ("battery_life", 5.0), ("memory_and_storage", 5.0)]),
        [product(p) for p in pids],
        values,
    )
    started = time.perf_counter()
    result = decide(inp)
    assert time.perf_counter() - started < 2.0
    assert len(result.ranked) == 50 and all(r.score is not None for r in result.ranked)


def test_a_generated_criterion_takes_its_stand_ins_direction() -> None:
    assert direction_of("battery", "laptop") == "higher"  # public_synonyms: battery -> battery_life
    assert direction_of("portability", "laptop") == "lower"  # -> weight, the first stand-in the laptop set has
    assert direction_of("display", "laptop") is None  # -> display_quality, an opinion
    assert direction_of("made_up_key", "laptop") is None
    values = [known(A, "portability", value="1.2 kg"), known(B, "portability", value="2.4 kg")]
    assert _scores(_run([("portability", 5.0)], values, (A, B)), "portability") == {A: 9.0, B: 1.0}


def test_the_note_names_the_criteria_scored_by_comparing_values() -> None:
    values = [known(A, "weight", value="1.2 kg"), known(B, "weight", value="2 kg")]
    values += [known(A, "battery_life", value="10 hours"), known(B, "battery_life", value="12 hours")]
    values += [known(A, "keyboard_comfort", score=6.0), known(B, "keyboard_comfort", score=8.0)]
    stage = WeightedDecideStage()
    inp = decide_input(
        rubric(weights=[("keyboard_comfort", 5.0), ("battery_life", 5.0), ("weight", 5.0)]),
        [product(A), product(B)],
        values,
    )
    stage.run(inp, CTX)
    note = stage.coverage_note(CTX.search_id)
    assert note == "We scored battery life and weight by comparing what the sources say about each product."
    only_opinions = [v for v in values if v.criterion_key == "keyboard_comfort"]
    stage.run(decide_input(rubric(weights=[("keyboard_comfort", 5.0)]), [product(A), product(B)], only_opinions), CTX)
    assert stage.coverage_note(CTX.search_id) is None  # nothing compared: no note


# --- time units: days and weeks are exact; months and years only rank (Om, 15 Nov) ---------------------------------


def _rule(value: float, unit: str, words: str, key: str = "battery_life") -> HardRule:
    return HardRule(attribute=key, operator=">=", value=value, unit=unit, source_text=words)


def _filtered_on(rule: HardRule, value: str) -> DecideResult:
    values = [known(A, rule.attribute, value=value), known(A, "keyboard_comfort", score=6.0)]
    return decide(decide_input(rubric(weights=[("keyboard_comfort", 5.0)], rules=[rule]), [product(A)], values))


def test_a_month_never_passes_a_rule_in_days() -> None:
    """A month has no exact length, so it never passes or fails a hard rule: the product is can't tell, as before."""
    result = _filtered_on(_rule(30, "days", "battery at least 30 days"), "1 month")
    assert filtered(result, A)[0] == "cant_tell"
    a_year = _filtered_on(_rule(300, "days", "battery at least 300 days"), "1 year")
    assert filtered(a_year, A)[0] == "cant_tell"


def test_weeks_and_days_compare_exactly_in_a_hard_rule() -> None:
    assert [r.product_id for r in _filtered_on(_rule(14, "days", "battery at least 14 days"), "2 weeks").ranked] == [A]
    assert filtered(_filtered_on(_rule(15, "days", "battery at least 15 days"), "2 weeks"), A)[0] == "filtered_out"


def test_years_against_years_in_a_rule_compare_as_before() -> None:
    rule = _rule(2, "years", "at least 2 years of warranty", key="warranty_length")
    assert [r.product_id for r in _filtered_on(rule, "3 years").ranked] == [A]


def test_months_and_years_compare_by_their_average_length_for_derived_scores() -> None:
    values = [known(A, "software_updates", value="2 years"), known(B, "software_updates", value="36 months")]
    assert _scores(_run([("software_updates", 5.0)], values, (A, B)), "software_updates") == {A: 1.0, B: 9.0}


# --- after the review ------------------------------------------------------------------------------------------------


def test_a_year_and_365_days_score_as_the_same() -> None:
    """Values within 1% are equal: a year's average length is 8,766 hours and 365 days are 8,760."""
    values = [known(A, "warranty_length", value="1 year"), known(B, "warranty_length", value="365 days")]
    assert _scores(_run([("warranty_length", 5.0)], values, (A, B)), "warranty_length") == {A: 5.0, B: 5.0}


def test_365_days_never_pass_a_rule_in_years() -> None:
    rule = _rule(1, "year", "at least 1 year of warranty", key="warranty_length")
    assert filtered(_filtered_on(rule, "365 days"), A)[0] == "cant_tell"  # a year is not exact, so never compared


def test_a_unitless_number_never_pushes_out_values_with_a_unit() -> None:
    """Two bare numbers and two in hours tie: the hours compare, and the bare numbers are estimated."""
    d = "prd_delta_four"
    values = [known(A, "battery_life", value=10.0), known(B, "battery_life", value=12.0)]
    values += [known(C, "battery_life", value="8 hours"), known(d, "battery_life", value="14 hours")]
    result = _run([("battery_life", 5.0)], values, (A, B, C, d))
    assert (_of(result, C, "battery_life").score, _of(result, d, "battery_life").score) == (1.0, 9.0)
    assert _of(result, A, "battery_life").state is ValueState.ESTIMATED


def test_verify_reads_days_and_hours_on_one_scale() -> None:
    from app.verify.combine import measure

    days, hours = measure("14 days", None, "battery_life"), measure("336 hours", None, "battery_life")
    assert (days.dimension, days.number) == (hours.dimension, hours.number) == ("time", 336.0)
    assert measure("1 month", None, "warranty_length").dimension == "unit:month"  # as before: compared only with itself


def test_a_reason_writes_days_and_weeks_in_plain_words() -> None:
    from app.decide.rules import _value_text

    assert (_value_text(14, "battery_life_days"), _value_text(1, "battery_life_days")) == ("14 days", "1 day")
    assert _value_text(2, "warranty_years") == "2"  # years are not a unit a rule converts: written as before


def test_a_stand_in_need_not_be_the_first_target() -> None:
    assert direction_of("storage", "smartphone") == "higher"  # memory_and_storage isn't in the set: storage_space
    assert direction_of("portability", "printer") == "lower"  # no weight in the computing set: size_and_weight
    assert direction_of("ram", "laptop") is None  # memory_and_storage has no direction: RAM and SSD share GB


def test_the_note_joins_the_left_out_and_compared_notes() -> None:
    values = [known(A, "weight", value="1.2 kg"), known(B, "weight", value="2 kg")]
    stage = WeightedDecideStage()
    inp = decide_input(rubric(weights=[("weight", 5.0), ("keyboard_comfort", 5.0)]), [product(A), product(B)], values)
    stage.run(inp, CTX)
    assert stage.coverage_note(CTX.search_id) == (
        "We found no information on keyboard comfort for any product, so it was left out of the scores. "
        "We scored weight by comparing what the sources say about each product."
    )


def test_derived_scores_never_let_a_rule_breaker_through() -> None:
    """The lightest laptop has too little RAM: it is filtered out, whatever its compared score would be (DR-01)."""
    values = [known(A, "weight", value="1.0 kg"), known(A, "ram_gb", value="8 GB")]
    values += [known(B, "weight", value="2.0 kg"), known(B, "ram_gb", value="16 GB")]
    values += [known(C, "weight", value="1.5 kg"), known(C, "ram_gb", value="32 GB")]
    result = decide(
        decide_input(rubric(weights=[("weight", 5.0)], rules=[RULE_RAM]), [product(p) for p in (A, B, C)], values)
    )
    assert filtered(result, A)[0] == "filtered_out"
    assert [r.product_id for r in result.ranked] == [C, B]
    assert _scores(result, "weight") == {C: 9.0, B: 1.0}  # compared among the shown products only
