"""The trust baselines (US-16, #30): majority vote, tier order, scoring, report and results. All synthetic."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import trust_baseline
from check_conflicts import TIERS, Claim, Conflict
from conflict_rows import as_real, claim_rows, header_line, text_rows, write_conflicts
from trust_baseline import (
    SYNTHETIC_WARNING,
    Totals,
    answer_source_is_a_claim,
    is_correct,
    main,
    majority_answer,
    majority_totals,
    score,
    tier_order_answer,
    tier_totals,
)

ML = Path(__file__).resolve().parents[1]
SCRIPT = ML / "scripts" / "trust_baseline.py"
EXAMPLES = ML / "examples" / "conflicts_synthetic.csv"
TODAY = date(2026, 11, 8)


def _conflict(
    claims: Sequence[tuple[str, str]],
    known: str = "10 h",
    tolerance: str | None = "0.5",
    unit: str = "h",
    how_established: str = "Weighed by Alex",
) -> Conflict:
    """A conflict that would pass the checker, with one made-up site per claim."""
    return Conflict(
        line=2,
        conflict_id="syn001",
        category="laptop",
        product="Nova Book 14",
        criterion="battery_life",
        unit=unit,
        known_answer=known,
        tolerance=None if tolerance is None else Decimal(tolerance),
        how_established=how_established,
        added_by="Alex",
        claims=tuple(
            Claim(
                line=number + 2,
                url=f"https://site{number}.example/page",
                site=f"site{number}.example",
                tier=tier,
                value=value,
                retrieved_at=TODAY,
                quote="",
            )
            for number, (tier, value) in enumerate(claims)
        ),
    )


def _text_conflict(values: Sequence[str], known: str = "USB-C") -> Conflict:
    return _conflict([("editorial", value) for value in values], known=known, tolerance=None, unit="")


# --- majority vote -----------------------------------------------------------------------------------


def test_majority_picks_the_most_common_value() -> None:
    conflict = _conflict([("community", "12 h"), ("manufacturer", "10 h"), ("community", "12 h")])
    assert majority_answer(conflict) == "12 h"


def test_a_majority_tie_is_no_answer() -> None:
    assert majority_answer(_conflict([("manufacturer", "10 h"), ("editorial", "12 h")])) is None


def test_a_tie_among_the_top_values_is_no_answer_even_with_a_smaller_value_present() -> None:
    conflict = _conflict(
        [("editorial", "10 h"), ("editorial", "10 h"), ("editorial", "12 h"), ("editorial", "12 h"), ("lab", "9 h")]
    )
    assert majority_answer(conflict) is None


def test_10_and_10_point_0_are_one_vote() -> None:
    conflict = _conflict([("editorial", "10 h"), ("editorial", "10.0 h"), ("editorial", "12 h")])
    assert majority_answer(conflict) == "10 h"


def test_10_and_10_point_2_are_two_votes() -> None:
    """Tolerance belongs to the answer key, so voting never uses it: these three values are all different."""
    conflict = _conflict([("editorial", "10 h"), ("editorial", "10.2 h"), ("editorial", "12 h")], tolerance="5")
    assert majority_answer(conflict) is None


def test_text_votes_ignore_case_and_spacing() -> None:
    assert majority_answer(_text_conflict(["usb-c", "USB-C ", "Micro-USB"])) == "usb-c"
    assert majority_answer(_text_conflict(["Micro  USB", "micro usb", "USB-C"])) == "Micro  USB"


# --- tier order --------------------------------------------------------------------------------------


def test_the_highest_tier_wins_over_more_votes() -> None:
    conflict = _conflict(
        [("community", "12 h"), ("community", "12 h"), ("editorial", "12 h"), ("manufacturer", "10 h")]
    )
    answer = tier_order_answer(conflict)
    assert (answer.answer, answer.decided_by, answer.tied) == ("10 h", "manufacturer", False)


@pytest.mark.parametrize(
    ("higher", "lower"),
    [
        ("manufacturer", "lab"),
        ("lab", "editorial"),
        ("editorial", "community"),
        ("community", "retailer"),
        ("manufacturer", "retailer"),
    ],
)
def test_the_tier_order_runs_from_manufacturer_down_to_retailer(higher: str, lower: str) -> None:
    """Whichever comes first in the file, and however many votes the lower tier has."""
    for claims in (
        [(higher, "10 h"), (lower, "12 h"), (lower, "12 h")],
        [(lower, "12 h"), (lower, "12 h"), (higher, "10 h")],
    ):
        answer = tier_order_answer(_conflict(claims))
        assert (answer.answer, answer.decided_by) == ("10 h", higher)
    assert TIERS.index(higher) < TIERS.index(lower)


def test_two_to_one_inside_the_top_tier_is_not_a_tie() -> None:
    conflict = _conflict(
        [("lab", "10 h"), ("lab", "10 h"), ("lab", "12 h"), ("community", "12 h"), ("community", "12 h")]
    )
    answer = tier_order_answer(conflict)
    assert (answer.answer, answer.decided_by, answer.tied) == ("10 h", "lab", False)


def test_a_tier_tie_falls_back_to_the_whole_conflicts_majority() -> None:
    conflict = _conflict([("editorial", "10 h"), ("editorial", "12 h"), ("community", "12 h")])
    answer = tier_order_answer(conflict)
    assert (answer.answer, answer.decided_by, answer.tied) == ("12 h", "majority (tier tie)", True)


def test_the_fallback_majority_may_pick_a_value_the_top_tier_did_not_state() -> None:
    conflict = _conflict(
        [("manufacturer", "10 h"), ("manufacturer", "12 h"), ("community", "11 h"), ("community", "11 h")]
    )
    answer = tier_order_answer(conflict)
    assert (answer.answer, answer.tied) == ("11 h", True)


def test_a_tier_tie_with_a_majority_tie_is_no_answer() -> None:
    answer = tier_order_answer(_conflict([("editorial", "10 h"), ("editorial", "12 h")]))
    assert (answer.answer, answer.decided_by, answer.tied) == (None, "none (tier tie)", True)


# --- all sources agree -------------------------------------------------------------------------------


def test_all_sources_agreeing_gives_that_answer_for_both_and_is_counted() -> None:
    conflict = _conflict([("lab", "10 h"), ("editorial", "10.0 h"), ("community", "10 h")])
    scored = score(conflict)
    assert scored.majority == "10 h"
    assert scored.tier.answer == "10 h"
    assert scored.majority_correct
    assert scored.tier_correct
    assert scored.all_agree
    assert not score(_conflict([("lab", "10 h"), ("editorial", "12 h")])).all_agree


# --- scoring -----------------------------------------------------------------------------------------


def test_a_value_within_tolerance_is_correct() -> None:
    assert is_correct(_conflict([("lab", "10 h"), ("lab", "12 h")]), "10.2 h")


@pytest.mark.parametrize(
    ("known", "answer", "tolerance"),
    [("10 h", "10.3 h", "0.3"), ("1.5 h", "1.6 h", "0.1"), ("1 h", "1.1 h", "0.1")],
)
def test_a_value_exactly_at_the_tolerance_is_correct(known: str, answer: str, tolerance: str) -> None:
    """Each of these is wrong in binary floats: 10.3 - 10 is 0.3000000000000007, so exact decimals are needed."""
    assert is_correct(_conflict([("lab", "10 h"), ("lab", "12 h")], known=known, tolerance=tolerance), answer)


def test_tolerance_applies_below_as_well_as_above() -> None:
    conflict = _conflict([("lab", "10 h"), ("lab", "12 h")])
    assert is_correct(conflict, "9.5 h")
    assert is_correct(conflict, "10.5 h")


@pytest.mark.parametrize("answer", ["9.4 h", "10.6 h", "600 h"])
def test_a_value_outside_tolerance_is_wrong(answer: str) -> None:
    assert not is_correct(_conflict([("lab", "10 h"), ("lab", "12 h")]), answer)


def test_zero_tolerance_needs_the_exact_number() -> None:
    conflict = _conflict([("lab", "10 h"), ("lab", "12 h")], tolerance="0")
    assert is_correct(conflict, "10 h")
    assert is_correct(conflict, "10.00 h")
    assert not is_correct(conflict, "10.01 h")


@pytest.mark.parametrize("dash", [0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0x2212])
def test_a_dash_from_a_web_page_counts_as_a_hyphen(dash: int) -> None:
    """Maker pages often write USB-C with a no-break hyphen, which looks the same and must vote and score the same."""
    written = f"USB{chr(dash)}C"
    conflict = _text_conflict([written, "USB-C", "Micro-USB"])
    assert majority_answer(conflict) == written
    assert is_correct(conflict, written)


def test_text_answers_match_ignoring_case() -> None:
    conflict = _text_conflict(["USB-C", "Micro-USB"])
    assert is_correct(conflict, "usb-c")
    assert is_correct(conflict, " USB-C ")
    assert not is_correct(conflict, "Micro-USB")


def test_no_answer_is_never_correct() -> None:
    assert not is_correct(_conflict([("lab", "10 h"), ("lab", "12 h")]), None)


def test_no_answer_counts_against_accuracy() -> None:
    scored = [
        score(_conflict([("editorial", "10 h"), ("editorial", "12 h")])),
        score(_conflict([("editorial", "10 h"), ("editorial", "10 h"), ("editorial", "12 h")])),
    ]
    assert majority_totals(scored).accuracy == 0.5


# --- totals ------------------------------------------------------------------------------------------


def test_accuracy_ties_and_no_answers_are_counted_for_each_method() -> None:
    """Worked by hand. A: the maker is right, the crowd wrong. B: an even split. C: a tier tie the crowd settles."""
    conflict_a = _conflict([("manufacturer", "10 h"), ("editorial", "12 h"), ("editorial", "12 h")])
    conflict_b = _conflict([("editorial", "10 h"), ("editorial", "12 h")])
    conflict_c = _conflict([("editorial", "10 h"), ("editorial", "12 h"), ("community", "10 h")])
    scored = [score(conflict_a), score(conflict_b), score(conflict_c)]

    majority = majority_totals(scored)
    assert (majority.correct, majority.wrong, majority.ties, majority.no_answer) == (1, 1, 1, 1)
    assert majority.accuracy == pytest.approx(1 / 3)

    tier = tier_totals(scored)
    assert (tier.correct, tier.wrong, tier.ties, tier.no_answer) == (2, 0, 2, 1)
    assert tier.accuracy == pytest.approx(2 / 3)


def test_no_conflicts_gives_null_accuracy_not_zero() -> None:
    assert Totals(0, 0, 0, 0).accuracy is None
    assert majority_totals([]).accuracy is None
    assert tier_totals([]).accuracy is None


def _results(tmp_path: Path, argv: Sequence[str | Path], name: str = "results.json") -> dict[str, Any]:
    out = tmp_path / name
    assert main([*(str(arg) for arg in argv), "--out", str(out)]) == 0
    data: dict[str, Any] = json.loads(out.read_text(encoding="utf-8"))
    return data


def test_the_synthetic_examples_give_the_known_numbers(tmp_path: Path) -> None:
    """Worked by hand from ml/examples/conflicts_synthetic.csv, one conflict at a time."""
    data = _results(tmp_path, [EXAMPLES])
    assert data["conflicts"] == 9
    assert data["claims"] == 26
    assert data["all_agree"] == 1
    assert data["answer_source_is_a_claim"] == 3
    assert data["all_conflicts"] == {
        "conflicts": 9,
        "majority_vote": {"correct": 4, "wrong": 3, "ties": 2, "no_answer": 2, "accuracy": 0.444444},
        "tier_order_baseline": {"correct": 7, "wrong": 1, "ties": 2, "no_answer": 1, "accuracy": 0.777778},
    }
    # syn001, syn003 and syn006 drop out: their known answer's source is also one of their claims.
    assert data["source_not_a_claim"] == {
        "conflicts": 6,
        "majority_vote": {"correct": 3, "wrong": 2, "ties": 1, "no_answer": 1, "accuracy": 0.5},
        "tier_order_baseline": {"correct": 4, "wrong": 1, "ties": 2, "no_answer": 1, "accuracy": 0.666667},
    }

    rows = data["per_conflict"]
    assert isinstance(rows, list)
    assert [(row["conflict_id"], row["majority_correct"], row["tier_order_correct"]) for row in rows] == [
        ("syn001", False, True),
        ("syn002", True, True),
        ("syn003", False, True),
        ("syn004", False, False),
        ("syn005", True, True),
        ("syn006", True, True),
        ("syn007", False, False),
        ("syn008", True, True),
        ("syn009", False, True),
    ]
    assert [row["decided_by"] for row in rows] == [
        "manufacturer",
        "majority (tier tie)",
        "manufacturer",
        "none (tier tie)",
        "lab",
        "lab",
        "manufacturer",
        "manufacturer",
        "community",
    ]
    assert [row["answer_source_is_a_claim"] for row in rows] == [
        True,
        False,
        True,
        False,
        False,
        True,
        False,
        False,
        False,
    ]
    assert rows[2]["majority"] is None
    assert rows[3]["tier_order"] is None


# --- the second table --------------------------------------------------------------------------------


def _source_is_a_claim() -> list[dict[str, str]]:
    """The maker is right, and its own site is where the known answer came from."""
    return claim_rows([("manufacturer", "10 h"), ("editorial", "12 h")], how_established="https://site1.example/specs")


def _source_is_not_a_claim(conflict_id: str = "syn002") -> list[dict[str, str]]:
    """The maker is wrong, and the known answer came from somewhere else."""
    return claim_rows(
        [("manufacturer", "12 h"), ("editorial", "10 h")],
        conflict_id=conflict_id,
        criterion=f"runtime_{conflict_id}",
        how_established="https://other.example/specs",
    )


def test_the_second_table_leaves_out_conflicts_whose_answer_source_is_a_claim(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write_conflicts(tmp_path / "conflicts.csv", [*_source_is_a_claim(), *_source_is_not_a_claim()])
    data = _results(tmp_path, [path])
    assert data["all_conflicts"]["conflicts"] == 2
    assert data["all_conflicts"]["tier_order_baseline"]["correct"] == 1
    assert data["all_conflicts"]["tier_order_baseline"]["accuracy"] == 0.5
    assert data["source_not_a_claim"]["conflicts"] == 1
    assert data["source_not_a_claim"]["tier_order_baseline"]["correct"] == 0
    assert data["source_not_a_claim"]["tier_order_baseline"]["accuracy"] == 0.0
    tier_rows = [line for line in capsys.readouterr().out.splitlines() if line.startswith("tier order ")]
    assert tier_rows[1].split()[2:7] == ["0", "1", "0", "0", "0.000"]


def test_the_results_file_states_the_rule_each_table_follows(tmp_path: Path) -> None:
    """The second table's accuracy is out of its own conflicts, and the committed file must say so."""
    assert "correct / the conflicts in that table" in _results(tmp_path, [EXAMPLES])["scoring_rule"]


