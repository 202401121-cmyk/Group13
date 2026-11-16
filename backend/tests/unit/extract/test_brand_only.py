"""A name that is only a brand is not a product (US-15, S4-23; S3-19's "Dell" and "Lenovo").

A mention whose parsed name holds nothing but a brand and generic words ("Dell", "Lenovo laptops", "Samsung phone") is
dropped by the extract parser, in code, before clustering, and so are its claims: they are attached to no product. The
drop is counted as "brand_only" in the parser's claims-dropped line, which the live funnel reads, and the names in one
line of their own: counts only, never the page's words. A series or model word, a model code or a number keeps a name.
The counting lists in config/reddit_quality.yaml decide what a brand is. Every page here is made up.
"""

from __future__ import annotations

import logging

import pytest

from app.contracts import ExtractionResult, MatchInput, Source
from app.decide.rules import PRICE_KEY
from app.entities import FuzzyScorer
from app.entities.clustering import match_names
from app.entities.normalise import Vocabulary, load_vocabulary
from app.extract import parsing
from app.extract.parsing import RawClaim, RawExtraction, RawMention, brand_only, to_extraction_result
from tests.fakes.reddit import NOW
from tests.fakes.research import review_source

KEYS = ["battery_life", PRICE_KEY]
LOGGER = "app.extract.parsing"

DROPPED = ["Dell", "Lenovo", "Lenovo laptops", "Samsung phone", "Infinix", "Xiaomi Redmi", "Dell 16GB RAM"]
CASINGS = ["DELL", "dell", "lenovo LAPTOPS", "SaMsUnG Phone"]
MARKED = ["Dell's", "Lenovo’s laptops", "Dell™", "Samsung® phone"]  # a possessive or a trademark sign
KEPT = ["MacBook Pro", "Galaxy S25", "IdeaPad Slim 3", "Dell 15 3530", "Dell Pro", "Lava", "Dell's XPS 13"]


def _page(text: str) -> Source:
    return review_source("https://www.reviews.example/brands", title="A made-up page about laptops", text=text)


def _battery(name: str, quote: str) -> RawClaim:
    return RawClaim(product_mention=name, criterion_key="battery_life", score=8.0, quote=quote, polarity="positive")


def _price(name: str, quote: str, value: str) -> RawClaim:
    return RawClaim(product_mention=name, criterion_key=PRICE_KEY, value=value, quote=quote, polarity="neutral")


def _one(name: str, *, is_product: bool = True, category: str = "laptop") -> ExtractionResult:
    """A page with one sentence about `name`: the model names it and gives one claim, quoted from the page."""
    sentence = f"{name} lasts about nine hours on a charge."
    raw = RawExtraction(mentions=[RawMention(raw_name=name, is_product=is_product)], claims=[_battery(name, sentence)])
    return to_extraction_result(raw, _page(sentence), KEYS, NOW, category=category)


def _lines(caplog: pytest.LogCaptureFixture, start: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == LOGGER and r.getMessage().startswith(start)]


# --- the rule ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", DROPPED + CASINGS + MARKED)
def test_a_brand_with_only_generic_words_is_brand_only(name: str) -> None:
    assert brand_only(name)


def test_the_searchs_own_category_counts_as_generic_words() -> None:
    """ "Air" is an edition word (MacBook Air), so only the category's own name makes these brand only."""
    assert not brand_only("Philips air fryer")
    assert brand_only("Philips air fryer", kinds=["air fryer"])
    assert brand_only("LG Air-Conditioners", kinds=["air conditioner", "air conditioners"])
    assert not brand_only("Philips Air Fryer HD9252", kinds=["air fryer"])  # a model code keeps it
    assert not brand_only("Philips Airfryer", kinds=["air fryer"])  # one word: a series name
    assert not brand_only("Apple Watch", kinds=["analog watch"])  # only the category's whole name counts


