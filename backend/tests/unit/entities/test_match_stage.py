"""US-15: the fuzzy match stage (task #35, first half). Names in, one product per real product out.

Each DECIDED item and edge case of S3-3 has a test here. The scorer is the fuzzy baseline unless a test swaps it.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Sequence

import pytest

from app.contracts import MatchInput, MatchResult, ProductMention, RecommenderCount, StageContext
from app.contracts import examples as ex
from app.entities import FuzzyMatchStage, alias_keys
from app.entities.clustering import product_id_for
from app.entities.scorer import FUZZY_THRESHOLD, FuzzyScorer

CTX = StageContext(search_id="srch_aaaaaaaaaaaaaaaaaaaaaaaa")
WH = "prd_sony_wh1000xm5"


def _mention(name: str, brand: str | None = None, *, product: bool = True) -> ProductMention:
    return ProductMention(raw_name=name, is_product=product, brand=brand)


def _match(*names: str | ProductMention, stage: FuzzyMatchStage | None = None, **extra: object) -> MatchResult:
    mentions = [n if isinstance(n, ProductMention) else _mention(n) for n in names]
    inp = MatchInput.model_validate({"mentions": mentions, **extra})
    return (stage or FuzzyMatchStage()).run(inp, CTX)


def _groups(result: MatchResult) -> list[set[str]]:
    """The raw names of each product, as sets: easy to compare whatever order the products come in."""
    by_id: dict[str, set[str]] = {}
    for name, product_id in result.alias_map.items():
        by_id.setdefault(product_id, set()).add(name)
    return sorted(by_id.values(), key=lambda names: sorted(names))


class TableScorer:
    """A scorer that answers from a table, so a test can set up any chain of scores."""

    threshold = 0.5

    def __init__(self, table: dict[frozenset[str], float]) -> None:
        self.table = table

    def score(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
        return [self.table.get(frozenset(pair), 0.0) for pair in pairs]


# --- the edge cases of S3-3 ---------------------------------------------------------------------------------------


def test_three_names_for_the_xm5_become_one_product() -> None:
    result = _match("Sony WH-1000XM5", "XM5", "1000XM5 headphones")
    assert _groups(result) == [{"Sony WH-1000XM5", "XM5", "1000XM5 headphones"}]
    (product,) = result.products
    assert product.name == "Sony WH-1000XM5"
    assert sorted(product.aliases) == ["1000XM5 headphones", "XM5"]
    assert result.method == "fuzzy" and result.unmatched == []


def test_wh_and_wf_1000xm5_stay_apart() -> None:
    result = _match("Sony WH-1000XM5", "Sony WF-1000XM5")
    assert _groups(result) == [{"Sony WF-1000XM5"}, {"Sony WH-1000XM5"}]


def test_iphone_16_128_and_256_gb_are_one_product_with_two_variants() -> None:
    result = _match(_mention("iPhone 16 128 GB", "Apple"), _mention("iPhone 16 256GB", "Apple"))
    (product,) = result.products
    assert sorted(v.attributes["storage"] for v in product.variants) == ["128GB", "256GB"]
    assert sorted(v.label for v in product.variants) == ["128 GB", "256 GB"]
    assert len({v.id for v in product.variants}) == 2


def test_names_that_differ_in_anything_but_variants_stay_separate_products() -> None:
    assert len(_match("iPhone 15 128GB", "iPhone 16 128GB").products) == 2
    colours = _match("Galaxy A35 8GB 128GB Black", "Galaxy A35 8GB 128GB Blue")
    assert len(colours.products) == 1 and len(colours.products[0].variants) == 2
    ram = _match("Redmi Note 13 6+128GB", "Redmi Note 13 8+128GB")
    (phone,) = ram.products
    assert sorted(v.attributes["ram"] for v in phone.variants) == ["6GB", "8GB"]


def test_galaxy_a35_and_a55_stay_apart() -> None:
    result = _match("Samsung Galaxy A35", "Samsung Galaxy A55", "Galaxy A35 5G")
    assert {frozenset(g) for g in _groups(result)} == {
        frozenset({"Samsung Galaxy A35", "Galaxy A35 5G"}),
        frozenset({"Samsung Galaxy A55"}),
    }


def test_an_edition_keeps_a_product_apart() -> None:
    assert len(_match("Samsung Galaxy S24", "Galaxy S24 Ultra").products) == 2
    assert len(_match("Galaxy S24", "Galaxy S24+").products) == 2  # "+" is the Plus, never the S24
    assert len(_match("Galaxy S24+", "Samsung Galaxy S24 Plus").products) == 1
    assert len(_match("Redmi Note 13 Pro", "Redmi Note 13 Pro+").products) == 2


def test_a_name_that_is_another_plus_more_words_stays_apart() -> None:
    """With no model code, "Redmi 13" is not "Redmi Note 13", and "Kindle" is not "Kindle Paperwhite"."""
    assert len(_match("Redmi 13", "Redmi Note 13").products) == 2
    assert len(_match("Kindle", "Kindle Paperwhite").products) == 2
    assert len(_match("Lenovo Legion 5", "Lenovo Legion Slim 5").products) == 2


def test_spacing_and_sub_brands_do_not_split_a_product() -> None:
    assert len(_match("iPhone15", "iPhone 15").products) == 1
    assert len(_match("Galaxy Buds2 Pro", "Galaxy Buds 2 Pro").products) == 1
    assert len(_match("Xiaomi Redmi Note 13 Pro", "Redmi Note 13 Pro").products) == 1
    assert len(_match("Sony WH-1000XM5", "Bose WH-1000XM5").products) == 2  # two brands with nothing in common


def test_a_chain_a_b_c_keeps_a_and_c_apart() -> None:
    """A~B and B~C, but A not ~ C: C must not ride into A's cluster through B (DECIDED 4)."""
    a, b, c = "alpha bravo charlie", "alpha bravo delta", "alpha echo delta"
    scorer = TableScorer({frozenset({a, b}): 0.9, frozenset({b, c}): 0.9, frozenset({a, c}): 0.1})
    groups = _groups(_match(a, b, c, stage=FuzzyMatchStage(scorer=scorer)))
    assert {a, b} in groups and {c} in groups


