"""Which fixed criteria a stated value can score (US-18, P-20): those whose entry in config/criteria_fallback.yaml
states `direction:`. Opinion criteria leave it out, so they are scored by opinions only."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]  # PyYAML ships no type stubs

from app.contracts import Direction
from app.criteria.fallback import DEFAULT_FALLBACK_PATH, fallback_criteria, load_category_sets, load_fallback_sets
from app.decide.directions import direction_of, fixed_keys, load_directions, stated_directions
from app.intake.lexicon import load_lexicon

HIGHER = {
    "attachments",
    "battery_life",
    "capacity",
    "height_presets",
    "load_capacity",
    "loudness",
    "motion_handling",
    "noise_cancellation",
    "phone_compatibility",
    "power",
    "safety_standards",
    "software_updates",
    "storage_space",
    "temperature_range",
    "trial_and_warranty",
    "warranty_length",
    "water_resistance",
    "weather_resistance",
}
LOWER = {"noise_level", "running_cost", "size_and_weight", "weight"}  # stated before US-18
# Left out on purpose: a stated number could rank backwards. energy_efficiency: watts and kWh are lower-better, the
# star rating higher-better. memory_and_storage: RAM and SSD sizes in GB would share one scale.
NO_DIRECTION = {"energy_efficiency", "memory_and_storage", "charging_speed", "speed", "display_quality"}


def test_criteria_that_could_rank_backwards_have_no_direction() -> None:
    assert NO_DIRECTION.isdisjoint(stated_directions())


def test_the_directions_are_the_agreed_list() -> None:
    assert stated_directions() == {key: "higher" for key in HIGHER} | {key: "lower" for key in LOWER}


def test_a_key_has_one_direction_in_every_set() -> None:
    """A criterion means the same thing in every set it is in, so its direction is stated everywhere or nowhere."""
    raw = yaml.safe_load(DEFAULT_FALLBACK_PATH.read_text(encoding="utf-8"))
    seen: dict[str, set[str | None]] = {}
    for section in ("families", "categories"):
        for criteria_set in (raw.get(section) or {}).values():
            for item in criteria_set["criteria"]:
                seen.setdefault(item["key"], set()).add(item.get("direction"))
    mixed = {key: directions for key, directions in seen.items() if len(directions) > 1}
    assert mixed == {}


def test_the_fallback_criteria_load_as_before() -> None:
    """Every direction US-18 states is the field's default, so the sets other stages read are unchanged."""
    sets = [*load_fallback_sets().values(), *load_category_sets().values()]
    lowered = {c.key for s in sets for c in s.criteria if c.direction is Direction.LOWER_IS_BETTER}
    assert lowered == LOWER


def test_a_missing_file_means_no_directions(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    assert load_directions(tmp_path / "missing.yaml") == {}
    broken = tmp_path / "broken.yaml"
    broken.write_text("families: [", encoding="utf-8")
    assert load_directions(broken) == {}
    assert "no directions" in caplog.text


def test_a_key_stated_two_ways_has_no_direction(tmp_path: Path) -> None:
    path = tmp_path / "criteria.yaml"
    rows = [
        "families:",
        "  a:",
        "    criteria:",
        "      - {key: weight, direction: lower_is_better}",
        "  b:",
        "    criteria:",
        "      - {key: weight, direction: higher_is_better}",
        "      - {key: ram, direction: higher_is_better}",
    ]
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    assert load_directions(path) == {"ram": "higher"}


def test_each_category_uses_the_same_fixed_set_as_the_criteria_stage() -> None:
    """decide finds a category's set from the files itself (it imports no other stage): it must match US-03's."""
    categories = [*load_lexicon().categories, "a_category_nobody_knows"]
    for category in categories:
        assert fixed_keys(category) == {c.key for c in fallback_criteria(category).criteria}, category


def test_a_word_matched_generated_criterion_takes_its_stand_ins_direction() -> None:
    """Decide maps a generated key as extract does (US-14 stand-ins), so its stated values can be scored."""
    assert direction_of("capacity_litres", "air_fryer") == "higher"
    assert direction_of("power_watts", "air_fryer") == "higher"
    assert direction_of("cooking_temperature_range", "air_fryer") == "higher"
    assert direction_of("timer_accuracy", "air_fryer") is None
    assert direction_of("capacity_litres", "mixer_grinder") is None  # the family set has no capacity


def test_a_bad_synonyms_table_means_none_as_in_extract(tmp_path: Path) -> None:
    """Extract runs with no table when one entry is bad, so decide reads none either: the two stages still agree."""
    from app.decide.directions import load_synonyms

    good = tmp_path / "good.yaml"
    good.write_text("public_synonyms:\n  battery: [battery_life]\n", encoding="utf-8")
    assert load_synonyms(good) == {"battery": ["battery_life"]}
    for bad in ("  temp: []\n", "  Battery: [battery_life]\n", "  x: [battery_life]\n"):
        path = tmp_path / "bad.yaml"
        path.write_text("public_synonyms:\n  battery: [battery_life]\n" + bad, encoding="utf-8")
        assert load_synonyms(path) == {}, bad
