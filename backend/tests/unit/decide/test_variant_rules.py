"""A RAM or storage must-have is checked against every known version of a product (US-09, S4-24; S3-19's R1).

S3-19 left "vivo T5x 8GB RAM" out of "at least 8 GB RAM": a review page's claim gave 6 GB, its other version's. A
version is a value the product's claims give, or one its own names state in words ("8GB RAM", "512GB SSD", "8+128GB";
DR-02). The rule passes when any version meets it, and the reason names that version; it fails only when every known
version fails. A value that comes only from a name is said to be from that name, never from a spec page. A bare
capacity in a name is a guess, never a statement. Other rules, the budget, and a product whose names state no version
are checked as before.

The shopping requests here are made up. "vivo T5x" is the S3-19 case's own product, with the values that check saw;
every other product is made up.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.contracts import HardRule, MatchInput, Product, ProductMention, ResolvedValue, Variant, examples
from app.decide import decide
from app.decide.rules import Check, check_rule, checks_for
from app.decide.variants import stated_values, variant_word
from app.entities import FuzzyScorer
from app.entities.clustering import match_names
from tests.unit.decide.build import (
    decide_input,
    disputed,
    filtered,
    group_of,
    known,
    product,
    rubric,
    shown,
    unknown,
)

A = "prd_vivo_t5x"
NAMED = ", going by the name a source gave it, not a spec page"

AT_LEAST_8 = HardRule(attribute="ram_gb", operator=">=", value=8, unit="GB", source_text="at least 8 GB RAM")
AT_MOST_8 = HardRule(attribute="ram_gb", operator="<=", value=8, unit="GB", source_text="at most 8 GB RAM")
STORAGE_512 = HardRule(
    attribute="storage_gb", operator=">=", value=512, unit="GB", source_text="at least 512 GB storage"
)
LIGHT = HardRule(attribute="weight_kg", operator="<=", value=1.5, unit="kg", source_text="under 1.5 kg")


def _phone(*names: str) -> Product:
    """A product going by these names: its own, then its aliases."""
    return product(A, names[0], aliases=list(names[1:]))


def _matched(name: str) -> Product:
    """The one product matching makes from a name a source gave."""
    mention = ProductMention(raw_name=name, is_product=True)
    (item,) = match_names(MatchInput(mentions=[mention]), FuzzyScorer()).products
    return item


def _check(rule: HardRule, item: Product, value: ResolvedValue | None) -> Check:
    values = {} if value is None else {(item.id, rule.attribute): value}
    (check,) = checks_for(item, None, [rule], [], values)
    return check


# --- the S3-19 case -----------------------------------------------------------------------------------------------


def test_the_vivo_case_passes_in_its_8_gb_version() -> None:
    check = _check(AT_LEAST_8, _phone("vivo T5x 8GB RAM"), known(A, "ram_gb", value=6))
    assert check.verdict == "pass"
    assert check.reason == f'It meets "at least 8 GB RAM" in its 8 GB version{NAMED}'
    assert check.attribute == "ram_gb"


def test_the_vivo_case_is_shown() -> None:
    values = [known(A, "battery_life", score=7.0), known(A, "ram_gb", value=6)]
    result = decide(decide_input(rubric(rules=[AT_LEAST_8]), [_phone("vivo T5x 8GB RAM")], values))
    assert shown(result) == [A]


def test_the_value_a_name_states_reaches_the_rule_through_matching() -> None:
    item = _matched("vivo T5x 8GB RAM")
    assert stated_values(item, "ram_gb") == ["8GB"]
    check = _check(AT_LEAST_8, item, known(item.id, "ram_gb", value=6))
    assert (check.verdict, check.reason) == ("pass", f'It meets "at least 8 GB RAM" in its 8 GB version{NAMED}')


def test_a_name_states_a_version_when_no_claim_gives_one() -> None:
    assert _check(AT_LEAST_8, _phone("vivo T5x 8GB RAM"), unknown(A, "ram_gb")).verdict == "pass"
    assert _check(AT_LEAST_8, _phone("vivo T5x 8GB RAM"), None).verdict == "pass"


# --- what a name states -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "ram", "storage"),
    [
        pytest.param("vivo T5x 8GB RAM", ["8GB"], [], id="ram-in-words"),
        pytest.param("Nova Book 14 16 GB RAM 512 GB SSD", ["16GB"], ["512GB"], id="both-in-words"),
        pytest.param("Nova Book 14 16GB-RAM 1TB SSD", ["16GB"], ["1TB"], id="hyphen-and-terabytes"),
        pytest.param("vivo T5x 8+256GB", ["8GB"], ["256GB"], id="ram-plus-storage"),
        pytest.param("Nova Phone 5 8GB + 128GB", ["8GB"], ["128GB"], id="ram-plus-storage-spelled"),
        pytest.param("Nova Book 14 (Core i5/ 16GB/ 512GB SSD/ 2GB Graph)", [], ["512GB"], id="bare-ram-is-a-guess"),
        pytest.param("Nova Phone 5 8GB", [], [], id="a-lone-capacity-is-a-guess"),
        pytest.param("Nova Phone 5 128GB/256GB", [], [], id="two-bare-capacities"),
        pytest.param("Nova Phone 5 128GB memory", [], [], id="memory-may-be-storage"),
        pytest.param("Nova Phone 5 8GB+8GB RAM", ["8GB"], [], id="virtual-ram-is-no-storage"),
        pytest.param("Nova Book 14 8GB LPDDR5 RAM", [], [], id="a-word-between"),
        pytest.param("Nova Book 14", [], [], id="none"),
    ],
)
def test_a_name_states_a_capacity_only_in_words(name: str, ram: list[str], storage: list[str]) -> None:
    item = product(A, name)
    assert stated_values(item, "ram_gb") == ram
    assert stated_values(item, "storage_gb") == storage


def test_each_storage_word_keeps_its_own_capacity() -> None:
    item = product(A, "Nova Book 14 512GB SSD 1TB HDD")
    assert stated_values(item, "ssd_gb") == ["512GB"]
    assert stated_values(item, "hdd_gb") == ["1TB"]
    assert stated_values(item, "storage_gb") == ["512GB", "1TB"]


def test_every_name_a_product_goes_by_counts() -> None:
    item = _phone("vivo T5x", "vivo T5x 6GB RAM", "vivo T5x 8GB RAM", "VIVO T5X 8GB RAM")
    assert stated_values(item, "ram_gb") == ["6GB", "8GB"]


def test_a_variant_label_alone_is_not_read() -> None:
    """Matching's variant labels hold its guesses ("16GB/ 512GB SSD/ 2GB Graph" gives 2 GB RAM): only names count."""
    guessed = Variant(id="var_nova_book_14_2gb", label="2 GB RAM / 512 GB", attributes={"ram": "2GB"})
    item = product(A, "Nova Book 14", variants=[guessed])
    assert stated_values(item, "ram_gb") == []


