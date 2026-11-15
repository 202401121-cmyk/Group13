"""US-14's funnel in the live briefs' record: where a search's sources stop turning into scored products, counted per
brief with a reason for each count. Everything here is offline, on the contracts' own example search."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.contracts import (
    Claim,
    DataClass,
    ExtractionResult,
    FilteredProduct,
    LLMCallRecord,
    LLMTask,
    Provider,
    SearchResult,
    examples,
)
from app.extract.settings import load_extract_settings
from evals import live_briefs

NOW = datetime(2026, 11, 15, 0, 30, tzinfo=UTC)
RESERVE = {DataClass.PUBLIC: 2000, DataClass.REDDIT: 1200, DataClass.PRIVATE: 2000}
NAMES = ("Nova Book 14", "Kestrel Slim 15", "Orion Play 15", "Aurora Air 13", "Vela Pro 14")


def _extractions(claims: list[Claim] | None = None) -> list[ExtractionResult]:
    by_source: dict[str, list[Claim]] = defaultdict(list)
    for claim in examples.claims() if claims is None else claims:
        by_source[claim.source_id].append(claim)
    return [ExtractionResult(source_id=s.id, claims=by_source.get(s.id, [])) for s in examples.sources()]


def _changed(claim: Claim, **fields: Any) -> Claim:
    return Claim.model_validate({**claim.model_dump(), **fields})


def _funnel(result: SearchResult | None = None, **given: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "extractions": _extractions(),
        "stage_counts": {"sent": 3, "over_reddit_budget": 0, "over_source_cap": 0, "unreadable": 0, "no_criteria": 0},
        "fallbacks": {},
        "dropped": {"criterion": 2, "quote": 1},
        "returned": {"claims": 27, "price_inr": 0},
        "alias_map": examples.match().alias_map,
        "calls": [],
        "reserve": RESERVE,
        "spec_keys": {"ram_gb", "price_inr"},
    }
    return live_briefs.funnel(result or examples.search_result(), **(args | given))


def _call(provider: Provider, data_class: DataClass, outcome: str, out: int = 300, search: str = "") -> LLMCallRecord:
    return LLMCallRecord(
        search_id=search or examples.search_result().search_id,
        task=LLMTask.EXTRACT_CLAIMS,
        data_class=data_class,
        provider=provider,
        model="a-model",
        input_tokens=900,
        output_tokens=out,
        duration_ms=1200,
        outcome=outcome,  # type: ignore[arg-type]  # one of the record's own outcomes
        at=NOW,
    )


def test_the_funnel_splits_kept_claims_by_kind_and_key() -> None:
    claims = examples.claims()
    unasked = _changed(claims[0], id="clm_00000000feedbeef", criterion_key="fan_noise")
    funnel = _funnel(extractions=_extractions([*claims, unasked]))
    kept = funnel["claims"]
    assert (kept["returned"], kept["kept"]) == (27, 25)
    assert kept["dropped_by_reason"] == {"criterion": 2, "quote": 1}
    assert kept["kept_by_kind"] == {"score": 20, "value": 5}  # 19 + the unasked score; 2 battery and 3 RAM values
    assert kept["kept_by_key"] == {"rubric": 21, "spec": 3, "neither": 1}  # RAM is a hard rule's key
    assert kept["kept_by_source_type"] == {"manufacturer": 2, "reddit": 7, "review_site": 16}
    assert kept["scorable"] == 19  # a score, on a rubric key, about a product match found


def test_claims_whose_name_matches_no_product_are_counted_as_verify_would() -> None:
    names = examples.match().alias_map
    aliases = {"nova book 14": names["Nova Book 14"], "Orion Play 15": names["Orion Play 15"]}  # case alone is fine
    funnel = _funnel(alias_map=aliases)
    assert funnel["match"] == {  # Kestrel's 9 are lost: no product has its name
        "claims_matching_a_product": 15,
        "claims_matching_nothing": 9,
        "nothing_by_reason": {"no_product": 9},
    }
    assert funnel["claims"]["scorable"] == 13  # Nova's 7 scores and Orion's 6

    spaced = {"Nova  Book 14": names["Nova Book 14"]}  # verify finds a name by its exact text, then its case only
    match = _funnel(alias_map=spaced)["match"]
    assert match["claims_matching_a_product"] == 0
    assert match["nothing_by_reason"] == {"no_product": 16, "spacing": 8}  # Nova's 8 differ only in spacing


def test_llm_calls_are_grouped_by_provider_data_class_and_outcome() -> None:
    calls = [
        _call(Provider.GROQ, DataClass.REDDIT, "ok"),
        _call(Provider.GROQ, DataClass.REDDIT, "invalid_output", out=1200),  # at the reserve: cut off
        _call(Provider.GEMINI, DataClass.PUBLIC, "invalid_output", out=400),  # bad JSON, not cut off
        _call(Provider.GEMINI, DataClass.PUBLIC, "skipped_budget", out=0),
        _call(Provider.CEREBRAS, DataClass.PUBLIC, "rate_limited", out=0),
        _call(Provider.GROQ, DataClass.PUBLIC, "ok", search="srch_another0001"),  # another search: not this brief's
    ]
    llm = _funnel(calls=calls)["llm"]
    assert llm["calls"] == 5
    assert llm["by_provider"] == {"cerebras": {"public": 1}, "gemini": {"public": 2}, "groq": {"reddit": 2}}
    assert llm["by_outcome"] == {"invalid_output": 2, "ok": 1, "rate_limited": 1, "skipped_budget": 1}
    assert (llm["failed"], llm["cut_off"], llm["no_room"]) == (4, 1, 2)


def test_products_held_back_for_price_are_counted() -> None:
    result = examples.search_result()
    assert result.decision is not None
    held = FilteredProduct(
        product_id="prd_aurora_air_13", group="cant_tell", reasons=["No price"], rule_attribute="price_inr"
    )
    decision = result.decision.model_validate({**result.decision.model_dump(), "filtered": [held.model_dump()]})
    changed = SearchResult.model_validate({**result.model_dump(), "decision": decision.model_dump()})
    value = next(c for c in examples.claims() if c.value is not None and c.criterion_key == "ram_gb")
    price = _changed(value, id="clm_00000000cafe0001", criterion_key="price_inr", value=74990, unit=None)
    lost = _changed(price, id="clm_00000000cafe0002", product_mention="Nova  Book 14")  # its name matches nothing
    off = _changed(price, id="clm_00000000cafe0003", value="₹5,000 off")  # a discount, not a price
    lakh = _changed(price, id="clm_00000000cafe0004", value="₹1.2 lakh")  # decide would read 1.2
    sources = {
        "s1": {"type": "review_site", "kind": "list", "sent": True, "price_asked": True, "price_returned": 3},
        "s2": {"type": "review_site", "kind": "list", "sent": True, "price_asked": True, "price_returned": 2},
        "s3": {"type": "reddit", "kind": "thread", "sent": True, "price_asked": False, "price_returned": 0},
        "s4": {"type": "retailer", "kind": "store", "sent": False, "price_asked": False, "price_returned": 0},
    }
    funnel = _funnel(
        changed,
        extractions=_extractions([*examples.claims(), price, lost, off, lakh]),
        returned={"price_inr": 7},
        price_dropped={"quote": 2, "product": 1},
        per_source=sources,
    )
    assert funnel["price"] == {
        "asked": True,
        "returned": 7,
        "dropped_by_reason": {"product": 1, "quote": 2},
        "kept": 4,
        "kept_unusable_by_reason": {"discount": 1, "k_or_lakh": 1},
        "attached": 3,  # all but the one whose name matches nothing
        "products_with_a_price": 0,  # verify gave the example's products no price value
        "shown_with_a_price": 0,
        "cant_tell_for_price": 1,
        "filtered_for_price": 0,
        "by_source": {
            "reddit.thread": {"reached": 1, "sent": 1, "gave_up": 0, "price_asked": 0, "price_returned": 0},
            "retailer.store": {"reached": 1, "sent": 0, "gave_up": 0, "price_asked": 0, "price_returned": 0},
            "review_site.list": {"reached": 2, "sent": 2, "gave_up": 0, "price_asked": 2, "price_returned": 5},
        },
    }
    assert funnel["held_back"] == {"filtered_out": 0, "cant_tell": 1, "cant_tell_for_price": 1}


def test_the_funnel_counts_sources_and_where_each_stopped() -> None:
    counts = {"sent": 1, "over_reddit_budget": 1, "over_source_cap": 0, "unreadable": 1, "no_criteria": 1}
    funnel = _funnel(extractions=_extractions()[:2], stage_counts=counts, fallbacks={"timeout": 1})
    assert funnel["sources"] == {
        "found": 3,
        "by_type": {"manufacturer": 1, "reddit": 1, "review_site": 1},
        "by_via": {"api": 1, "direct": 2},
    }
    assert funnel["extract"] == {
        "reached_the_stage": 2,
        "not_started_time_limit": 1,
        "search_snippet": 0,
        "over_source_cap": 0,
        "over_reddit_budget": 1,
        "over_reddit_share": 0,
        "sent": 1,
        "sent_with_no_criteria": 1,
        "no_valid_answer": 1,
        "no_answer_by_reason": {},
        "timed_out": 1,
        "failed": 0,
    }
    prompt = _funnel(settings=load_extract_settings())["public_prompt"]  # cpu_performance goes as processor_speed
    assert prompt == {
        "criteria": 7,
        "of_criteria": 7,
        "spec_keys": 2,
        "price_asked": True,
        "rules_asked": 1,
        "rules": 1,
    }
    assert "public_prompt" not in _funnel()
    shown = _funnel()["shown"]
    assert (shown["products"], shown["with_a_score"]) == (3, 3)
    assert shown["states"] == {"disputed": 1, "estimated": 1, "known": 19, "unknown": 0}
    three_known = {"disputed": 0, "estimated": 0, "known": 3, "unknown": 0}
    # by the rubric's order, never its keys: battery life is disputed for one, build durability estimated for one
    assert shown["by_criterion"] == [
        three_known,
        {"disputed": 1, "estimated": 0, "known": 2, "unknown": 0},
        three_known,
        three_known,
        three_known,
        {"disputed": 0, "estimated": 1, "known": 2, "unknown": 0},
        three_known,
    ]
    assert shown["per_product"][1] == {
        "rank": 2,
        "scored": True,
        "known": 6,
        "disputed": 1,
        "unknown": 0,
        "estimated": 0,
    }


def test_the_funnel_holds_counts_and_labels_only() -> None:
    calls = [_call(Provider.GROQ, DataClass.REDDIT, "ok"), _call(Provider.MISTRAL, DataClass.PUBLIC, "timeout")]
    funnel = _funnel(calls=calls)
    assert live_briefs.text_in({"funnel": funnel}) == []
    written = json.dumps(funnel)
    for text in (*NAMES, "http", examples.claims()[0].quote[:20], examples.search_result().request.raw_query[:20]):
        assert text not in written


def test_the_extract_wrapper_reads_the_counts_before_the_note_clears_them() -> None:
    class Stage:
        name = "extract"

        def counts_for(self, search_id: str) -> dict[str, int]:
            return {"sent": 2}

        def coverage_note(self, search_id: str) -> str:
            return "2 sources were not read."

    wrapper = live_briefs._KeepExtract(Stage())
    assert wrapper.coverage_note("srch_a") == "2 sources were not read."
    assert wrapper.counts == {"sent": 2}


def test_the_returned_claims_are_counted_before_the_parser_drops_any() -> None:
    from app.extract.parsing import RawClaim, RawExtraction

    raw = RawExtraction(
        mentions=[],
        claims=[
            RawClaim(product_mention="X", criterion_key=k, quote="q", score=5.0, polarity="neutral")
            for k in ("price_inr", "a", "b")
        ],
    )

    class Client:
        def complete(self, *args: Any, **kwargs: Any) -> RawExtraction:
            return raw

    counter = live_briefs._Returned(Client())
    assert counter.complete("task") is raw
    assert dict(counter.counts) == {"claims": 3, "price_inr": 1}


def test_the_options_choose_the_order_the_cap_and_the_output(tmp_path: Path) -> None:
    defaults = live_briefs.options([])
    assert (defaults.out, defaults.categories) == (live_briefs.LIVE_OUT, live_briefs.CATEGORIES)
    assert (defaults.max_sources, defaults.budget) == (live_briefs.MAX_SOURCES, live_briefs.BUDGET)

    out = tmp_path / "before.json"
    order = ["smartphone", "laptop", "earbuds", "air_fryer", "smartwatch"]
    budgets = ["--budget-llm", "30", "--budget-serper", "15", "--budget-reddit", "15", "--budget-jina", "10"]
    chosen = live_briefs.options(
        ["--out", str(out), "--categories", *order, "--max-sources", "4", *budgets, "--budget-firecrawl", "5"]
    )
    assert (chosen.out, chosen.categories, chosen.max_sources) == (out, tuple(order), 4)
    assert chosen.budget == {"llm": 30, "serper": 15, "reddit": 15, "jina": 10, "firecrawl": 5}

    seen: list[tuple[str, dict[str, int]]] = []

    def run(category: str, query: str) -> dict[str, Any]:
        seen.append((category, dict(budget._caps)))
        return {"category": category, "finished": False, "why": live_briefs.NOT_FINISHED[1]}

    budget = live_briefs.LiveBudget(chosen.budget)
    record = live_briefs.run_all(chosen, budget, run)
    assert [category for category, _ in seen] == order  # in the order given
    assert seen[0][1] == {"llm": 6, "serper": 3, "reddit": 3, "jina": 2, "firecrawl": 1}  # an even share each
    assert "EXTRACT_MAX_SOURCES=4" in record["budget_note"] and "'serper': 15" in record["budget_note"]

    out.write_text(json.dumps(record), encoding="utf-8")
    assert live_briefs.main(["--rebuild", "--out", str(out)]) == 0  # rebuilds the file it is given, not the record
    assert json.loads(out.read_text(encoding="utf-8"))["measures"]


def test_an_unknown_category_is_refused() -> None:
    with pytest.raises(SystemExit):
        live_briefs.options(["--categories", "toaster"])


class _QuotingClient:
    """Answers each source with the example claims whose quotes are in it, as a model reading it would."""

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, task: LLMTask, system: str, user: str, schema: type[Any], **kwargs: Any) -> Any:
        from app.extract.parsing import RawClaim, RawExtraction

        self.calls += 1
        claims = [
            RawClaim(
                product_mention=c.product_mention,
                criterion_key=c.criterion_key,
                value=c.value if isinstance(c.value, float | int | str) else None,
                unit=c.unit,
                score=c.score,
                quote=c.quote,
                polarity=c.polarity,
            )
            for c in examples.claims()
            if c.quote in user
        ]
        return RawExtraction(mentions=[], claims=claims)


def test_a_brief_runs_offline_and_records_its_funnel(monkeypatch: pytest.MonkeyPatch) -> None:
    """The wiring the live run relies on: the counting client, the stage's counts read before the note, match's names
    and the parser's drop log all reach the funnel. The research stub's example sources, and a fake model."""
    from app.extract import GuardedExtractStage
    from app.extract.settings import ExtractSettings

    client = _QuotingClient()
    settings = load_extract_settings(env={})
    small = ExtractSettings.model_validate({**settings.model_dump(), "max_sources_per_search": 4})
    monkeypatch.setattr("app.pipeline.default_extract_stage", lambda: GuardedExtractStage(client, small))
    brief = live_briefs.run_brief("laptop", live_briefs.QUERIES["laptop"])
    assert brief["finished"] is True
    funnel = brief["funnel"]
    extract, claims = funnel["extract"], funnel["claims"]
    assert extract["sent"] == client.calls == extract["reached_the_stage"] == funnel["sources"]["found"]
    assert claims["returned"] >= claims["kept"] > 0
    assert claims["kept"] == sum(claims["kept_by_key"].values()) == sum(claims["kept_by_kind"].values())
    match = funnel["match"]
    assert match["claims_matching_a_product"] + match["claims_matching_nothing"] == claims["kept"]
    assert funnel["public_prompt"]["price_asked"] is True  # the brief has a budget
    assert live_briefs.text_in(brief) == []


def test_a_kept_price_decide_cannot_use_is_named_by_why() -> None:
    usable = [(24999, None), (24999, "INR"), ("₹24,999", None), ("Rs. 24,999", None), ("24,999 INR", None)]
    for value, unit in usable:
        assert live_briefs.price_problem(value, unit) is None, value
    assert live_briefs.price_problem("₹2,000 off", None) == "discount"
    assert live_briefs.price_problem("25k", "INR") == "k_or_lakh"
    assert live_briefs.price_problem(299, "USD") == "other_currency"
    assert live_briefs.price_problem("ask the shop", None) == "unreadable"
    assert live_briefs.price_problem(True, None) == "unreadable"


def test_each_page_is_labelled_by_kind_from_its_type_title_and_names() -> None:
    assert live_briefs.page_kind("review_site", "Best laptops under 70000", 0) == "list"
    assert live_briefs.page_kind("review_site", "A made-up laptop review", 3) == "list"
    assert live_briefs.page_kind("review_site", "A made-up laptop review", 1) == "review"
    assert live_briefs.page_kind("retailer", "Best price", 9) == "store"
    assert live_briefs.page_kind("reddit", "Best laptop?", 4) == "thread"


def test_the_extract_wrapper_records_each_source_with_its_price_counts() -> None:
    from app.extract.parsing import RawClaim, RawExtraction

    priced = RawClaim(product_mention="X", criterion_key="price_inr", value=1.0, quote="q", polarity="neutral")
    prompt = "\n".join(["Category: laptop", "Criteria:", "- battery_life: how long it lasts", "- price_inr: its price"])

    class Client:
        def complete(self, task: Any, system: str, user: str, schema: Any, **kwargs: Any) -> RawExtraction:
            return RawExtraction(mentions=[], claims=[priced, priced])

    class Stage:
        name = "extract"

        def __init__(self, llm: Any) -> None:
            self.llm = llm

        def run(self, inp: Any, ctx: Any) -> ExtractionResult:
            if inp.source.type.value != "manufacturer":  # the maker's page is skipped, as if past the cap
                self.llm.complete("task", "system", prompt, RawExtraction)
            return ExtractionResult(source_id=inp.source.id)

    returned = live_briefs._Returned(Client())
    wrapper = live_briefs._KeepExtract(Stage(returned), returned)
    for source in examples.sources():
        wrapper.run(type("Inp", (), {"source": source})(), None)
    rows = {row["type"]: row for row in wrapper.sources.values()}
    assert rows["review_site"] == {
        "type": "review_site",
        "kind": "review",
        "sent": True,
        "price_asked": True,
        "price_returned": 2,
        "claims_returned": 2,
        "answer": "ok",
        "gave_up": False,
    }
    assert (rows["manufacturer"]["kind"], rows["manufacturer"]["sent"]) == ("maker", False)
    assert (rows["reddit"]["kind"], rows["reddit"]["price_returned"]) == ("thread", 2)
    assert returned.counts["price_inr"] == 4


# --- after the review: cut-off limits, runs given up on, why a source got no answer, drops by source, the cap ---------


def _fitted(call: LLMCallRecord, fitted: str) -> LLMCallRecord:
    return LLMCallRecord.model_validate({**call.model_dump(), "fitted": fitted})


def test_cut_off_uses_the_limit_the_call_really_had() -> None:
    groq_last_resort = _call(
        Provider.GROQ, DataClass.PUBLIC, "invalid_output", out=1200
    )  # Groq answers a page in 1,200
    gemini_full = _call(Provider.GEMINI, DataClass.PUBLIC, "invalid_output", out=1200)  # Gemini had 2,000: not cut off
    chunked = _fitted(_call(Provider.GEMINI, DataClass.PUBLIC, "invalid_output", out=2000), "chunked")  # chunks add up
    llm = _funnel(calls=[groq_last_resort, gemini_full, chunked], last_resort={"groq": 1200})["llm"]
    assert llm["cut_off"] == 1
    assert _funnel(calls=[groq_last_resort])["llm"]["cut_off"] == 0  # with no last-resort size, the stage's 2,000


class _Inp:
    def __init__(self, source: Any) -> None:
        self.source = source


PRICE_PROMPT = "\n".join(["Criteria:", "- price_inr: its price"])


def test_a_source_the_runner_gave_up_on_is_left_out_of_the_counts() -> None:
    from app.extract.parsing import RawClaim, RawExtraction

    claim = RawClaim(product_mention="X", criterion_key="price_inr", value=1.0, quote="q", polarity="neutral")

    class Client:
        def complete(self, *args: Any, **kwargs: Any) -> RawExtraction:
            return RawExtraction(mentions=[], claims=[claim, claim, claim])

    class Stage:
        name = "extract"

        def __init__(self, llm: Any) -> None:
            self.llm = llm

        def run(self, inp: Any, ctx: Any) -> ExtractionResult:
            self.llm.complete("task", "system", PRICE_PROMPT, RawExtraction)
            return ExtractionResult(source_id=inp.source.id)

        def fallback(self, inp: Any, ctx: Any, error: Exception) -> ExtractionResult:
            return ExtractionResult(source_id=inp.source.id)

    returned = live_briefs._Returned(Client())
    wrapper = live_briefs._KeepExtract(Stage(returned), returned)
    late, on_time = examples.sources()[1], examples.sources()[2]
    wrapper.fallback(_Inp(late), None, TimeoutError("took too long"))  # the runner gave up on it...
    wrapper.run(_Inp(late), None)  # ...and its run finished later anyway
    wrapper.run(_Inp(on_time), None)
    rows, outputs = wrapper.snapshot()
    assert (rows[late.id]["gave_up"], rows[late.id]["answer"]) == (True, "gave_up")
    assert rows[on_time.id]["answer"] == "ok"
    used, _, _ = live_briefs.tally(rows)
    assert used == {"claims": 3, "price_inr": 3}  # only the source the search used
    assert wrapper.fallbacks == {"timeout": 1}
    assert len(outputs) == 3  # the fallback's, the late run's and the other: _first_per_source keeps the fallback's


def test_why_a_source_got_no_answer_is_recorded_from_its_own_attempts() -> None:
    from app.contracts import LLMUnavailable
    from app.llm import InMemoryCallLog

    def ended(outcomes: list[str], refused: int = 0) -> str:
        log = InMemoryCallLog()

        class Client:
            call_log = log

            def complete(self, *args: Any, **kwargs: Any) -> Any:
                for outcome in outcomes:
                    log.record(_call(Provider.GROQ, DataClass.REDDIT, outcome, out=0))
                live_briefs._CURRENT.refused = refused
                raise LLMUnavailable("no provider")

        returned = live_briefs._Returned(Client())
        undo = returned.watch()
        try:
            returned.start()
            with pytest.raises(LLMUnavailable):
                returned.complete("task", "system", "user", object)
        finally:
            undo()
            live_briefs._CURRENT.refused = 0
        assert "record" not in vars(log)  # the log is left as it was
        call = returned.last()
        assert call is not None
        return str(call["answer"])

    assert ended(["rate_limited", "skipped_budget"]) == "no_room"
    assert ended(["timeout", "rate_limited"]) == "timed_out"
    assert ended(["invalid_output", "invalid_output"]) == "failed"
    assert ended(["error"], refused=1) == "over_run_budget"


def test_parser_drops_are_credited_to_the_source_that_made_them_only() -> None:
    import logging

    wrapper = live_briefs._KeepExtract(type("Stage", (), {"name": "extract"})())
    handler = live_briefs._Dropped(wrapper)
    source = examples.sources()[1]
    wrapper.started.add(source.id)

    def log(message: str, counts: dict[str, int]) -> logging.LogRecord:
        return logging.LogRecord("app.extract.parsing", logging.INFO, "", 0, message, ("review_site", counts), None)

    live_briefs._CURRENT.source = source.id
    handler.emit(log("extract: claims dropped from a %s source: %s", {"quote": 2}))
    handler.emit(log("extract: price claims dropped from a %s source: %s", {"quote": 1}))
    live_briefs._CURRENT.source = "src_from_another_brief"  # a late run from an earlier brief
    handler.emit(log("extract: claims dropped from a %s source: %s", {"product": 5}))
    live_briefs._CURRENT.source = None
    assert (wrapper.drops, wrapper.price_drops) == ({source.id: {"quote": 2}}, {source.id: {"quote": 1}})


def test_the_cap_option_never_lifts_configs_cap() -> None:
    for bad in ("0", "41", "eight"):
        with pytest.raises(SystemExit):
            live_briefs.options(["--max-sources", bad])
    assert live_briefs.options(["--max-sources", "40"]).max_sources == 40
    record = {"budget_note": "limits {}, EXTRACT_MAX_SOURCES=4, MATCHER=fuzzy"}
    assert live_briefs._recorded_cap(record, 8) == 4  # --rebuild describes the cap the record was made with
    assert live_briefs._recorded_cap({}, 8) == 8


def test_a_search_snippet_is_counted_as_one() -> None:
    result = examples.search_result()
    snippet = next(s for s in result.sources if s.type.value == "manufacturer")
    sources = [
        {**s.model_dump(), "via": "search_snippet"} if s.id == snippet.id else s.model_dump() for s in result.sources
    ]
    changed = SearchResult.model_validate({**result.model_dump(), "sources": sources})
    assert _funnel(changed)["extract"]["search_snippet"] == 1
