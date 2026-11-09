"""A claim survives only if its quote is in the cleaned source, whatever its case and spacing (US-14, build rule 4)."""

from __future__ import annotations

import logging

import pytest

from app.contracts import Claim, Source, SourceComment
from app.extract.parsing import RawClaim, RawExtraction, found_quote, to_extraction_result
from app.research.reddit.archive import add_comments
from tests.fakes.reddit import NOW
from tests.fakes.research import review_source, thread

QUOTE = "Nova Book 14 lasts about nine hours on a charge."
PAGE = review_source("https://www.reviews.example/a", title="A made-up review", text=f"{QUOTE}\n\nThe hinge is firm.")
KEYS = ["battery_life"]


def _raw(quote: str) -> RawClaim:
    return RawClaim(
        product_mention="Nova Book 14", criterion_key="battery_life", score=8.0, quote=quote, polarity="positive"
    )


def _claims(quote: str, source: Source = PAGE) -> list[Claim]:
    return to_extraction_result(RawExtraction(mentions=[], claims=[_raw(quote)]), source, KEYS, NOW).claims


def _thread(*comments: str) -> Source:
    rows = tuple(SourceComment(id=f"m{i}", depth=1, upvotes=3, text=text) for i, text in enumerate(comments))
    return add_comments(thread("aaa", text="A made-up post about the Nova Book 14."), rows)


@pytest.mark.parametrize(
    "quote",
    [
        pytest.param(QUOTE, id="the-same"),
        pytest.param(QUOTE.upper(), id="upper-case"),
        pytest.param(QUOTE.lower(), id="lower-case"),
        pytest.param(QUOTE.replace(" ", "   "), id="more-spaces"),
        pytest.param(QUOTE.replace(" about ", "\nabout\t"), id="line-breaks-and-tabs"),
        pytest.param(f"  {QUOTE}  ", id="spaces-around"),
    ],
)
def test_a_quote_differing_only_in_case_and_spaces_is_kept(quote: str) -> None:
    assert len(_claims(quote)) == 1


def test_the_stored_quote_is_the_sources_own_words() -> None:
    (claim,) = _claims(QUOTE.upper())
    assert claim.quote == QUOTE


def test_a_quote_across_a_line_break_keeps_the_sources_spacing() -> None:
    text = "Nova Book 14 lasts about\nnine hours."
    source = review_source("https://www.reviews.example/b", text=text)
    (claim,) = _claims("nova book 14 lasts about nine hours", source)
    assert claim.quote == "Nova Book 14 lasts about\nnine hours"
    assert claim.quote in source.text


def test_a_quote_not_in_the_source_is_dropped() -> None:
    assert _claims("Nova Book 14 is the best laptop ever made.") == []


def test_a_quote_with_a_changed_word_is_dropped() -> None:
    assert _claims(QUOTE.replace("nine", "ninety")) == []


def test_a_blank_quote_is_dropped() -> None:
    assert _claims("   ") == []


def test_a_blank_quote_matches_nothing() -> None:
    assert found_quote("   ", [QUOTE]) is None


def test_a_quote_stitched_across_two_comments_is_dropped() -> None:
    source = _thread("The battery is fine.", "The screen is dim.")
    assert _claims("The battery is fine. The screen is dim.", source) == []


def test_a_quote_stitched_from_the_post_into_a_comment_is_dropped() -> None:
    source = _thread("The battery is fine.")
    assert _claims("about the Nova Book 14. The battery is fine.", source) == []


def test_a_quote_from_a_comment_is_kept() -> None:
    source = _thread("The hinge is firm.", "The battery is fine.")
    (claim,) = _claims("the BATTERY is fine.", source)
    assert claim.quote == "The battery is fine."


def test_a_quote_from_the_title_is_dropped() -> None:
    """A page's title can be invisible on the page, so a fact only there must not become a claim."""
    source = review_source("https://www.reviews.example/c", title="Nova Book 14 scored ten", text=QUOTE)
    assert _claims("Nova Book 14 scored ten", source) == []


def test_characters_a_pattern_treats_as_special_are_taken_literally() -> None:
    source = review_source("https://www.reviews.example/d", text="Nova Book 14 costs (about) 62,990+ [before tax].")
    assert len(_claims("Nova Book 14 costs (about) 62,990+ [before tax].", source)) == 1
    assert _claims("Nova Book 14 costs .* tax", source) == []


def test_the_log_holds_counts_and_no_text(caplog: pytest.LogCaptureFixture) -> None:
    secret = "a sentence only a made-up model would write"
    with caplog.at_level(logging.INFO, logger="app.extract"):
        assert _claims(secret) == []
    assert "quote" in caplog.text
    assert secret not in caplog.text
    assert QUOTE not in caplog.text
