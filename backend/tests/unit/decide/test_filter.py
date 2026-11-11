"""US-17: products that break the shopper's rules are removed in code before ranking (DR-01, DR-08).

Every product and request here is made up.
"""

from __future__ import annotations

import ast
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from app.contracts import Exclusion, HardRule, Variant
from app.decide import decide
from app.decide.units import rupees
from tests.unit.decide.build import (
    RULE_RAM,
    decide_input,
    disputed,
    estimated,
    filtered,
    group_of,
    known,
    product,
    rubric,
    shown,
    unknown,
)

DECIDE_DIR = Path(__file__).resolve().parents[3] / "app" / "decide"
A, B, C, D = "prd_alpha_one", "prd_bravo_two", "prd_charlie_3", "prd_delta_four"


def _priced(pid: str, price: Any) -> list[Any]:
    return [known(pid, "battery_life", score=7.0), known(pid, "price_inr", value=price)]


# --- the budget (DECIDED 1) ----------------------------------------------------------------------------------------


def test_a_price_above_the_budget_is_filtered_out_with_the_reason() -> None:
    result = decide(decide_input(rubric(budget_max=80000), [product(A)], _priced(A, 94990)))
    assert shown(result) == []
    assert filtered(result, A) == ("filtered_out", ["₹94,990 is above your ₹80,000 budget"], "price_inr")


def test_a_price_equal_to_the_budget_passes() -> None:
    values = _priced(A, 80000) + _priced(B, 20000) + _priced(C, "₹80,000")
    result = decide(
        decide_input(rubric(budget_max=80000, budget_min=20000), [product(A), product(B), product(C)], values)
    )
    assert sorted(shown(result)) == sorted([A, B, C])


def test_a_price_below_the_minimum_is_filtered_out() -> None:
    result = decide(decide_input(rubric(budget_min=20000), [product(A)], _priced(A, 9990)))
    assert filtered(result, A) == ("filtered_out", ["₹9,990 is below your ₹20,000 minimum"], "price_inr")


@pytest.mark.parametrize(
    "price_value",
    [
        pytest.param(None, id="no-value-at-all"),
        pytest.param(unknown(A, "price_inr"), id="unknown"),
        pytest.param(estimated(A, "price_inr", 6.0), id="estimated-is-not-a-price"),
        pytest.param(known(A, "price_inr", value="call for price"), id="unreadable-text"),
        pytest.param(known(A, "price_inr", value=True), id="not-a-number"),
        pytest.param(known(A, "price_inr", score=7.0), id="a-score-but-no-price"),
    ],
)
def test_an_unknown_price_is_cant_tell(price_value: Any) -> None:
    values = [known(A, "battery_life", score=7.0)] + ([price_value] if price_value is not None else [])
    result = decide(decide_input(rubric(budget_max=70000), [product(A)], values))
    assert filtered(result, A) == ("cant_tell", ["We couldn't find its price"], "price_inr")


@pytest.mark.parametrize(
    ("written", "reason"),
    [
        ("Rs84,990", "₹84,990 is above your ₹70,000 budget"),
        ("INR84990", "₹84,990 is above your ₹70,000 budget"),
        ("MRP84,990", "₹84,990 is above your ₹70,000 budget"),
        ("Rs1,24,990", "₹1,24,990 is above your ₹70,000 budget"),
        ("Rs. 84,990/-", "₹84,990 is above your ₹70,000 budget"),
        ("₹ 84,990.00", "₹84,990 is above your ₹70,000 budget"),
    ],
)
def test_a_price_written_against_its_currency_is_read_whole(written: str, reason: str) -> None:
    """Never the 990 after the comma: an over-budget price must not slip under the budget (DR-01)."""
    result = decide(decide_input(rubric(budget_max=70000), [product(A)], _priced(A, written)))
    assert filtered(result, A) == ("filtered_out", [reason], "price_inr")


@pytest.mark.parametrize("price", ["$899", "USD 899"])
def test_a_price_in_another_currency_is_cant_tell(price: str) -> None:
    result = decide(decide_input(rubric(budget_max=70000), [product(A)], _priced(A, price)))
    assert filtered(result, A) == ("cant_tell", ["We couldn't find its price"], "price_inr")