@pytest.mark.parametrize("name", [*KEPT, "gaming laptop", "laptops", ""])
def test_a_series_word_a_model_code_or_a_number_keeps_a_name(name: str) -> None:
    assert not brand_only(name)


def test_the_counting_lists_decide_what_a_brand_is() -> None:
    known = load_vocabulary()
    assert "infinix" in known.brands  # so "Infinix" alone is brand only, and dropped
    without = Vocabulary(brands=known.brands - {"infinix"}, generic_words=known.generic_words)
    assert not brand_only("Infinix", without)
    assert "lava" not in known.brands  # a brand the lists don't know: "Lava" alone stays a product
    assert not brand_only("Lava")


# --- through the parser -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", DROPPED + CASINGS + MARKED)
def test_a_brand_only_name_and_its_claims_are_dropped(name: str) -> None:
    result = _one(name)
    assert result.mentions == []
    assert result.claims == []


def test_a_brand_and_the_searchs_category_name_is_dropped() -> None:
    assert _one("Philips air fryer", category="air_fryer").mentions == []
    assert _one("LG air conditioner", category="air_conditioner").claims == []
    kept = _one("Philips air fryer", category="laptop")  # another search's kind: "air" keeps it, as before
    assert [m.raw_name for m in kept.mentions] == ["Philips air fryer"]


@pytest.mark.parametrize("name", KEPT)
def test_a_name_with_a_series_or_model_word_stays_a_product(name: str) -> None:
    result = _one(name)
    assert [(m.raw_name, m.is_product) for m in result.mentions] == [(name, True)]
    assert [c.product_mention for c in result.claims] == [name]


def test_a_brand_the_lists_dont_know_stays_a_product(monkeypatch: pytest.MonkeyPatch) -> None:
    known = load_vocabulary()
    without = Vocabulary(brands=known.brands - {"infinix"}, generic_words=known.generic_words)
    monkeypatch.setattr(parsing, "load_vocabulary", lambda: without)
    result = _one("Infinix")
    assert [m.raw_name for m in result.mentions] == ["Infinix"]
    assert [c.product_mention for c in result.claims] == ["Infinix"]


def test_a_brand_the_model_called_a_kind_of_product_is_dropped_too() -> None:
    """S3-19: "Dell" was marked a kind of product, and still became prd_dell in "Can't tell"."""
    result = _one("Dell", is_product=False)
    assert result.mentions == []
    assert result.claims == []


def test_the_models_brand_field_does_not_make_a_name_brand_only() -> None:
    """Code decides what a brand is: a model calling "Kindle" a brand doesn't drop the Kindle."""
    sentence = "Kindle lasts about nine hours on a charge."
    raw = RawExtraction(
        mentions=[RawMention(raw_name="Kindle", is_product=True, brand="Kindle")], claims=[_battery("Kindle", sentence)]
    )
    result = to_extraction_result(raw, _page(sentence), KEYS, NOW, category="laptop")
    assert [m.raw_name for m in result.mentions] == ["Kindle"]
    assert len(result.claims) == 1


def test_no_product_is_made_and_its_claims_attach_to_nothing() -> None:
    text = (
        "Dell and Lenovo both sell laptops for coding. Dell is a safe brand for students. "
        "The Dell XPS 13 lasts about nine hours on a charge."
    )
    raw = RawExtraction(
        mentions=[
            RawMention(raw_name="Dell", is_product=False),
            RawMention(raw_name="Lenovo", is_product=True),
            RawMention(raw_name="Dell XPS 13", is_product=True),
        ],
        claims=[
            _battery("Dell", "Dell is a safe brand for students."),
            _battery("Dell XPS 13", "The Dell XPS 13 lasts about nine hours on a charge."),
        ],
    )
    result = to_extraction_result(raw, _page(text), KEYS, NOW, category="laptop")
    assert [c.product_mention for c in result.claims] == ["Dell XPS 13"]
    match = match_names(MatchInput(mentions=result.mentions), FuzzyScorer())
    assert [p.name for p in match.products] == ["Dell XPS 13"]
    assert set(match.alias_map) == {"Dell XPS 13"}  # no name for "Dell" or "Lenovo" claims to attach through