def test_when_every_answer_source_is_a_claim_the_second_table_is_empty_not_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write_conflicts(tmp_path / "conflicts.csv", _source_is_a_claim())
    data = _results(tmp_path, [path])
    assert data["source_not_a_claim"]["conflicts"] == 0
    assert data["source_not_a_claim"]["majority_vote"]["accuracy"] is None
    assert data["source_not_a_claim"]["tier_order_baseline"]["accuracy"] is None
    out = capsys.readouterr().out
    assert "Known answer's source is not a claim (0)" in out
    assert "n/a" in out


def test_when_no_answer_source_is_a_claim_the_two_tables_match(tmp_path: Path) -> None:
    rows = [*_source_is_not_a_claim("syn001"), *_source_is_not_a_claim("syn002")]
    data = _results(tmp_path, [write_conflicts(tmp_path / "conflicts.csv", rows)])
    assert data["source_not_a_claim"] == data["all_conflicts"]


# --- the fairness count ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("how_established", "expected"),
    [
        ("https://site0.example/specs", True),
        ("Spec page: https://support.site0.example/specs.", True),
        ("See https://site0.example.", True),
        ("[https://www.site0.example]", True),
        ("https://other.example/specs?utm_source=site0.example", False),
        ("https://other.example/specs", False),
        ("https://notsite0.example/specs", False),
        ("Weighed on a kitchen scale", False),
        ("site0.example said so", False),
    ],
    ids=[
        "same_site",
        "under_the_site_in_a_sentence",
        "after_a_full_stop",
        "in_brackets",
        "other_site_naming_it_in_a_query",
        "other_site",
        "lookalike",
        "free_text",
        "no_link",
    ],
)
def test_the_answer_source_count_goes_by_site(how_established: str, expected: bool) -> None:
    conflict = _conflict([("manufacturer", "10 h"), ("editorial", "12 h")], how_established=how_established)
    assert answer_source_is_a_claim(conflict) is expected
    assert score(conflict).answer_source_is_a_claim is expected


