"""Generated criteria reach public prompts through fixed stand-ins, matched in code by their words (US-14, D-51).

A generated key maps to a fixed criterion of its category when its words, with unit words removed, name it: the fixed
key's own words, or a one-word alias in config/extract.yaml's public_synonyms. An exact public_synonyms entry wins.
Only the fixed key and our own definition are ever sent, never the generated key or its words. Every key, page and
product here is made up.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from app.contracts import DataClass, ExtractInput, LLMTask, StageContext
from app.criteria.fallback import fallback_criteria
from app.decide.directions import stand_in
from app.extract import GuardedExtractStage
from app.extract.parsing import RawClaim, RawExtraction
from app.extract.prompts import allowed_criteria, user_message
from app.extract.settings import load_extract_settings
from app.intake.lexicon import load_lexicon
from tests.fakes.llm import FakeLLMClient
from tests.fakes.research import review_source

SETTINGS = load_extract_settings(env={})
SYNONYMS = SETTINGS.public_synonyms
TEXT = "The Crispo Five holds 5.5 litres and draws 1700 watts."
PAGE = review_source("https://www.reviews.example/fryer", title="A made-up air fryer review", text=TEXT)
CTX = StageContext(search_id="srch_standins")


def _fixed(category: str) -> set[str]:
    return {c.key for c in fallback_criteria(category).criteria}


def _sent(keys: list[str], category: str = "air_fryer") -> dict[str, str]:
    """Each fixed key a public prompt carries, and the search's own key its claims come back under."""
    criteria = allowed_criteria(
        ExtractInput(source=PAGE, criterion_keys=keys, category_label=category), DataClass.PUBLIC, None, SETTINGS
    )
    return {key: criteria.claim_key(key) for key in criteria.keys}


def test_capacity_litres_maps_to_capacity() -> None:
    assert stand_in("capacity_litres", _fixed("air_fryer"), SYNONYMS) == "capacity"
    assert _sent(["capacity_litres"]) == {"capacity": "capacity_litres"}


def test_power_watts_maps_to_power() -> None:
    assert stand_in("power_watts", _fixed("air_fryer"), SYNONYMS) == "power"
    assert stand_in("wattage", _fixed("air_fryer"), SYNONYMS) == "power"  # a one-word alias


def test_cooking_temperature_range_maps_to_temperature_range() -> None:
    assert stand_in("cooking_temperature_range", _fixed("air_fryer"), SYNONYMS) == "temperature_range"
    assert stand_in("max_temperature", _fixed("air_fryer"), SYNONYMS) == "temperature_range"


def test_a_criterion_naming_nothing_fixed_stays_out() -> None:
    for key in ("timer_accuracy", "evenness_of_cooking", "dialysis_records"):
        assert stand_in(key, _fixed("air_fryer"), SYNONYMS) is None, key
    assert _sent(["timer_accuracy", "capacity_litres"]) == {"capacity": "capacity_litres"}


def test_public_synonyms_win_over_the_word_match() -> None:
    fixed = {"capacity", "power"}
    assert stand_in("capacity_litres", fixed, {"capacity_litres": ["power"]}) == "power"
    assert stand_in("capacity_litres", fixed, {"capacity_litres": ["missing"]}) is None  # it named the key: no guess
    # in the config: battery_performance names battery_life, though "performance" alone would mean processor speed
    assert stand_in("battery_performance", _fixed("smartphone"), SYNONYMS) == "battery_life"


def test_an_ambiguous_word_match_stays_out() -> None:
    assert stand_in("noise_cancellation_level", {"noise_level", "noise_cancellation"}, {}) is None
    assert stand_in("camera_battery", _fixed("smartphone"), SYNONYMS) is None  # two aliases, two criteria
    # the most specific fixed key wins when one has more words: comfort_and_fit over comfort
    assert stand_in("comfort_and_fit_rating", {"comfort", "comfort_and_fit"}, {}) == "comfort_and_fit"


def test_a_one_word_alias_matches_inside_a_longer_key() -> None:
    assert stand_in("battery_backup_hours", _fixed("smartphone"), SYNONYMS) == "battery_life"
    assert stand_in("basket_volume", _fixed("air_fryer"), SYNONYMS) == "capacity"


def test_a_stand_in_already_used_is_sent_once() -> None:
    assert _sent(["capacity_litres", "basket_volume"]) == {"capacity": "capacity_litres"}
    assert _sent(["capacity", "capacity_litres"]) == {"capacity": "capacity"}  # the search's own fixed key first


