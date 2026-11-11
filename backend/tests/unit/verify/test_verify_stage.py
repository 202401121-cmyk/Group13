"""US-16 through the stage: claims reach their product, every product and key gets a value, and it is fast and stable.

Every site, product and claim here is made up.
"""

from __future__ import annotations

import time

import pytest

from app.contracts import (
    SourceType,
    StageContext,
    StageName,
    StageStatus,
    ValueState,
    VerifyResult,
    examples,
)
from app.contracts.common import STAGE_TIME_LIMITS_S
from app.pipeline import run_stage
from app.verify import TrustVerifyStage
from app.verify.resolve import unsourced
from tests.unit.verify.build import claim, match, reddit, source, value_of, verify_input


def _ctx() -> StageContext:
    """A fresh search context each time: one made at import would run out of its 150 seconds during a long
    test run, and run_stage would then report a timeout that never happened."""
    return StageContext(search_id="srch_verify0001")


NOVA = "prd_nova_book_14"


def _run(inp: object) -> VerifyResult:
    return TrustVerifyStage().run(inp, _ctx())  # type: ignore[arg-type]


def test_claims_reach_their_product_through_the_alias_map() -> None:
    maker = source("https://nova.example/book-14/specs", SourceType.MANUFACTURER)
    matched = match("Nova Book 14", aliases={"Nova 14": "Nova Book 14"})
    claims = [
        claim(maker, "Nova 14", "ram_gb", value=16, unit="GB"),
        claim(maker, "Some Other Laptop", "ram_gb", value=8, unit="GB"),  # not matched: left out
        claim(maker, "Nova Book 14", None, value="great"),  # a general opinion: no criterion
    ]
    result = _run(verify_input(matched, claims, [maker]))
    ram = value_of(result, NOVA, "ram_gb")
    assert (ram.state, ram.value) == (ValueState.KNOWN, "16 GB")
    assert ram.supporting_claim_ids == [claims[0].id]
    assert {v.product_id for v in result.values} == {NOVA}


def test_every_product_and_key_gets_a_value() -> None:
    review = source("https://reviews.example/nova-book-14")
    matched = match("Nova Book 14", "Orion Play 15")
    result = _run(verify_input(matched, [claim(review, "Nova Book 14", "battery_life", score=8.0)], [review]))
    assert len(result.values) == 2 * 3
    nova = value_of(result, NOVA, "battery_life")
    assert (nova.state, nova.score) == (ValueState.KNOWN, 8.0)
    for pid, key in [("prd_orion_play_15", "battery_life"), (NOVA, "price_inr"), (NOVA, "ram_gb")]:
        unknown = value_of(result, pid, key)
        assert unknown.state is ValueState.UNKNOWN
        assert unknown.score is None  # never scored as zero (DR-09)
    assert result.trust_method == "learned"


def test_price_and_rule_attributes_resolve_as_values() -> None:
    """US-17 filters on these, so they come out as values with their unit, not as scores."""
    shop = source("https://www.flipkart.com/nova-book-14/p/itm1", SourceType.RETAILER)
    other = source("https://www.amazon.in/Nova-Book-14/dp/B0TEST", SourceType.RETAILER)
    review = source("https://www.digit.in/reviews/nova-book-14.html")
    claims = [
        claim(shop, "Nova Book 14", "price_inr", value=64990, unit="INR"),
        claim(other, "Nova Book 14", "price_inr", value="₹65,490"),  # 0.8% apart: inside 3%
        claim(review, "Nova Book 14", "ram_gb", value="16 GB"),
    ]
    result = _run(verify_input(match("Nova Book 14"), claims, [shop, other, review]))
    price = value_of(result, NOVA, "price_inr")
    assert price.state is ValueState.KNOWN
    assert price.value in ("64990 INR", "₹65,490")
    assert price.score is None
    assert value_of(result, NOVA, "ram_gb").value == "16 GB"


