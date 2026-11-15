"""Decide never reads a price smaller than it is (US-17, DR-01).

A price written "₹25k" or "Rs 1.2 lakh" is ₹25,000 or ₹1,20,000, never 25 or 1.2. Text that is not a selling price (a
discount, an EMI, a range, an estimate) is no price at all, so the product is "can't tell", never shown. Outside
money, every value reads as it did before: "4K" stays 4 with the unit "k". Every amount here is made up.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.contracts import Budget, Money
from app.decide import decide
from app.decide.rules import check_budget
from app.decide.units import Quantity, quantity, rupee_price
from app.intake.budget import parse_budget
from app.verify.combine import measure
from tests.unit.decide.build import decide_input, disputed, filtered, known, product, rubric

A = "prd_alpha_one"
UNDER_50K = Budget(max=Money(amount=50000, currency="INR"))

# Money, with a scale word: its exact amount in rupees (DECIDED 1).
SCALED = [
    ("₹25k", 25_000),
    ("₹25K", 25_000),
    ("Rs 1.2 lakh", 120_000),
    ("Rs. 1.5 lakhs", 150_000),
    ("INR 2 lac", 200_000),
    ("₹1.2L", 120_000),
    ("₹2 crore", 20_000_000),
    ("₹1.5 cr", 15_000_000),
    ("MRP 25k", 25_000),
    ("₹25 thousand", 25_000),
    ("₹25 thousands", 25_000),
    ("₹25 hazaar", 25_000),
    ("₹1.5 crs", 15_000_000),
    ("₹1.15 lakh", 115_000),  # exactly: never 114999.99999999999
    ("₹2.3 lakh", 230_000),
    ("25k INR", 25_000),
    ("Rs 26k", 26_000),
]
# Under the price's own key, with no other unit: the same reading with no money mark.
BARE = [("25k", 25_000), ("1.2 lakh", 120_000), ("1.2 L", 120_000), ("2 cr", 20_000_000), ("1.15 lakh", 115_000)]
# What read correctly before still does.
AS_BEFORE: list[tuple[object, int]] = [
    (24999, 24_999),
    (24999.0, 24_999),
    ("24,999", 24_999),
    ("₹24,999", 24_999),
    ("Rs.24,999/-", 24_999),
    ("Rs 24,999", 24_999),
    ("24,999 INR", 24_999),
    ("MRP 29,999", 29_999),
    ("₹29,999 M.R.P.", 29_999),
    ("24999 rupees", 24_999),
    ("Price - ₹24,999", 24_999),  # a dash between words and the amount is no minus
    ("Amazon – ₹24,999", 24_999),
    ("Offer price ₹24,999", 24_999),
    ("Discounted price ₹24,999", 24_999),  # what the shopper pays
    ("₹24,999 only", 24_999),
    ("1,20,000", 120_000),
    ("₹1,24,990", 124_990),
]
# Not a selling price, written as money: the reader gives no number at all, so verify keeps it as text and decide
# finds no price (DECIDED 2, 3).
MONEY_NOT_A_PRICE = [
    "₹24,999 off",
    "Flat ₹2,000 discount",
    "Save ₹2,000",
    "₹2,000 savings",
    "₹1,500 cashback",
    "₹2,000 less",
    "-₹2,000",
    "- Rs 2,000",
    "EMI ₹2,999/month",
    "₹2,999 per month",
    "₹2,999 / month",
    "₹999/mo",
    "₹1,999 monthly",
    "₹9,999 per year",
    "₹9,999/yr",
    "₹20,000-25,000",
    "₹20k-₹25k",
    "around ₹25k",
    "approx. ₹25,000",
    "~₹25k",
    "under ₹25,000",
    "up to ₹25k",
    "expected ₹25,000",
    "₹2 million",
    "₹2m",
    "$25k",
    "25k INR off",
    # an amount per period
    "₹2,999 p.m.",
    "₹2,999 pm",
    "₹2,999/m",
    "₹2,999 a month",
    "₹2,999 per-month",
    "₹2,999 per mo",
    "₹2,999/mth",
    "₹9,999 yearly",
    "₹9,999 p.a.",
    "₹9,999/annum",
    "₹99/day",
    "₹499 per week",
    # an offer, or money taken off
    "Apply ₹2,000 coupon",
    "₹5,000 exchange bonus",
    "₹2,000 rebate",
    "₹2,000 price drop",
    "With bank offer ₹22,999",
    "Effective price ₹21,999",
    "—₹2,000",
    # a bound with one end, which may be well below what the shopper pays
    "₹24,999 onwards",
    "From ₹24,999",
    "Starting at ₹24,999",
    "₹25k+",
    "₹24,999 + GST",
    "₹24,999 or more",
    # a size word we don't scale
    "₹12 hundred",
    "₹2 mil",
    "₹2 grand",
]
# Under the price's key, also no price: no money mark, another unit or currency, or not a number.
OTHER_NOT_A_PRICE: list[object] = ["20 to 25k", "25k off", "EMI 2,999", "120k mAh", "24,999 onwards", "ask the shop"]
OTHER_NOT_A_PRICE += [-24999, 0, True, None, float("inf"), "₹9" + "9" * 400 + " crore"]
OTHER_NOT_A_PRICE += ["₹-2,000"]  # the minus parts the mark from the number: read as before, refused as money off
# Another currency, wherever it is written: never rupees.
OTHER_NOT_A_PRICE += ["$299", "299 $", "25k $", "299 (USD)", "USD price: 299", "€999", "£999", "US$ 999", "AED 999"]

# Outside money, exactly what f2cdadc read (DECIDED 1: "never apply this outside money").
OUTSIDE_MONEY: list[tuple[str, Quantity]] = [
    ("4K", Quantity(4.0, "k", True)),
    ("8K", Quantity(8.0, "k", True)),
    ("120k mAh", Quantity(120.0, "k", False)),
    ("16 GB", Quantity(16.0, "gb", True)),
    ("2.5 kg", Quantity(2.5, "kg", True)),
    ("Wi-Fi 6", Quantity(6.0, None, False)),
    ("1.2 L", Quantity(1.2, "l", True)),
    ("25k", Quantity(25.0, "k", True)),
    ("1.2 lakh", Quantity(1.2, "lakh", True)),
    ("up to 10 hours", Quantity(10.0, "h", False)),
    ("6E", Quantity(6.0, "e", True)),
    # a money word that is not next to the number leaves the text as it was read before
    ("RS-232 port", Quantity(232.0, "port", False)),
    ("Price in INR: 25k", Quantity(25.0, "k", False)),
    ("rupee 5 coin set", Quantity(5.0, "coin", False)),
    ("2 hrs", Quantity(2.0, "h", True)),
    ("5 bucks", Quantity(5.0, "bucks", True)),
]


@pytest.mark.parametrize(("text", "rupees"), SCALED)
def test_money_with_a_scale_word_reads_in_full(text: str, rupees: int) -> None:
    assert quantity(text) is not None
    assert (quantity(text).number, quantity(text).unit) == (rupees, "inr")  # type: ignore[union-attr]
    assert rupee_price(text) == rupees


@pytest.mark.parametrize(("value", "rupees"), BARE + AS_BEFORE)
def test_a_bare_scaled_amount_under_price_inr_reads_in_full(value: object, rupees: int) -> None:
    assert rupee_price(value) == rupees


@pytest.mark.parametrize("value", MONEY_NOT_A_PRICE + OTHER_NOT_A_PRICE)
def test_text_that_is_not_a_selling_price_is_unknown(value: object) -> None:
    assert rupee_price(value) is None


@pytest.mark.parametrize("text", MONEY_NOT_A_PRICE)
def test_money_text_that_is_not_a_selling_price_has_no_number_at_all(text: str) -> None:
    """With a money mark, the reader itself gives nothing: verify then keeps it as text, never as an amount."""
    assert quantity(text) is None


@pytest.mark.parametrize(("text", "before"), OUTSIDE_MONEY)
def test_amounts_outside_money_keep_todays_reading(text: str, before: Quantity) -> None:
    assert quantity(text) == before


def test_a_price_is_never_read_smaller_than_its_text() -> None:
    """Every money form with a known meaning reads as that amount or is Unknown: never less."""
    meant: list[tuple[Any, int]] = [*SCALED, *BARE, *AS_BEFORE, ("₹60k", 60_000), ("₹45k", 45_000)]
    for value, rupees in meant:
        read = rupee_price(value)
        assert read is None or read >= rupees, value


@pytest.mark.parametrize(
    ("price", "verdict", "reason"),
    [
        ("₹60k", "fail", "₹60,000 is above your ₹50,000 budget"),
        ("Rs 1.2 lakh", "fail", "₹1,20,000 is above your ₹50,000 budget"),
        ("₹45k", "pass", ""),
        ("45k", "pass", ""),
        ("₹24,999 off", "unknown", "We couldn't find its price"),
        ("EMI ₹2,999/month", "unknown", "We couldn't find its price"),
        ("₹20,000-25,000", "unknown", "We couldn't find its price"),
    ],
)
def test_the_budget_check_reads_scaled_prices_and_refuses_the_rest(price: str, verdict: str, reason: str) -> None:
    check = check_budget(UNDER_50K, known(A, "price_inr", value=price))
    assert (check.verdict, check.reason) == (verdict, reason)


def test_a_disputed_price_with_a_scaled_side_is_cant_tell_naming_both() -> None:
    values = [known(A, "battery_life", score=7.0), disputed(A, "price_inr", value="₹55k", others=[("₹48,999", None)])]
    result = decide(decide_input(rubric(budget_max=50000), [product(A)], values))
    assert filtered(result, A) == ("cant_tell", ["Sources disagree: ₹55,000 or ₹48,999"], "price_inr")


def test_a_discount_on_a_disputed_side_is_cant_tell_never_a_pass() -> None:
    values = [known(A, "battery_life", score=7.0), disputed(A, "price_inr", value="₹45k", others=[("₹5k off", None)])]
    result = decide(decide_input(rubric(budget_max=50000), [product(A)], values))
    group, reasons, _ = filtered(result, A)
    assert group == "cant_tell"
    assert reasons == ["Sources disagree: ₹45,000 or ₹5k off"]


def test_verify_reads_money_as_decide_does() -> None:
    """Verify reads values with the same reader, so a price it keeps is the one decide checks."""
    scaled = measure("₹25k", None, "price_inr")
    assert (scaled.raw, scaled.unit, scaled.dimension) == (25_000, "inr", "money_inr")
    assert measure("Rs 1.2 lakh", None, "price_inr").number == 120_000
    discount = measure("₹24,999 off", None, "price_inr")
    assert (discount.number, discount.text) == (None, "24 999 off")  # text, never an amount
    four_k = measure("4K", None, "resolution")
    assert (four_k.raw, four_k.unit) == (4.0, "k")  # outside money: as before


@pytest.mark.parametrize(
    ("words", "low", "high"),
    [
        ("phone under 25k", None, 25_000),
        ("laptop under 1.2 lakh", None, 120_000),
        ("earbuds between 2k and 3k", 2_000, 3_000),
        ("4K monitor under 30000", None, 30_000),
    ],
)
def test_the_shoppers_budget_words_read_as_before(words: str, low: int | None, high: int) -> None:
    """Intake reads the shopper's budget with its own reader (DECIDED 4): the same amounts as on f2cdadc."""
    budget = parse_budget(words)
    assert budget is not None and budget.max is not None
    assert (budget.min.amount if budget.min else None, budget.max.amount) == (low, high)