def test_a_brand_only_price_claim_never_takes_the_room_of_a_products_price() -> None:
    text = "Dell laptops start at ₹40,000. The Dell XPS 13 costs ₹99,990."
    raw = RawExtraction(
        mentions=[RawMention(raw_name="Dell XPS 13", is_product=True)],
        claims=[
            _price("Dell laptops", "Dell laptops start at ₹40,000", "₹40,000"),
            _price("Dell XPS 13", "The Dell XPS 13 costs ₹99,990", "₹99,990"),
        ],
    )
    result = to_extraction_result(raw, _page(text), KEYS, NOW, category="laptop", room=1, others=0, first=(PRICE_KEY,))
    assert [(c.criterion_key, c.product_mention) for c in result.claims] == [(PRICE_KEY, "Dell XPS 13")]


# --- the counts -------------------------------------------------------------------------------------------------------


def test_the_drop_is_counted_as_brand_only_with_no_words(caplog: pytest.LogCaptureFixture) -> None:
    text = "Dell lasts about nine hours on a charge. DELL costs ₹45,000."
    raw = RawExtraction(
        mentions=[RawMention(raw_name="Dell", is_product=True), RawMention(raw_name="DELL", is_product=False)],
        claims=[
            _battery("Dell", "Dell lasts about nine hours on a charge."),
            _price("DELL", "DELL costs ₹45,000", "₹45,000"),
        ],
    )
    page = _page(text)
    with caplog.at_level(logging.INFO, logger=LOGGER):
        to_extraction_result(raw, page, KEYS, NOW, category="laptop")
    (claims,) = _lines(caplog, "extract: claims dropped")
    assert claims.args == (page.type.value, {"brand_only": 2})  # the shape the live funnel reads
    (prices,) = _lines(caplog, "extract: price claims dropped")
    assert prices.args == (page.type.value, {"brand_only": 1})
    (names,) = _lines(caplog, "extract: names dropped")
    assert names.args == (page.type.value, {"brand_only": 1})  # two spellings of one name count once
    logged = " ".join(r.getMessage() for r in caplog.records).casefold()
    for words in ("dell", "nine hours", "45,000"):
        assert words not in logged


def test_nothing_is_counted_when_no_name_is_brand_only(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger=LOGGER):
        _one("Dell XPS 13")
    assert _lines(caplog, "extract: names dropped") == []
    assert _lines(caplog, "extract: claims dropped") == []


# --- unchanged --------------------------------------------------------------------------------------------------------


def test_generic_words_with_no_brand_stay_a_kind_of_product() -> None:
    result = _one("gaming laptop", is_product=False)
    assert [(m.raw_name, m.is_product) for m in result.mentions] == [("gaming laptop", False)]
    assert [c.is_product_type for c in result.claims] == [True]


def test_a_brand_the_page_never_names_is_dropped_as_before(caplog: pytest.LogCaptureFixture) -> None:
    """The source check comes first: a name the page doesn't hold is dropped for "product", not counted as a brand."""
    sentence = "The Dell XPS 13 lasts about nine hours on a charge."
    raw = RawExtraction(
        mentions=[RawMention(raw_name="Lenovo", is_product=True)], claims=[_battery("Lenovo", sentence)]
    )
    with caplog.at_level(logging.INFO, logger=LOGGER):
        result = to_extraction_result(raw, _page(sentence), KEYS, NOW, category="laptop")
    assert result.mentions == []
    assert result.claims == []
    (claims,) = _lines(caplog, "extract: claims dropped")
    assert claims.args == (_page(sentence).type.value, {"product": 1})
    assert _lines(caplog, "extract: names dropped") == []
