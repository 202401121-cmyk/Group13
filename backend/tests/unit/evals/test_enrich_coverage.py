"""US-41 (S5-6, DECIDED 7): the top 5's data coverage before and after enrich, as evals/enrich_coverage.py measures it.
Offline: the recorded gate runs replayed with the stub enrich, so nothing is looked up and no call is made."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.contracts import ResolvedValue, ValueState
from app.contracts import examples as ex
from app.enrich import EnrichStage
from evals import enrich_coverage, live_briefs
from evals.gate import recordings


def _value(product_id: str, key: str, state: ValueState) -> ResolvedValue:
    cited = state in (ValueState.KNOWN, ValueState.DISPUTED)
    return ResolvedValue.model_validate(
        {
            "product_id": product_id,
            "criterion_key": key,
            "state": state,
            "confidence": 0.8,
            "value": 8.0 if cited else None,
            "score": 6.0 if state is ValueState.ESTIMATED else None,
            "supporting_claim_ids": ["clm_aaaaaaa1"] if cited else [],
            "conflicting_claim_ids": ["clm_bbbbbbb1"] if state is ValueState.DISPUTED else [],
        }
    )


def test_evidence_counts_the_shoppers_whole_weight_and_only_known_or_disputed_values() -> None:
    rubric = ex.rubric()
    weight = {w.key: w.normalized for w in rubric.weights}
    values = [
        _value("prd_a_1", "battery_life", ValueState.KNOWN),
        _value("prd_a_1", "weight", ValueState.DISPUTED),
        _value("prd_a_1", "cpu_performance", ValueState.ESTIMATED),  # an estimate is no evidence
        _value("prd_b_2", "battery_life", ValueState.UNKNOWN),
    ]
    share = enrich_coverage.evidence_share(rubric, ["prd_a_1", "prd_b_2"], values)
    assert share == pytest.approx((weight["battery_life"] + weight["weight"]) / 2)
    assert enrich_coverage.evidence_share(rubric, [], values) is None


def test_a_replay_records_before_and_after_and_its_look_ups() -> None:
    folder = next(f for f in recordings() if f.name == "gate_laptop")
    run = enrich_coverage.replay_coverage(folder, EnrichStage)
    assert run["rankings"] == 2  # the first ranking, then decide's own
    assert run["before"] == run["after"]  # the stub looks nothing up
    assert run["look_ups"]["fact_targets"] > 0 and run["look_ups"]["llm_calls"] == 0
    assert live_briefs.text_in({"runs": {folder.name: run}}) == []  # numbers and fixed labels only


def test_live_briefs_take_people_written_briefs_from_a_file_and_never_write_their_words(tmp_path: Path) -> None:
    briefs = tmp_path / "briefs.json"
    briefs.write_text(json.dumps([{"category": "ereader", "request": "a made-up e-reader request"}]), encoding="utf-8")
    opts = live_briefs.options(["--briefs", str(briefs), "--budget-llm", "10"])
    assert opts.briefs == (("ereader", "a made-up e-reader request"),)
    seen: list[str] = []
    record = live_briefs.run_all(
        opts, live_briefs.LiveBudget(opts.budget), lambda c, q: seen.append(q) or {"category": c, "finished": False}
    )
    assert seen == ["a made-up e-reader request"]
    assert "made-up e-reader" not in json.dumps(record)
    assert live_briefs.text_in(record) == []
    unknown = tmp_path / "unknown.json"
    unknown.write_text(json.dumps([{"category": "spaceship", "request": "x"}]), encoding="utf-8")
    with pytest.raises(SystemExit):
        live_briefs.options(["--briefs", str(unknown)])


def test_measures_are_the_means_over_runs_with_a_first_ranking() -> None:
    runs = {"gate_a": {"before": 0.2, "after": 0.4}, "gate_b": {"before": 0.4, "after": 0.4}, "gate_c": {"top": 0}}
    before, after, gain = enrich_coverage.measures(runs, "made-up source")
    assert (gain["id"], gain["value"], gain["unit"]) == ("enrich.top5_evidence_gain", 0.1, "difference")
    assert (before["id"], before["value"]) == ("enrich.top5_evidence_before", 0.3)
    assert (after["id"], after["value"]) == ("enrich.top5_evidence_after", 0.4)


def test_a_run_with_no_ranking_or_no_look_up_records_what_it_has() -> None:
    assert enrich_coverage.top5_evidence(enrich_coverage.Recorder(EnrichStage())) == {"top": 0}
    folder = next(f for f in recordings() if f.name == "gate_laptop")
    kept: dict[str, enrich_coverage.Recorder] = {}

    def adjust(stages: Any) -> Any:
        kept["decide"] = stages.decide = enrich_coverage.Recorder(stages.decide)
        return stages

    from evals.gate import replay

    replay(folder, adjust=adjust)
    record = enrich_coverage.top5_evidence(kept["decide"], None)
    assert record["rankings"] >= 1 and "look_ups" not in record
    assert enrich_coverage.measures({}, "made-up source") == []


def test_the_harness_writes_the_offline_record_rebuilds_it_and_merges_live_briefs(tmp_path: Path) -> None:
    out = tmp_path / "coverage.json"
    assert enrich_coverage.main(["--offline", "--out", str(out)]) == 0
    record = json.loads(out.read_text(encoding="utf-8"))
    assert len(record["runs"]) == 5 and record["runner"].endswith("(offline)")
    briefs = tmp_path / "briefs.json"
    briefs.write_text(json.dumps({"briefs": [{"category": "ereader", "top5_evidence": {"before": 0.1, "after": 0.3}}]}))
    assert enrich_coverage.main(["--rebuild", "--merge", str(briefs), "--out", str(out)]) == 0
    rebuilt = json.loads(out.read_text(encoding="utf-8"))
    assert set(rebuilt["runs"]) == {*record["runs"], "brief_ereader_1"} and rebuilt["briefs"] == 6


def test_a_live_run_holds_every_call_to_its_budget_and_refuses_free_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installed: list[dict[str, int]] = []
    monkeypatch.setattr(live_briefs.LiveBudget, "install", lambda self: installed.append(dict(self.limits)))
    monkeypatch.setattr("app.enrich.default_enrich_stage", EnrichStage)  # no call leaves the test
    out = tmp_path / "live.json"
    assert enrich_coverage.main(["--out", str(out), "--budget-serper", "7"]) == 0
    assert installed and installed[0]["serper"] == 7 and installed[0]["reddit"] == 0
    monkeypatch.setattr(live_briefs, "text_in", lambda record: ["a free text"])
    assert enrich_coverage.main(["--offline", "--out", str(tmp_path / "refused.json")]) == 1
    assert not (tmp_path / "refused.json").exists()


def test_the_harness_runs_as_a_script(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import runpy
    import sys

    out = tmp_path / "script.json"
    monkeypatch.setattr(sys, "argv", ["python -m evals.enrich_coverage", "--offline", "--out", str(out)])
    with pytest.raises(SystemExit) as ended:
        runpy.run_module("evals.enrich_coverage", run_name="__main__")
    assert ended.value.code == 0 and out.exists()
