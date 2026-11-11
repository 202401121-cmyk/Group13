"""US-16: combining claims for one product and criterion by source weight, and learning those weights.

Every site and number here is made up. Trust scores are written out, so each test reads on its own.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.contracts import SourceTier, ValueState
from app.verify import combine
from app.verify.combine import Evidence, Tolerance, resolve_all
from app.verify.trust import TrustTable

TABLE = TrustTable()  # the built-in thresholds, which trust.yaml repeats: 40, 30%, 2 points, 5%
M, L, E, C, R = (
    SourceTier.MANUFACTURER,
    SourceTier.LAB,
    SourceTier.EDITORIAL,
    SourceTier.COMMUNITY,
    SourceTier.RETAILER,
)


def ev(
    cid: str, site: str, tier: SourceTier, trust: float, product: str = "p1", key: str = "battery_life", **kw: Any
) -> Evidence:
    return Evidence.of(f"clm_{cid}", product, key, site, tier, trust, **kw)


def resolve(items: list[Evidence], method: str = "learned", tolerance: Tolerance | None = None) -> Any:
    outcome = resolve_all(items, TABLE, method, (lambda p, k: tolerance) if tolerance else None)  # type: ignore[arg-type]
    return outcome


def one(items: list[Evidence], method: str = "learned", **kw: Any) -> Any:
    outcome = resolve(items, method, **kw)
    return outcome.resolutions[("p1", "battery_life")]


# --- DR-03 and independence ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("method", ["learned", "tier_order"])
def test_a_maker_claim_beats_an_editorial_one_which_beats_reddit(method: str) -> None:
    maker = ev("m", "nova.example", M, 95, value=10, unit="h")
    review = ev("e", "reviews.example", E, 70, value=8, unit="h")
    thread = ev("c", "reddit:r/laptops", C, 45, value=6, unit="h")
    assert one([maker, review], method).value == "10 h"
    assert one([review, thread], method).value == "8 h"
    assert one([maker, thread], method).value == "10 h"
    assert one([thread, review, maker], "tier_order").value == "10 h"  # the top tier present wins outright


def test_one_site_repeated_three_times_counts_once() -> None:
    """Three pages of one shop saying 45 h are one voice against one review saying 30 h."""
    shop = [ev(f"r{i}", "shop.example", R, 45, value=45, unit="h") for i in range(3)]
    review = ev("e", "reviews.example", E, 70, value=30, unit="h")
    resolved = one([*shop, review], "tier_order")
    assert resolved.value == "30 h"
    learned = one([*shop, review])
    assert learned.value == "30 h"
    assert sorted(learned.conflicting) == ["clm_r0", "clm_r1", "clm_r2"]  # every claim keeps its id


def test_each_claim_is_listed_by_what_it_says_though_its_site_counts_once() -> None:
    """A shop whose pages say 150, 200 and 200 min speaks once, as 200; its 150 page isn't listed as backing 200."""
    shop = [
        ev("s1", "shop.example", R, 45, value=150, unit="min"),
        ev("s2", "shop.example", R, 45, value=200, unit="min"),
        ev("s3", "shop.example", R, 45, value=200, unit="min"),
    ]
    review = ev("e", "reviews.example", E, 72, value=200, unit="min")
    resolved = one([*shop, review])
    assert resolved.value == "200 min"
    assert resolved.state is ValueState.KNOWN  # by site, nobody disagrees
    assert (resolved.supporting, resolved.conflicting) == (["clm_e", "clm_s2", "clm_s3"], ["clm_s1"])


def test_values_in_other_units_are_converted_before_the_median() -> None:
    items = [
        ev("a", "a.example", E, 70, key="weight_kg", value=1.6, unit="kg"),
        ev("b", "b.example", E, 70, key="weight_kg", value="1600 g"),
        ev("c", "c.example", E, 60, key="weight_kg", value=3.5, unit="lb"),  # 1.59 kg: within 5%
    ]
    resolved = resolve(items).resolutions[("p1", "weight_kg")]
    assert resolved.state is ValueState.KNOWN
    assert resolved.conflicting == []
    assert resolved.value in ("1.6 kg", "1600 g")


def test_claims_inside_the_tolerance_agree() -> None:
    items = [ev("a", "a.example", E, 70, value=10, unit="h"), ev("b", "b.example", E, 70, value=10.4, unit="h")]
    assert one(items).state is ValueState.KNOWN  # 4% apart, inside the default 5%
    exact = one(items, tolerance=Tolerance(relative=0.0))
    assert exact.state is ValueState.DISPUTED  # no tolerance at all


