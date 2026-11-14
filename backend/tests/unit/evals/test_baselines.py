"""S3-18 (task #45): the Sprint 3 baselines. The offline runner, the floors in evals/thresholds.yaml and its schema,
and the live record. Everything here is offline: the live briefs ran once, by hand (python -m evals.live_briefs)."""

from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml  # type: ignore[import-untyped]  # PyYAML ships no type stubs
from pydantic import ValidationError

from app.contracts import DecideInput
from app.decide import decide
from evals import checks, live_briefs, pairs, run_baselines, thresholds

BRIEFS = run_baselines.default_paths().briefs


def _brief(name: str) -> DecideInput:
    return DecideInput.model_validate(json.loads((BRIEFS / f"{name}.json").read_text(encoding="utf-8"))["input"])


# --- the offline runner -------------------------------------------------------------------------------------------


def test_the_offline_run_gives_the_same_numbers_twice() -> None:
    first = run_baselines.run_offline(run_baselines.default_paths())
    second = run_baselines.run_offline(run_baselines.default_paths())
    assert first["measures"] == second["measures"]
    assert first["context"] == second["context"]
    assert any(m["status"] == "measured" for m in first["measures"])


def test_the_committed_offline_record_is_what_the_runner_gives_now() -> None:
    committed = json.loads(run_baselines.OFFLINE_OUT.read_text(encoding="utf-8"))
    assert committed["measures"] == run_baselines.run_offline(run_baselines.default_paths())["measures"]


def test_a_missing_result_file_is_reported_not_invented(tmp_path: Path) -> None:
    paths = run_baselines.default_paths()
    shutil.copy(paths.ml_results / "matcher_v1.json", tmp_path / "matcher_v1.json")  # matching_baseline.json is gone
    found = {m["id"]: m for m in run_baselines.run_offline(replace(paths, ml_results=tmp_path))["measures"]}
    fuzzy = [m for key, m in found.items() if key.startswith("matching.fuzzy.")]
    assert fuzzy and all(m["status"] == "missing" and m["value"] is None for m in fuzzy)
    assert all("matching_baseline.json" in m["detail"] for m in fuzzy)
    assert found["matching.model.own_pairs.precision"]["status"] == "measured"  # the file that is there still counts


def test_what_nothing_recorded_can_measure_is_marked_not_recorded() -> None:
    found = {m["id"]: m for m in run_baselines.run_offline(run_baselines.default_paths())["measures"]}
    assert found["extraction.quote_found_share"]["status"] == "not_recorded"
    assert found["extraction.quote_found_share"]["value"] is None


# --- decide: the checks behind the safety floors --------------------------------------------------------------------


def test_a_shown_product_that_breaks_a_hard_rule_is_counted() -> None:
    inp = _brief("laptop_coding_70k")
    result = decide(inp)
    shown = [p for p in inp.products if p.id in {r.product_id for r in result.ranked}]
    assert checks.shown_violations(inp.rubric, shown, inp.values) == (0, 0)
    over_budget = next(p for p in inp.products if p.id == "prd_aurora_air_13")  # ₹84,990 against a ₹70,000 budget
    assert checks.shown_violations(inp.rubric, [*shown, over_budget], inp.values) == (1, 0)


def test_the_rule_check_is_independent_of_decides_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    """A filter that lets everything through must not blind the check: it reads the rubric itself."""
    from app.decide import rules
    from app.decide.rules import FilterOutcome

    monkeypatch.setattr(rules, "filter_products", lambda products, *a, **k: FilterOutcome(list(products), [], None))
    inp = _brief("laptop_coding_70k")
    pico = next(p for p in inp.products if p.id == "prd_pico_lite_14")  # 8 GB against "at least 16 GB"
    assert checks.shown_violations(inp.rubric, [pico], inp.values) == (1, 0)


def test_the_most_mentioned_product_winning_while_fitting_worst_is_counted() -> None:
    ranked = [("a", 1), ("b", 2), ("c", 3)]  # a is mentioned most and fits worst, yet ranks first
    fits = {"a": 4.0, "b": 7.0, "c": 5.5}
    assert checks.most_mentioned_wins_fitting_worst(ranked, fits, {"a": 120, "b": 1, "c": 10}) is True
    assert checks.most_mentioned_wins_fitting_worst(ranked, fits, {"a": 1, "b": 120, "c": 10}) is False
    tied = {"a": 4.0, "b": 7.0, "c": 4.0}  # a tie for the worst fit still counts when a better one ranks below
    assert checks.most_mentioned_wins_fitting_worst(ranked, tied, {"a": 120, "b": 1, "c": 10}) is True
    assert checks.most_mentioned_wins_fitting_worst(ranked, {"a": None, "b": None}, {"a": 9, "b": 1}) is False
    inp = _brief("laptop_coding_70k")  # its most-mentioned laptop fits worst (tests/fixtures/decide/README.md)
    result = decide(inp)
    counts = {p.id: p.recommender_count for p in inp.products}
    indexed = checks.by_product(inp.values)
    brief_fits = {r.product_id: checks.fit(inp.rubric, indexed.get(r.product_id, {})) for r in result.ranked}
    assert all(f is not None for f in brief_fits.values())  # fit is read from the values, not decide's score
    assert (
        checks.most_mentioned_wins_fitting_worst([(r.product_id, r.rank) for r in result.ranked], brief_fits, counts)
        is False
    )