def test_no_example_product_states_its_ram_in_a_name() -> None:
    """The sample products' variants are made by hand: Vela Pro 14 keeps its "can't tell" for RAM."""
    for item in examples.match().products:
        assert stated_values(item, "ram_gb") == [], item.name


# --- every version fails ------------------------------------------------------------------------------------------


def test_two_versions_both_under_the_rule_fail_naming_both() -> None:
    item = _phone("vivo T5x 4GB RAM", "vivo T5x 6GB RAM")
    check = _check(AT_LEAST_8, item, unknown(A, "ram_gb"))
    assert check.verdict == "fail"
    assert check.reason == 'It fails "at least 8 GB RAM" in every version we found: 4 GB and 6 GB'
    values = [known(A, "battery_life", score=7.0)]
    result = decide(decide_input(rubric(rules=[AT_LEAST_8]), [item], values))
    assert filtered(result, A) == ("filtered_out", [check.reason], "ram_gb")


def test_a_claim_and_the_names_all_under_the_rule_fail_naming_each_once() -> None:
    item = _phone("vivo T5x 4GB RAM", "vivo T5x 6GB RAM")
    check = _check(AT_LEAST_8, item, known(A, "ram_gb", value=6))
    assert check.reason == 'It fails "at least 8 GB RAM" in every version we found: 4 GB and 6 GB'


