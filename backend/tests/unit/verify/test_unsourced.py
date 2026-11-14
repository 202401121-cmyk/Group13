"""US-40 (S3-7, FR-25): a product no source names, and no claim about it quotes a source, is dropped before scoring.

Every site, product, quote and comment here is made up.
"""

from __future__ import annotations

import logging

import pytest

from app.contracts import MatchResult, Product, SourceType, StageContext, StageStatus, ValueState, VerifyResult
from app.pipeline import run_stage
from app.verify import TrustVerifyStage, combine
from app.verify.resolve import all_unknown, unsourced, verify
from app.verify.sourced import UNSOURCED_REASON
from app.verify.sourced import unsourced as unsourced_with_reasons
from app.verify.trust import default_trust_table
from tests.unit.verify.build import claim, reddit, source, verify_input

KEYS = ["battery_life", "price_inr"]
PLANTED = "Zentrix Pro 9"


def _products(*products: Product) -> MatchResult:
    alias_map = {name: p.id for p in products for name in (p.name, *p.aliases)}
    return MatchResult(products=list(products), alias_map=alias_map, method="fuzzy")


def _product(name: str, aliases: tuple[str, ...] = ()) -> Product:
    return Product(id="prd_" + "_".join(name.lower().split()), name=name, aliases=list(aliases))


NOVA = _product("Nova Book 14")
ZENTRIX = _product(PLANTED)


def _dropped(*args: object) -> list[str]:
    return unsourced(verify_input(*args))  # type: ignore[arg-type]


def test_a_product_named_only_in_a_title_counts() -> None:
    page = source("https://reviews.example/a", title="Nova Book 14 review", text="It is a fine laptop, we found.")
    assert _dropped(_products(NOVA, ZENTRIX), [], [page], KEYS) == [ZENTRIX.id]


def test_a_product_named_only_in_a_comment_counts() -> None:
    thread = reddit(text="Which laptop for coding?", comments=["Get the nova book 14, it is great."])
    assert _dropped(_products(NOVA, ZENTRIX), [], [thread], KEYS) == [ZENTRIX.id]


def test_an_alias_keeps_the_product() -> None:
    nova = _product("Nova Book 14", aliases=("NB14",))
    page = source("https://reviews.example/a", text="The NB14 lasts all day.")
    assert _dropped(_products(nova), [], [page], KEYS) == []


def test_a_product_only_in_a_quote_from_its_own_source_counts() -> None:
    """Neither the page nor the quote names it, but a claim about it quotes the page it came from."""
    page = source("https://reviews.example/a", text="I get about 9 hours on one charge, which is great.")
    found = claim(page, "Nova Book 14", "battery_life", score=8.0, quote="about 9 hours on one charge")
    assert _dropped(_products(NOVA, ZENTRIX), [found], [page], KEYS) == [ZENTRIX.id]


def test_a_quote_found_in_another_source_does_not_save_it() -> None:
    quoted = source("https://reviews.example/a", text="Zentrix claims nine hours of battery.")
    other = source("https://reviews.example/b", text="Some other page about nine hours of battery.")
    # The quote is in `other`, but the claim says it came from `quoted`, which does not hold it.
    found = claim(quoted, PLANTED, "battery_life", score=8.0, quote="some other page about nine hours")
    assert _dropped(_products(ZENTRIX), [found], [quoted, other], KEYS) == [ZENTRIX.id]


def test_a_claim_whose_quote_is_not_in_its_source_does_not_save_it() -> None:
    page = source("https://reviews.example/a", text="A page about other laptops entirely.")
    invented = claim(page, PLANTED, "battery_life", score=9.0, quote="Zentrix Pro 9 lasts twenty hours")
    assert _dropped(_products(ZENTRIX), [invented], [page], KEYS) == [ZENTRIX.id]


def test_a_claim_whose_source_is_missing_does_not_save_it() -> None:
    gone = source("https://reviews.example/gone", text="Zentrix Pro 9 lasts twenty hours.")
    found = claim(gone, PLANTED, "battery_life", score=9.0, quote="Zentrix Pro 9 lasts twenty hours")
    here = source("https://reviews.example/a", text="Nothing about it here.")
    assert _dropped(_products(ZENTRIX), [found], [here], KEYS) == [ZENTRIX.id]