def test_the_chain_guard_holds_when_the_middle_name_is_the_representative() -> None:
    """B is the most specific, so it leads the cluster: A and C both match it, but not each other."""
    a, b, c = "alpha bravo", "alpha charlie delta foxtrot", "echo charlie"
    scorer = TableScorer({frozenset({a, b}): 0.9, frozenset({b, c}): 0.9, frozenset({a, c}): 0.1})
    groups = _groups(_match(a, b, c, stage=FuzzyMatchStage(scorer=scorer)))
    assert {a, c} not in groups and not any({a, c} <= g for g in groups)


def test_the_same_mentions_always_give_the_same_products_and_ids() -> None:
    names = ["Sony WH-1000XM5", "XM5", "Sony WF-1000XM5", "iPhone 16 128GB", "iPhone 16 256GB", "Galaxy A35"]
    first = _match(*names)
    for seed in range(5):
        shuffled = names[:]
        random.Random(seed).shuffle(shuffled)
        assert _match(*shuffled) == first


def test_a_product_type_is_never_merged() -> None:
    result = _match(
        _mention("noise cancelling headphones", product=False),
        _mention("Sony WH-1000XM5 noise cancelling headphones"),
        _mention("Sony WH-1000XM5"),
    )
    kind = {p.name: p.kind for p in result.products}
    assert kind["noise cancelling headphones"] == "product_type"
    assert result.alias_map["noise cancelling headphones"] != result.alias_map["Sony WH-1000XM5"]
    assert {p.kind for p in result.products if p.id == result.alias_map["Sony WH-1000XM5"]} == {"product"}


def test_a_name_matching_two_products_stays_apart_and_is_listed_unmatched() -> None:
    result = _match("Sony WH-1000XM5", "Sony WF-1000XM5", "XM5")
    assert result.unmatched == ["XM5"]
    assert {"XM5"} in _groups(result)


def test_a_vague_name_stays_on_its_own() -> None:
    assert len(_match("Sony", "Sony WH-1000XM5").products) == 2


def test_the_alias_map_covers_every_raw_name() -> None:
    names = [m.raw_name for m in ex.mentions()] + ["XM5", "Sony WH-1000XM5"]
    result = _match(*names)
    assert set(result.alias_map) == set(names)
    assert set(result.alias_map.values()) == {p.id for p in result.products}