def test_one_version_from_the_name_alone_fails_saying_so() -> None:
    check = _check(AT_LEAST_8, _phone("vivo T5x 6GB RAM"), unknown(A, "ram_gb"))
    assert (check.verdict, check.reason) == ("fail", f'It fails "at least 8 GB RAM": it has 6 GB{NAMED}')


# --- which version a pass names -----------------------------------------------------------------------------------


def test_an_at_most_rule_names_the_smallest_version_that_meets_it() -> None:
    item = _phone("vivo T5x 6GB RAM", "vivo T5x 16GB RAM", "vivo T5x 4GB RAM")
    check = _check(AT_MOST_8, item, None)
    assert (check.verdict, check.reason) == ("pass", f'It meets "at most 8 GB RAM" in its 4 GB version{NAMED}')


def test_an_at_least_rule_names_the_largest_version_that_meets_it() -> None:
    item = _phone("vivo T5x 8GB RAM", "vivo T5x 16GB RAM", "vivo T5x 4GB RAM")
    check = _check(AT_LEAST_8, item, None)
    assert check.reason == f'It meets "at least 8 GB RAM" in its 16 GB version{NAMED}'


def test_an_exact_rule_names_the_version_that_equals_it() -> None:
    exactly = HardRule(attribute="ram_gb", operator="==", value=8, unit="GB", source_text="exactly 8 GB RAM")
    check = _check(exactly, _phone("vivo T5x 6GB RAM", "vivo T5x 8GB RAM"), known(A, "ram_gb", value=6))
    assert (check.verdict, check.reason) == ("pass", f'It meets "exactly 8 GB RAM" in its 8 GB version{NAMED}')


def test_a_version_a_claim_gives_is_named_without_the_name_note() -> None:
    check = _check(AT_LEAST_8, _phone("vivo T5x 6GB RAM"), known(A, "ram_gb", value=12))
    assert check.reason == 'It meets "at least 8 GB RAM" in its 12 GB version'


def test_a_value_both_claimed_and_named_counts_as_claimed() -> None:
    check = _check(AT_LEAST_8, _phone("vivo T5x 8GB RAM"), known(A, "ram_gb", value="8 GB"))
    assert check.reason == 'It meets "at least 8 GB RAM" in its 8 GB version'


def test_an_attribute_with_no_unit_takes_the_rules_and_lists_a_version_once() -> None:
    ram = HardRule(attribute="ram", operator=">=", value=16, unit="GB", source_text="at least 16 GB RAM")
    check = _check(ram, _phone("vivo T5x 8GB RAM"), known(A, "ram", value=8))
    assert (check.verdict, check.reason) == ("fail", 'It fails "at least 16 GB RAM": it has 8 GB')


# --- storage ------------------------------------------------------------------------------------------------------


def test_storage_passes_in_its_largest_version() -> None:
    item = _phone("Nova Book 14 256GB SSD", "Nova Book 14 1TB SSD")
    check = _check(STORAGE_512, item, known(A, "storage_gb", value=256))
    assert check.reason == f'It meets "at least 512 GB storage" in its 1 TB version{NAMED}'


def test_storage_under_the_rule_in_every_version_fails() -> None:
    item = _phone("Nova Phone 5 128GB storage", "Nova Phone 5 256GB ROM")
    check = _check(STORAGE_512, item, None)
    assert (check.verdict, check.reason) == (
        "fail",
        'It fails "at least 512 GB storage" in every version we found: 128 GB and 256 GB',
    )


