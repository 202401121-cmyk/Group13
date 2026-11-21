"""US-20: when the LLM fails, a template built from the same numbers is shown, in plain words (Grade 9 or under)."""

from __future__ import annotations

import pytest

from app.contracts import RankedProduct, ValueState
from app.explain.facts import allowed_numbers, facts_for, label
from app.explain.numbers import unmatched
from app.explain.readability import GRADE_LIMIT, grade
from app.explain.templates import COMMUNITY_ONLY, strength_sentence, template

from .build import contribution, every_claim, fourth, kestrel, nova, orion, ranked


def _text(product: RankedProduct) -> str:
    explanation = template(facts_for(product, every_claim()))
    return " ".join([*(s.text for s in explanation.sentences), explanation.trade_off or ""])


def test_the_template_states_the_top_3_and_the_trade_off_from_codes_numbers() -> None:
    explanation = template(facts_for(nova(), every_claim()))
    assert [s.text for s in explanation.sentences] == [
        "It ranks #1 with a score of 73 out of 100.",
        "Battery life adds 24 points, with a score of 8 out of 10.",
        "Cpu performance adds 19 points, with a score of 7.5 out of 10.",
        "Keyboard comfort adds 18 points, with a score of 9 out of 10.",
        "We could not check its ports.",
    ]
    assert explanation.trade_off == "Its weak point is weight, with a score of 4 out of 10."
    assert explanation.unverified == ["ports"]
    assert explanation.method == "template"
    assert explanation.product_id == nova().product_id


def test_a_compared_score_says_so_and_a_dispute_is_named() -> None:
    explanation = template(facts_for(kestrel(), every_claim()))
    texts = [s.text for s in explanation.sentences]
    assert "Battery life adds 21 points, with a score of 7 out of 10, but sources disagree." in texts
    assert (
        "Cpu performance adds 20 points, with a score of 8 out of 10 compared with the other products we found."
        in texts
    )


def test_an_estimate_says_it_is_one() -> None:
    product = ranked(
        "Guess Book 1",
        1,
        contribution("battery_life", 6.0, 0.6, ValueState.ESTIMATED, "estimated"),
        contribution("weight", 7.0, 0.4),
    )
    texts = [s.text for s in template(facts_for(product, [])).sentences]
    assert "Battery life adds 36 points, but its score of 6 out of 10 is an estimate from similar products." in texts
    assert texts[-1] == "We could not check its battery life."


def test_a_disputed_trade_off_says_sources_disagree() -> None:
    product = ranked(
        "Split Book 2",
        1,
        contribution("battery_life", 9.0, 0.4, ValueState.DISPUTED),
        contribution("weight", 6.0, 0.3),
        contribution("ports", 7.0, 0.3),
    )
    assert template(facts_for(product, [])).trade_off == "Sources disagree on its battery life."


def test_several_unverified_criteria_are_all_named() -> None:
    product = ranked(
        "Named Book 3",
        1,
        contribution("weight", 7.0, 1.0),
        contribution("ports", None, 0.0, ValueState.UNKNOWN),
        contribution("webcam", None, 0.0, ValueState.UNKNOWN),
        contribution("speakers", None, 0.0, ValueState.UNKNOWN),
    )
    explanation = template(facts_for(product, []))
    assert explanation.sentences[-1].text == "We could not check its ports, speakers or webcam."
    assert explanation.unverified == ["ports", "speakers", "webcam"]


def test_many_long_unverified_names_split_so_no_sentence_breaks_the_contract() -> None:
    names = [f"criterion_{'x' * 50}_{n}" for n in range(9)]
    product = ranked(
        "Wide Book 7",
        1,
        contribution("weight", 7.0, 1.0),
        *(contribution(n, None, 0.0, ValueState.UNKNOWN) for n in names),
    )
    explanation = template(facts_for(product, []))  # 400 characters a sentence at most: it would raise otherwise
    unverified = [s.text for s in explanation.sentences if "could not check" in s.text]
    assert len(unverified) == 2
    assert unverified[1].startswith("We also could not check its ")
    assert all(label(n) in " ".join(unverified) for n in names)
    assert all(len(s.text) <= 400 for s in explanation.sentences)


def test_a_tie_with_another_product_is_said() -> None:
    tied = RankedProduct.model_validate({**kestrel().model_dump(), "tied_with": [nova().product_id]})
    texts = [s.text for s in template(facts_for(tied, every_claim())).sentences]
    assert texts[:2] == [
        "It ranks #2 with a score of 75 out of 100.",
        "It is too close to call with a product next to it.",
    ]


def test_one_point_is_singular() -> None:
    product = ranked("Small Book 4", 1, contribution("weight", 2.0, 0.05), contribution("ports", 9.0, 0.95))
    texts = [s.text for s in template(facts_for(product, [])).sentences]
    assert "Weight adds 1 point, with a score of 2 out of 10." in texts