def test_recommender_count_is_the_largest_count_of_the_cluster() -> None:
    counts = [
        RecommenderCount(product_mention="Sony WH-1000XM5", distinct_recommenders=4, threads=2),
        RecommenderCount(product_mention="xm5", distinct_recommenders=6, threads=3),
    ]
    result = _match("Sony WH-1000XM5", "XM5", recommender_counts=counts)
    assert result.products[0].recommender_count == 6  # never 10: the same people may be in both counts
    folded = [
        RecommenderCount(product_mention="Sony WH-1000XM5", distinct_recommenders=6, threads=2),
        RecommenderCount(product_mention="Sony WH1000XM5", distinct_recommenders=2, threads=1),
    ]
    assert _match("Sony WH-1000XM5", recommender_counts=folded).products[0].recommender_count == 6


# --- aliases from earlier searches (DR-06) -------------------------------------------------------------------------


def test_a_known_alias_gives_its_product_id() -> None:
    known = {"sony wh1000xm5": WH, "xm5": WH}
    result = _match("XM5", "Sony WF-1000XM5", known_aliases=known)
    assert result.alias_map["XM5"] == WH
    assert result.alias_map["Sony WF-1000XM5"] != WH  # the WF's own name says it is another product


def test_two_names_with_one_known_id_are_one_product() -> None:
    known = {"xm5": WH, "sony wh1000xm5": WH}
    result = _match("XM5", "Sony WH-1000XM5", known_aliases=known)
    assert [p.id for p in result.products] == [WH]


def test_a_known_alias_settles_a_name_that_would_be_unsure() -> None:
    known = {"xm5": WH, "sony wh1000xm5": WH}
    result = _match("Sony WH-1000XM5", "Sony WF-1000XM5", "XM5", known_aliases=known)
    assert result.alias_map["XM5"] == result.alias_map["Sony WH-1000XM5"] == WH
    assert result.unmatched == []


def test_a_known_alias_never_pulls_a_product_apart() -> None:
    """A fuller name that no earlier search saw leads the cluster; the known name still joins it, with its id."""
    names = ("Sony WH-1000XM5 Wireless Noise Cancelling Headphones", "Sony WH-1000XM5", "XM5")
    without = _match(*names)
    assert len(without.products) == 1
    known = _match(*names, known_aliases={"sony wh1000xm5": WH})
    assert [p.id for p in known.products] == [WH]
    short = _match("1000XM5 headphones", "XM5", known_aliases={"sony wh1000xm5": WH})
    assert len(short.products) == 1


def test_alias_keys_keep_only_names_with_the_full_model_code() -> None:
    """ "XM5" joined because its code ends the full one; it could as well be the WF, so it is not kept."""
    result = _match("Sony WH-1000XM5", "XM5", "Sony WH-1000XM5 Wireless", "Galaxy A35", _mention("case", product=False))
    product_id = result.alias_map["XM5"]
    assert alias_keys(result) == {"sony wh1000xm5": product_id}
    no_codes = _match("Kindle Paperwhite", "Kindle Paperwite")
    assert set(alias_keys(no_codes)) == {"kindle paperwhite", "kindle paperwite"}


# --- the products ---------------------------------------------------------------------------------------------------


def test_the_example_mentions_give_the_example_product_ids() -> None:
    """The stub research and extract feed these, three times over, and the decide stub ranks these ids."""
    result = _match(*(ex.mentions() * 3))
    ids = {p.id for p in result.products if p.kind == "product"}
    assert ids == {p.id for p in ex.match().products}
    assert result.alias_map["Nova 14"] == "prd_nova_book_14"


def test_ids_fit_the_contract_and_collisions_get_a_suffix() -> None:
    same = _match("Nova 14 Pro!", "Nova 14 Pro?", "nova-14-pro")
    assert [p.id for p in same.products] == ["prd_nova_14_pro"]
    # A product and a kind of product with the same name never merge, so their ids would collide.
    both = _match(_mention("Smart Watch X1", product=False), _mention("Smart Watch X1 Pro"), _mention("smart watch x1"))
    ids = [p.id for p in both.products]
    assert len(ids) == len(set(ids)) == 3
    long = _match("Kestrel " + "Supercalifragilistic " * 6 + "X1")  # 136 characters, within raw_name's 150
    assert len(long.products[0].id) <= 68
    assert product_id_for("Sony WH-1000XM5") == WH


