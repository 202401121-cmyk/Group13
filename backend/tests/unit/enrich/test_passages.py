"""D-101: the fact call reads passages, not whole pages: the text around each place a page names its product or uses a
word of the criteria looked up for it. Plain code; every page here is made up.
"""

from __future__ import annotations

from app.enrich.passages import GAP, criterion_words, passages

FILLER = "The keyboard has a soft feel and the ports sit on both sides of the base."


def _page(*middle: str, filler: int = 30) -> str:
    padding = " ".join([FILLER] * filler)
    return " ".join([padding, *(f"{part} {padding}" for part in middle)])


def test_a_short_page_goes_whole() -> None:
    text = "The Vela Pro 14 lasted 11 hours in our battery test."
    assert passages(text, "Vela Pro 14", ["battery"]) == text


def test_a_long_page_keeps_only_the_parts_around_the_name_and_the_criterion() -> None:
    fact = "In our tests the Vela Pro 14 lasted 11 hours on one charge of its battery."
    text = _page(fact)
    kept = passages(text, "Vela Pro 14", ["battery"], around=60, most=400)
    assert fact in kept and len(kept) <= 400
    assert len(kept) < len(text) / 4


def test_parts_are_cut_at_spaces_never_inside_a_word() -> None:
    text = _page("The Vela Pro 14 weighs 1.4 kg.")
    kept = passages(text, "Vela Pro 14", ["weight"], around=37, most=500)
    for part in kept.split(GAP):
        assert part == part.strip() and part in text
        first, last = part.split()[0], part.split()[-1]
        assert f" {first}" in f" {text}" and f"{last} " in f"{text} "  # whole words at both ends


def test_a_criterions_other_forms_are_found_and_its_parts_come_first_when_room_is_short() -> None:
    name_only = "The Vela Pro 14 comes in grey."
    fact = "Its batteries lasted 11 hours."
    text = _page(name_only, fact)
    kept = passages(text, "Vela Pro 14", ["battery"], around=20, most=60)
    assert "batteries lasted 11 hours." in kept and "comes in grey" not in kept  # the fact first, the name after


def test_parts_far_apart_are_joined_by_the_gap_in_page_order() -> None:
    text = _page("The Vela Pro 14 is light.", "Battery life is 11 hours.")
    kept = passages(text, "Vela Pro 14", ["battery"], around=10, most=2000)
    assert kept.count(GAP) == 1
    assert kept.index("Vela Pro 14") < kept.index("Battery")


def test_parts_that_touch_are_merged() -> None:
    text = _page("The Vela Pro 14 battery lasted 11 hours.")
    kept = passages(text, "Vela Pro 14", ["battery", "hours"], around=30, most=2000)
    assert GAP not in kept and "Vela Pro 14 battery lasted 11 hours." in kept


def test_a_page_with_neither_the_name_nor_a_word_keeps_its_start() -> None:
    text = _page()
    kept = passages(text, "Vela Pro 14", ["battery"], most=100)
    assert text.startswith(kept) and 0 < len(kept) <= 100


def test_a_name_with_no_words_finds_nothing_by_name() -> None:
    text = _page("Battery life is 11 hours.")
    assert "Battery life" in passages(text, "   ", ["battery"], around=20, most=100)


def test_a_part_with_no_whole_word_in_it_is_left_out() -> None:
    assert passages("battery" + "z" * 3000, "Vela Pro 14", ["battery"], around=3, most=2000) == ""
    kept = passages("x" * 50 + " The Vela Pro 14 " + "y" * 3000, "Vela Pro 14", [], around=3, most=2000)
    assert kept == "Vela Pro 14"  # the partial words on either side are dropped


def test_criterion_words_are_the_label_and_key_words_once_each() -> None:
    assert criterion_words(["battery_life_hours", "Battery life"]) == ["battery", "life", "hours"]
    assert criterion_words(["weight_kg", "Weight (kg)", "is it light for the bag"]) == ["weight", "light", "bag"]


def test_once_a_part_fills_the_room_no_other_is_kept() -> None:
    text = "battery life is long " + "x " * 200 + "The Vela Pro 14 is light."
    assert passages(text, "Vela Pro 14", ["battery"], around=100, most=20) == "battery life is long"