def test_a_shown_product_with_no_source_is_counted() -> None:
    from app.contracts import Product, SourceType
    from tests.unit.verify.build import source

    page = source("https://reviews.example/nova-14", SourceType.REVIEW_SITE, text="The Nova Book 14 is light.")
    named = Product(id="prd_nova_book_14", name="Nova Book 14")
    nowhere = Product(id="prd_orbit_pad_11", name="Orbit Pad 11")
    assert checks.unsourced([named], [], [page], []) == 0
    assert checks.unsourced([named, nowhere], [], [page], []) == 1


# --- matching: the stage on labelled pairs ---------------------------------------------------------------------------


def test_the_stage_pair_check_reports_same_variant_and_different_apart() -> None:
    made_up = [
        pairs.LabelledPair("p1", "Sony WH-1000XM5", "Sony WH1000XM5 headphones", "same"),
        pairs.LabelledPair("p2", "Galaxy A35", "Galaxy A55", "different"),
        pairs.LabelledPair("p3", "iPhone 16 128GB", "iPhone 16 256GB", "variant"),
    ]
    rates = pairs.stage_rates(made_up)
    assert rates == {"same": (1, 1), "different": (0, 1), "variant": (1, 1), "mixed": (0, 0)}


def test_labels_are_read_as_s3_5_reads_them(tmp_path: Path) -> None:
    header = "pair_id,left_title,right_title,category,product_family,label,labeller,labelled_at,notes\n"
    (tmp_path / "batches").mkdir()
    (tmp_path / "batches" / "agree-1.pairs.csv").write_text("pair_id\n", encoding="utf-8")  # names the batch
    (tmp_path / "agree-1.machine-a.csv").write_text(
        header
        + "p1,Nova 14,Nova 14 Laptop,laptop,nova,same,Machine A,2026-11-10,\n"
        + "p2,Kettle Pro,Kettle Max,kettle,k,different,Machine A,2026-11-10,\n"
        + "p3,Vela X1,Vela X1 Pro,phone,vela,unsure,Machine A,2026-11-10,\n"
        + "p4,Orion 5,Orion 5 (Black),phone,orion,same,Machine A,2026-11-10,\n"
        + "p5,Lyra 2,Lyra 2 8GB,phone,lyra,variant,Machine A,2026-11-10,\n",
        encoding="utf-8",
    )
    (tmp_path / "agree-1.machine-b.csv").write_text(
        header
        + "p1,Nova 14,Nova 14 Laptop,laptop,nova,same,Machine B,2026-11-10,\n"
        + "p2,Kettle Pro,Kettle Max,kettle,k,different,Machine B,2026-11-10,\n"
        + "p3,Vela X1,Vela X1 Pro,phone,vela,different,Machine B,2026-11-10,\n"
        + "p4,Orion 5,Orion 5 (Black),phone,orion,variant,Machine B,2026-11-10,\n"
        + "p5,Lyra 2,Lyra 2 8 GB,phone,lyra,different,Machine B,2026-11-10,\n",
        encoding="utf-8",
    )
    found, dropped = pairs.load_pairs(tmp_path)
    assert [(p.pair_id, p.kind) for p in found] == [("p1", "same"), ("p2", "different")]
    assert dropped == {"unsure": 1, "labellers disagree": 1, "texts differ": 1}


# --- thresholds.yaml --------------------------------------------------------------------------------------------------


def _committed() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(thresholds.THRESHOLDS.read_text(encoding="utf-8"))
    return data


def test_thresholds_yaml_validates_against_its_schema() -> None:
    loaded = thresholds.load(thresholds.THRESHOLDS)
    assert loaded.version == 1
    assert loaded.measures


def _with(measure_id: str, **changes: Any) -> dict[str, Any]:
    data = _committed()
    data["measures"][measure_id] = {**data["measures"][measure_id], **changes}
    return data