def test_scores_are_a_weighted_mean() -> None:
    items = [ev("a", "a.example", E, 80, score=8.0), ev("b", "b.example", E, 40, score=7.0)]
    resolved = one(items, "tier_order")
    assert resolved.score == pytest.approx((8.0 * 0.8 + 7.0 * 0.4) / 1.2, abs=0.01)
    assert resolved.value is None
    assert resolved.state is ValueState.KNOWN  # 1 point apart, inside the 2-point tolerance


# --- Disputed and Unknown --------------------------------------------------------------------------------------------


def test_two_independent_sites_disagreeing_give_disputed_with_both_claim_ids() -> None:
    maker = ev("m", "nova.example", M, 95, value=10, unit="h")
    thread = ev("c", "reddit:r/laptops", C, 45, value=6, unit="h")  # 45 of 140: 32%, over the 30% line
    resolved = one([maker, thread])
    assert resolved.state is ValueState.DISPUTED
    assert (resolved.supporting, resolved.conflicting) == (["clm_m"], ["clm_c"])
    assert resolved.value == "10 h"
    scores = one([ev("a", "a.example", E, 70, score=9.0), ev("b", "b.example", E, 70, score=4.0)])
    assert scores.state is ValueState.DISPUTED


def test_a_small_minority_is_not_a_dispute() -> None:
    items = [
        ev("m", "nova.example", M, 95, value=10, unit="h"),
        ev("e", "reviews.example", E, 70, value=10, unit="h"),
        ev("c", "reddit:r/laptops", C, 45, value=6, unit="h"),  # 45 of 210: 21%
    ]
    resolved = one(items, "tier_order")
    assert resolved.state is ValueState.KNOWN
    assert resolved.conflicting == ["clm_c"]  # the minority is still on record
    same_site = [ev("a", "a.example", E, 70, value=10, unit="h"), ev("b", "a.example", E, 70, value=6, unit="h")]
    assert one(same_site).state is ValueState.KNOWN  # one site disagreeing with itself is not two sources


def test_no_trustworthy_claim_gives_unknown_with_no_score() -> None:
    weak = [ev("q", "quora.example", C, 35, score=2.0), ev("x", "spam.example", C, 20, value=99, unit="h")]
    resolved = one(weak)
    assert resolved.state is ValueState.UNKNOWN
    assert (resolved.score, resolved.value, resolved.confidence) == (None, None, 0.0)
    assert resolved.supporting == [] and resolved.conflicting == []
    edge = one([ev("s", "shop.example", R, 40, score=6.0)])
    assert edge.state is ValueState.KNOWN  # exactly 40 counts as trustworthy


def test_text_values_are_a_weighted_vote() -> None:
    items = [
        ev("a", "a.example", M, 95, key="panel", value="IPS"),
        ev("b", "b.example", E, 70, key="panel", value="ips "),
        ev("c", "c.example", C, 45, key="panel", value="VA"),
    ]
    resolved = resolve(items).resolutions[("p1", "panel")]
    assert resolved.value == "IPS"
    assert resolved.conflicting == ["clm_c"]


# --- the two methods -------------------------------------------------------------------------------------------------


def _run_with_a_liar() -> list[Evidence]:
    """Three sites on ten products: two agree everywhere, the third disagrees on most of them."""
    items: list[Evidence] = []
    for n in range(10):
        product = f"p{n}"
        items.append(ev(f"a{n}", "good-a.example", E, 70, product, value=10 + n, unit="h"))
        items.append(ev(f"b{n}", "good-b.example", E, 70, product, value=10 + n, unit="h"))
        liar = 10 + n if n < 2 else 30 + n
        items.append(ev(f"c{n}", "liar.example", E, 70, product, value=liar, unit="h"))
    return items


def test_learned_weights_fall_for_a_site_that_often_disagrees() -> None:
    outcome = resolve(_run_with_a_liar())
    weights = outcome.weights
    assert weights["liar.example"] < weights["good-a.example"] - 0.2
    assert weights["good-a.example"] == pytest.approx(weights["good-b.example"])
    assert outcome.method == "learned"


def test_two_sources_alone_teach_nothing_about_who_is_reliable() -> None:
    """With only two sources, the heavier one is the consensus by definition, so their disagreement is no evidence.
    The maker and the Reddit thread keep their starting weights, and the dispute stays visible."""
    maker = ev("m", "nova.example", M, 95, value=10, unit="h")
    thread = ev("c", "reddit:r/laptops", C, 45, value=6, unit="h")
    outcome = resolve([maker, thread])
    assert outcome.weights == {"nova.example": pytest.approx(0.95), "reddit:r/laptops": pytest.approx(0.45)}
    assert outcome.resolutions[("p1", "battery_life")].state is ValueState.DISPUTED


def test_learning_stops_within_ten_rounds() -> None:
    outcome = resolve(_run_with_a_liar())
    assert 1 <= outcome.rounds <= TABLE.max_rounds
    slow = TABLE.model_validate({**TABLE.model_dump(), "min_weight_change": 0.0, "max_rounds": 3})
    assert resolve_all(_run_with_a_liar(), slow, "learned").rounds == 3