def test_the_answer_source_count_also_covers_a_claim_under_the_source_site() -> None:
    conflict = _conflict(
        [("manufacturer", "10 h"), ("editorial", "12 h")], how_established="https://www.nova.example/x"
    )
    moved = replace(conflict.claims[0], site="support.nova.example")
    assert answer_source_is_a_claim(replace(conflict, claims=(moved, conflict.claims[1])))


# --- the command line --------------------------------------------------------------------------------


def _real_file(tmp_path: Path) -> Path:
    rows = [
        *as_real(claim_rows([("manufacturer", "10 h"), ("editorial", "12 h"), ("editorial", "12 h")])),
        *as_real(text_rows(("USB-C", "USB-C", "Micro-USB")), "c002"),
    ]
    return write_conflicts(tmp_path / "real.csv", rows)


def test_the_report_prints_both_methods_and_a_row_per_conflict(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([str(EXAMPLES), "--out", os.devnull]) == 0
    out = capsys.readouterr().out
    assert "Conflicts: 9 (targets: 15 by the Sprint 2 review, 30 in Sprint 3). Claims: 26." in out
    assert "All sources agree on: 1. Known answer's source is also a claim on: 3." in out
    lines = out.splitlines()
    all_conflicts = lines.index("All conflicts (9)")
    second_table = lines.index("Known answer's source is not a claim (6)")
    assert all_conflicts < second_table
    majority = [line for line in lines if line.startswith("majority ")]
    tier = [line for line in lines if line.startswith("tier order ")]
    assert lines.index(majority[0]) < second_table < lines.index(majority[1])
    assert lines.index(tier[0]) < second_table < lines.index(tier[1])
    assert majority[0].split()[1:6] == ["4", "3", "2", "2", "0.444"]
    assert tier[0].split()[2:7] == ["7", "1", "2", "1", "0.778"]
    assert majority[1].split()[1:6] == ["3", "2", "1", "1", "0.500"]
    assert tier[1].split()[2:7] == ["4", "1", "2", "1", "0.667"]
    for number in range(1, 10):
        assert sum(line.startswith(f"syn00{number} ") for line in out.splitlines()) == 1
    row = next(line for line in out.splitlines() if line.startswith("syn001 "))
    assert "Nova Book 14" in row
    assert "battery_life" in row
    assert "10 h ±0.5" in row
    assert "12 h wrong" in row
    assert "10 h right" in row
    assert "manufacturer" in row
    assert "no answer" in next(line for line in out.splitlines() if line.startswith("syn004 "))


def test_the_results_json_holds_the_date_and_the_conflict_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(trust_baseline, "today_utc", lambda: TODAY)
    data = _results(tmp_path, [_real_file(tmp_path)])
    assert data["date"] == "2026-11-08"
    assert data["conflicts"] == 2
    assert data["claims"] == 6
    assert data["synthetic"] is False
    assert "warning" not in data
    assert data["tier_order"] == ["manufacturer", "lab", "editorial", "community", "retailer"]
    assert data["tool"] == "ml/scripts/trust_baseline.py"


def test_the_results_json_never_holds_a_path_from_this_machine(tmp_path: Path) -> None:
    """The results file is committed, and a full path would carry someone's user name."""
    assert _results(tmp_path, [_real_file(tmp_path)])["conflicts_file"] == "real.csv"
    assert _results(tmp_path, [EXAMPLES])["conflicts_file"] == "ml/examples/conflicts_synthetic.csv"


def test_a_real_run_writes_the_default_results_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "results" / "trust_baseline.json"
    monkeypatch.setattr(trust_baseline, "DEFAULT_OUT", out)
    assert main([str(_real_file(tmp_path))]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["conflicts"] == 2
    assert f"Wrote {out}" in capsys.readouterr().out


def test_the_default_paths_are_the_real_file_and_next_to_the_matching_baseline() -> None:
    assert trust_baseline.DEFAULT_FILE == ML / "labelling" / "conflicts.csv"
    assert trust_baseline.DEFAULT_OUT == ML / "results" / "trust_baseline.json"
    assert (trust_baseline.DEFAULT_OUT.parent / "matching_baseline.json").exists()


def test_with_no_file_it_reads_the_default_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(trust_baseline, "DEFAULT_FILE", _real_file(tmp_path))
    monkeypatch.setattr(trust_baseline, "DEFAULT_OUT", tmp_path / "out.json")
    assert main([]) == 0
    assert "Conflicts: 2" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("claims", "fragment"),
    [
        ([("manufacturer", "10 h"), ("editorial", "600 min")], "never converted"),
        ([("manufacturer", "10 h"), ("blog", "12 h")], "claim_tier"),
    ],
    ids=["units_that_differ", "unknown_tier"],
)
def test_input_errors_exit_1_and_write_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], claims: list[tuple[str, str]], fragment: str
) -> None:
    path = write_conflicts(tmp_path / "conflicts.csv", claim_rows(claims))
    out = tmp_path / "results.json"
    assert main([str(path), "--out", str(out)]) == 1
    printed = capsys.readouterr().out
    assert f"{path}:3: " in printed
    assert fragment in printed
    assert "Accuracy" not in printed
    assert not out.exists()


