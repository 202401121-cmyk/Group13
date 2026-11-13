"""The category moves a site's trust, and community words alone never settle a fact we filter on (S3-10b, DR-03).

Reddit and forums are good at experience and poor at specs, so a price or a hard-rule attribute that only community
sources give stays Unknown ("can't tell"), while their criterion scores still count, weighted by trust. Every site,
product and claim here is made up, apart from the trust table's own listed domains.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.contracts import SourceType, StageContext, ValueState, VerifyInput, VerifyResult
from app.verify import TrustVerifyStage
from tests.unit.verify.build import claim, match, reddit, source, value_of, verify_input

NOVA = "prd_nova_book_14"
SEARCH_ID = "srch_verify0003"


def _run(inp: VerifyInput, filter_keys: Sequence[str] | None = None) -> VerifyResult:
    stage = TrustVerifyStage()
    if filter_keys is not None:
        stage.use_filter_keys(SEARCH_ID, filter_keys)
    return stage.run(inp, StageContext(search_id=SEARCH_ID))


def _with_category(inp: VerifyInput, category: str | None) -> VerifyInput:
    return VerifyInput.model_validate({**inp.model_dump(), "category": category})


# --- the category ----------------------------------------------------------------------------------------------------


def test_the_category_adjustment_changes_which_site_is_trusted() -> None:
    """gsmarena.com starts at 75 and loses 20 for laptops (trust.yaml); a review site we don't list starts at 70."""
    phones = source("https://www.gsmarena.com/nova_book_14-review.php")
    other = source("https://reviews.example/nova-book-14")
    claims = [
        claim(phones, "Nova Book 14", "ram_gb", value="8 GB"),
        claim(other, "Nova Book 14", "ram_gb", value="16 GB"),
    ]
    inp = verify_input(match("Nova Book 14"), claims, [phones, other])
    assert value_of(_run(_with_category(inp, None)), NOVA, "ram_gb").value == "8 GB"
    assert value_of(_run(_with_category(inp, "laptop")), NOVA, "ram_gb").value == "16 GB"


def test_the_fallback_uses_the_category_too() -> None:
    phones = source("https://www.gsmarena.com/nova_book_14-review.php")
    other = source("https://reviews.example/nova-book-14")
    claims = [
        claim(phones, "Nova Book 14", "ram_gb", value="8 GB"),
        claim(other, "Nova Book 14", "ram_gb", value="16 GB"),
    ]
    inp = _with_category(verify_input(match("Nova Book 14"), claims, [phones, other]), "laptop")
    ctx = StageContext(search_id=SEARCH_ID)
    fell_back = TrustVerifyStage().fallback(inp, ctx, RuntimeError("made-up failure"))
    assert fell_back.trust_method == "tier_order"
    assert value_of(fell_back, NOVA, "ram_gb").value == "16 GB"


# --- community words and the facts we filter on ----------------------------------------------------------------------


def _community_price() -> VerifyInput:
    one, two = reddit("r/SuggestALaptop", "aaa111"), reddit("r/IndianGaming", "bbb222")
    claims = [
        claim(one, "Nova Book 14", "price_inr", value=65000, unit="INR"),
        claim(two, "Nova Book 14", "price_inr", value=65500, unit="INR"),
    ]
    return verify_input(match("Nova Book 14"), claims, [one, two])


def test_a_price_only_community_gives_stays_unknown() -> None:
    price = value_of(_run(_community_price()), NOVA, "price_inr")
    assert price.state is ValueState.UNKNOWN
    assert price.value is None and price.supporting_claim_ids == []


def test_a_price_only_community_gives_stays_unknown_in_the_fallback_too() -> None:
    inp = _community_price()
    fell_back = TrustVerifyStage().fallback(inp, StageContext(search_id=SEARCH_ID), RuntimeError("made-up failure"))
    assert value_of(fell_back, NOVA, "price_inr").state is ValueState.UNKNOWN


def test_a_price_a_shop_backs_is_known_even_when_reddit_agrees() -> None:
    thread = reddit("r/SuggestALaptop", "aaa111")
    shop = source("https://www.flipkart.com/nova-book-14/p/itm1", SourceType.RETAILER)
    claims = [
        claim(thread, "Nova Book 14", "price_inr", value=65000, unit="INR"),
        claim(shop, "Nova Book 14", "price_inr", value=64990, unit="INR"),
    ]
    price = value_of(_run(verify_input(match("Nova Book 14"), claims, [thread, shop])), NOVA, "price_inr")
    assert price.state is ValueState.KNOWN