def test_the_matched_claims_come_back_under_the_searchs_own_key() -> None:
    raw = RawExtraction(
        mentions=[],
        claims=[
            RawClaim(
                product_mention="Crispo Five",
                criterion_key="capacity",
                value=5.5,
                unit="l",
                quote="holds 5.5 litres",
                polarity="neutral",
            )
        ],
    )
    stage = GuardedExtractStage(FakeLLMClient({LLMTask.EXTRACT_CLAIMS: raw}), SETTINGS, lambda: datetime.now(UTC))
    out = stage.run(ExtractInput(source=PAGE, criterion_keys=["capacity_litres"], category_label="air_fryer"), CTX)
    assert [c.criterion_key for c in out.claims] == ["capacity_litres"]


MARKERS = ("zebra", "grandma", "quokka", "dialysis")


def _generated_rubric(category: str) -> list[str]:
    """A made-up generated rubric: each fixed criterion wrapped in a shopper's words, unit-word variants, and keys that
    name nothing fixed. The marker words stand for what a shopper typed."""
    keys = ["quokka_dialysis_records", "capacity_litres", "power_watts", "battery_backup_hours"]
    return keys + [f"zebra_{key}_for_grandma" for key in sorted(_fixed(category))][:6]  # 10 keys, the most a rubric has


def test_no_generated_text_appears_in_any_public_prompt() -> None:
    """For every category the lexicon knows, a public prompt carries only fixed keys and our own definitions (D-51)."""
    for category in load_lexicon().categories:
        ours = {c.key: c.definition for c in fallback_criteria(category).criteria}
        keys = _generated_rubric(category)
        criteria = allowed_criteria(
            ExtractInput(source=PAGE, criterion_keys=keys, category_label=category), DataClass.PUBLIC, None, SETTINGS
        )
        message = user_message(PAGE, criteria)
        for marker in MARKERS:
            assert marker not in message.casefold(), (category, marker)
        lines = re.findall(r"^- (\w+): (.+)$", message, flags=re.MULTILINE)
        assert lines, category
        for key, text in lines:
            assert key in ours, (category, key)
            assert text == ours[key], (category, key)  # our own definition, never words from the generated key
        assert set(criteria.renames.values()) <= set(keys)  # claims come back under the search's own keys


# --- after the review: words that turn a criterion around, and decide agreeing with extract ------------------------

TURNED_AROUND = [
    ("auto_power_off", "electric_kettle"),  # a safety feature, not watts
    ("power_consumption", "microwave"),  # less is better: power is more-is-better
    ("power_saving", "air_fryer"),
    ("compact_size", "air_fryer"),  # small is wanted: capacity is bigger-is-better
    ("small_size", "microwave"),
    ("exterior_temperature", "air_fryer"),  # a cool outside is better
    ("min_temperature", "air_fryer"),
    ("max_child_weight", "pram"),  # a load limit, not the pram's own weight
    ("weight_capacity_kg", "pram"),  # names weight and capacity equally
    ("price_to_performance_ratio", "laptop"),  # value for money, not processor speed
    ("grinding_performance", "mixer_grinder"),  # "performance" is too broad to read inside a longer key
    ("cost_effectiveness", "refrigerator"),
    ("customer_support_quality", "running_shoes"),  # not foot support
    ("after_sales_support", "running_shoes"),
    ("warranty_support", "running_shoes"),  # "warranty" points elsewhere than "support"
    ("storage_space", "laptop"),  # a smartphone criterion, not memory_and_storage
    ("size_and_weight", "air_fryer"),  # a computing criterion, not capacity
]


def test_words_that_turn_a_criterion_around_never_match() -> None:
    for key, category in TURNED_AROUND:
        assert stand_in(key, _fixed(category), SYNONYMS) is None, (key, category)


def test_decide_maps_every_generated_key_as_extract_does() -> None:
    """For every category, each key extract sends under a stand-in takes that stand-in's direction in decide."""
    from app.decide.directions import direction_of, stated_directions

    stated = stated_directions()
    for category in load_lexicon().categories:
        keys = _generated_rubric(category) + [key for key, _ in TURNED_AROUND][:6]
        criteria = allowed_criteria(
            ExtractInput(source=PAGE, criterion_keys=keys[:10], category_label=category),
            DataClass.PUBLIC,
            None,
            SETTINGS,
        )
        for found, key in criteria.renames.items():
            assert direction_of(key, category) == stated.get(found), (category, key, found)