def test_the_example_claims_resolve_with_a_disputed_battery() -> None:
    """The examples' Kestrel Slim 15: its maker says 10 hours, an owner on Reddit about 6."""
    inp = verify_input(examples.match(), examples.claims(), examples.sources(), ["battery_life", "ram_gb"])
    result = _run(inp)
    battery = value_of(result, "prd_kestrel_slim_15", "battery_life")
    assert battery.state is ValueState.DISPUTED
    assert battery.value == 10.0
    by_id = {c.id: c for c in examples.claims()}
    assert [by_id[i].value for i in battery.supporting_claim_ids] == [10.0]
    assert [by_id[i].value for i in battery.conflicting_claim_ids] == [6.0]
    assert value_of(result, "prd_nova_book_14", "ram_gb").value == 16.0
    assert VerifyResult.model_validate_json(result.model_dump_json()) == result


def test_same_inputs_same_output() -> None:
    inp = verify_input(examples.match(), examples.claims(), examples.sources(), ["battery_life", "ram_gb"])
    first = _run(inp)
    shuffled = inp.model_validate({**inp.model_dump(), "claims": list(reversed(inp.model_dump()["claims"]))})
    assert _run(inp) == first
    assert _run(shuffled) == first


def test_dropped_unsourced_stays_empty_for_now() -> None:
    """US-40 (S3-7) fills this through unsourced(); until then nothing is dropped."""
    inp = verify_input(examples.match(), examples.claims(), examples.sources(), ["battery_life"])
    assert unsourced(inp) == []
    assert _run(inp).dropped_unsourced == []


PRODUCTS_N = 40
LARGE_KEYS = [f"criterion_{k}" for k in range(25)] + ["price_inr", "ram_gb"]


