"""A disputed value is checked on every side verify kept (S3-10b, US-17, DR-08).

All sides pass: shown. All sides fail: filtered out, naming the values. Some pass and some fail: can't tell, naming
them, so the shopper can look it up. A disputed value with no other side kept (a result from before 1.8.0) stays
can't tell, as before. Every product and request here is made up.
"""

from __future__ import annotations

from typing import Any

from app.contracts import (
    AlternativeValue,
    DecideResult,
    HardRule,
    Product,
    ResolvedValue,
    StageContext,
    ValueState,
)
from app.decide import decide
from app.verify import TrustVerifyStage
from tests.unit.decide.build import (
    RULE_RAM,
    claim_id,
    decide_input,
    disputed,
    filtered,
    group_of,
    known,
    product,
    rubric,
)
from tests.unit.verify.build import claim, match, source, verify_input

A = "prd_alpha_one"
SCORE = known(A, "battery_life", score=7.0)


def _price(value: Any, *others: tuple[Any, str | None]) -> list[ResolvedValue]:
    return [SCORE, disputed(A, "price_inr", value=value, others=others)]


def _budget(*values: ResolvedValue) -> DecideResult:
    return decide(decide_input(rubric(budget_max=70000), [product(A)], values))


# --- the price -------------------------------------------------------------------------------------------------------


def test_a_price_disputed_across_the_budget_is_cant_tell_with_both_prices() -> None:
    result = _budget(*_price("68990 INR", (74990.0, "INR")))
    assert filtered(result, A) == ("cant_tell", ["Sources disagree: ₹68,990 or ₹74,990"], "price_inr")


def test_both_prices_over_the_budget_is_filtered_out_naming_both() -> None:
    result = _budget(*_price("78990 INR", (74990.0, "INR")))
    group, reasons, attribute = filtered(result, A)
    assert (group, attribute) == ("filtered_out", "price_inr")
    assert reasons == ["Sources give ₹78,990 or ₹74,990, and both are above your ₹70,000 budget"]


def test_both_prices_under_the_budget_is_shown() -> None:
    assert group_of(_budget(*_price("64990 INR", (66990.0, "INR"))), A) == "shown"


def test_three_prices_all_over_name_them_all() -> None:
    result = _budget(*_price(78990.0, (74990.0, "INR"), (81990.0, None)))
    _, reasons, _ = filtered(result, A)
    assert reasons == ["Sources give ₹78,990, ₹74,990 or ₹81,990, and all are above your ₹70,000 budget"]


def test_prices_below_a_minimum_name_the_minimum() -> None:
    values = _price(18990.0, (17990.0, "INR"))
    result = decide(decide_input(rubric(budget_min=20000), [product(A)], values))
    _, reasons, _ = filtered(result, A)
    assert reasons == ["Sources give ₹18,990 or ₹17,990, and both are below your ₹20,000 minimum"]


def test_prices_on_both_sides_of_a_range_fit_none_of_it() -> None:
    values = _price(18990.0, (94990.0, "INR"))
    result = decide(decide_input(rubric(budget_min=20000, budget_max=70000), [product(A)], values))
    _, reasons, _ = filtered(result, A)
    assert reasons == ["Sources give ₹18,990 or ₹94,990, and none fits your ₹20,000 to ₹70,000 budget"]


def test_a_side_we_cannot_read_as_a_price_is_cant_tell() -> None:
    result = _budget(*_price(64990.0, ("about sixty thousand", None)))
    group, reasons, _ = filtered(result, A)
    assert group == "cant_tell"
    assert reasons == ["Sources disagree: ₹64,990 or about sixty thousand"]


def test_a_disputed_price_with_no_other_side_kept_is_cant_tell_as_before() -> None:
    result = _budget(*_price(68990.0))
    assert filtered(result, A) == (
        "cant_tell",
        ["Sources disagree on its price: one says ₹68,990"],
        "price_inr",
    )


# --- a hard rule -----------------------------------------------------------------------------------------------------


def _ram(value: Any, *others: tuple[Any, str | None]) -> DecideResult:
    values = [SCORE, disputed(A, "ram_gb", value=value, others=others)]
    return decide(decide_input(rubric(rules=[RULE_RAM]), [product(A)], values))


