"""US-20, DECIDED 1: code picks each product's top 3, its one trade-off and what it could not verify."""

from __future__ import annotations

from app.contracts import RankedProduct, ValueState
from app.explain.facts import WEIGHT_LINE, allowed_numbers, facts_for, label, linked_claims, shown_score, whole
from app.explain.numbers import numbers_in

from .build import KESTREL, NOVA, ORION, claim, contribution, every_claim, kestrel, nova, orion, ranked


def test_the_top_3_are_the_largest_contributions() -> None:
    facts = facts_for(nova(), every_claim())
    assert [f.key for f in facts.top] == ["battery_life", "cpu_performance", "keyboard_comfort"]
    assert [f.points for f in facts.top] == [24, 19, 18]  # contribution x 10: points of the 0-100 score
    assert [f.score for f in facts.top] == [8.0, 7.5, 9.0]


def test_a_tie_in_contribution_goes_by_key() -> None:
    product = ranked(
        "Tie Book 1",
        1,
        contribution("zeta", 5.0, 0.25),
        contribution("alpha", 5.0, 0.25),
        contribution("mid", 5.0, 0.25),
        contribution("beta", 5.0, 0.25),
    )
    assert [f.key for f in facts_for(product, []).top] == ["alpha", "beta", "mid"]


def test_a_criterion_with_no_score_is_never_in_the_top_3() -> None:
    product = ranked(
        "Gap Book 2",
        1,
        contribution("battery_life", None, 0.0, ValueState.UNKNOWN),
        contribution("weight", 6.0, 1.0),
    )
    facts = facts_for(product, [])
    assert [f.key for f in facts.top] == ["weight"]
    assert facts.unverified == ("battery_life",)


def test_the_trade_off_is_the_weakest_criterion() -> None:
    trade = facts_for(nova(), every_claim()).trade_off
    assert trade is not None
    assert (trade.fact.key, trade.kind, trade.fact.score) == ("weight", "weakest", 4.0)
    assert facts_for(orion(), every_claim()).trade_off.fact.key == "weight"  # type: ignore[union-attr]


def test_a_weak_point_never_scores_higher_than_a_strength() -> None:
    """The top 3 can be its lowest scores: then one of them is the weak point, never a criterion scoring higher."""
    product = ranked(
        "Even Book 1",
        1,
        contribution("a", 6.0, 0.3),
        contribution("b", 6.0, 0.3),
        contribution("c", 6.0, 0.3),
        contribution("d", 9.0, 0.1),
    )
    trade = facts_for(product, []).trade_off
    assert trade is not None and (trade.fact.key, trade.fact.score, trade.kind) == ("a", 6.0, "weakest")


def test_on_a_tie_the_weak_point_is_one_outside_the_top_3_then_the_larger_weight() -> None:
    product = ranked(
        "Tie Book 2",
        1,
        contribution("a", 7.0, 0.3),
        contribution("b", 8.0, 0.25),
        contribution("c", 9.0, 0.2),
        contribution("d", 7.0, 0.1),
        contribution("e", 7.0, 0.15),
    )
    trade = facts_for(product, []).trade_off
    assert trade is not None and trade.fact.key == "e"  # a is a strength; e outweighs d


def test_a_weight_of_0_is_never_the_trade_off() -> None:
    product = ranked(
        "Zero Book 3", 1, contribution("a", 8.0, 0.6), contribution("b", 7.0, 0.4), contribution("c", 1.0, 0.0)
    )
    facts = facts_for(product, [])
    assert facts.trade_off is not None and facts.trade_off.fact.key == "b"
    assert [f.key for f in facts.top] == ["a", "b"]  # nothing the shopper doesn't weigh is a strength


def test_a_tie_with_another_product_is_kept() -> None:
    product = RankedProduct.model_validate({**nova().model_dump(), "tied_with": [kestrel().product_id]})
    assert facts_for(product, []).tied
    assert not facts_for(nova(), []).tied


def test_a_criterion_under_weight_2_is_never_the_trade_off() -> None:
    """Weight 2 or more on the shopper's 0-10 scale, read from the shares: 0.2 of the largest share."""
    product = ranked(
        "Line Book 3",
        1,
        contribution("a", 8.0, 0.4),
        contribution("b", 8.0, 0.3),
        contribution("c", 8.0, 0.15),
        contribution("d", 7.0, 0.09),
        contribution("e", 1.0, 0.06),  # 0.06 < 0.2 x 0.4: the shopper barely cares
    )
    assert WEIGHT_LINE == 0.2
    trade = facts_for(product, []).trade_off
    assert trade is not None and trade.fact.key == "d"


def test_a_disputed_value_is_the_trade_off_when_nothing_is_left_outside_the_top_3() -> None:
    product = ranked(
        "Split Book 4",
        1,
        contribution("battery_life", 9.0, 0.4, ValueState.DISPUTED),
        contribution("weight", 6.0, 0.3),
        contribution("ports", 7.0, 0.3),
    )
    trade = facts_for(product, []).trade_off
    assert trade is not None and (trade.fact.key, trade.kind) == ("battery_life", "disputed")


def test_with_no_dispute_the_weakest_of_the_top_3_is_the_trade_off() -> None:
    product = ranked(
        "Three Book 5", 1, contribution("a", 9.0, 0.4), contribution("b", 5.0, 0.3), contribution("c", 7.0, 0.3)
    )
    trade = facts_for(product, []).trade_off
    assert trade is not None and (trade.fact.key, trade.kind) == ("b", "weakest")


