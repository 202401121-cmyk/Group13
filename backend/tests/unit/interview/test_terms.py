"""Unfamiliar terms explained on request (US-07): a question about a technical criterion carries one term, explained
in at most 40 words from the saved criterion's own definition. Code only: no LLM is asked (criterion 4)."""

from __future__ import annotations

import yaml  # type: ignore[import-untyped]

from app.contracts import MAX_EXPLANATION_WORDS, Criterion
from app.criteria.fallback import DEFAULT_FALLBACK_PATH
from app.interview.questions import question_for, technical_words, term_for


def criterion(key: str, label: str, definition: str) -> Criterion:
    return Criterion(key=key, label=label, definition=definition, default_weight=5)


REFRESH = criterion(
    "refresh_rate",
    "Refresh rate",
    "How many times a second the screen redraws, in hertz; a higher rate makes scrolling and games look smoother.",
)


def test_a_technical_criterion_gets_a_term() -> None:
    [term] = question_for(REFRESH).terms
    assert (term.term, term.criterion_key) == ("Refresh rate", "refresh_rate")
    assert term.explanation == REFRESH.definition


def test_a_key_word_counts_too() -> None:
    ram = criterion("ram_gb", "Memory size", "How much memory it has, so many apps and tabs can stay open at once.")
    assert [t.term for t in question_for(ram).terms] == ["Memory size"]


def test_a_plain_criterion_gets_none() -> None:
    plain = criterion("value_for_money", "Value for money", "How much you get for the price you pay.")
    durable = criterion("build_durability", "Build durability", "How well the body holds up to daily use.")
    assert question_for(plain).terms == [] and question_for(durable).terms == []


def test_whole_words_only() -> None:
    program = criterion("programming_tools", "Programming tools", "Which coding tools it runs well, for your work.")
    assert question_for(program).terms == []  # "ram" inside "programming" is not the word RAM


def test_case_and_spellings_do_not_matter() -> None:
    anc = criterion("noise_cancelling", "ANC strength", "How well it blocks outside noise, so music stays clear.")
    assert question_for(anc).terms != []
    assert term_for(criterion("cpu", "cpu speed", "How fast the processor works, so apps open quickly.")) is not None


def test_a_41_word_definition_falls_back_to_its_first_sentence() -> None:
    first = "How bright the screen gets, in nits, so you can read it outdoors."
    long = first + " " + " ".join(["More", *(["more"] * 39)]) + "."  # a second sentence, starting with a capital
    assert len(long.split()) > MAX_EXPLANATION_WORDS
    bright = criterion("brightness_nits", "Brightness (nits)", long)
    [term] = question_for(bright).terms
    assert term.explanation == first


def test_no_short_sentence_means_no_term() -> None:
    long = " ".join(["word"] * 45) + "."
    assert question_for(criterion("gpu_power", "GPU power", long)).terms == []


def test_the_term_is_at_most_40_characters_and_never_cut_mid_word() -> None:
    label = "Refresh rate of the main display for fast games and smooth scrolling"[:60]
    [term] = question_for(criterion("refresh_rate", label, REFRESH.definition)).terms
    assert len(term.term) <= 40 and label.startswith(term.term)
    assert label[len(term.term)] == " "  # the next character starts a new word
    ois = criterion("ois_night", "Optical image stabilisation for night shots", REFRESH.definition)
    assert [t.term for t in question_for(ois).terms] == ["Optical image stabilisation for night"]


def test_a_unit_glued_to_a_number_is_found() -> None:
    battery = criterion(
        "battery_life", "Battery (5000mAh)", "How long it lasts on one charge, so it gets you through a day."
    )
    assert [t.term for t in question_for(battery).terms] == ["Battery (5000mAh)"]
    rated = criterion(
        "water_proofing", "IP68 sealing", "How well it keeps out dust and water, so a spill or rain is no worry."
    )
    assert question_for(rated).terms != []


def test_an_abbreviation_does_not_end_the_first_sentence() -> None:
    first = "Measured in mAh, e.g. a bigger number lasts longer through a busy day."
    long = first + " " + " ".join(["Extra"] * 30) + "."  # over 40 words, inside the 300-character limit
    [term] = question_for(criterion("battery_mah", "Battery size", long)).terms
    assert term.explanation == first


def test_every_fixed_criterion_with_a_term_is_valid() -> None:
    sets = yaml.safe_load(DEFAULT_FALLBACK_PATH.read_text(encoding="utf-8"))
    groups = [*sets["families"].values(), *sets.get("categories", {}).values()]
    found = []
    for group in groups:
        for raw in group["criteria"]:
            c = criterion(raw["key"], raw["label"], raw["definition"])
            for term in question_for(c).terms:
                assert len(term.explanation.split()) <= MAX_EXPLANATION_WORDS
                found.append(term.term)
    assert "Processor speed" in found and "Water resistance" in found
    assert "Value for money" not in found


def test_the_word_list_comes_from_config() -> None:
    words = technical_words()
    assert {"anc", "ois", "refresh rate", "hz", "nits", "mah", "ip rating", "codec", "latency"} <= words
    assert {"gpu", "cpu", "processor", "ram", "ssd"} <= words


def test_terms_need_no_llm() -> None:
    """question_for takes only the saved criterion: there is no client to call."""
    assert question_for(REFRESH).terms[0].explanation == REFRESH.definition
