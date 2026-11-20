"""What the filter read, and how a rule passed (1.10.0, S5-0; US-23, P-42, S4-24).

- `DecideResult.checked_values`: for every product decided, shown, can't tell or filtered out, its price and each hard
  rule's attribute (every rule, on or off), as the filter read them. So the browser can filter again with no server
  call: the price in rupees, a size in the rule's unit, every side of a disputed value, every version of a RAM or
  storage size.
- `RankedProduct.rule_notes`: how a RAM or storage must-have passed through one of a shown product's versions (S4-24
  had no field for it). Empty when no version decided a rule.

The shopping requests here are made up. "vivo T5x" is the S3-19 case's own product, with the values that check saw;
every other product is made up.
"""

from __future__ import annotations

from app.contracts import AlternativeValue, CheckedValue, DecideResult, HardRule, ResolvedValue, ValueState
from app.decide import decide
from tests.unit.decide.build import claim_id, decide_input, disputed, estimated, known, product, rubric, unknown

A, B, C = "prd_vivo_t5x", "prd_nova_phone_5", "prd_kestrel_phone"
AT_LEAST_8 = HardRule(attribute="ram_gb", operator=">=", value=8, unit="GB", source_text="at least 8 GB RAM")
OIS = HardRule(attribute="has_ois", operator="==", value=True, source_text="with OIS")
OFF = HardRule(attribute="weight_kg", operator="<=", value=0.2, unit="kg", source_text="under 200 g", active=False)
NAMED = ", going by the name a source gave it, not a spec page"


def _read(result: DecideResult, pid: str, attribute: str) -> CheckedValue:
    return next(v for v in result.checked_values if v.product_id == pid and v.attribute == attribute)


def _decide(
    *values: ResolvedValue,
    names: dict[str, str],
    rules: list[HardRule] | None = None,
    budget: float | None = 70000,
) -> DecideResult:
    """The products named, each scored on battery life, with these values: by default "at least 8 GB RAM" and a
    ₹70,000 budget."""
    products = [product(pid, name) for pid, name in names.items()]
    scores = [known(pid, "battery_life", score=7.0) for pid in names]
    rb = rubric(rules=rules if rules is not None else [AT_LEAST_8], budget_max=budget)
    return decide(decide_input(rb, products, [*scores, *values]))


# --- rule notes ----------------------------------------------------------------------------------------------------


def test_a_rule_passed_through_one_version_is_noted_on_the_ranked_product() -> None:
    result = _decide(known(A, "ram_gb", value=6), known(A, "price_inr", value=18999), names={A: "vivo T5x 8GB RAM"})
    (ranked,) = result.ranked
    assert ranked.rule_notes == [f'It meets "at least 8 GB RAM" in its 8 GB version{NAMED}']


def test_rule_notes_are_empty_when_no_version_decided_a_rule() -> None:
    result = _decide(known(B, "ram_gb", value="16 GB"), known(B, "price_inr", value=24999), names={B: "Nova Phone 5"})
    (ranked,) = result.ranked
    assert ranked.rule_notes == []


def test_rule_notes_are_empty_with_no_rule_at_all() -> None:
    result = _decide(known(A, "price_inr", value=18999), rules=[], names={A: "vivo T5x 8GB RAM"})
    assert [r.rule_notes for r in result.ranked] == [[]]


def test_a_product_held_back_has_no_rule_notes_to_carry() -> None:
    result = _decide(known(A, "ram_gb", value=6), names={A: "vivo T5x 8GB RAM"})  # no price: can't tell
    assert result.ranked == []
    assert [f.product_id for f in result.filtered] == [A]


# --- what the filter read ------------------------------------------------------------------------------------------


def test_every_product_decided_has_its_price_and_each_rules_attribute() -> None:
    result = _decide(
        known(A, "ram_gb", value=6),
        known(A, "price_inr", value=18999),
        known(B, "ram_gb", value="4 GB"),
        known(B, "price_inr", value=24999),
        unknown(C, "ram_gb"),
        rules=[AT_LEAST_8, OIS, OFF],
        names={A: "vivo T5x 8GB RAM", B: "Nova Phone 5", C: "Kestrel Phone"},
    )
    assert {r.product_id for r in result.ranked} | {f.product_id for f in result.filtered} == {A, B, C}
    pairs = {(v.product_id, v.attribute) for v in result.checked_values}
    assert pairs == {(pid, key) for pid in (A, B, C) for key in ("price_inr", "ram_gb", "has_ois", "weight_kg")}


