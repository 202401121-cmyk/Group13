"""US-41 (S5-23, D-102 item 1): when fewer than 5 products rank, the fact look-ups reach decide's "can't tell" products,
each with the unknown that held it back first. Code decides; decide's rules and ranking never change. Every product and
value here is made up."""

from __future__ import annotations

import app.contracts as C
from app.contracts import EnrichTarget, ValueState
from app.contracts import examples as ex
from app.contracts.enrich import ENRICH_TOP_N, MAX_ENRICH_SEARCHES
from app.decide.rules import PRICE_KEY
from app.enrich.targets import filled_targets, provisional_ranking, top_weights

RUBRIC = ex.rubric()  # cpu_performance 0.20, keyboard_comfort 0.18, battery_life 0.16, weight 0.14, ...
TOP = top_weights(RUBRIC)


def product(n: int) -> C.Product:
    return C.Product(id=f"prd_made_up_{n}", name=f"Made-up Laptop {n}")


PRODUCTS = {product(n).id: product(n) for n in range(12)}
ALIASES = {p.name: p.id for p in PRODUCTS.values()}


def held(n: int, attribute: str | None = PRICE_KEY, group: str = "cant_tell") -> C.FilteredProduct:
    return C.FilteredProduct(
        product_id=f"prd_made_up_{n}",
        group=group,  # type: ignore[arg-type]
        reasons=["We couldn't find its price."],
        rule_attribute=attribute,
    )


def known(n: int, key: str, score: float = 7.0) -> C.ResolvedValue:
    return C.ResolvedValue(
        product_id=f"prd_made_up_{n}",
        criterion_key=key,
        state=ValueState.KNOWN,
        score=score,
        confidence=0.8,
        supporting_claim_ids=[f"clm_{n:016x}"],
    )


def ranked(n: int, rank: int, gaps: list[str], score: float | None = 7.0) -> C.RankedProduct:
    contributions = [
        C.Contribution(criterion_key=k, score=None, normalized_weight=0.1, contribution=0.0, state=ValueState.UNKNOWN)
        for k in gaps
    ] + [
        C.Contribution(criterion_key="weight", score=7, normalized_weight=0.1, contribution=0.7, state=ValueState.KNOWN)
    ]
    return C.RankedProduct(
        product_id=f"prd_made_up_{n}",
        rank=rank,
        score=score,
        contributions=contributions,
        coverage=0.5,
        confidence="medium",
        gap_to_leader=0.0,
    )


def fill(
    ranked_: list[C.RankedProduct],
    held_: list[C.FilteredProduct],
    provisional: list[C.RankedProduct],
    values: list[C.ResolvedValue] | None = None,
    prices: list[EnrichTarget] | None = None,
    askable: frozenset[str] = frozenset({"ram_gb"}),
    unknown: dict[str, list[str]] | None = None,
) -> list[EnrichTarget]:
    return filled_targets(
        RUBRIC,
        ranked_,
        held_,
        provisional,
        PRODUCTS,
        values or [],
        ALIASES,
        searchable=lambda key: key in {"cpu_performance", "battery_life"},
        askable=lambda key: key in askable,
        prices=prices or [],
        unknown=unknown,
    )


def test_nothing_ranked_fills_the_five_from_cant_tell_with_each_ones_hold_first() -> None:
    holding = [held(n) for n in range(7)]
    provisional = [ranked(n, n + 1, ["cpu_performance"]) for n in range(7)]
    targets = fill([], holding, provisional)
    holds = targets[:ENRICH_TOP_N]
    assert [t.criterion_key for t in holds] == [PRICE_KEY] * ENRICH_TOP_N  # the price held each one back
    assert [t.product_id for t in holds] == [f"prd_made_up_{n}" for n in range(ENRICH_TOP_N)]  # five, in decide's order
    assert len(targets) == MAX_ENRICH_SEARCHES  # the same six searches
    assert targets[-1] == EnrichTarget(
        product_id="prd_made_up_0", product_name="Made-up Laptop 0", criterion_key="cpu_performance"
    )


def test_three_ranked_and_two_from_cant_tell() -> None:
    top = [ranked(n, n + 1, ["battery_life"]) for n in range(3)]
    holding = [held(n) for n in range(3, 8)]
    provisional = [ranked(n, n - 2, []) for n in range(3, 8)]
    targets = fill(top, holding, provisional)
    assert [(t.product_id, t.criterion_key) for t in targets] == [
        ("prd_made_up_3", PRICE_KEY),  # the two held ones' holds first
        ("prd_made_up_4", PRICE_KEY),
        ("prd_made_up_0", "battery_life"),  # then the ranked ones' gaps
        ("prd_made_up_1", "battery_life"),
        ("prd_made_up_2", "battery_life"),
    ]
    assert len({t.product_id for t in targets}) == ENRICH_TOP_N


def test_a_product_held_only_by_its_price_gets_its_price_first_then_its_gaps() -> None:
    targets = fill([], [held(0)], [ranked(0, 1, ["battery_life", "cpu_performance"])])
    assert [t.criterion_key for t in targets] == [PRICE_KEY, "cpu_performance", "battery_life"]


def test_a_rules_known_spec_is_its_hold_and_an_unknown_rule_key_has_none() -> None:
    targets = fill([], [held(0, "ram_gb"), held(1, "warranty_years")], [ranked(0, 1, []), ranked(1, 2, [])])
    assert [(t.product_id, t.criterion_key) for t in targets] == [("prd_made_up_0", "ram_gb")]