@pytest.mark.parametrize(
    ("key", "value", "group"),
    [
        ("price_inr", float("inf"), "cant_tell"),
        ("price_inr", float("nan"), "cant_tell"),
        ("price_inr", 1e29, "filtered_out"),
        ("ram_gb", float("inf"), "cant_tell"),
        ("ram_gb", float("nan"), "cant_tell"),
    ],
)
def test_a_value_that_is_not_a_usable_number_never_breaks_the_stage(key: str, value: float, group: str) -> None:
    """Build rule 9: one odd value must not throw the whole decision away to the fallback."""
    values = [known(A, "battery_life", score=7.0), known(A, "price_inr", value=60000), known(A, "ram_gb", value=16)]
    values = [known(A, key, value=value) if v.criterion_key == key else v for v in values]
    result = decide(decide_input(rubric(budget_max=70000, rules=[RULE_RAM]), [product(A)], values))
    assert group_of(result, A) == group


@pytest.mark.parametrize("price", [68990, 94990])
def test_a_disputed_price_is_cant_tell_and_names_the_price_we_have(price: int) -> None:
    values = [known(A, "battery_life", score=7.0), disputed(A, "price_inr", value=price)]
    result = decide(decide_input(rubric(budget_max=70000), [product(A)], values))
    assert filtered(result, A) == (
        "cant_tell",
        [f"Sources disagree on its price: one says {rupees(price)}"],
        "price_inr",
    )


def test_a_budget_in_another_currency_is_cant_tell() -> None:
    result = decide(decide_input(rubric(budget_max=900, currency="USD"), [product(A)], _priced(A, 64990)))
    group, reasons, attribute = filtered(result, A)
    assert (group, attribute) == ("cant_tell", "price_inr")
    assert "USD" in reasons[0]


def test_without_a_budget_the_price_is_never_checked() -> None:
    result = decide(decide_input(rubric(), [product(A)], [known(A, "battery_life", score=7.0)]))
    assert shown(result) == [A]


@pytest.mark.parametrize(
    ("amount", "text"),
    [
        (0, "₹0"),
        (999, "₹999"),
        (1000, "₹1,000"),
        (94990, "₹94,990"),
        (124990, "₹1,24,990"),
        (1234567, "₹12,34,567"),
        (80000.5, "₹80,000.50"),
        (99999.999, "₹1,00,000"),
    ],
)
def test_rupees_use_indian_digit_grouping(amount: float, text: str) -> None:
    assert rupees(amount) == text


# --- hard rules (DECIDED 2) ----------------------------------------------------------------------------------------


def _with_rule(rule: HardRule, value: Any, attribute: str | None = None) -> str:
    """The group of one product whose rule attribute has `value` (None: no value at all)."""
    key = attribute or rule.attribute
    values = [known(A, "battery_life", score=7.0)]
    if value is not None:
        values.append(value if not isinstance(value, _Raw) else known(A, key, value=value.value))
    return group_of(decide(decide_input(rubric(rules=[rule]), [product(A)], values)), A)


class _Raw:
    def __init__(self, value: Any) -> None:
        self.value = value


def test_a_failing_hard_rule_filters_out_with_its_words() -> None:
    values = [known(A, "battery_life", score=7.0), known(A, "ram_gb", value=8)]
    result = decide(decide_input(rubric(rules=[RULE_RAM]), [product(A)], values))
    assert filtered(result, A) == ("filtered_out", ['It fails "at least 16 GB RAM": it has 8 GB'], "ram_gb")


@pytest.mark.parametrize(
    "ram",
    [
        pytest.param(None, id="no-value-at-all"),
        pytest.param(unknown(A, "ram_gb"), id="unknown"),
        pytest.param(estimated(A, "ram_gb", 5.0), id="estimated"),
        pytest.param(known(A, "ram_gb", score=8.0), id="a-score-but-no-value"),
    ],
)
def test_an_unknown_hard_rule_value_is_cant_tell_naming_the_attribute(ram: Any) -> None:
    values = [known(A, "battery_life", score=7.0)] + ([ram] if ram is not None else [])
    result = decide(decide_input(rubric(rules=[RULE_RAM]), [product(A)], values))
    assert filtered(result, A) == (
        "cant_tell",
        ["We couldn't find its RAM, so we can't check \"at least 16 GB RAM\""],
        "ram_gb",
    )


@pytest.mark.parametrize("ram", ["16 GB + 8 GB", "plenty", "16 Hz"])
def test_a_value_that_cannot_be_compared_is_cant_tell(ram: str) -> None:
    values = [known(A, "battery_life", score=7.0), known(A, "ram_gb", value=ram)]
    result = decide(decide_input(rubric(rules=[RULE_RAM]), [product(A)], values))
    group, reasons, attribute = filtered(result, A)
    assert (group, attribute) == ("cant_tell", "ram_gb")
    assert reasons == [f'We couldn\'t compare its RAM ("{ram}") with "at least 16 GB RAM"']


