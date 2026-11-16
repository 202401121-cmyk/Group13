"""US-20, DECIDED 2: a sentence naming a number must match the code's number. How numbers are found and matched."""

from __future__ import annotations

import pytest

from app.explain.numbers import matches, numbers_in, unmatched


def _values(text: str) -> list[float]:
    return [n.value for n in numbers_in(text)]


@pytest.mark.parametrize(
    ("text", "values"),
    [
        ("It scores 8 out of 10.", [8, 10]),
        ("It scores 7.5 out of 10.", [7.5, 10]),
        ("It ranks #1 with 78 out of 100.", [1, 78, 100]),
        ("It adds 24% to its score.", [24]),
        ("It costs 1,200 more.", [1200]),
        ("Ten hours, or about twenty-five in all.", [10, 20, 5]),  # neither 20 nor 5 is 25, so it can't pass
        ("One of its best points.", []),  # "one" is a pronoun far more often than a number
        ("A thirty-minute charge, then a hundred more.", [30, 100]),
        ("No numbers here at all.", []),
        ("Its 2nd best point.", [2]),
        ("It costs ₹1,14,900.", [114900]),  # Indian grouping
        ("Under ₹10,00,000 in all.", [1000000]),
        ("A .5 point lead.", [0.5]),
        ("It ranks second, and is twice as fast.", [2, 2]),
        ("Half the weight, at a lakh or a thousand.", [0.5, 100000, 1000]),
        ("It comes first.", [1]),
    ],
)
def test_numbers_are_found_as_digits_and_words(text: str, values: list[float]) -> None:
    assert _values(text) == values


def test_a_number_matches_at_the_precision_it_is_written() -> None:
    (whole,) = numbers_in("about 8")
    (exact,) = numbers_in("8.5")
    (wrong,) = numbers_in("8.4")
    assert matches(whole, 8.5)  # 8 is 8.5 at a whole number's precision
    assert matches(numbers_in("9")[0], 8.5)
    assert matches(exact, 8.5)
    assert not matches(wrong, 8.5)
    assert not matches(numbers_in("7")[0], 8.5)


def test_unmatched_lists_every_number_code_did_not_give() -> None:
    allowed = [8.0, 24, 10, 100, 1]
    assert unmatched("It scores 8 out of 10 and adds 24 points.", allowed) == []
    assert unmatched("It lasts 12 hours and scores 8 out of 10.", allowed) == ["12"]
    assert unmatched("Twelve hours of battery.", allowed) == ["Twelve"]  # as written


def test_a_number_glued_to_letters_still_counts() -> None:
    """ "16GB" or "M3" is a fact too: one code didn't give is invented, so it must be caught."""
    assert _values("A USB-C port and an M3 chip") == [3]
    assert _values("16GB of memory") == [16]
    assert _values("Wi-Fi6E") == [6]