def test_ram_disputed_across_the_rule_is_cant_tell_with_both_values() -> None:
    assert filtered(_ram("16 GB", (8.0, "GB")), A) == ("cant_tell", ["Sources disagree: 16 GB or 8 GB"], "ram_gb")


def test_both_ram_values_under_the_rule_is_filtered_out_naming_both() -> None:
    assert filtered(_ram("8 GB", (12.0, "GB")), A) == (
        "filtered_out",
        ['It fails "at least 16 GB RAM": sources give 8 GB or 12 GB'],
        "ram_gb",
    )


def test_both_ram_values_over_the_rule_is_shown() -> None:
    assert group_of(_ram("16 GB", (32.0, "GB")), A) == "shown"


def test_a_bare_number_side_takes_the_attributes_unit() -> None:
    assert group_of(_ram(16.0, (32.0, None)), A) == "shown"
    _, reasons, _ = filtered(_ram(16.0, (8.0, None)), A)
    assert reasons == ["Sources disagree: 16 GB or 8 GB"]


def test_a_disputed_rule_value_with_no_other_side_kept_is_cant_tell_as_before() -> None:
    assert filtered(_ram(16.0), A) == ("cant_tell", ["Sources disagree on its RAM: one says 16 GB"], "ram_gb")


# --- a dispute in scores only (S3-10b review) ------------------------------------------------------------------------


def _score_dispute(key: str, value: Any) -> ResolvedValue:
    """Sources agree on the value, but their scores split: verify keeps only a score on the other side."""
    return ResolvedValue(
        product_id=A,
        criterion_key=key,
        state=ValueState.DISPUTED,
        score=6.5,
        value=value,
        confidence=0.6,
        supporting_claim_ids=[claim_id(A, key, "a")],
        conflicting_claim_ids=[claim_id(A, key, "b")],
        alternatives=[AlternativeValue(score=4.0, claim_ids=[claim_id(A, key, "b")])],
    )


def test_a_score_only_dispute_checks_the_agreed_value_as_known() -> None:
    battery = HardRule(attribute="battery_life", operator=">=", value=8, unit="h", source_text="at least 8 hours")
    shown_one = decide(
        decide_input(rubric(rules=[battery]), [product(A)], [_score_dispute("battery_life", "10 hours")])
    )
    assert group_of(shown_one, A) == "shown"
    held = decide(decide_input(rubric(rules=[battery]), [product(A)], [_score_dispute("battery_life", "6 hours")]))
    assert filtered(held, A) == ("filtered_out", ['It fails "at least 8 hours": it has 6 hours'], "battery_life")


def test_a_score_only_dispute_on_the_price_checks_the_agreed_price() -> None:
    values = [SCORE, _score_dispute("price_inr", "64990 INR")]
    assert group_of(_budget(*values), A) == "shown"
    over = _budget(SCORE, _score_dispute("price_inr", "74990 INR"))
    assert filtered(over, A) == ("filtered_out", ["₹74,990 is above your ₹70,000 budget"], "price_inr")


def test_agreed_hours_with_split_review_scores_pass_the_rule_end_to_end() -> None:
    """Everyone says 10 hours; the reviews score it 9 and 4. The value is agreed, so "at least 8 hours" passes."""
    a = source("https://a.example/nova")
    b = source("https://b.example/nova")
    claims = [
        claim(a, "Nova Book 14", "battery_life", value=10, unit="hours"),
        claim(b, "Nova Book 14", "battery_life", value=10, unit="hours"),
        claim(a, "Nova Book 14", "battery_life", score=9.0, n=1),
        claim(b, "Nova Book 14", "battery_life", score=4.0, n=1),
    ]
    stage = TrustVerifyStage()
    stage.use_filter_keys("srch_disputed01", ["battery_life"])
    verified = stage.run(
        verify_input(match("Nova Book 14"), claims, [a, b], ["battery_life"]), StageContext(search_id="srch_disputed01")
    )
    battery = verified.values[0]
    assert battery.state is ValueState.DISPUTED and all(alt.value is None for alt in battery.alternatives)
    rule = HardRule(attribute="battery_life", operator=">=", value=8, unit="h", source_text="at least 8 hours")
    nova = Product(id=battery.product_id, name="Nova Book 14")
    result = decide(decide_input(rubric(rules=[rule]), [nova], verified.values))
    assert group_of(result, nova.id) == "shown"
