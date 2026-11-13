"""Exclusions typed in words (US-10): "no Xiaomi", "not touchscreen", "no Galaxy A35". Code only, never guessed.

A brand comes from the counting brands (config/reddit_quality.yaml), a feature from config/exclusions.yaml, and a model
is a name with a digit. Anything else comes back as not understood. "always" is never set here (D-36).
"""

from __future__ import annotations

import pytest

from app.contracts import ExclusionParseResponse
from app.intake.exclusions import load_feature_words, parse_exclusions
from app.intake.rules import detect_by_rules


def found(text: str, **options: bool) -> list[tuple[str, str]]:
    return [(e.kind, e.value) for e in parse_exclusions(text, **options).exclusions]


def test_no_xiaomi_is_a_brand() -> None:
    assert found("no Xiaomi") == [("brand", "Xiaomi")]


def test_not_touchscreen_is_a_feature() -> None:
    assert found("not touchscreen") == [("feature", "touchscreen")]
    assert found("without a touch screen") == [("feature", "touchscreen")]


def test_no_galaxy_a35_is_a_model() -> None:
    assert found("no Galaxy A35") == [("model", "Galaxy A35")]
    assert found("avoid the Pixel 8") == [("model", "Pixel 8")]


def test_no_xiaomi_or_realme_gives_two() -> None:
    assert found("no Xiaomi or Realme") == [("brand", "Xiaomi"), ("brand", "Realme")]
    assert found("no Xiaomi, Realme and Oppo") == [("brand", "Xiaomi"), ("brand", "Realme"), ("brand", "Oppo")]


def test_i_dont_want_anything_from_apple_is_a_brand() -> None:
    assert found("I don't want anything from Apple") == [("brand", "Apple")]
    assert found("anything but Samsung phones") == [("brand", "Samsung")]
    assert found("except HP laptops") == [("brand", "HP")]


def test_an_unknown_word_comes_back_not_understood() -> None:
    result = parse_exclusions("no Zorblax")
    assert result.exclusions == []
    assert result.not_understood == ["Zorblax"]


def test_mixed_case_and_punctuation() -> None:
    assert found("NO xiaomi!!! Also, AVOID: Curved Displays.") == [("brand", "xiaomi"), ("feature", "curved display")]
    assert found("No Xiaomi; no notch") == [("brand", "Xiaomi"), ("feature", "notch")]


@pytest.mark.parametrize("text", ["no more than 1.5 kg", "not over 70k", "no less than 16gb", "not above ₹50,000"])
def test_measures_are_never_models(text: str) -> None:
    assert found(text) == []


def test_always_is_never_set_here() -> None:
    result = parse_exclusions("never show me Xiaomi, always avoid Realme")
    assert result.exclusions and all(e.always is False for e in result.exclusions)


def test_a_bare_list_is_read_by_the_route_but_not_by_intake() -> None:
    assert found("Xiaomi, Realme") == [("brand", "Xiaomi"), ("brand", "Realme")]
    assert found("Xiaomi, Realme", require_trigger=True) == []
    assert parse_exclusions("best laptop for coding under 70k", require_trigger=True) == ExclusionParseResponse()


def test_the_keyword_fallback_finds_the_same_exclusions() -> None:
    text = "best phone under 30k, no Xiaomi or Realme and not curved display"
    intent = detect_by_rules(text)
    assert intent.category == "smartphone"
    assert [(e.kind, e.value) for e in intent.exclusions] == found(text, require_trigger=True)
    assert [(e.kind, e.value) for e in intent.exclusions] == [
        ("brand", "Xiaomi"),
        ("brand", "Realme"),
        ("feature", "curved display"),
    ]


def test_a_request_with_no_exclusion_keeps_an_empty_list() -> None:
    assert detect_by_rules("best laptop for coding under 70k").exclusions == []


def test_feature_words_come_from_config() -> None:
    features = load_feature_words()
    assert {"touchscreen", "curved display", "notch", "glossy screen"} <= set(features.values())
    assert features["touch screen"] == "touchscreen"


def test_the_limits_of_the_answer_hold() -> None:
    many = "no " + ", ".join(f"Zorb{chr(97 + i)}x" for i in range(25))
    result = parse_exclusions(many)
    assert len(result.not_understood) == 20


# --- from the review -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "laptop not too heavy, Dell or Lenovo",
        "phone under 30k, not sure which brand, Samsung or OnePlus",
        "I have no budget limit, Apple or Samsung",
    ],
)
def test_in_a_request_brands_after_a_word_it_cannot_place_are_never_ruled_out(text: str) -> None:
    assert found(text, require_trigger=True) == []


def test_in_the_editors_box_every_item_is_still_read() -> None:
    result = parse_exclusions("no Zorblax or Realme")
    assert [(e.kind, e.value) for e in result.exclusions] == [("brand", "Realme")]
    assert result.not_understood == ["Zorblax"]


@pytest.mark.parametrize(
    "text",
    ["I don't want to spend more than 50000", "laptop not exceeding 70000", "no budget above 50000"],
)
def test_an_amount_without_a_unit_is_never_a_model(text: str) -> None:
    assert found(text) == [] and found(text, require_trigger=True) == []


def test_a_long_request_with_no_breaks_never_crashes_the_keyword_fallback() -> None:
    text = (
        "laptop not too heavy because I travel every day from my home to my college on a crowded local train where I "
        "stand for almost two hours each way with my bag on my shoulder which also has my books lunch box water bottle "
        "charger"
    )
    result = parse_exclusions(text)
    assert all(len(item) <= 200 for item in result.not_understood)
    assert detect_by_rules(text).exclusions == []