def _large(sources_n: int = 60, claims_n: int = 5000) -> object:
    """A dense run: 40 products by 27 keys, each pair spoken to by 4 or 5 different sites that often disagree, so
    learning, disputes and every comparison across sites are exercised (each claim n lands on pair n mod 1080, from a
    different page each time it comes round)."""
    pages = [source(f"https://site-{i:02d}.example/page") for i in range(sources_n - 10)]
    pages += [reddit(f"r/sub{i}", f"p{i}") for i in range(10)]
    names = [f"Laptop Model {n:03d}" for n in range(PRODUCTS_N)]
    pairs = PRODUCTS_N * len(LARGE_KEYS)
    claims = []
    for n in range(claims_n):
        name, key = names[n % PRODUCTS_N], LARGE_KEYS[(n // PRODUCTS_N) % len(LARGE_KEYS)]
        lap = n // pairs
        page = pages[(n * 7 + lap * 13) % sources_n]
        if key == "price_inr":
            claims.append(claim(page, name, key, value=float(50000 + (n % 9) * 1000), unit="INR", n=n))
        elif key == "ram_gb":
            claims.append(claim(page, name, key, value="16 GB" if lap % 4 else "8 GB", n=n))
        else:
            claims.append(claim(page, name, key, score=float((n * 13) % 11), n=n))
    return verify_input(match(*names), claims, pages, LARGE_KEYS)


def test_60_sources_and_5000_claims_take_under_two_seconds() -> None:
    inp = _large()
    sites = {(c.product_mention, c.criterion_key): set() for c in inp.claims}  # type: ignore[attr-defined]
    for c in inp.claims:  # type: ignore[attr-defined]
        sites[(c.product_mention, c.criterion_key)].add(c.source_id)
    assert min(len(s) for s in sites.values()) >= 4  # the fixture really is dense
    started = time.perf_counter()
    result = _run(inp)
    assert time.perf_counter() - started < 2.0
    assert len(result.values) == PRODUCTS_N * len(LARGE_KEYS)
    states = {v.state for v in result.values}
    assert {ValueState.KNOWN, ValueState.DISPUTED} <= states
    out, report = run_stage(TrustVerifyStage(), inp, _ctx(), STAGE_TIME_LIMITS_S[StageName.VERIFY])  # type: ignore[arg-type]
    assert report.status is StageStatus.OK
    assert out == result


@pytest.mark.parametrize("bad_unit", ["GB", "kg"])
def test_a_value_in_another_dimension_is_left_out(bad_unit: str) -> None:
    a = source("https://a.example/nova")
    b = source("https://b.example/nova")
    c = source("https://c.example/nova")
    claims = [
        claim(a, "Nova Book 14", "battery_life", value=10, unit="h"),
        claim(b, "Nova Book 14", "battery_life", value=10.2, unit="hours"),
        claim(c, "Nova Book 14", "battery_life", value=6, unit=bad_unit),  # not a time: can't be compared
    ]
    battery = value_of(_run(verify_input(match("Nova Book 14"), claims, [a, b, c])), NOVA, "battery_life")
    assert battery.state is ValueState.KNOWN
    assert claims[2].id not in battery.supporting_claim_ids


@pytest.mark.parametrize(
    ("first", "second", "state"),
    [
        pytest.param(("https://a.example/nova", 9.0), ("https://b.example/nova", 4.0), ValueState.DISPUTED, id="even"),
        pytest.param(("https://a.example/nova", 8.0), ("reddit", 2.0), ValueState.DISPUTED, id="review-and-reddit"),
    ],
)
def test_disagreeing_scores_never_crash_the_stage(
    first: tuple[str, float], second: tuple[str, float], state: ValueState
) -> None:
    """No single score is within 2 points of the mean, yet the value still cites a claim on each side."""
    pages = [reddit() if url == "reddit" else source(url) for url, _ in (first, second)]
    claims = [
        claim(page, "Nova Book 14", "battery_life", score=score)
        for page, (_, score) in zip(pages, (first, second), strict=True)
    ]
    result = _run(verify_input(match("Nova Book 14"), claims, pages, ["battery_life"]))
    battery = value_of(result, NOVA, "battery_life")
    assert battery.state is state
    assert battery.supporting_claim_ids and battery.conflicting_claim_ids
    assert result.trust_method == "learned"  # no fallback was needed


def test_one_sites_scattered_scores_stay_known() -> None:
    thread_a, thread_b = reddit("r/laptops", "x1"), reddit("r/laptops", "x2")  # one subreddit: one voice
    claims = [
        claim(thread_a, "Nova Book 14", "keyboard_comfort", score=2.0),
        claim(thread_b, "Nova Book 14", "keyboard_comfort", score=8.0, n=1),
    ]
    result = _run(verify_input(match("Nova Book 14"), claims, [thread_a, thread_b], ["keyboard_comfort"]))
    keyboard = value_of(result, NOVA, "keyboard_comfort")
    assert (keyboard.state, keyboard.score) == (ValueState.KNOWN, 5.0)
    assert sorted(keyboard.supporting_claim_ids) == sorted(c.id for c in claims)


def test_a_forum_on_a_makers_domain_never_speaks_as_the_maker() -> None:
    """DR-03: an owner's post on asus.com is community evidence, a voice apart from the spec page."""
    spec = source("https://www.asus.com/laptops/nova/techspec/", SourceType.MANUFACTURER)
    forum = source("https://www.asus.com/community/forum/nova-battery/", SourceType.WEB_SEARCH)
    review = source("https://reviews.example/nova")
    claims = [
        claim(spec, "Nova Book 14", "battery_life", value=10, unit="h"),
        claim(forum, "Nova Book 14", "battery_life", value=6, unit="h"),
        claim(review, "Nova Book 14", "battery_life", value=10, unit="h"),
    ]
    battery = value_of(_run(verify_input(match("Nova Book 14"), claims, [spec, forum, review])), NOVA, "battery_life")
    assert battery.value == "10 h"
    assert battery.conflicting_claim_ids == [claims[1].id]  # the forum post, apart from the maker's own page