def test_the_order_is_fewest_top_weights_unknown_then_the_higher_provisional_score() -> None:
    values = [known(2, key) for key in TOP] + [known(1, TOP[0])]
    provisional = [ranked(0, 3, [], score=9.0), ranked(1, 2, [], score=3.0), ranked(2, 1, [], score=1.0)]
    holding = [held(0), held(1), held(2), held(3)]
    targets = fill([], holding, provisional, values)
    # 2 has every top weight known, 1 has one, 0 and 3 none: then 0 (score 9) before 3 (no score: last).
    assert [t.product_id for t in targets] == ["prd_made_up_2", "prd_made_up_1", "prd_made_up_0", "prd_made_up_3"]


def test_filtered_unnamed_and_unknown_products_are_never_chosen() -> None:
    unknown = C.FilteredProduct(product_id="prd_not_kept_9", group="cant_tell", reasons=["x"], rule_attribute=PRICE_KEY)
    nameless = C.Product(id="prd_made_up_11", name="Made-up Laptop 11")
    products = {**PRODUCTS, nameless.id: nameless}
    aliases = {k: v for k, v in ALIASES.items() if v != nameless.id}
    targets = filled_targets(
        RUBRIC,
        [],
        [held(0, group="filtered_out"), unknown, held(11), held(1)],
        [],
        products,
        [],
        aliases,
        searchable=lambda key: True,
        askable=lambda key: True,
        prices=[],
    )
    assert [t.product_id for t in targets] == ["prd_made_up_1"]


def test_other_prices_fill_what_is_left_and_never_twice() -> None:
    price = [
        EnrichTarget(product_id=f"prd_made_up_{n}", product_name=f"P {n}", criterion_key=PRICE_KEY) for n in (0, 9)
    ]
    targets = fill([], [held(0)], [ranked(0, 1, [])], prices=price)
    assert [(t.product_id, t.criterion_key) for t in targets] == [
        ("prd_made_up_0", PRICE_KEY),
        ("prd_made_up_9", PRICE_KEY),
    ]


def test_five_ranked_fill_nothing_from_cant_tell() -> None:
    top = [ranked(n, n + 1, ["battery_life"]) for n in range(5)]
    targets = fill(top, [held(7)], [ranked(7, 1, ["battery_life"])])
    assert all(t.product_id != "prd_made_up_7" for t in targets)


def test_the_provisional_ranking_is_decides_own_scoring_of_the_held_products() -> None:
    assert provisional_ranking(RUBRIC, [], []) == []
    values = [known(0, key, 9.0) for key in TOP] + [known(1, TOP[0], 2.0)]
    scored = provisional_ranking(RUBRIC, [product(0), product(1)], values)
    assert [item.product_id for item in scored] == ["prd_made_up_0", "prd_made_up_1"]
    assert all(item.score is not None for item in scored)


def checked(n: int, attribute: str, state: ValueState = ValueState.UNKNOWN) -> C.CheckedValue:
    return C.CheckedValue(product_id=f"prd_made_up_{n}", attribute=attribute, state=state)


def test_every_unknown_that_holds_a_product_is_looked_up_price_first() -> None:
    from app.enrich.targets import held_keys

    found = held_keys(
        RUBRIC,  # a 70,000 budget and an active "at least 16 GB RAM" rule
        [
            checked(0, PRICE_KEY),
            checked(0, "ram_gb"),
            checked(1, PRICE_KEY, ValueState.KNOWN),  # known: holds nothing
            checked(1, "ram_gb"),
            checked(2, "storage_gb"),  # no rule on it: holds nothing
        ],
    )
    assert found == {"prd_made_up_0": [PRICE_KEY, "ram_gb"], "prd_made_up_1": ["ram_gb"]}
    targets = fill([], [held(0), held(1, "ram_gb")], [ranked(0, 1, []), ranked(1, 2, [])], unknown=found)
    assert [(t.product_id, t.criterion_key) for t in targets] == [
        ("prd_made_up_0", PRICE_KEY),
        ("prd_made_up_0", "ram_gb"),
        ("prd_made_up_1", "ram_gb"),
    ]


def test_without_a_budget_or_with_a_rule_switched_off_neither_holds() -> None:
    from app.enrich.targets import held_keys

    rules = [{**rule.model_dump(), "active": False} for rule in RUBRIC.hard_rules]
    off = C.Rubric.model_validate({**RUBRIC.model_dump(), "budget": None, "hard_rules": rules})
    assert held_keys(off, [checked(0, PRICE_KEY), checked(0, "ram_gb")]) == {}


def test_a_product_whose_holds_cant_be_looked_up_gives_its_place_to_the_next() -> None:
    unknown = {"prd_made_up_0": ["warranty_years"], "prd_made_up_1": [PRICE_KEY]}
    targets = fill(
        [], [held(0, "warranty_years"), held(1)], [ranked(0, 1, ["battery_life"]), ranked(1, 2, [])], unknown=unknown
    )
    assert [(t.product_id, t.criterion_key) for t in targets] == [("prd_made_up_1", PRICE_KEY)]


def test_the_ranked_gaps_come_before_the_held_ones_after_the_holds() -> None:
    top = [ranked(n, n + 1, ["battery_life"]) for n in range(3)]
    holding = [held(3), held(4)]
    provisional = [ranked(3, 1, ["cpu_performance"]), ranked(4, 2, [])]
    targets = fill(top, holding, provisional)
    assert [(t.product_id, t.criterion_key) for t in targets] == [
        ("prd_made_up_3", PRICE_KEY),
        ("prd_made_up_4", PRICE_KEY),
        ("prd_made_up_0", "battery_life"),
        ("prd_made_up_1", "battery_life"),
        ("prd_made_up_2", "battery_life"),
        ("prd_made_up_3", "cpu_performance"),  # a held product's own gap, last
    ]