def test_a_floor_breaking_the_schema_is_refused() -> None:
    bad = [
        _with("decide.hard_rule_violations_shown", floor=1),  # a safety floor is exact: zero
        _with("decide.hard_rule_violations_shown", direction="at_least"),
        _with("decide.coverage_mean", floor=1.0),  # a quality floor above what was measured
        _with("matching.model.own_pairs.precision", target={"value": 0.9, "source": "lowered"}),  # a target lowered
        _with("trust.learned_minus_majority", target=None),  # a plan target dropped
        _with("decide.coverage_mean", colour="blue"),  # a field the schema doesn't know
        _with("decide.coverage_mean", kind="safety", direction="at_most", floor=0),  # a quality measure made safety
        {**_committed(), "version": 2},
    ]
    for data in bad:
        with pytest.raises(ValidationError):
            thresholds.Thresholds.model_validate(data)


def test_every_measured_value_has_exactly_one_floor() -> None:
    measured = {m["id"] for m in thresholds.all_measures() if m["status"] == "measured"}
    assert measured == set(thresholds.load(thresholds.THRESHOLDS).measures)


def test_the_committed_measures_pass_their_floors() -> None:
    assert thresholds.check(thresholds.load(thresholds.THRESHOLDS), thresholds.all_measures()) == []


def test_the_gate_names_a_measure_below_its_floor_or_missing() -> None:
    loaded = thresholds.load(thresholds.THRESHOLDS)
    measures = [dict(m) for m in thresholds.all_measures()]
    worse = next(m for m in measures if m["id"] == "decide.coverage_mean")
    worse["value"] = 0.0
    gone = next(m for m in measures if m["id"] == "trust.learned.correct")
    measures.remove(gone)
    problems = thresholds.check(loaded, measures)
    assert any("decide.coverage_mean" in p and "below its floor" in p for p in problems)
    assert any("trust.learned.correct" in p and "missing" in p for p in problems)


def test_a_deleted_floor_is_caught_by_the_gate() -> None:
    data = _committed()
    del data["measures"]["decide.hard_rule_violations_shown"]  # still valid yaml, and the schema can't know
    problems = thresholds.check(thresholds.Thresholds.model_validate(data), thresholds.all_measures())
    assert problems == ["decide.hard_rule_violations_shown is measured but has no floor in thresholds.yaml"]


def test_a_difference_floor_is_never_clamped_at_zero() -> None:
    measured = {"id": "trust.learned_minus_majority", "unit": "conflicts", "detail": "learned 2 against majority 3"}
    rule = thresholds.rule_for(measured)
    assert thresholds.floor_for(rule, -1.0) == -2.0
    count = thresholds.rule_for({"id": "live.claims_per_source_given", "unit": "claims", "detail": "x"})
    assert thresholds.floor_for(count, 2.3) == 2.07  # 10% below, to the hundredth, with no float error


# --- the live record and its guard ----------------------------------------------------------------------------------


def test_the_live_record_keeps_numbers_only() -> None:
    record = json.loads(live_briefs.LIVE_OUT.read_text(encoding="utf-8"))
    assert live_briefs.text_in(record) == []  # no quote, name, link or request: numbers and fixed labels only
    assert len(record["briefs"]) == 5


def test_the_live_guard_refuses_a_call_past_its_share() -> None:
    budget = live_briefs.LiveBudget({"llm": 1, "serper": 0, "reddit": 2, "jina": 0, "firecrawl": 0})
    arctic = httpx.Request("GET", "https://arctic-shift.photon-reddit.com/api/posts/search")
    budget.admit(arctic)
    budget.admit(arctic)  # Reddit's archives are allowed here, up to their share
    with pytest.raises(live_briefs.OverBudget):
        budget.admit(arctic)
    budget.admit(httpx.Request("POST", "https://api.groq.com/v1/chat/completions"))
    with pytest.raises(live_briefs.OverBudget):
        budget.admit(httpx.Request("POST", "https://api.groq.com/v1/chat/completions"))
    with pytest.raises(live_briefs.OverBudget):
        budget.admit(httpx.Request("POST", "https://google.serper.dev/search"))
    assert budget.used == {"llm": 1, "serper": 0, "reddit": 2, "jina": 0, "firecrawl": 0}


def test_a_brief_is_held_to_its_share_though_the_total_has_room() -> None:
    budget = live_briefs.LiveBudget({"llm": 10, "serper": 10, "reddit": 10, "jina": 10, "firecrawl": 10})
    serper = httpx.Request("POST", "https://google.serper.dev/search")
    with budget.share(serper=1):
        budget.admit(serper)
        with pytest.raises(live_briefs.OverBudget):
            budget.admit(serper)  # the brief's share is spent, though 9 are left in all
    budget.admit(serper)  # the next brief starts with what is left
    assert budget.used["serper"] == 2


def test_a_name_in_a_key_is_caught_too() -> None:
    assert live_briefs.text_in({"coverage": {"Nova Book 14": 0.5}}) == ["Nova Book 14"]
    assert live_briefs.text_in({"stages": {"research": {"status": "ok"}}}) == []