def test_a_rule_attribute_only_community_gives_stays_unknown_when_it_is_a_filter_key() -> None:
    thread = reddit("r/SuggestALaptop", "aaa111")
    claims = [claim(thread, "Nova Book 14", "ram_gb", value=16, unit="GB")]
    inp = verify_input(match("Nova Book 14"), claims, [thread])
    assert value_of(_run(inp, ["price_inr", "ram_gb"]), NOVA, "ram_gb").state is ValueState.UNKNOWN
    assert value_of(_run(inp, ["price_inr"]), NOVA, "ram_gb").state is ValueState.KNOWN  # not a filter key here


def test_a_criterion_score_only_community_gives_still_counts() -> None:
    thread = reddit("r/SuggestALaptop", "aaa111")
    claims = [claim(thread, "Nova Book 14", "battery_life", score=8.0)]
    inp = verify_input(match("Nova Book 14"), claims, [thread])
    battery = value_of(_run(inp, ["price_inr", "ram_gb"]), NOVA, "battery_life")
    assert (battery.state, battery.score) == (ValueState.KNOWN, 8.0)


def test_filter_keys_belong_to_their_own_search() -> None:
    stage = TrustVerifyStage()
    stage.use_filter_keys("srch_other0001", ["price_inr", "ram_gb"])
    thread = reddit("r/SuggestALaptop", "aaa111")
    inp = verify_input(match("Nova Book 14"), [claim(thread, "Nova Book 14", "ram_gb", value=16, unit="GB")], [thread])
    result = stage.run(inp, StageContext(search_id=SEARCH_ID))  # this search set none: only the price is one
    assert value_of(result, NOVA, "ram_gb").state is ValueState.KNOWN


# --- DR-01: community value claims never decide a filter (S3-10b review) ---------------------------------------------


def test_a_store_price_against_reddit_prices_is_known_and_not_disputed() -> None:
    """Two subreddits saying 90,000 form no side: the store's price stands, with nothing to dispute it."""
    shop = source("https://www.flipkart.com/nova-book-14/p/itm1", SourceType.RETAILER)
    one, two = reddit("r/SuggestALaptop", "aaa111"), reddit("r/IndianGaming", "bbb222")
    claims = [
        claim(shop, "Nova Book 14", "price_inr", value=64990, unit="INR"),
        claim(one, "Nova Book 14", "price_inr", value=90000, unit="INR"),
        claim(two, "Nova Book 14", "price_inr", value=90000, unit="INR"),
    ]
    price = value_of(_run(verify_input(match("Nova Book 14"), claims, [shop, one, two])), NOVA, "price_inr")
    assert price.state is ValueState.KNOWN
    assert price.value == "64990 INR"
    assert price.supporting_claim_ids == [claims[0].id]
    assert price.conflicting_claim_ids == [] and price.alternatives == []


def test_a_rule_attribute_that_is_also_a_weight_ignores_reddit_values_and_counts_reddit_scores() -> None:
    review = source("https://reviews.example/nova-book-14")
    thread = reddit("r/SuggestALaptop", "aaa111")
    claims = [
        claim(review, "Nova Book 14", "battery_life", value=6, unit="hours"),
        claim(thread, "Nova Book 14", "battery_life", value=12, unit="hours", n=1),
        claim(thread, "Nova Book 14", "battery_life", score=9.0, n=2),
    ]
    inp = verify_input(match("Nova Book 14"), claims, [review, thread], ["battery_life"])
    battery = value_of(_run(inp, ["price_inr", "battery_life"]), NOVA, "battery_life")
    assert battery.state is ValueState.KNOWN
    assert battery.value == "6 hours"
    assert battery.score == 9.0  # the community score still counts
    assert claims[1].id not in (*battery.supporting_claim_ids, *battery.conflicting_claim_ids)
    assert claims[2].id in battery.supporting_claim_ids


def test_a_rule_attribute_with_only_a_community_score_keeps_the_score_and_no_value() -> None:
    thread = reddit("r/SuggestALaptop", "aaa111")
    claims = [
        claim(thread, "Nova Book 14", "battery_life", value=12, unit="hours", n=1),
        claim(thread, "Nova Book 14", "battery_life", score=8.0, n=2),
    ]
    inp = verify_input(match("Nova Book 14"), claims, [thread], ["battery_life"])
    battery = value_of(_run(inp, ["battery_life"]), NOVA, "battery_life")
    assert (battery.state, battery.value, battery.score) == (ValueState.KNOWN, None, 8.0)


# --- filter keys last until the search is done with verify ---------------------------------------------------------


def test_filter_keys_survive_other_searches_until_forgotten() -> None:
    stage = TrustVerifyStage()
    stage.use_filter_keys(SEARCH_ID, ["ram_gb"])
    for n in range(200):  # a busy server: many searches set theirs meanwhile
        stage.use_filter_keys(f"srch_busy{n:06d}", ["storage_gb"])
    assert stage.filter_keys(SEARCH_ID) == frozenset({"price_inr", "ram_gb"})
    stage.forget(SEARCH_ID)
    assert stage.filter_keys(SEARCH_ID) == frozenset({"price_inr"})
