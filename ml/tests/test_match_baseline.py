"""The fuzzy title baseline and its command line (ML-0, #13), run end to end on made-up files only."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
from match_baseline import (
    OWN_PAIRS_DATASET,
    SCORERS,
    SYNTHETIC_WARNING,
    CrossScoring,
    Result,
    kept_own_pairs,
    main,
    normalise,
    sweep_matrix,
    to_json,
)
from match_metrics import Choice, Counts, Evaluation, Sweep, sweep_cross, sweep_pairs
from pairsets import DOWNLOAD_HINT, SPLITS, Pair, load_leipzig, required_files

ML = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
EXAMPLE_A = ML / "examples" / "pairs_labeller_a.csv"
EXAMPLE_B = ML / "examples" / "pairs_labeller_b.csv"


def _run(argv: Sequence[str | Path], capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    code = main([str(arg) for arg in argv])
    return code, capsys.readouterr().out


def test_normalise_applies_nfkc_then_rapidfuzz_default_process() -> None:
    assert normalise("ＮＯＶＡ Book-14 (16GB)") == "nova book 14  16gb"  # fullwidth letters become plain ones
    assert normalise("Nova Café") == "nova café"
    assert normalise("  ---  ") == ""


def test_scores_run_from_0_to_1() -> None:
    pairs = [Pair("p1", "Nova Book 14", "Nova Book 14", True, "made-up", "test")]
    pairs.append(Pair("p2", "Nova Book 14", "Kestrel Buds 2", False, "made-up", "test"))
    for scorer in SCORERS:
        same, different = scorer.score_pairs(pairs)
        assert same == 1.0
        assert 0.0 <= different < 1.0


def test_the_matrix_path_gives_exactly_what_scoring_each_pair_gives() -> None:
    for name in ("abt_buy", "amazon_google"):
        cross = load_leipzig(FIXTURES / "public", name)
        scoring = CrossScoring(cross)
        for scorer in SCORERS:
            for split in SPLITS:
                one_by_one = sweep_pairs(scorer, list(cross.iter_pairs(split)))
                assert scoring.sweep(scorer, split) == one_by_one
                assert sweep_cross(scorer, cross, split) == one_by_one


@dataclass(frozen=True)
class FirstLetterScorer:
    """A made-up non-rapidfuzz scorer: 1 when the texts start with the same letter."""

    name: str = "first_letter"

    def score_pairs(self, pairs: Sequence[Pair]) -> list[float]:
        return [float(p.left_text[:1].lower() == p.right_text[:1].lower()) for p in pairs]


def test_a_scorer_without_a_matrix_takes_the_general_all_pairs_path() -> None:
    cross = load_leipzig(FIXTURES / "public", "abt_buy")
    scorer = FirstLetterScorer()
    assert CrossScoring(cross).sweep(scorer, "dev") == sweep_pairs(scorer, list(cross.iter_pairs("dev")))


def test_sweep_matrix_rejects_scores_outside_0_to_1() -> None:
    with pytest.raises(ValueError, match="scores must be from 0 to 1"):
        sweep_matrix(np.array([[0.5, 1.5]]), np.array([[True, False]]))


def test_a_missing_raw_file_names_it_and_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = _run(["wdc", "--level", "80", "--raw-dir", tmp_path], capsys)
    assert code == 2
    for path in required_files(tmp_path, "wdc_products", [80]):
        assert f"Missing raw file: {path}" in out
    assert out.rstrip().endswith(DOWNLOAD_HINT)
    assert DOWNLOAD_HINT == "Download it with the steps in ml/DATA.md section 7.4."


def test_one_missing_file_is_enough_to_stop_before_any_scoring(
    raw_tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (raw_tree / "amazon_google" / "GoogleProducts.csv").unlink()
    code, out = _run(["all", "--raw-dir", raw_tree], capsys)
    assert code == 2
    assert "GoogleProducts.csv" in out
    assert "done:" not in out  # every file is checked before anything runs


@pytest.mark.parametrize("argv", [[], ["wdc", "--level", "70"], ["nonsense"]])
def test_usage_errors_exit_1_not_2(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as stopped:
        main(argv)
    assert stopped.value.code == 1


def test_a_bad_labelling_file_exits_1(raw_tree: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bad = tmp_path / "bad.csv"
    bad.write_text("not,the,header\n", encoding="utf-8")
    code, out = _run(["own", bad, "--raw-dir", raw_tree], capsys)
    assert code == 1
    assert f"{bad}:1:" in out


def test_examples_are_reported_with_the_synthetic_warning_on_every_line(
    raw_tree: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = _run(["own", EXAMPLE_A, EXAMPLE_B, "--raw-dir", raw_tree], capsys)
    assert code == 0
    own_lines = [line for line in out.splitlines() if "pairs_labeller" in line or "- own:" in line]
    assert len(own_lines) >= 3  # the table row, the split rule and the dropped-pairs note
    assert all(line.startswith(SYNTHETIC_WARNING) for line in own_lines)
    assert "chosen on WDC Products 80% (80cc20rnd000un, title, validation set)" in out


def test_our_fixture_pairs_are_not_marked_synthetic(raw_tree: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = _run(
        ["own", FIXTURES / "own_labeller_a.csv", FIXTURES / "own_labeller_b.csv", "--raw-dir", raw_tree], capsys
    )
    assert code == 0
    assert SYNTHETIC_WARNING not in out
    assert "3 pairs kept; dropped: 1 unsure, 1 the labellers disagree on, 1 in only one file" in out


def test_undefined_numbers_are_null_in_the_json() -> None:
    evaluation = Evaluation(Choice("ratio", 90, 0.5), Counts(tp=0, fp=0, fn=0, tn=7), 7, None, None)
    result = Result("own", "x.csv", "title", "rule", "dev", evaluation, 0.01)
    data = to_json([result], datetime(2026, 10, 27, tzinfo=UTC))
    test = json.loads(json.dumps(data))["results"][0]["test"]
    assert test["precision"] is None and test["recall"] is None and test["f1"] is None
    assert data["created"] == "2026-10-27T00:00:00+00:00"


def test_all_runs_end_to_end_on_made_up_files_and_writes_json(
    raw_tree: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out_file = tmp_path / "results" / "matching_baseline.json"
    code, out = _run(["all", "--own", EXAMPLE_A, EXAMPLE_B, "--raw-dir", raw_tree, "--out", out_file], capsys)
    assert code == 0, out
    assert f"Wrote {out_file}" in out
    data = json.loads(out_file.read_text(encoding="utf-8"))
    assert {"created", "rapidfuzz_version", "numpy_version", "python_version", "threshold_rule", "results"} <= set(data)
    results = data["results"]
    wdc = [r for r in results if r["dataset"] == "wdc_products" and r["text"] == "title"]
    assert len(wdc) == 9
    assert {r["dataset"] for r in results} == {"wdc_products", "abt_buy", "amazon_google", "own"}
    own = next(r for r in results if r["dataset"] == "own")
    assert own["synthetic"] is True and own["warning"] == SYNTHETIC_WARNING
    for r in results:
        assert r["scorer"] in {s.name for s in SCORERS}
        assert 0.0 <= r["threshold"] <= 1.0
        assert r["test"]["pairs"] == r["test"]["tp"] + r["test"]["fp"] + r["test"]["fn"] + r["test"]["tn"]
        assert r["split_rule"] and r["seconds"] >= 0
    assert wdc[0]["test"]["hard_negatives"] == 4  # the WDC fixture's hard negatives are counted
    leipzig = next(r for r in results if r["dataset"] == "abt_buy")
    assert leipzig["test"]["hard_negatives"] is None
    assert leipzig["test"]["pairs"] == 1 * 6  # one test left record (1004) against six right records


def test_rewriting_the_results_keeps_the_own_pairs_entries(
    raw_tree: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The L2 record (own_eval.py) lives in the same file: a later rerun of this script must not drop it (ML-1)."""
    out_file = tmp_path / "matching_baseline.json"
    record = {"dataset": OWN_PAIRS_DATASET, "subset": "SYNTHETIC record", "labels": "machine labels"}
    stale = {"dataset": "abt_buy", "subset": "an old run"}
    out_file.write_text(json.dumps({"results": [stale, record]}), encoding="utf-8")
    code, out = _run(["abt_buy", "--raw-dir", raw_tree, "--out", out_file], capsys)
    assert code == 0, out
    results = json.loads(out_file.read_text(encoding="utf-8"))["results"]
    assert results[-1] == record
    assert stale not in results  # every other entry is this run's own
    assert [r["dataset"] for r in results] == ["abt_buy", OWN_PAIRS_DATASET]
    assert kept_own_pairs(tmp_path / "missing.json") == []


def test_the_script_runs_from_the_command_line(raw_tree: Path) -> None:
    done = subprocess.run(
        [sys.executable, str(ML / "scripts" / "match_baseline.py"), "abt_buy", "--raw-dir", str(raw_tree)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=120,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "abt_buy  all pairs" in done.stdout


def test_sweeps_from_the_matrix_and_from_scores_agree_at_the_edges() -> None:
    scores = np.array([[0.0, 0.29, 0.5], [0.71, 0.995, 1.0]])
    truth = np.array([[False, True, False], [True, False, True]])
    expected = Sweep.from_scores([float(s) for s in scores.ravel()], [bool(t) for t in truth.ravel()])
    assert sweep_matrix(scores, truth) == expected