def test_a_failed_run_leaves_an_earlier_results_file_alone(tmp_path: Path) -> None:
    path = write_conflicts(tmp_path / "conflicts.csv", claim_rows([("manufacturer", "10 h"), ("blog", "12 h")]))
    out = tmp_path / "results.json"
    out.write_text("earlier numbers", encoding="utf-8")
    assert main([str(path), "--out", str(out)]) == 1
    assert out.read_text(encoding="utf-8") == "earlier numbers"


@pytest.mark.parametrize(
    "text",
    [
        "[https://nova.in]",
        "See https://nova.in.",
        "https://nova.in:notaport/x",
        "http://[::1]/x",
        "https://[paste",
        "http://",
    ],
)
def test_an_odd_link_in_how_established_never_crashes_the_runner(tmp_path: Path, text: str) -> None:
    """Whatever a person types there, the run ends with an answer or a named line, never a traceback."""
    rows = as_real(claim_rows(how_established=text))
    path = write_conflicts(tmp_path / "conflicts.csv", rows)
    assert main([str(path), "--out", str(tmp_path / "results.json")]) in (0, 1)


def test_a_link_that_cannot_be_read_stops_the_run_at_its_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write_conflicts(tmp_path / "conflicts.csv", claim_rows(how_established="https://[paste link here]"))
    assert main([str(path), "--out", str(tmp_path / "results.json")]) == 1
    assert f"{path}:2: how_established" in capsys.readouterr().out