def test_a_disputed_hard_rule_value_is_cant_tell() -> None:
    values = [known(A, "battery_life", score=7.0), disputed(A, "ram_gb", value=16)]
    result = decide(decide_input(rubric(rules=[RULE_RAM]), [product(A)], values))
    assert filtered(result, A) == ("cant_tell", ["Sources disagree on its RAM: one says 16 GB"], "ram_gb")


@pytest.mark.parametrize("ram", [known(A, "ram_gb", value=8), unknown(A, "ram_gb"), None])
def test_a_switched_off_rule_does_not_filter(ram: Any) -> None:
    """DR-08: a rule the shopper switched to a preference no longer filters, whatever the value."""
    off = RULE_RAM.model_validate({**RULE_RAM.model_dump(), "active": False})
    values = [known(A, "battery_life", score=7.0)] + ([ram] if ram is not None else [])
    result = decide(decide_input(rubric(rules=[off]), [product(A)], values))
    assert shown(result) == [A]
    assert result.filtered == []


@pytest.mark.parametrize(
    ("attribute", "operator", "rule_value", "unit", "value", "expected"),
    [
        ("weight_g", "<=", 300, "g", "0.28 kg", "shown"),
        ("weight_g", "<=", 300, "g", "0.32 kg", "filtered_out"),
        ("weight_g", "<=", 300, "g", "10 oz", "shown"),
        ("weight_g", "<=", 300, "g", "2 lb", "filtered_out"),
        ("weight_kg", "<=", 1.5, "kg", "1400 g", "shown"),
        ("weight_kg", "<=", 1.5, "kg", "1.6 kg", "filtered_out"),
        ("storage_gb", ">=", 1, "TB", 1000, "shown"),
        ("storage_gb", ">=", 1, "TB", 512, "filtered_out"),
        ("storage_gb", ">=", 512, "GB", "1 TB", "shown"),
        ("screen_size_inch", ">=", 27, "inch", "68.6 cm", "shown"),
        ("screen_size_inch", ">=", 27, "inch", '27"', "shown"),
        ("screen_size_inch", ">=", 27, "inch", "60 cm", "filtered_out"),
        ("refresh_rate_hz", ">=", 144, "Hz", "0.165 kHz", "shown"),
        ("battery_life_h", ">=", 600, "min", "10 hours", "shown"),
        ("battery_mah", ">=", 5000, "mAh", "5 Ah", "shown"),
        ("ram_gb", ">=", "16 GB", None, 16, "shown"),
        ("ram_gb", ">=", 16, "GB", "16 Hz", "cant_tell"),
        ("ram_gb", ">=", 16, "furlongs", 16, "cant_tell"),
        ("cpu_cores", ">=", 8, None, 8, "shown"),
        ("cpu_cores", ">=", 8, None, 6, "filtered_out"),
        ("price_inr", "<=", 50000, "INR", "₹49,999", "shown"),
        ("weight", "<=", 1.5, "kg", 1.4, "shown"),  # a bare value takes the rule's known unit
        ("weight", "<=", 1.5, None, "1400 g", "cant_tell"),  # the rule says no unit: grams are not guessed for it
        ("weight", "<=", 1.5, "kg", "1400 g", "shown"),
        ("wifi_version", ">=", 6, None, "6E", "cant_tell"),  # "E" is not a unit to lend to the rule
    ],
)
def test_units_are_converted_before_comparing(
    attribute: str, operator: Any, rule_value: Any, unit: str | None, value: Any, expected: str
) -> None:
    rule = HardRule(attribute=attribute, operator=operator, value=rule_value, unit=unit, source_text="the rule")
    assert _with_rule(rule, _Raw(value)) == expected