def test_the_price_is_read_in_rupees() -> None:
    result = _decide(known(A, "price_inr", value="₹18,999"), names={A: "vivo T5x 8GB RAM"})
    price = _read(result, A, "price_inr")
    assert (price.state, price.values, price.unit, price.any_version) == (ValueState.KNOWN, [18999.0], "INR", False)


def test_a_disputed_price_gives_every_side() -> None:
    result = _decide(disputed(B, "price_inr", value=68990, others=[(74990, "INR")]), names={B: "Nova Phone 5"})
    price = _read(result, B, "price_inr")
    assert (price.state, price.values, price.unit) == (ValueState.DISPUTED, [68990.0, 74990.0], "INR")


def test_a_price_the_filter_could_not_read_has_no_values() -> None:
    result = _decide(estimated(B, "price_inr", score=5.0), names={B: "Nova Phone 5"})
    assert _read(result, B, "price_inr").model_dump() == {
        "product_id": B,
        "attribute": "price_inr",
        "state": ValueState.ESTIMATED,
        "values": [],
        "unit": None,
        "any_version": False,
        "from_names": 0,
    }
    none = _decide(names={B: "Nova Phone 5"})
    assert (_read(none, B, "price_inr").state, _read(none, B, "price_inr").values) == (ValueState.UNKNOWN, [])


def test_the_price_is_read_with_no_budget_too() -> None:
    result = _decide(known(B, "price_inr", value=24999), budget=None, rules=[], names={B: "Nova Phone 5"})
    assert _read(result, B, "price_inr").values == [24999.0]


def test_a_size_is_read_in_the_rules_unit() -> None:
    result = _decide(known(B, "ram_gb", value="16 GB"), known(B, "price_inr", value=24999), names={B: "Nova Phone 5"})
    ram = _read(result, B, "ram_gb")
    assert (ram.values, ram.unit, ram.any_version) == ([16.0], "gb", False)


def test_every_version_is_read_when_the_names_state_one() -> None:
    result = _decide(known(A, "ram_gb", value=6), known(A, "price_inr", value=18999), names={A: "vivo T5x 8GB RAM"})
    ram = _read(result, A, "ram_gb")
    assert (ram.state, ram.values, ram.unit, ram.any_version) == (ValueState.KNOWN, [6.0, 8.0], "gb", True)
    assert ram.from_names == 1  # the claims' 6 GB first, then the 8 GB the name states


def test_a_version_a_name_states_is_read_when_no_claim_gives_one() -> None:
    result = _decide(unknown(A, "ram_gb"), known(A, "price_inr", value=18999), names={A: "vivo T5x 8GB RAM"})
    ram = _read(result, A, "ram_gb")
    assert (ram.state, ram.values, ram.any_version, ram.from_names) == (ValueState.UNKNOWN, [8.0], True, 1)


def test_a_yes_or_no_value_is_read_as_it_is() -> None:
    result = _decide(
        known(B, "has_ois", value=True), known(B, "price_inr", value=24999), rules=[OIS], names={B: "Nova Phone 5"}
    )
    ois = _read(result, B, "has_ois")
    assert (ois.values, ois.unit) == ([True], None)


def test_a_rule_switched_off_is_read_too_so_it_can_be_switched_on_again() -> None:
    result = _decide(
        known(B, "weight_kg", value="190 g"), known(B, "price_inr", value=24999), rules=[OFF], names={B: "Nova Phone 5"}
    )
    weight = _read(result, B, "weight_kg")
    assert (weight.values, weight.unit) == ([0.19], "kg")
    assert [r.product_id for r in result.ranked] == [B]  # off, so it filtered nothing


def test_a_disputed_size_gives_every_side() -> None:
    result = _decide(
        disputed(B, "ram_gb", value=8, others=[(12, "GB")]),
        known(B, "price_inr", value=24999),
        names={B: "Nova Phone 5"},
    )
    ram = _read(result, B, "ram_gb")
    assert (ram.state, ram.values, ram.any_version) == (ValueState.DISPUTED, [8.0, 12.0], False)


# --- read as decide compares (the review of S5-0) ------------------------------------------------------------------


def _shown(result: DecideResult, pid: str) -> bool:
    return any(r.product_id == pid for r in result.ranked)