def test_a_results_file_that_cannot_be_written_is_named_and_exits_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The path is a folder here. A results file open in another program on Windows fails the same way."""
    assert main([str(EXAMPLES), "--out", str(tmp_path)]) == 1
    assert "Cannot write" in capsys.readouterr().out


def test_usage_errors_exit_1_not_2(capsys: pytest.CaptureFixture[str]) -> None:
    """Exit code 2 always means the file is missing."""
    with pytest.raises(SystemExit) as caught:
        main(["a.csv", "b.csv"])
    assert caught.value.code == 1
    assert "usage" in capsys.readouterr().err


def test_a_missing_file_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    missing = tmp_path / "nowhere.csv"
    out = tmp_path / "results.json"
    assert main([str(missing), "--out", str(out)]) == 2
    assert str(missing) in capsys.readouterr().out
    assert not out.exists()


def test_the_header_only_real_file_reports_0_conflicts_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "conflicts.csv"
    path.write_text(header_line(), encoding="utf-8")
    default_out = tmp_path / "default.json"
    monkeypatch.setattr(trust_baseline, "DEFAULT_OUT", default_out)
    explicit_out = tmp_path / "explicit.json"
    assert main([str(path)]) == 0
    assert main([str(path), "--out", str(explicit_out)]) == 0
    out = capsys.readouterr().out
    assert "Conflicts: 0" in out
    assert "All conflicts (0)" in out
    assert "Known answer's source is not a claim (0)" in out
    assert "n/a" in out
    assert "No conflicts yet" in out
    assert not default_out.exists()
    assert not explicit_out.exists()


# --- synthetic runs ----------------------------------------------------------------------------------


def test_a_synthetic_run_is_marked_on_the_report_and_in_the_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = _results(tmp_path, [EXAMPLES])
    assert data["synthetic"] is True
    assert data["warning"] == SYNTHETIC_WARNING
    lines = capsys.readouterr().out.splitlines()
    assert SYNTHETIC_WARNING in lines
    method_rows = [line for line in lines if line.startswith(("majority ", "tier order "))]
    assert len(method_rows) == 4
    assert all("SYNTHETIC" in line for line in method_rows)


def test_a_real_run_carries_no_synthetic_mark(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _results(tmp_path, [_real_file(tmp_path)])
    assert "SYNTHETIC" not in capsys.readouterr().out


def test_a_synthetic_run_writes_nothing_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    default_out = tmp_path / "default.json"
    monkeypatch.setattr(trust_baseline, "DEFAULT_OUT", default_out)
    assert main([str(EXAMPLES)]) == 0
    assert not default_out.exists()
    assert "Wrote" not in capsys.readouterr().out


def test_the_id_marks_a_run_as_synthetic_wherever_the_file_is(tmp_path: Path) -> None:
    path = write_conflicts(tmp_path / "copied.csv", claim_rows(conflict_id="syn001"))
    assert _results(tmp_path, [path])["synthetic"] is True


@pytest.mark.parametrize("name", ["trust_baseline.json", "other.json"])
def test_synthetic_numbers_are_refused_at_the_real_results_path(capsys: pytest.CaptureFixture[str], name: str) -> None:
    target = ML / "results" / name
    before = target.read_bytes() if target.exists() else None
    assert main([str(EXAMPLES), "--out", str(target)]) == 1
    assert (target.read_bytes() if target.exists() else None) == before
    assert "synthetic" in capsys.readouterr().out.lower()


@pytest.mark.parametrize(
    "typed",
    [
        "ml/results/trust_baseline.json",
        "ml/results/other.json",
        "./ml/results/other.json",
        "ml/examples/../results/other.json",
    ],
)
def test_synthetic_numbers_are_refused_however_the_path_is_typed(monkeypatch: pytest.MonkeyPatch, typed: str) -> None:
    monkeypatch.chdir(ML.parent)
    target = ML.parent / typed
    before = target.read_bytes() if target.exists() else None
    assert main([str(EXAMPLES), "--out", typed]) == 1
    assert (target.read_bytes() if target.exists() else None) == before


@pytest.mark.skipif(os.name != "nt", reason="checked on Windows, where a folder in another case is the same folder")
def test_synthetic_numbers_are_refused_with_the_folder_in_another_case() -> None:
    target = ML / "RESULTS" / "other.json"
    assert main([str(EXAMPLES), "--out", str(target)]) == 1
    assert not target.exists()


def test_real_numbers_are_allowed_at_the_real_results_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The same check with the results folder moved, so nothing is written into the repository."""
    monkeypatch.setattr(trust_baseline, "RESULTS_DIR", tmp_path / "results")
    assert main([str(_real_file(tmp_path)), "--out", str(tmp_path / "results" / "trust_baseline.json")]) == 0
    assert (tmp_path / "results" / "trust_baseline.json").exists()