def test_tier_order_takes_the_top_tier_and_trust_breaks_ties() -> None:
    items = [
        ev("l1", "lab-a.example", L, 80, value=9, unit="h"),
        ev("l2", "lab-b.example", L, 90, value=11, unit="h"),
        ev("e1", "reviews.example", E, 99, value=7, unit="h"),
        ev("e2", "reviews-2.example", E, 99, value=7, unit="h"),
    ]
    resolved = one(items, "tier_order")
    assert resolved.value == "11 h"  # the lab tier wins, and in it the more trusted lab
    tie = [ev("x", "b-site.example", E, 70, value=9, unit="h"), ev("y", "a-site.example", E, 70, value=11, unit="h")]
    assert one(tie, "tier_order").value == one(list(reversed(tie)), "tier_order").value  # order never matters


def test_same_inputs_same_output() -> None:
    first = resolve(_run_with_a_liar())
    again = resolve(list(reversed(_run_with_a_liar())))
    assert first.resolutions == again.resolutions
    assert first.weights == again.weights


def test_the_learned_method_can_be_broken_on_purpose(monkeypatch: pytest.MonkeyPatch) -> None:
    """The stage's fallback test breaks learn_weights; this proves resolve_all really calls it."""

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("learning failed")

    monkeypatch.setattr(combine, "learn_weights", broken)
    with pytest.raises(RuntimeError):
        resolve(_run_with_a_liar())
    assert resolve(_run_with_a_liar(), "tier_order").method == "tier_order"


# --- found by review ---------------------------------------------------------------------------------------------


def test_disagreeing_scores_still_cite_a_claim_on_each_side() -> None:
    """No score is within 2 points of the 6.5 mean, yet the side the weight is on has a claim to cite."""
    even = one([ev("a", "a.example", E, 70, score=9.0), ev("b", "b.example", E, 70, score=4.0)])
    assert even.state is ValueState.DISPUTED
    assert even.supporting and even.conflicting
    assert even.score == pytest.approx(6.5)
    one_site = one([ev("a", "r/laptops", C, 45, score=2.0), ev("b", "r/laptops", C, 45, score=8.0)])
    assert (one_site.state, one_site.supporting) == (ValueState.KNOWN, ["clm_a", "clm_b"])


def test_a_bare_number_takes_the_unit_its_group_uses() -> None:
    maker = ev("m", "nova.example", M, 95, value=10)  # no unit, and battery_life names none
    thread = ev("c", "reddit:r/laptops", C, 45, value=6, unit="h")
    resolved = one([maker, thread])
    assert resolved.state is ValueState.DISPUTED  # compared, not left out
    assert (resolved.supporting, resolved.conflicting) == (["clm_m"], ["clm_c"])


def test_text_with_a_unit_we_know_is_a_number() -> None:
    items = [ev("a", "a.example", E, 70, value="up to 10 hours"), ev("b", "b.example", E, 70, value=10, unit="h")]
    assert one(items).conflicting == []
    words = [
        ev("a", "a.example", E, 70, key="wifi", value="Wi-Fi 6E"),
        ev("b", "b.example", E, 70, key="wifi", value="Wi-Fi 6"),
    ]
    assert resolve(words).resolutions[("p1", "wifi")].state is ValueState.DISPUTED  # text, and not the same text


def test_claims_with_no_weight_are_not_trustworthy() -> None:
    sarcastic = [ev("a", "a.example", E, 70, score=9.0, weight=0.0)]
    resolved = one(sarcastic)
    assert (resolved.state, resolved.confidence) == (ValueState.UNKNOWN, 0.0)


def test_tier_order_ignores_sources_below_the_trust_line() -> None:
    """A Quora answer (community, 35) outranks a shop by tier, but it is below 40, so the shop decides."""
    quora = ev("q", "quora.com", C, 35, key="price_inr", value=50000, unit="INR")
    shop = ev("s", "flipkart.com", R, 45, key="price_inr", value=64990, unit="INR")
    resolved = resolve([quora, shop], "tier_order").resolutions[("p1", "price_inr")]
    assert resolved.value == "64990 INR"


def test_learning_stops_early_once_no_weight_moves_by_a_hundredth() -> None:
    assert resolve(_run_with_a_liar()).rounds < TABLE.max_rounds


def test_a_sites_scores_are_summed_in_the_same_order_whatever_order_they_came_in() -> None:
    scores = [ev(f"s{i}", "a.example", E, 70, score=s) for i, s in enumerate([7.3, 8.1, 6.7, 9.9, 5.3])]
    other = ev("o", "b.example", E, 70, score=7.0)
    assert one([*scores, other]) == one([other, *reversed(scores)])