def test_an_ssd_rule_never_passes_on_a_hard_disk() -> None:
    ssd = HardRule(attribute="ssd_gb", operator=">=", value=512, unit="GB", source_text="at least a 512 GB SSD")
    check = _check(ssd, _phone("Nova Book 14 256GB SSD 1TB HDD"), unknown(A, "ssd_gb"))
    assert (check.verdict, check.reason) == ("fail", f'It fails "at least a 512 GB SSD": it has 256 GB{NAMED}')


# --- a Disputed value ---------------------------------------------------------------------------------------------


def test_a_disputed_value_under_the_rule_passes_on_the_named_version() -> None:
    value = disputed(A, "ram_gb", value=6, others=[(4, "GB")])
    check = _check(AT_LEAST_8, _phone("vivo T5x 8GB RAM"), value)
    assert (check.verdict, check.reason) == ("pass", f'It meets "at least 8 GB RAM" in its 8 GB version{NAMED}')


def test_a_disputed_value_with_a_side_that_meets_it_passes_on_that_claim() -> None:
    value = disputed(A, "ram_gb", value=6, others=[(8, "GB")])
    check = _check(AT_LEAST_8, _phone("vivo T5x 8GB RAM"), value)
    assert (check.verdict, check.reason) == ("pass", 'It meets "at least 8 GB RAM" in its 8 GB version')


def test_a_name_that_states_a_version_makes_a_disputes_sides_versions() -> None:
    """A stated version says the product comes in versions, so each side a claim gives is one of them: 8 GB meets it,
    though the name states 6 GB. With no stated version, the same dispute stays "Sources disagree" (below)."""
    value = disputed(A, "ram_gb", value=6, others=[(8, "GB")])
    check = _check(AT_LEAST_8, _phone("vivo T5x 6GB RAM"), value)
    assert (check.verdict, check.reason) == ("pass", 'It meets "at least 8 GB RAM" in its 8 GB version')
    assert _check(AT_LEAST_8, _phone("vivo T5x"), value).reason == "Sources disagree: 6 GB or 8 GB"


def test_a_disputed_value_and_a_name_all_under_the_rule_fail() -> None:
    value = disputed(A, "ram_gb", value=4, others=[(6, "GB")])
    check = _check(AT_LEAST_8, _phone("vivo T5x 6GB RAM"), value)
    assert (check.verdict, check.reason) == (
        "fail",
        'It fails "at least 8 GB RAM" in every version we found: 4 GB and 6 GB',
    )


def test_a_dispute_whose_other_side_was_not_kept_is_never_a_fail() -> None:
    """Verify kept no other side: a version we can't see may meet the rule, and missing evidence is never a fail."""
    value = disputed(A, "ram_gb", value=6)
    check = _check(AT_LEAST_8, _phone("vivo T5x 6GB RAM"), value)
    assert (check.verdict, check.reason) == ("unknown", "Sources disagree on its RAM: one says 6 GB")
    assert _check(AT_LEAST_8, _phone("vivo T5x 8GB RAM"), value).verdict == "pass"  # a named version that meets it


def test_a_value_that_cant_be_compared_keeps_it_cant_tell() -> None:
    """Every known version must fail for a fail: one that can't be compared leaves the rule unknown."""
    check = _check(AT_LEAST_8, _phone("vivo T5x 6GB RAM"), known(A, "ram_gb", value="8 GB + 8 GB"))
    assert (check.verdict, check.reason) == (
        "unknown",
        'We couldn\'t compare its RAM ("8 GB + 8 GB") with "at least 8 GB RAM"',
    )