def test_a_size_with_words_around_it_is_read_as_the_number_an_at_least_rule_compares() -> None:
    result = _decide(
        known(B, "ram_gb", value="8 GB RAM"), known(B, "price_inr", value=24999), names={B: "Nova Phone 5"}
    )
    assert _shown(result, B)  # decide compared 8 GB with "at least 8 GB RAM"
    assert (_read(result, B, "ram_gb").values, _read(result, B, "ram_gb").unit) == ([8.0], "gb")


def test_an_equal_rule_reads_text_with_words_as_text() -> None:
    wifi = HardRule(attribute="wifi", operator="==", value="Wi-Fi 6", source_text="Wi-Fi 6")
    result = _decide(
        known(B, "wifi", value="Wi-Fi 6"), known(B, "price_inr", value=24999), rules=[wifi], names={B: "Nova Phone 5"}
    )
    read = _read(result, B, "wifi")
    assert (read.values, read.unit) == (["Wi-Fi 6"], None)


def test_a_bare_number_takes_the_attributes_unit_as_decide_does() -> None:
    grams = HardRule(attribute="weight_kg", operator="<=", value=1500, unit="g", source_text="under 1.5 kg")
    result = _decide(
        known(B, "weight_kg", value=1.8), known(B, "price_inr", value=24999), rules=[grams], names={B: "Nova Phone 5"}
    )
    assert not _shown(result, B)  # 1.8 kg is 1,800 g, over 1,500 g
    read = _read(result, B, "weight_kg")
    assert (read.values, read.unit) == ([1800.0], "g")


def test_a_value_disputed_in_scores_only_is_read_as_the_agreed_one_it_is_checked_as() -> None:
    light = HardRule(attribute="weight_kg", operator="<=", value=1.5, unit="kg", source_text="under 1.5 kg")
    scores_only = ResolvedValue(
        product_id=B,
        criterion_key="weight_kg",
        state=ValueState.DISPUTED,
        value=1.4,
        score=7.0,
        confidence=0.5,
        supporting_claim_ids=[claim_id(B, "a")],
        conflicting_claim_ids=[claim_id(B, "b")],
        alternatives=[AlternativeValue(value=None, score=3.0, claim_ids=[claim_id(B, "b")])],
    )
    result = _decide(scores_only, known(B, "price_inr", value=24999), rules=[light], names={B: "Nova Phone 5"})
    assert _shown(result, B)
    read = _read(result, B, "weight_kg")
    assert (read.state, read.values) == (ValueState.KNOWN, [1.4])


def test_a_one_sided_dispute_beside_a_named_version_says_which_came_from_the_name() -> None:
    sixteen = HardRule(attribute="ram_gb", operator=">=", value=16, unit="GB", source_text="at least 16 GB RAM")
    result = _decide(
        disputed(B, "ram_gb", value=8), known(B, "price_inr", value=24999), rules=[sixteen],
        names={B: "Nova Phone 5 4GB RAM"},
    )  # fmt: skip
    assert [f.group for f in result.filtered] == ["cant_tell"]  # the one side kept can't settle it
    read = _read(result, B, "ram_gb")
    assert (read.state, read.values, read.any_version, read.from_names) == (ValueState.DISPUTED, [8.0, 4.0], True, 1)


def test_a_rule_on_the_price_itself_reads_as_the_price() -> None:
    cheap = HardRule(attribute="price_inr", operator="<=", value=30000, source_text="under 30k")
    result = _decide(known(B, "price_inr", value=24999), rules=[cheap], names={B: "Nova Phone 5"})
    assert [v.attribute for v in result.checked_values if v.product_id == B] == ["price_inr"]


def test_one_version_the_claims_and_the_name_agree_on_is_noted_as_s4_24_reasons() -> None:
    result = _decide(known(A, "ram_gb", value=8), known(A, "price_inr", value=18999), names={A: "vivo T5x 8GB RAM"})
    (ranked,) = result.ranked
    assert ranked.rule_notes == ['It meets "at least 8 GB RAM" in its 8 GB version']


def test_a_version_only_a_name_states_is_noted() -> None:
    result = _decide(unknown(A, "ram_gb"), known(A, "price_inr", value=18999), names={A: "vivo T5x 8GB RAM"})
    (ranked,) = result.ranked
    assert ranked.rule_notes == [f'It meets "at least 8 GB RAM" in its 8 GB version{NAMED}']