def test_whole_words_only() -> None:
    """ "Pro 9" isn't found inside "Pro 99", nor "Zentrix Pro 9" inside "Zentrix Pro 99"."""
    page = source("https://reviews.example/a", text="The Zentrix Pro 99 is the one to buy.")
    pro9 = _product("Pro 9")
    assert _dropped(_products(ZENTRIX, pro9), [], [page], KEYS) == sorted([ZENTRIX.id, pro9.id])


def test_any_case_counts() -> None:
    page = source("https://reviews.example/a", text="NOVA BOOK 14 is my pick.")
    assert _dropped(_products(NOVA), [], [page], KEYS) == []


def test_each_drop_has_its_reason() -> None:
    page = source("https://reviews.example/a", text="Nova Book 14 is my pick.")
    inp = verify_input(_products(NOVA, ZENTRIX), [], [page], KEYS)
    assert unsourced_with_reasons(inp) == [(ZENTRIX.id, UNSOURCED_REASON)]
    assert UNSOURCED_REASON == "no source names it, and no claim about it quotes a source"


def test_a_planted_name_is_dropped_and_logged_once(caplog: pytest.LogCaptureFixture) -> None:
    page = source("https://reviews.example/a", text="Nova Book 14 is my pick. Its secret: a great keyboard.")
    inp = verify_input(_products(NOVA, ZENTRIX), [], [page], KEYS)
    with caplog.at_level(logging.INFO, logger="app.verify"):
        result = TrustVerifyStage().run(inp, StageContext(search_id="srch_unsourced01"))
    assert result.dropped_unsourced == [ZENTRIX.id]
    lines = [r.getMessage() for r in caplog.records if ZENTRIX.id in r.getMessage()]
    assert len(lines) == 1
    assert PLANTED in lines[0] and UNSOURCED_REASON in lines[0]
    assert all("secret" not in r.getMessage() for r in caplog.records)  # names only, never the source's text


def test_dropped_products_have_no_values() -> None:
    page = source("https://reviews.example/a", text="Nova Book 14 lasts nine hours.")
    claims = [claim(page, "Nova Book 14", "battery_life", score=8.0, quote="Nova Book 14 lasts nine hours")]
    inp = verify_input(_products(NOVA, ZENTRIX), claims, [page], KEYS)
    result = verify(inp, default_trust_table(), "learned")
    assert result.dropped_unsourced == [ZENTRIX.id]
    assert {v.product_id for v in result.values} == {NOVA.id}
    assert VerifyResult.model_validate_json(result.model_dump_json()) == result  # the contract's own check holds


def test_the_last_resort_honours_the_drops_too() -> None:
    page = source("https://reviews.example/a", text="Nova Book 14 lasts nine hours.")
    result = all_unknown(verify_input(_products(NOVA, ZENTRIX), [], [page], KEYS))
    assert result.dropped_unsourced == [ZENTRIX.id]
    assert {v.product_id for v in result.values} == {NOVA.id}
    assert all(v.state is ValueState.UNKNOWN for v in result.values)


def test_a_product_type_follows_the_same_rule() -> None:
    foam = Product(id="prd_memory_foam", name="memory foam", kind="product_type")
    nails = Product(id="prd_ring_shank_nails", name="ring-shank nails", kind="product_type")
    page = source("https://reviews.example/a", text="Memory foam pillows sleep warm.")
    assert _dropped(_products(foam, nails), [], [page], KEYS) == [nails.id]


def test_the_stage_counts_drops_per_search_until_it_forgets() -> None:
    stage = TrustVerifyStage()
    page = source("https://reviews.example/a", text="Nova Book 14 is my pick.")
    novalux, quorra = _product("Novalux Air 7"), _product("Quorra Book X3")
    inp = verify_input(_products(NOVA, ZENTRIX, novalux, quorra), [], [page], KEYS)
    stage.run(inp, StageContext(search_id="srch_unsourced02"))
    assert stage.report_notes("srch_unsourced02") == ["dropped 3 products no source names"]
    assert stage.report_notes("srch_unsourced03") == []  # another search dropped nothing
    stage.forget("srch_unsourced02")
    assert stage.report_notes("srch_unsourced02") == []