# --- unchanged ----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(known(A, "ram_gb", value=6), id="known-under"),
        pytest.param(known(A, "ram_gb", value=16), id="known-over"),
        pytest.param(unknown(A, "ram_gb"), id="unknown"),
        pytest.param(None, id="missing"),
        pytest.param(disputed(A, "ram_gb", value=6, others=[(8, "GB")]), id="disputed-split"),
        pytest.param(disputed(A, "ram_gb", value=4, others=[(6, "GB")]), id="disputed-under"),
        pytest.param(known(A, "ram_gb", value="8 GB + 8 GB"), id="cant-compare"),
    ],
)
@pytest.mark.parametrize(
    "item",
    [
        pytest.param(product(A, "vivo T5x"), id="no-variants"),
        pytest.param(
            product(
                A, "vivo T5x", variants=[Variant(id="var_vivo_t5x_b", label="Blue", attributes={"colour": "blue"})]
            ),
            id="colour-only",
        ),
        pytest.param(product(A, "vivo T5x 8GB"), id="a-guessed-capacity"),
    ],
)
def test_a_product_whose_names_state_no_version_is_checked_as_before(item: Product, value: Any) -> None:
    assert stated_values(item, "ram_gb") == []
    assert _check(AT_LEAST_8, item, value) == check_rule(AT_LEAST_8, value)


def test_the_unchanged_check_keeps_its_wording() -> None:
    check = _check(AT_LEAST_8, product(A, "vivo T5x"), known(A, "ram_gb", value=6))
    assert (check.verdict, check.reason) == ("fail", 'It fails "at least 8 GB RAM": it has 6 GB')


@pytest.mark.parametrize(
    ("rule", "value"),
    [
        pytest.param(
            HardRule(attribute="storage", operator="contains", value="SSD", source_text="must have SSD storage"),
            unknown(A, "storage"),
            id="contains",
        ),
        pytest.param(
            HardRule(attribute="ssd", operator="==", value=True, source_text="must have an SSD"),
            known(A, "ssd", value=False),
            id="yes-or-no",
        ),
        pytest.param(
            HardRule(attribute="ram", operator=">=", value=16, source_text="at least 16 RAM"),
            known(A, "ram", value=8),
            id="no-unit",
        ),
        pytest.param(LIGHT, known(A, "weight_kg", value=1.8), id="another-attribute"),
    ],
)
def test_a_rule_that_compares_no_size_is_checked_as_before(rule: HardRule, value: ResolvedValue) -> None:
    item = _phone("Nova Book 14 16GB RAM 512GB SSD")
    assert _check(rule, item, value) == check_rule(rule, value)


def test_the_budget_ignores_versions() -> None:
    item = _phone("vivo T5x 8GB RAM", "vivo T5x 16GB RAM")
    values = {(A, "price_inr"): known(A, "price_inr", value=31999)}
    (budget,) = checks_for(item, rubric(budget_max=30000).budget, [], [], values)
    assert (budget.verdict, budget.reason) == ("fail", "₹31,999 is above your ₹30,000 budget")


@pytest.mark.parametrize(
    ("attribute", "word"),
    [
        ("ram_gb", "ram"),
        ("ram", "ram"),
        ("memory_gb", "memory"),
        ("storage_gb", "storage"),
        ("ssd_gb", "ssd"),
        ("hdd_gb", "hdd"),
        ("rom_gb", "rom"),
        ("storage", "storage"),
        ("weight_kg", None),
        ("price_inr", None),
        ("has_ram_slot", None),
        ("battery_mah", None),
        ("colour", None),
    ],
)
def test_only_ram_and_storage_are_checked_by_version(attribute: str, word: str | None) -> None:
    assert variant_word(attribute) == word


def test_a_memory_rule_reads_a_names_ram() -> None:
    memory = HardRule(attribute="memory_gb", operator=">=", value=8, unit="GB", source_text="at least 8 GB memory")
    assert stated_values(_phone("vivo T5x 8GB RAM"), "memory_gb") == ["8GB"]
    assert _check(memory, _phone("vivo T5x 8GB RAM"), known(A, "memory_gb", value=6)).verdict == "pass"


def test_a_switched_off_rule_never_filters_a_product_with_versions() -> None:
    off = AT_LEAST_8.model_validate({**AT_LEAST_8.model_dump(), "active": False})
    item = _phone("vivo T5x 4GB RAM", "vivo T5x 6GB RAM")
    result = decide(decide_input(rubric(rules=[off]), [item], [known(A, "battery_life", score=7.0)]))
    assert group_of(result, A) == "shown"
