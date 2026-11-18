"""US-07 follow-up (S4-22, P-25; S3-19 failures S3-F1 and S3-F4): every question with a technical term gets "What does
this mean?", and its explanation is in plain words, checked in code. No LLM is asked. Made-up criteria only.
"""

from __future__ import annotations

import logging
import re

import pytest

from app.contracts import MAX_EXPLANATION_WORDS, Criterion
from app.explain.readability import GRADE_LIMIT, grade
from app.interview.questions import question_for, term_for
from app.interview.terms import (
    is_plain,
    is_plain_definition,
    is_technical,
    jargon_in,
    own_terms,
    plain_words,
    terms,
    words_of,
)


def criterion(key: str, label: str, definition: str) -> Criterion:
    return Criterion(key=key, label=label, definition=definition, default_weight=5)


PLAIN_SENSOR = "How much light the camera's sensor catches, so photos at night look clear and not grainy."


@pytest.mark.parametrize(
    "label",
    [
        "Sensor Size",
        "Maximum Aperture",
        "Low-Light SNR",
        "Dynamic Range",
        "Resolution (MP)",
        "35mm Equivalent Focal Length",
        "Keyboard Travel",
    ],
)
def test_each_label_s3_19_found_without_a_link_now_gets_one(label: str) -> None:
    c = criterion(re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_"), label, PLAIN_SENSOR)
    assert is_technical(c)
    assert [t.term for t in question_for(c).terms] == [label]


@pytest.mark.parametrize(
    ("key", "label"), [("price", "Price"), ("value_for_money", "Value for money"), ("battery_life", "Battery life")]
)
def test_plain_labels_get_no_link(key: str, label: str) -> None:
    c = criterion(key, label, "How long it lasts or how much it costs, for the way you will use it every day.")
    assert not is_technical(c)
    assert question_for(c).terms == []


def test_a_label_with_a_word_not_in_the_plain_list_is_technical_by_default() -> None:
    c = criterion("motion_handling", "Motion handling", "How smooth fast scenes look, such as sports and games.")
    assert is_technical(c)
    assert question_for(c).terms != []


def test_a_number_in_a_label_is_not_a_word() -> None:
    c = criterion("warranty_years", "Warranty (2 years)", "How long the maker fixes it for free if it breaks.")
    assert "years" not in plain_words()
    assert is_technical(c)  # "years" is not a plain word yet; "2" alone would never make it so
    assert not is_technical(criterion("price", "Price 2", "How much it costs you, compared with others."))


@pytest.mark.parametrize(
    "jargon",
    [
        "Total volatile memory in gigabytes, so more apps stay open at once.",
        "The sequential read throughput in megabytes per second, so files open fast.",
        "The processor's single-core clock frequency in gigahertz, so apps open quickly.",
    ],
)
def test_a_definition_that_explains_with_more_jargon_is_not_plain(jargon: str) -> None:
    ram = criterion("ram", "RAM", jargon)
    assert jargon_in(jargon, own_terms(ram)) != []
    assert not is_plain(jargon, own_terms(ram))
    assert not is_plain_definition(ram)


def test_a_hard_to_read_definition_is_not_plain() -> None:
    hard = (
        "Characterises computational responsiveness, operational efficiency and multitasking capability considerably."
    )
    assert grade(hard) > GRADE_LIMIT
    assert not is_plain(hard, ())


def test_the_term_itself_is_allowed_in_its_own_explanation() -> None:
    ram = criterion("ram_gb", "RAM", "How much RAM it has, so many apps and tabs stay open without slowing down.")
    assert is_plain_definition(ram)
    [term] = question_for(ram).terms
    assert term.explanation == ram.definition  # a plain definition is shown as it is


def test_a_term_written_another_way_counts_as_the_term() -> None:
    anc = criterion("anc", "Active noise cancellation", "How well active noise cancellation hides the hum of a bus.")
    assert is_plain_definition(anc)  # "noise cancellation" is inside the label's own term


def test_a_word_only_in_the_key_is_not_the_terms_own() -> None:
    """The shopper reads the label, never the key: "Storage speed" is not explained with "SSD throughput"."""
    c = criterion("ssd_throughput", "Storage speed", "How high the SSD throughput is, so big files open fast.")
    assert not is_plain_definition(c)
    [term] = question_for(c).terms
    assert term.explanation == next(t.plain for t in terms() if t.word == "ssd")  # the key's word, reviewed


def test_a_plain_fragment_of_a_short_definition_is_never_shown_alone() -> None:
    ram = criterion("ram", "RAM", "Measured in GB. More volatile memory lets more apps stay open.")
    [term] = question_for(ram).terms
    assert term.explanation != "Measured in GB."  # P-25 item 4: the plain definition, else the word's reviewed text
    assert term.explanation == next(t.plain for t in terms() if t.word == "ram")


@pytest.mark.parametrize(
    "text",
    [
        "How well the sensors and lenses handle low light, for clear night photos.",
        "How many SSDs it can hold, so your files and games always fit.",
        "How well the processors keep apps quick when many are open at once.",
    ],
)
def test_a_plural_of_a_listed_word_is_found(text: str) -> None:
    assert jargon_in(text, ()) != []


@pytest.mark.parametrize("label", ["IP65 rating", "IP66 dust and water", "Bluetooth range (MHz)"])
def test_more_spellings_are_found(label: str) -> None:
    c = criterion(re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_"), label, PLAIN_SENSOR)
    assert own_terms(c) != []


def test_the_words_plain_explanation_is_used_when_the_definition_is_not_plain() -> None:
    ram = criterion("ram", "RAM", "Total volatile memory in gigabytes, so more apps stay open at once.")
    [term] = question_for(ram).terms
    ram_plain = next(t.plain for t in terms() if t.word == "ram")
    assert ram_plain is not None and term.explanation == ram_plain
    assert term.term == "RAM"  # the shown term is still the label


def test_the_first_listed_word_in_the_label_gives_the_plain_explanation() -> None:
    both = criterion("ram_and_ssd", "RAM and SSD", "The volatile memory and sequential read throughput of the machine.")
    ram_plain = next(t.plain for t in terms() if t.word == "ram")
    assert term_for(both) is not None and term_for(both).explanation == ram_plain  # type: ignore[union-attr]


def test_with_no_plain_explanation_the_first_sentence_is_used_and_logged(caplog: pytest.LogCaptureFixture) -> None:
    first = "How smooth fast scenes look, helped by a high refresh rate and a fast panel."
    motion = criterion("motion_handling", "Motion handling", f"{first} Sports look better.")
    with caplog.at_level(logging.INFO, logger="app.interview.questions"):
        [term] = question_for(motion).terms
    assert term.explanation == first
    assert "motion_handling" in caplog.text and "not plain" in caplog.text


def test_every_plain_explanation_in_the_config_is_plain_and_at_most_40_words() -> None:
    listed = terms()
    assert all(t.plain for t in listed), [t.word for t in listed if not t.plain]  # every listed word has one (S4-22)
    for t in listed:
        assert t.plain is not None
        assert 0 < len(t.plain.split()) <= MAX_EXPLANATION_WORDS, t.word
        assert grade(t.plain) <= GRADE_LIMIT, (t.word, grade(t.plain))
        assert jargon_in(t.plain, (t,)) == [], (t.word, jargon_in(t.plain, (t,)))


def test_the_words_s3_19_found_are_listed() -> None:
    forms = {form for t in terms() for form in t.forms}
    found = {"sensor", "aperture", "snr", "dynamic range", "megapixels", "mp", "focal length", "keyboard travel"}
    jargon = {"volatile memory", "throughput", "clock frequency", "clock speed", "ghz", "gigahertz", "sequential read"}
    assert {" ".join(words_of(w)) for w in found | jargon} <= forms  # stored as matched: plurals folded


def test_the_plain_word_list_comes_from_config() -> None:
    words = plain_words()
    assert {"price", "budget", "value", "money", "brand", "warranty", "durability", "build", "quality"} <= words
    assert {"battery", "life", "of", "for", "and", "the"} <= words
    assert "sensor" not in words and "ram" not in words


def test_every_explanation_is_at_most_40_words() -> None:
    long = " ".join(["Long"] * 45) + "."
    gpu = criterion("gpu_power", "GPU power", long)
    [term] = question_for(gpu).terms
    assert len(term.explanation.split()) <= MAX_EXPLANATION_WORDS  # gpu's plain explanation, never the long text
    no_word = criterion("motion_handling", "Motion handling", long)
    assert question_for(no_word).terms == []  # no listed word and no short sentence: no link rather than 45 words


def test_opening_an_explanation_needs_no_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Criterion 4: the explanation is in the question already; building it never reaches an LLM."""

    def no_llm(*_: object, **__: object) -> None:
        raise AssertionError("an LLM was asked")

    monkeypatch.setattr("app.llm.client.WrapperLLMClient.complete", no_llm)
    ram = criterion("ram", "RAM", "Total volatile memory in gigabytes, so more apps stay open at once.")
    assert question_for(ram).terms[0].explanation