def test_one_drop_reads_in_the_singular() -> None:
    stage = TrustVerifyStage()
    page = source("https://reviews.example/a", text="Nova Book 14 is my pick.")
    stage.run(verify_input(_products(NOVA, ZENTRIX), [], [page], KEYS), StageContext(search_id="srch_unsourced04"))
    assert stage.report_notes("srch_unsourced04") == ["dropped 1 product no source names"]


def test_the_fallback_counts_drops_too() -> None:
    stage = TrustVerifyStage()
    page = source("https://reviews.example/a", text="Nova Book 14 is my pick.")
    inp = verify_input(_products(NOVA, ZENTRIX), [], [page], KEYS)
    out = stage.fallback(inp, StageContext(search_id="srch_unsourced05"), RuntimeError("made-up"))
    assert out.dropped_unsourced == [ZENTRIX.id]
    assert stage.report_notes("srch_unsourced05") == ["dropped 1 product no source names"]


def test_a_reddit_title_counts_as_a_source_naming_it() -> None:
    thread = reddit(title="Zentrix Pro 9 or Nova Book 14?", text="Help me choose.")
    assert thread.type is SourceType.REDDIT
    assert _dropped(_products(NOVA, ZENTRIX), [], [thread], KEYS) == []


def test_a_broken_check_in_the_last_resort_keeps_every_product(monkeypatch: pytest.MonkeyPatch) -> None:
    """Build rule 9: a bug in the check must not cost the search every product. Their values are all Unknown."""

    def broken(inp: object) -> list[tuple[str, str]]:
        raise RuntimeError("made-up bug")

    monkeypatch.setattr("app.verify.sourced.unsourced", broken)
    page = source("https://reviews.example/a", text="Nova Book 14 lasts nine hours.")
    result = all_unknown(verify_input(_products(NOVA, ZENTRIX), [], [page], KEYS))
    assert result.dropped_unsourced == []
    assert {v.product_id for v in result.values} == {NOVA.id, ZENTRIX.id}


# --- overlapping names (review item 2) -------------------------------------------------------------------------------


def test_two_overlapping_names_both_count() -> None:
    """Counting people keeps one name per stretch of text; sourcing counts every whole-word name, so neither
    "Samsung Galaxy S24" nor "Galaxy S24 Ultra" is dropped when a page names the Ultra in full."""
    s24, ultra = _product("Samsung Galaxy S24"), _product("Galaxy S24 Ultra")
    page = source("https://reviews.example/a", text="The Samsung Galaxy S24 Ultra has the best zoom we tried.")
    assert _dropped(_products(s24, ultra), [], [page], KEYS) == []


def test_a_name_inside_a_longer_word_still_does_not_count() -> None:
    page = source("https://reviews.example/a", text="The Galaxy S24Ultra and the Galaxy S245 are not it.")
    assert _dropped(_products(_product("Galaxy S24")), [], [page], KEYS) == ["prd_galaxy_s24"]


# --- logged once per search (review item 3) --------------------------------------------------------------------------


def _drop_lines(caplog: pytest.LogCaptureFixture, product_id: str) -> list[str]:
    return [r.getMessage() for r in caplog.records if product_id in r.getMessage() and "dropped" in r.getMessage()]


def test_the_check_itself_logs_nothing(caplog: pytest.LogCaptureFixture) -> None:
    page = source("https://reviews.example/a", text="Nova Book 14 is my pick.")
    with caplog.at_level(logging.DEBUG):
        assert unsourced_with_reasons(verify_input(_products(NOVA, ZENTRIX), [], [page], KEYS)) == [
            (ZENTRIX.id, UNSOURCED_REASON)
        ]
    assert caplog.records == []