def test_an_unscored_product_shows_no_number_and_names_every_criterion() -> None:
    product = ranked(
        "Blank Book 5",
        2,
        contribution("battery_life", None, 0.0, ValueState.UNKNOWN),
        contribution("weight", None, 0.0, ValueState.UNKNOWN),
        scored=False,
    )
    explanation = template(facts_for(product, []))
    assert [s.text for s in explanation.sentences] == ["Listed #2. We found no scores to rank it on."]
    assert explanation.unverified == ["battery_life", "weight"]
    assert explanation.trade_off is None


@pytest.mark.parametrize("product", [nova(), kestrel(), orion(), fourth()], ids=lambda p: p.product_id)
def test_every_number_in_a_template_is_codes_own(product: RankedProduct) -> None:
    facts = facts_for(product, every_claim())
    assert unmatched(_text(product), allowed_numbers(facts)) == []


@pytest.mark.parametrize("product", [nova(), kestrel(), orion(), fourth()], ids=lambda p: p.product_id)
def test_every_template_reads_at_grade_9_or_under(product: RankedProduct) -> None:
    assert grade(_text(product)) <= GRADE_LIMIT


def test_a_template_with_long_criterion_names_still_reads_at_grade_9_or_under() -> None:
    long_names = [
        "build_durability",
        "display_quality",
        "value_for_money",
        "keyboard_comfort",
        "battery_life",
        "thermal_performance",
        "speaker_quality",
        "webcam_quality",
        "operating_system_compatibility",
        "customer_service_reliability",
    ]
    product = ranked(
        "Long Book 6",
        1,
        *(contribution(k, 7.0, 0.1) for k in long_names[:3]),
        *(contribution(k, None, 0.0, ValueState.UNKNOWN) for k in long_names[3:]),
    )
    explanation = template(facts_for(product, []))
    assert grade(" ".join([*(s.text for s in explanation.sentences), explanation.trade_off or ""])) <= GRADE_LIMIT


# --- D-91: the personal part is code's, said for this shopper -------------------------------------------------------


def test_a_strength_that_is_one_of_the_shoppers_priorities_says_so() -> None:
    explanation = template(facts_for(nova(), every_claim()), priorities=["battery_life", "keyboard_comfort"])
    assert [s.text for s in explanation.sentences][1:4] == [
        "Battery life, one of your priorities, adds 24 points, with a score of 8 out of 10.",
        "Cpu performance adds 19 points, with a score of 7.5 out of 10.",
        "Keyboard comfort, one of your priorities, adds 18 points, with a score of 9 out of 10.",
    ]


def test_the_priority_mention_never_takes_a_sentence_past_grade_9() -> None:
    """DECIDED 4: an estimated priority's sentence would read at 9.3 with the mention, so it is said without it."""
    product = ranked("Made Up One", 1, contribution("battery_life", 6.0, 0.6, ValueState.ESTIMATED, "estimated"))
    (_, sentence, *_) = template(facts_for(product, []), priorities=["battery_life"]).sentences
    assert (
        sentence.text
        == "Battery life adds 36 points, but its score of 6 out of 10 is an estimate from similar products."
    )
    assert grade(sentence.text) <= GRADE_LIMIT


@pytest.mark.parametrize("state", [ValueState.KNOWN, ValueState.DISPUTED, ValueState.ESTIMATED])
@pytest.mark.parametrize("basis", ["opinion", "compared", "estimated"])
@pytest.mark.parametrize("key", ["battery_life", "keyboard_comfort", "build_durability", "value_for_money"])
def test_every_priority_strength_reads_at_grade_9_or_under_or_as_before(
    key: str, basis: str, state: ValueState
) -> None:
    product = ranked("Made Up One", 1, contribution(key, 7.0, 0.3, state, basis))
    (fact,) = facts_for(product, []).top
    mine, plain = strength_sentence(fact, priority=True), strength_sentence(fact)
    assert mine == plain or grade(mine) <= GRADE_LIMIT
    assert grade(mine) <= max(GRADE_LIMIT, grade(plain))  # never harder than code's sentence was before


def test_a_product_with_only_community_evidence_gets_the_community_sentence() -> None:
    explanation = template(facts_for(nova(), every_claim()), community_only=True)
    assert explanation.sentences[-1].text == COMMUNITY_ONLY == "Community posts discuss this; see the sources."
    assert explanation.method == "template"


@pytest.mark.parametrize("product", [nova(), kestrel(), orion(), fourth()])
def test_the_personal_template_keeps_codes_numbers_and_reads_at_grade_9_or_under(product: RankedProduct) -> None:
    facts = facts_for(product, every_claim())
    explanation = template(
        facts, priorities=["battery_life", "cpu_performance", "keyboard_comfort"], community_only=True
    )
    for sentence in explanation.sentences:
        assert not unmatched(sentence.text, allowed_numbers(facts)), sentence.text
        assert grade(sentence.text) <= GRADE_LIMIT, sentence.text