@pytest.mark.parametrize(
    ("operator", "rule_value", "value", "expected"),
    [
        ("==", "ips", "IPS", "shown"),
        ("==", "ips", "va", "filtered_out"),
        ("!=", "va", "ips", "shown"),
        ("!=", "ips", "IPS", "filtered_out"),
        ("in", ["ips", "oled"], "OLED", "shown"),
        ("in", ["ips", "oled"], "va", "filtered_out"),
        ("not_in", ["tn"], "ips", "shown"),
        ("not_in", ["tn"], "TN", "filtered_out"),
        ("contains", "usb-c", "2x USB-C, 1x HDMI", "shown"),
        ("contains", "thunderbolt", "USB-C, HDMI", "filtered_out"),
        ("contains", ["usb-c", "hdmi"], "USB-C and HDMI", "shown"),
        ("contains", ["usb-c", "sd card"], "USB-C and HDMI", "filtered_out"),
        ("==", True, True, "shown"),
        ("==", True, False, "filtered_out"),
        ("==", True, "yes", "shown"),
        ("==", False, "No", "shown"),
        ("!=", True, False, "shown"),
        ("==", 16, 16.0, "shown"),
        ("!=", 16, 8, "shown"),
        ("==", 16, 8, "filtered_out"),
        ("in", ["16", "32"], 32, "shown"),
        ("in", ["16", "32"], 8, "filtered_out"),
        ("<=", 1.5, True, "cant_tell"),
        ("==", True, "maybe", "cant_tell"),
        (">=", 16, "ips", "cant_tell"),
        ("==", "Wi-Fi 6E", "Wi-Fi 6", "cant_tell"),  # never 6 == 6: the words around the number differ
        ("==", "Wi-Fi 6E", "wi-fi 6e", "shown"),
        ("==", "HDMI 2.1", "DisplayPort 2.1", "cant_tell"),
        ("==", "16 GB", "16 GB DDR5", "cant_tell"),
        ("==", "16 GB", "16GB", "shown"),
        ("in", ["16 GB", "32 GB"], "16 GB DDR5", "cant_tell"),
        ("in", ["16 GB", "32 GB"], "32000 MB", "shown"),  # decimal units, as makers label them
    ],
)
def test_every_operator_is_checked(operator: Any, rule_value: Any, value: Any, expected: str) -> None:
    rule = HardRule(attribute="spec", operator=operator, value=rule_value, source_text="the rule")
    assert _with_rule(rule, _Raw(value)) == expected


def test_a_fail_beats_a_cant_tell_and_keeps_every_fail_in_order() -> None:
    values = [known(A, "battery_life", score=7.0), known(A, "price_inr", value=94990), known(A, "ram_gb", value=8)]
    rb = rubric(budget_max=70000, rules=[RULE_RAM], exclusions=[Exclusion(kind="brand", value="Zenith")])
    result = decide(decide_input(rb, [product(A, "Zenith Flex 14")], values))
    group, reasons, attribute = filtered(result, A)
    assert group == "filtered_out"
    assert reasons == [
        "₹94,990 is above your ₹70,000 budget",
        'It fails "at least 16 GB RAM": it has 8 GB',
        "You ruled out Zenith",
    ]
    assert attribute == "price_inr"
    only_unknown_price = [known(B, "battery_life", score=7.0), known(B, "ram_gb", value=8)]
    result = decide(decide_input(rb, [product(B)], only_unknown_price))
    assert filtered(result, B)[:2] == ("filtered_out", ['It fails "at least 16 GB RAM": it has 8 GB'])


# --- exclusions (DECIDED 3) ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "brand", "expected"),
    [
        ("Mi Notebook 14", "Xiaomi", "filtered_out"),
        ("Mi Notebook 14", "XIAOMI", "filtered_out"),
        ("Xiaomi Redmi Book 15", None, "filtered_out"),
        ("xiaomi-Redmi Book 15", "Redmi", "filtered_out"),
        ("Redmi Book 15", "Redmi", "shown"),
        ("Book 15 for Xiaomi fans", None, "shown"),
        ("Xiaomiplus Book", None, "shown"),
    ],
)
def test_an_excluded_brand_is_filtered_out(name: str, brand: str | None, expected: str) -> None:
    rb = rubric(exclusions=[Exclusion(kind="brand", value="Xiaomi")])
    result = decide(decide_input(rb, [product(A, name, brand)], [known(A, "battery_life", score=7.0)]))
    assert group_of(result, A) == expected
    if expected == "filtered_out":
        assert filtered(result, A) == ("filtered_out", ["You ruled out Xiaomi"], "brand")


