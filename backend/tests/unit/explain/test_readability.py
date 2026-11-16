"""US-20, DECIDED 3: readability in code, Flesch-Kincaid grade 9 or under, with no new package."""

from __future__ import annotations

import pytest

from app.explain.readability import GRADE_LIMIT, grade, syllables


@pytest.mark.parametrize(
    ("word", "count"),
    [
        ("cat", 1),
        ("the", 1),
        ("battery", 3),
        ("performance", 3),
        ("durability", 5),
        ("simple", 2),
        ("life", 1),
        ("keyboard", 2),
        ("comfort", 2),
        ("money", 2),
        ("estimate", 3),
        ("Sources", 2),
        ("boxes", 2),
        ("rated", 2),
        ("worked", 1),
    ],
)
def test_syllables_are_counted_by_vowel_groups(word: str, count: int) -> None:
    assert syllables(word) == count


def test_the_grade_is_the_flesch_kincaid_formula() -> None:
    # 4 words, 1 sentence, 4 syllables: 0.39 x 4 + 11.8 x 1 - 15.59
    assert grade("The cat sat down.") == pytest.approx(0.39 * 4 + 11.8 - 15.59)
    # 2 sentences of 3 words each, 9 syllables: 0.39 x 3 + 11.8 x 1.5 - 15.59
    assert grade("Battery life rocks. Its keyboard fits.") == pytest.approx(0.39 * 3 + 11.8 * 9 / 6 - 15.59)


def test_numbers_and_symbols_are_not_words() -> None:
    assert grade("It scores 8 out of 10.") == grade("It scores out of.")


def test_plain_sentences_pass_and_dense_ones_fail() -> None:
    assert GRADE_LIMIT == 9
    plain = "It has a long battery life. Its keyboard is easy to type on. It is light to carry."
    dense = (
        "Its exceptional computational performance, combined with considerable battery durability, constitutes "
        "a particularly compelling proposition for professional developers."
    )
    assert grade(plain) <= GRADE_LIMIT
    assert grade(dense) > GRADE_LIMIT


def test_an_empty_text_has_grade_0() -> None:
    assert grade("") == 0.0
    assert grade("8 / 10 #1") == 0.0


def test_a_decimal_point_does_not_end_a_sentence() -> None:
    assert grade("It scores 7.5 out of 10.") == grade("It scores out of.")