def test_a_variant_label_always_fits() -> None:
    name = "X1 " + "1" * 60 + "GB " + "2" * 60 + "GB"
    result = _match(name, "X1 8GB")
    assert all(len(v.label) <= 120 for p in result.products for v in p.variants)
    stage = FuzzyMatchStage()
    assert stage.fallback(MatchInput(mentions=[_mention(name)]), CTX, RuntimeError("x")).products


def test_any_error_in_run_falls_back_to_every_name_unmerged_with_one_log_line(
    caplog: pytest.LogCaptureFixture,
) -> None:
    stage = FuzzyMatchStage()
    inp = MatchInput(mentions=[_mention("Sony WH-1000XM5"), _mention("XM5"), _mention("XM5")])
    with caplog.at_level(logging.WARNING, logger="app.entities.stage"):
        result = stage.fallback(inp, CTX, RuntimeError("the model would not load"))
    assert _groups(result) == [{"Sony WH-1000XM5"}, {"XM5"}]
    assert result.method == "fuzzy"
    lines = [r for r in caplog.records if r.name == "app.entities.stage"]
    assert len(lines) == 1 and "RuntimeError" in lines[0].getMessage()
    assert "would not load" not in lines[0].getMessage()  # the type only, never the text


def test_a_broken_scorer_raises_so_the_runner_uses_the_fallback() -> None:
    class Broken:
        threshold = 0.5

        def score(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
            raise RuntimeError("broken")

    with pytest.raises(RuntimeError):  # two names with no codes reach the scorer
        _match("Kindle Paperwhite", "Kindle Paperwite", stage=FuzzyMatchStage(scorer=Broken()))


def test_the_scorer_can_be_swapped_without_touching_the_rules() -> None:
    """The rules decide first: whatever a scorer says, different model codes never merge (DECIDED 3, 9)."""
    always = TableScorer({})
    always.score = lambda pairs: [1.0] * len(pairs)  # type: ignore[method-assign]
    result = _match("Sony WH-1000XM5", "Sony WF-1000XM5", stage=FuzzyMatchStage(scorer=always))
    assert len(result.products) == 2
    assert FuzzyScorer().threshold == FUZZY_THRESHOLD == 0.79  # D-56: the WDC 80% corner-case threshold


def test_the_fuzzy_scorer_scores_with_token_set_ratio() -> None:
    scores = FuzzyScorer().score([("nova 14", "nova 14"), ("kindle paperwhite", "kindle paperwite"), ("ab", "xyz")])
    assert scores[0] == 1.0 and scores[1] >= FUZZY_THRESHOLD and scores[2] < FUZZY_THRESHOLD


def test_a_typo_in_a_name_without_codes_still_merges() -> None:
    assert len(_match("Kindle Paperwhite", "Kindle Paperwite").products) == 1


# --- speed (DECIDED 9) ----------------------------------------------------------------------------------------------


def _timed(names: list[str]) -> tuple[float, MatchResult]:
    started = time.perf_counter()
    result = _match(*names)
    return time.perf_counter() - started, result


def test_1000_mentions_match_in_under_5_seconds() -> None:
    rng = random.Random(7)
    brands = ["Sony", "Samsung", "Apple", "Dell", "Asus", "Lenovo", "HP", "Acer", "Bose", "JBL"]
    names = [
        f"{rng.choice(brands)} {rng.choice(['Nova', 'Orion', 'Vela', 'Aurora'])} "
        f"{rng.choice('ABCGXZ')}{rng.randint(1, 400)} {rng.choice(['', '8GB', '16GB 512GB', 'Black'])}".strip()
        for _ in range(1000)
    ]
    seconds, result = _timed(names)
    assert seconds < 5.0
    assert len(result.products) <= 200


def test_1000_different_names_sharing_one_word_match_in_under_5_seconds() -> None:
    """The worst case for blocking: every name shares a word, has no code, and needs the scorer."""
    rng = random.Random(11)
    syllables = ["ka", "lo", "mi", "ra", "su", "te", "vo", "ze", "ni", "pa"]
    names = sorted({"Nova " + "".join(rng.choice(syllables) for _ in range(4)) for _ in range(1400)})[:1000]
    seconds, result = _timed(names)
    assert len(names) == 1000
    assert seconds < 5.0
    assert len(result.products) <= 200