# --- what never comes out, and encodings -------------------------------------------------------------


def test_no_quote_or_claim_url_reaches_the_report_or_the_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "results.json"
    assert main([str(EXAMPLES), "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    saved = out.read_text(encoding="utf-8")
    for text in (printed, saved):
        assert "Up to 10 hours on a full charge" not in text
        assert "Our sample weighed" not in text
        assert "reddit" not in text.lower()
        assert "techdesk" not in text
        assert "https://" not in text


def test_the_rupee_sign_round_trips_into_the_json(tmp_path: Path) -> None:
    data = _results(tmp_path, [EXAMPLES])
    rows = data["per_conflict"]
    assert isinstance(rows, list)
    assert rows[6]["known"] == "62990 ₹"
    assert "62990 ₹" in (tmp_path / "results.json").read_text(encoding="utf-8")


def test_the_results_file_uses_lf_line_endings(tmp_path: Path) -> None:
    _results(tmp_path, [EXAMPLES])
    data = (tmp_path / "results.json").read_bytes()
    assert b"\r\n" not in data
    assert data.endswith(b"}\n")


def test_the_same_input_gives_the_same_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(trust_baseline, "today_utc", lambda: TODAY)
    _results(tmp_path, [EXAMPLES], "first.json")
    _results(tmp_path, [EXAMPLES], "second.json")
    assert (tmp_path / "first.json").read_bytes() == (tmp_path / "second.json").read_bytes()


def test_a_long_product_or_value_is_cut_short_in_the_table(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    long_name = "Nova Book " + "x" * 200
    path = write_conflicts(tmp_path / "conflicts.csv", claim_rows(product=long_name))
    data = _results(tmp_path, [path])
    assert long_name not in capsys.readouterr().out
    rows = data["per_conflict"]
    assert isinstance(rows, list)
    assert rows[0]["product"] == long_name


def test_trust_baseline_runs_as_a_script(tmp_path: Path) -> None:
    """A child process with the console's default encoding and no UTF-8 mode: the ₹ still prints, from any folder."""
    env = {key: value for key, value in os.environ.items() if key not in ("PYTHONUTF8", "PYTHONIOENCODING")}
    done = subprocess.run(
        [sys.executable, str(SCRIPT), str(EXAMPLES), "--out", str(tmp_path / "results.json")],
        capture_output=True,
        env=env,
        check=False,
        cwd=tmp_path,
    )
    assert done.returncode == 0, (done.stdout + done.stderr).decode("utf-8", "replace")
    assert done.stderr == b""
    assert "62990 ₹" in done.stdout.decode("utf-8")
