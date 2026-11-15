"""Kitchen categories whose shoppers compare measured specs get their own fixed sets (US-14 stand-ins, P-20).

An air fryer, a microwave and a kettle are bought on capacity and power, and an air fryer on how hot it gets, so their
sets carry those criteria, with units and a direction, in place of the family's least fitting ones. The other kitchen
categories keep the family set.
"""

from __future__ import annotations

from app.contracts import Direction
from app.criteria.fallback import fallback_criteria, load_category_sets, load_fallback_sets

SPECS = {
    "air_fryer": {"capacity": "l", "power": "W", "temperature_range": "°C"},
    "microwave": {"capacity": "l", "power": "W"},
    "electric_kettle": {"capacity": "l", "power": "W"},
}
EXPECTED = {
    "air_fryer": [
        "cooking_results",
        "capacity",
        "power",
        "temperature_range",
        "ease_of_cleaning",
        "safety",
        "noise_level",
        "value_for_money",
    ],
    "microwave": [
        "cooking_results",
        "capacity",
        "power",
        "ease_of_cleaning",
        "safety",
        "build_durability",
        "after_sales_service",
        "value_for_money",
    ],
    "electric_kettle": [
        "capacity",
        "power",
        "safety",
        "ease_of_cleaning",
        "noise_level",
        "build_durability",
        "after_sales_service",
        "value_for_money",
    ],
}


def test_the_new_kitchen_sets_load_with_their_specs() -> None:
    for category, keys in EXPECTED.items():
        criteria = load_category_sets()[category].criteria
        assert [c.key for c in criteria] == keys, category
        for key, unit in SPECS[category].items():
            found = next(c for c in criteria if c.key == key)
            assert (found.unit, found.direction) == (unit, Direction.HIGHER_IS_BETTER), (category, key)


def test_other_kitchen_categories_keep_the_family_set() -> None:
    family = load_fallback_sets()["kitchen_appliance"]
    for category in ("mixer_grinder", "induction_cooktop", "kitchen_chimney"):
        assert fallback_criteria(category) == family, category
    assert [c.key for c in family.criteria] == [
        "cooking_results",
        "speed",
        "ease_of_cleaning",
        "safety",
        "noise_level",
        "build_durability",
        "after_sales_service",
        "value_for_money",
    ]