@pytest.mark.parametrize(
    ("name", "aliases", "excluded", "expected"),
    [
        ("Samsung Galaxy S23", [], "Galaxy S23", "filtered_out"),
        ("Samsung Galaxy S23 Ultra", [], "Galaxy S23", "filtered_out"),
        ("Samsung Galaxy S24", [], "Galaxy S23", "shown"),
        ("Google Pixel 8a", [], "Pixel 8", "shown"),
        ("Nova Book 14", ["Nova 14"], "nova 14", "filtered_out"),
        ("Nova Book 14", [], "nova 14", "shown"),
        ("S23 Galaxy Case", [], "Galaxy S23", "shown"),
    ],
)
def test_an_excluded_model_is_filtered_out_by_name_or_alias(
    name: str, aliases: list[str], excluded: str, expected: str
) -> None:
    rb = rubric(exclusions=[Exclusion(kind="model", value=excluded)])
    item = product(A, name, aliases=aliases)
    result = decide(decide_input(rb, [item], [known(A, "battery_life", score=7.0)]))
    assert group_of(result, A) == expected
    if expected == "filtered_out":
        assert filtered(result, A)[1] == [f"You ruled out {excluded}"]


def _variants(*displays: str) -> list[Variant]:
    return [
        Variant(id=f"var_alpha_{i}", label=f"Version {i}", attributes={"display": d}) for i, d in enumerate(displays)
    ]


@pytest.mark.parametrize(
    ("variants", "extra", "expected"),
    [
        pytest.param([], [known(A, "touchscreen", value=True)], "filtered_out", id="known-true-key"),
        pytest.param([], [known(A, "has_touchscreen", value=True)], "filtered_out", id="known-true-has-key"),
        pytest.param([], [known(A, "is_touchscreen", value="yes")], "filtered_out", id="known-yes-is-key"),
        pytest.param([], [known(A, "has_touchscreen", value=False)], "shown", id="known-false"),
        pytest.param([], [unknown(A, "has_touchscreen")], "shown", id="unknown-is-not-excluded"),
        pytest.param([], [disputed(A, "has_touchscreen", value=True)], "shown", id="disputed-is-not-known"),
        pytest.param(_variants("touchscreen", "Touchscreen"), [], "filtered_out", id="every-variant-has-it"),
        pytest.param(_variants("touchscreen", "matte"), [], "shown", id="one-variant-without-it"),
        pytest.param(
            [Variant(id="var_alpha_t", label="Touch", attributes={"touchscreen": "yes"})],
            [],
            "filtered_out",
            id="variant-key-with-yes",
        ),
        pytest.param(
            [Variant(id="var_alpha_t", label="Touch", attributes={"touchscreen": "no"})],
            [],
            "shown",
            id="variant-key-with-no",
        ),
    ],
)
def test_an_excluded_feature_is_filtered_out(variants: list[Variant], extra: list[Any], expected: str) -> None:
    rb = rubric(exclusions=[Exclusion(kind="feature", value="touchscreen")])
    values = [known(A, "battery_life", score=7.0), *extra]
    result = decide(decide_input(rb, [product(A, variants=variants)], values))
    assert group_of(result, A) == expected
    if expected == "filtered_out":
        assert filtered(result, A) == ("filtered_out", ["You ruled out touchscreen"], "feature")


def test_a_feature_of_two_words_matches_its_key() -> None:
    rb = rubric(exclusions=[Exclusion(kind="feature", value="Curved display")])
    values = [known(A, "battery_life", score=7.0), known(A, "has_curved_display", value=True)]
    result = decide(decide_input(rb, [product(A)], values))
    assert filtered(result, A)[1] == ["You ruled out Curved display"]


def test_a_seller_exclusion_never_filters_here() -> None:
    """Decide has no seller data: a seller is ruled out among the offers (US-24)."""
    rb = rubric(exclusions=[Exclusion(kind="seller", value="Nova")])
    result = decide(decide_input(rb, [product(A, "Nova Book 14", "Nova")], [known(A, "battery_life", score=7.0)]))
    assert shown(result) == [A]


# --- when nothing passes (DECIDED 4) -------------------------------------------------------------------------------


def _hint(rb: Any, items: Sequence[tuple[str, str, str | None, Any, Any]]) -> str | None:
    """items: (pid, name, brand, price, ram)."""
    values: list[Any] = []
    for pid, _, _, price, ram in items:
        values.append(known(pid, "battery_life", score=7.0))
        values.append(known(pid, "price_inr", value=price) if price is not None else unknown(pid, "price_inr"))
        values.append(known(pid, "ram_gb", value=ram))
    result = decide(decide_input(rb, [product(pid, name, brand) for pid, name, brand, _, _ in items], values))
    assert result.ranked == []
    return result.relaxation_hint


ZENITH = Exclusion(kind="brand", value="Zenith")