def test_an_estimate_is_never_the_trade_off_and_with_nothing_else_there_is_none() -> None:
    product = ranked("Guess Book 6", 1, contribution("a", 3.0, 1.0, ValueState.ESTIMATED, "estimated"))
    facts = facts_for(product, [])
    assert facts.trade_off is None
    assert facts.unverified == ("a",)


def test_unknown_and_estimated_criteria_are_named_by_weight() -> None:
    product = ranked(
        "Named Book 7",
        1,
        contribution("weight", 7.0, 0.5),
        contribution("ports", 6.0, 0.1, ValueState.ESTIMATED, "estimated"),
        contribution("webcam", None, 0.0, ValueState.UNKNOWN),
        contribution("speakers", 5.0, 0.4, ValueState.ESTIMATED, "estimated"),
    )
    assert facts_for(product, []).unverified == ("speakers", "ports", "webcam")


def test_claims_link_to_their_product_by_the_match_stages_id_rule() -> None:
    claims = [
        claim(NOVA, "battery_life", 1),
        claim("nova book 14", "battery_life", 2),  # the same name, spelled another way: the same id
        claim(KESTREL, "battery_life", 3),  # another product's
        claim(NOVA, "battery_life", 4, product_type=True),  # a kind of product, not this one
        claim(NOVA, None, 5, value="a fine pick"),  # a general opinion: no criterion to back
    ]
    linked = linked_claims(nova().product_id, claims)
    assert linked == {"battery_life": tuple(sorted((claims[0].id, claims[1].id)))}


def test_each_fact_cites_at_most_3_claims_in_a_fixed_order() -> None:
    claims = [claim(NOVA, "battery_life", n) for n in range(5)]
    battery = facts_for(nova(), claims).top[0]
    assert battery.claim_ids == tuple(sorted(c.id for c in claims)[:3])
    assert facts_for(nova(), list(reversed(claims))).top[0].claim_ids == battery.claim_ids


def test_a_product_with_no_linked_claim_cites_nothing() -> None:
    facts = facts_for(nova(), [claim(ORION, "battery_life")])
    assert all(f.claim_ids == () for f in facts.top)
    assert facts.claim_ids() == ()


def test_how_each_score_was_made_is_kept() -> None:
    facts = facts_for(kestrel(), every_claim())
    by_key = {f.key: f for f in facts.top}
    assert by_key["cpu_performance"].basis == "compared"
    assert by_key["battery_life"].state is ValueState.DISPUTED


def test_a_score_shows_one_decimal_at_most() -> None:
    assert shown_score(8.0) == "8"
    assert shown_score(7.5) == "7.5"
    assert shown_score(7.26) == "7.3"
    assert shown_score(7.25) == "7.3"  # half up, as people round: round() would give 7.2
    assert shown_score(10.0) == "10"
    assert whole(72.5) == 73
    assert whole(22.5) == 23
    assert label("battery_life") == "battery life"
    assert label("wi-fi") == "wi fi"


def test_the_allowed_numbers_are_codes_own() -> None:
    facts = facts_for(nova(), every_claim())
    allowed = allowed_numbers(facts)
    for number in (1, 73, 24, 19, 18, 8, 7.5, 9, 4, 6, 10, 100):
        assert any(abs(a - number) < 1e-9 for a in allowed), number
    assert facts.score == 73  # 72.75 shown as a whole number
    assert not any(abs(a - 72.75) < 1e-9 for a in allowed)  # only what the LLM is shown


def test_a_sentence_may_name_only_the_numbers_of_the_facts_it_is_about() -> None:
    facts = facts_for(nova(), every_claim())
    battery = facts.top[0]
    allowed = allowed_numbers(facts, [battery])
    assert all(any(abs(a - n) < 1e-9 for a in allowed) for n in (8, 24, 1, 73, 10, 100))
    assert not any(abs(a - 9) < 1e-9 for a in allowed)  # the keyboard's score
    assert not any(abs(a - 4) < 1e-9 for a in allowed)  # the trade-off's


def test_a_fact_with_no_claim_to_cite_may_be_named_in_any_sentence() -> None:
    product = ranked(
        "Guess Book 10",
        1,
        contribution("battery_life", 8.0, 0.5),
        contribution("ports", 6.5, 0.5, ValueState.ESTIMATED, "estimated"),
    )
    facts = facts_for(product, [claim("Guess Book 10", "battery_life")])
    battery, ports = sorted(facts.top, key=lambda f: f.key)
    assert ports.claim_ids == ()
    assert any(abs(a - 6.5) < 1e-9 for a in allowed_numbers(facts, [battery]))


def test_numbers_in_a_label_are_allowed() -> None:
    product = ranked("Port Book 8", 1, contribution("usb_4_ports", 8.0, 1.0))
    facts = facts_for(product, [])
    assert facts.top[0].label == "usb 4 ports"
    assert all(any(abs(a - n.value) < 1e-9 for a in allowed_numbers(facts)) for n in numbers_in("usb 4 ports"))


def test_an_unscored_product_has_no_top_and_names_every_criterion() -> None:
    product = ranked(
        "Blank Book 9",
        1,
        contribution("battery_life", None, 0.0, ValueState.UNKNOWN),
        contribution("weight", None, 0.0, ValueState.UNKNOWN),
        scored=False,
    )
    facts = facts_for(product, [])
    assert facts.score is None and facts.top == () and facts.trade_off is None
    assert facts.unverified == ("battery_life", "weight")