def test_the_fallback_after_a_raising_run_logs_each_drop_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def broken(*args: object, **kwargs: object) -> object:
        raise RuntimeError("made-up failure")

    monkeypatch.setattr(combine, "learn_weights", broken)
    stage = TrustVerifyStage()
    page = source("https://reviews.example/a", text="Nova Book 14 is my pick.")
    novalux = _product("Novalux Air 7")
    inp = verify_input(_products(NOVA, ZENTRIX, novalux), [], [page], KEYS)
    ctx = StageContext(search_id="srch_unsourced06")
    with caplog.at_level(logging.INFO, logger="app.verify"):
        out, report = run_stage(stage, inp, ctx, 2.0)
    assert report.status is StageStatus.FALLBACK
    assert out is not None and out.dropped_unsourced == sorted([ZENTRIX.id, novalux.id])
    assert len(_drop_lines(caplog, ZENTRIX.id)) == 1
    assert len(_drop_lines(caplog, novalux.id)) == 1
    assert stage.report_notes(ctx.search_id) == ["dropped 2 products no source names"]


def test_a_run_then_its_fallback_log_once_and_count_distinct_drops(caplog: pytest.LogCaptureFixture) -> None:
    stage = TrustVerifyStage()
    page = source("https://reviews.example/a", text="Nova Book 14 is my pick.")
    inp = verify_input(_products(NOVA, ZENTRIX), [], [page], KEYS)
    ctx = StageContext(search_id="srch_unsourced07")
    with caplog.at_level(logging.INFO, logger="app.verify"):
        stage.run(inp, ctx)
        stage.fallback(inp, ctx, RuntimeError("made-up"))
        stage.run(inp, ctx)
    assert len(_drop_lines(caplog, ZENTRIX.id)) == 1
    assert stage.report_notes(ctx.search_id) == ["dropped 1 product no source names"]
    other = StageContext(search_id="srch_unsourced08")  # another search logs its own drops
    with caplog.at_level(logging.INFO, logger="app.verify"):
        stage.run(inp, other)
    assert len(_drop_lines(caplog, ZENTRIX.id)) == 2
    stage.forget(ctx.search_id)
    assert stage.report_notes(ctx.search_id) == []


# --- dropped claims never reach the combiner (review item 4) ---------------------------------------------------------


SITES = [f"https://site-{n}.example/review" for n in "abcd"]


def _learning_run(with_planted: bool) -> tuple[VerifyResult, dict[str, float]]:
    """Four sites on Nova's battery, one of them off. With `with_planted`, the planted product's claims, which no
    source quotes, have that site agreeing and the others not, which would teach the method to trust it."""
    pages = [source(url, text=f"Nova Book 14 lasts about {n + 9} hours.") for n, url in enumerate(SITES)]
    nova_hours = [10.0, 10.2, 9.9, 6.0]
    claims = [
        claim(page, "Nova Book 14", "battery_life", value=hours, unit="h", n=n)
        for n, (page, hours) in enumerate(zip(pages, nova_hours, strict=True))
    ]
    products = [NOVA]
    if with_planted:
        products.append(ZENTRIX)
        planted_hours = [3.0, 3.1, 2.9, 40.0]
        claims += [
            claim(page, PLANTED, "battery_life", value=hours, unit="h", n=10 + n, quote="Zentrix Pro 9 lasts all week")
            for n, (page, hours) in enumerate(zip(pages, planted_hours, strict=True))
        ]
    seen: dict[str, float] = {}
    real = combine.resolve_all

    def spy(*args: object, **kwargs: object) -> combine.Outcome:
        outcome = real(*args, **kwargs)  # type: ignore[arg-type]
        seen.update(outcome.weights)
        return outcome

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(combine, "resolve_all", spy)
        result = verify(
            verify_input(_products(*products), claims, pages, ["battery_life"]), default_trust_table(), "learned"
        )
    return result, seen


def test_a_dropped_products_claims_never_reach_the_combiner() -> None:
    plain, plain_weights = _learning_run(with_planted=False)
    planted, planted_weights = _learning_run(with_planted=True)
    assert planted.dropped_unsourced == [ZENTRIX.id]
    assert planted_weights == plain_weights  # no trust learned from the planted claims
    assert planted.values == plain.values  # Nova's value, sides and claims are the same