def test_when_nothing_passes_the_hint_names_the_rule_that_lets_most_through() -> None:
    rb = rubric(budget_max=70000, rules=[RULE_RAM], exclusions=[ZENITH])
    by_budget = [(A, "Alpha One", None, 80000, 16), (B, "Bravo Two", None, None, 16), (C, "Zenith 3", None, 60000, 16)]
    assert _hint(rb, by_budget) == "Removing your ₹70,000 budget would let 2 products through."
    by_rule = [(A, "Alpha One", None, 60000, 8), (B, "Bravo Two", None, 60000, 8), (C, "Charlie 3", None, 80000, 16)]
    assert _hint(rb, by_rule) == 'Dropping your rule "at least 16 GB RAM" would let 2 products through.'
    by_brand = [(A, "Alpha One", "Zenith", 60000, 16), (B, "Bravo", "zenith", 60000, 16), (C, "Charlie", None, 1, 8)]
    assert _hint(rb, by_brand) == "Allowing Zenith again would let 2 products through."
    one = [(A, "Alpha One", None, 80000, 16)]
    assert _hint(rb, one) == "Removing your ₹70,000 budget would let 1 product through."


@pytest.mark.parametrize(
    ("budget_min", "budget_max", "words"),
    [
        (5000, 15000, "Removing your ₹5,000 to ₹15,000 budget"),
        (20000, None, "Removing your ₹20,000 minimum"),
    ],
)
def test_the_hint_names_a_range_or_a_minimum(budget_min: float, budget_max: float | None, words: str) -> None:
    rb = rubric(budget_min=budget_min, budget_max=budget_max)
    hint = _hint(rb, [(A, "Alpha One", None, 1000, 16)])
    assert hint == f"{words} would let 1 product through."


def test_a_tie_in_the_hint_goes_to_the_budget_then_rules_then_exclusions() -> None:
    rb = rubric(budget_max=70000, rules=[RULE_RAM], exclusions=[ZENITH])
    tie = [(A, "Alpha", None, 80000, 16), (B, "Bravo", None, 60000, 8), (C, "Zenith C", None, 60000, 16)]
    assert _hint(rb, tie) == "Removing your ₹70,000 budget would let 1 product through."
    rule_or_brand = [(B, "Bravo", None, 60000, 8), (C, "Zenith C", None, 60000, 16)]
    assert _hint(rb, rule_or_brand) == 'Dropping your rule "at least 16 GB RAM" would let 1 product through.'


def test_when_no_single_rule_helps_the_hint_says_so() -> None:
    rb = rubric(budget_max=70000, rules=[RULE_RAM], exclusions=[ZENITH])
    two_each = [(A, "Alpha", None, 80000, 8), (B, "Zenith B", None, None, 16)]
    assert _hint(rb, two_each) == (
        "No single change to your rules would let a product through. Try relaxing more than one."
    )


def test_the_hint_fits_its_300_characters() -> None:
    long_rule = HardRule(attribute="ram_gb", operator=">=", value=16, unit="GB", source_text="x" * 200)
    hint = _hint(rubric(rules=[long_rule]), [(A, "Alpha", None, 1, 8)])
    assert hint is not None and len(hint) <= 300


def test_with_no_products_there_is_nothing_to_hint() -> None:
    result = decide(decide_input(rubric(budget_max=70000), [], []))
    assert (result.ranked, result.filtered, result.relaxation_hint) == ([], [], None)


def test_values_for_other_products_are_ignored() -> None:
    values = [known(A, "battery_life", score=7.0), known(D, "price_inr", value=1)]
    result = decide(decide_input(rubric(budget_max=70000), [product(A)], values))
    assert filtered(result, A)[:2] == ("cant_tell", ["We couldn't find its price"])


# --- in code, never by the LLM (DR-01) -----------------------------------------------------------------------------


def test_decide_never_imports_the_llm_client() -> None:
    """Build rule 2: the LLM never filters, scores or ranks. Nothing in app/decide/ can even reach it."""
    allowed = ("app.contracts", "app.decide", "__future__")
    sdks = ("groq", "openai", "google", "mistralai", "cerebras", "httpx", "requests")
    files = sorted(DECIDE_DIR.glob("*.py"))
    assert files
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            elif isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            else:
                continue
            for name in names:
                assert not name.startswith("app.") or name.startswith(allowed), f"{path.name} imports {name}"
                assert name.split(".")[0] not in sdks, f"{path.name} imports {name}"
