"""scripts/eval_trust.py: the stage's combiner on the seeded conflicts, against tier order and majority vote (US-16)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.verify.trust import load_trust_table
from scripts.eval_trust import CONFLICTS, RESULTS_DIR, evaluate, json_report, load_conflicts, main

HEADER = (
    "conflict_id,category,product,criterion,unit,known_answer,tolerance,how_established,added_by,claim_url,claim_site,"
    "claim_tier,claim_value,claim_retrieved_at,claim_quote\n"
)


def _row(cid: str, criterion: str, unit: str, answer: str, tol: str, url: str, site: str, tier: str, value: str) -> str:
    # syn001's answer comes from the maker's page, which is also one of its claims (ml/DATA.md 9.2)
    note = "made up for a test https://maker.example/specs" if cid == "syn001" else "made up for a test"
    head = f"{cid},laptop,Made-up Book 14,{criterion},{unit},{answer},{tol},{note},Tester"
    return f"{head},{url},{site},{tier},{value},2026-11-01,\n"


def _made_up(tmp_path: Path) -> Path:
    rows = [
        # The maker is right; two shops copy a wrong figure. Majority votes them in, trust doesn't.
        _row(
            "syn001",
            "battery_life",
            "h",
            "10 h",
            "0",
            "https://maker.example/b14",
            "maker.example",
            "manufacturer",
            "10 h",
        ),
        _row(
            "syn001",
            "battery_life",
            "h",
            "10 h",
            "0",
            "https://shop-a.example/b14",
            "shop-a.example",
            "retailer",
            "12 h",
        ),
        _row(
            "syn001",
            "battery_life",
            "h",
            "10 h",
            "0",
            "https://shop-b.example/b14",
            "shop-b.example",
            "retailer",
            "12 h",
        ),
        # Two values tie, and the review is right.
        _row(
            "syn002",
            "weight_kg",
            "kg",
            "1.4 kg",
            "0.05",
            "https://reviews.example/b14",
            "reviews.example",
            "editorial",
            "1.4 kg",
        ),
        _row(
            "syn002",
            "weight_kg",
            "kg",
            "1.4 kg",
            "0.05",
            "https://shop-a.example/b14w",
            "shop-a.example",
            "retailer",
            "1.6 kg",
        ),
    ]
    path = tmp_path / "conflicts.csv"
    path.write_text(HEADER + "".join(rows), encoding="utf-8", newline="\n")
    return path


def test_it_scores_a_made_up_conflicts_file(tmp_path: Path) -> None:
    conflicts = load_conflicts(_made_up(tmp_path))
    assert [c.conflict_id for c in conflicts] == ["syn001", "syn002"]
    report = evaluate(conflicts, load_trust_table())
    assert report.totals["learned"].correct == 2
    assert report.totals["tier_order"].correct == 2
    assert report.totals["majority_vote"].correct == 0
    assert report.totals["majority_vote"].no_answer == 1  # syn002's even vote is no answer
    first = report.per_conflict[0]
    assert (first.answers["learned"], first.answers["majority_vote"]) == ("10 h", "12 h")


def test_on_the_seeded_conflicts_learned_beats_majority() -> None:
    """Criterion 8, on the conflicts that exist (5 on main; S3-16 works toward 30)."""
    report = evaluate(load_conflicts(CONFLICTS), load_trust_table())
    assert len(report.per_conflict) >= 5
    assert report.totals["learned"].correct > report.totals["majority_vote"].correct


def test_the_results_file_has_the_date_commit_and_numbers(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "trust_learned.json"
    assert main(["--file", str(_made_up(tmp_path)), "--out", str(out)]) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert {"date", "commit", "conflicts", "claims", "methods", "per_conflict", "learned_weights", "thresholds"} <= set(
        data
    )
    assert data["methods"]["learned"]["correct"] == 2
    assert data["methods"]["majority_vote"]["accuracy"] == 0.0
    text = out.read_text(encoding="utf-8")
    assert "https://" not in text and "made up for a test" not in text  # no claim URLs, quotes or notes
    printed = capsys.readouterr().out
    assert "learned" in printed and "tier order" in printed and "majority" in printed
    assert json_report is not None


def test_the_fairer_table_leaves_out_conflicts_whose_answer_source_is_a_claim(tmp_path: Path) -> None:
    report = evaluate(load_conflicts(_made_up(tmp_path)), load_trust_table())
    assert [p.conflict.answer_is_a_claim for p in report.per_conflict] == [True, False]
    assert report.fair_count == 1
    assert (report.fair["learned"].correct, report.fair["majority_vote"].correct) == (1, 0)


def test_made_up_conflicts_never_reach_ml_results(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = RESULTS_DIR / "made_up_should_not_exist.json"
    assert main(["--file", str(_made_up(tmp_path)), "--out", str(target)]) == 1
    assert not target.exists()
    assert "syn" in capsys.readouterr().err


def test_a_missing_file_says_so(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--file", str(tmp_path / "nothing.csv")]) == 2
    assert "not found" in capsys.readouterr().err
