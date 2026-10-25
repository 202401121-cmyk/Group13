"""Cohen's kappa between two labellers (ML-0): the formula, its edge cases and the command line."""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest
from check_labels import LABELS, check_file
from kappa import BINARY_LABELS, cohen_kappa, main, to_binary

WriteLabels = Callable[..., Path]  # the write_labels fixture in conftest.py

ML = Path(__file__).resolve().parents[1]
EXAMPLE_A = ML / "examples" / "pairs_labeller_a.csv"
EXAMPLE_B = ML / "examples" / "pairs_labeller_b.csv"


def _labels_file(write_labels: WriteLabels, name: str, labels: Sequence[str], first_id: int = 1) -> Path:
    """A valid labelling file with one row per label and ids counting up from p{first_id:04d}."""
    rows = [{"pair_id": f"p{first_id + i:04d}", "label": label} for i, label in enumerate(labels)]
    path: Path = write_labels(name, rows)
    return path


def _run(argv: Sequence[str | Path], capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    code = main([str(arg) for arg in argv])
    return code, capsys.readouterr().out


# Wikipedia's worked 2x2 example. 50 pairs: A and B both "same" 20, A same / B different 5, A different / B same 10,
# both "different" 15.
#   p_o = (20 + 15) / 50 = 0.7.   A says same 25/50 = 0.5, B says same 30/50 = 0.6.
#   p_e = 0.5 * 0.6 + 0.5 * 0.4 = 0.5.   kappa = (0.7 - 0.5) / (1 - 0.5) = 0.400
WIKIPEDIA_2X2 = (
    [("same", "same")] * 20
    + [("same", "different")] * 5
    + [("different", "same")] * 10
    + [("different", "different")] * 15
)
# A second 2x2 where rounding matters. 100 pairs: 45 / 15 / 25 / 15.
#   p_o = 0.6.   A same 0.6, B same 0.7.   p_e = 0.6 * 0.7 + 0.4 * 0.3 = 0.54.
#   kappa = (0.6 - 0.54) / (1 - 0.54) = 0.06 / 0.46 = 0.1304... -> 0.130
UNEVEN_2X2 = (
    [("same", "same")] * 45
    + [("same", "different")] * 15
    + [("different", "same")] * 25
    + [("different", "different")] * 15
)


@pytest.mark.parametrize(("pairs", "expected"), [(WIKIPEDIA_2X2, "0.400"), (UNEVEN_2X2, "0.130")])
def test_a_hand_computed_2x2_example(pairs: list[tuple[str, str]], expected: str) -> None:
    kappa = cohen_kappa(pairs, ("same", "different"))
    assert kappa is not None
    assert f"{kappa:.3f}" == expected
    # Classes nobody used add nothing to p_e, so the four-class kappa is the same number.
    assert cohen_kappa(pairs, LABELS) == pytest.approx(kappa)


def test_the_2x2_example_through_the_command_line(
    write_labels: WriteLabels, capsys: pytest.CaptureFixture[str]
) -> None:
    a = _labels_file(write_labels, "a.csv", [label_a for label_a, _ in WIKIPEDIA_2X2])
    b = _labels_file(write_labels, "b.csv", [label_b for _, label_b in WIKIPEDIA_2X2])
    code, out = _run([a, b], capsys)
    assert code == 0
    assert "Pairs compared: 50 (only in A: 0, only in B: 0)" in out
    assert "Agreement: 35 of 50 pairs (70.0%)" in out
    assert "Four-class kappa: 0.400" in out
    assert "Binary kappa (same vs variant or different): 0.400 (0 pairs dropped because a label is unsure)" in out
    assert re.search(r"^  same\s+20\s+0\s+5\s+0$", out, re.MULTILINE)  # rows are A, columns are B
    assert re.search(r"^  different\s+10\s+0\s+15\s+0$", out, re.MULTILINE)
    assert "Disagreements (15):" in out


def test_perfect_agreement_gives_one(write_labels: WriteLabels, capsys: pytest.CaptureFixture[str]) -> None:
    labels = ["same", "variant", "different", "unsure", "same", "different"]
    assert cohen_kappa([(label, label) for label in labels], LABELS) == 1.0
    a = _labels_file(write_labels, "a.csv", labels)
    b = _labels_file(write_labels, "b.csv", labels)
    code, out = _run([a, b, "--min", "0.8"], capsys)
    assert code == 0
    assert "Agreement: 6 of 6 pairs (100.0%)" in out
    assert "Four-class kappa: 1.000" in out
    assert "Disagreements (0):\n  none" in out


def test_one_label_for_every_pair_does_not_crash(write_labels: WriteLabels, capsys: pytest.CaptureFixture[str]) -> None:
    # Both labellers say "same" every time: p_e = 1 * 1 = 1, so the formula would divide by zero.
    assert cohen_kappa([("same", "same")] * 10, LABELS) == 1.0
    a = _labels_file(write_labels, "a.csv", ["same"] * 10)
    b = _labels_file(write_labels, "b.csv", ["same"] * 10)
    code, out = _run([a, b, "--min", "0.8"], capsys)
    assert code == 0
    assert "Four-class kappa: 1.000" in out
    assert "Binary kappa (same vs variant or different): 1.000" in out


def test_kappa_is_undefined_with_no_pairs() -> None:
    assert cohen_kappa([], LABELS) is None


def test_kappa_rejects_labels_outside_its_classes() -> None:
    with pytest.raises(ValueError, match="outside the classes"):
        cohen_kappa([("same", "maybe")], LABELS)


def test_binary_mode_drops_pairs_with_unsure_on_either_side() -> None:
    pairs = [
        ("same", "unsure"),
        ("unsure", "variant"),
        ("unsure", "unsure"),
        ("variant", "different"),
        ("same", "same"),
    ]
    kept, dropped = to_binary(pairs)
    assert dropped == 3
    assert kept == [("not same", "not same"), ("same", "same")]  # variant and different are both "not same"
    assert cohen_kappa(kept, BINARY_LABELS) == 1.0


def test_the_command_line_reports_how_many_unsure_pairs_were_dropped(
    write_labels: WriteLabels, capsys: pytest.CaptureFixture[str]
) -> None:
    a = _labels_file(write_labels, "a.csv", ["same", "unsure", "variant", "different", "same"])
    b = _labels_file(write_labels, "b.csv", ["same", "same", "unsure", "variant", "same"])
    code, out = _run([a, b], capsys)
    assert code == 0
    assert "Binary kappa (same vs variant or different): 1.000 (2 pairs dropped because a label is unsure)" in out
    assert "  p0002  A: unsure     B: same" in out
    assert "  p0003  A: variant    B: unsure" in out
    assert "  p0004  A: different  B: variant" in out


def test_binary_kappa_is_undefined_when_every_pair_is_dropped(
    write_labels: WriteLabels, capsys: pytest.CaptureFixture[str]
) -> None:
    a = _labels_file(write_labels, "a.csv", ["unsure", "unsure", "same"])
    b = _labels_file(write_labels, "b.csv", ["same", "unsure", "unsure"])
    code, out = _run([a, b], capsys)
    assert code == 0
    assert "Binary kappa (same vs variant or different): undefined (3 pairs dropped because a label is unsure)" in out


def test_min_sets_the_exit_code(write_labels: WriteLabels, capsys: pytest.CaptureFixture[str]) -> None:
    a = _labels_file(write_labels, "a.csv", [label_a for label_a, _ in WIKIPEDIA_2X2])
    b = _labels_file(write_labels, "b.csv", [label_b for _, label_b in WIKIPEDIA_2X2])  # kappa 0.400

    code, out = _run([a, b, "--min", "0.8"], capsys)
    assert code == 1
    assert "FAIL: four-class kappa 0.400 is below 0.800." in out

    code, out = _run([a, b, "--min", "0.4"], capsys)
    assert code == 0
    assert "PASS: four-class kappa 0.400 is at least 0.400." in out

    code, out = _run([a, b], capsys)  # no --min: report only
    assert code == 0
    assert "PASS" not in out and "FAIL" not in out


@pytest.mark.parametrize("value", ["1.5", "-0.1", "high"])
def test_min_must_be_a_number_from_0_to_1(
    write_labels: WriteLabels, capsys: pytest.CaptureFixture[str], value: str
) -> None:
    a = _labels_file(write_labels, "a.csv", ["same"])
    with pytest.raises(SystemExit) as exit_info:
        main([str(a), str(a), "--min", value])
    assert exit_info.value.code == 2
    assert "--min" in capsys.readouterr().err


def test_an_invalid_file_stops_before_any_kappa(write_labels: WriteLabels, capsys: pytest.CaptureFixture[str]) -> None:
    a = _labels_file(write_labels, "a.csv", ["same", "variant"])
    b = write_labels("b.csv", [{"pair_id": "p0001"}, {"pair_id": "p0002", "label": "maybe"}])
    code, out = _run([a, b, "--min", "0.8"], capsys)
    assert code == 1
    assert f"{b}:3: label 'maybe' must be one of: same, variant, different, unsure" in out
    assert "kappa:" not in out


def test_only_shared_pair_ids_are_compared(write_labels: WriteLabels, capsys: pytest.CaptureFixture[str]) -> None:
    a = _labels_file(write_labels, "a.csv", ["same"] * 5)  # p0001-p0005
    b = _labels_file(write_labels, "b.csv", ["same"] * 5, first_id=3)  # p0003-p0007
    code, out = _run([a, b], capsys)
    assert code == 0
    assert "Pairs compared: 3 (only in A: 2, only in B: 2)" in out
    assert "  only in A: p0001, p0002" in out
    assert "  only in B: p0006, p0007" in out


def test_files_with_no_shared_pair_id_fail(write_labels: WriteLabels, capsys: pytest.CaptureFixture[str]) -> None:
    a = _labels_file(write_labels, "a.csv", ["same"] * 2)
    b = _labels_file(write_labels, "b.csv", ["same"] * 2, first_id=10)
    code, out = _run([a, b], capsys)
    assert code == 1
    assert "nothing to compare" in out


def test_a_pair_with_different_titles_in_the_two_files_is_flagged(
    write_labels: WriteLabels, capsys: pytest.CaptureFixture[str]
) -> None:
    a = write_labels("a.csv", [{"pair_id": "p0001"}, {"pair_id": "p0002"}])
    b = write_labels("b.csv", [{"pair_id": "p0001"}, {"pair_id": "p0002", "right_title": "Nova Book 16"}])
    code, out = _run([a, b], capsys)
    assert code == 0
    assert "Warning: these pair_ids have different titles in the two files" in out
    assert out.count("p0002") == 1  # flagged once; not a label disagreement


def test_the_synthetic_sample_is_valid_and_lands_in_the_target_range(capsys: pytest.CaptureFixture[str]) -> None:
    a, b = check_file(EXAMPLE_A), check_file(EXAMPLE_B)
    assert a.ok and b.ok, a.errors + b.errors
    assert a.rows_checked == b.rows_checked == 50
    assert [(p.pair_id, p.left_title, p.right_title) for p in a.pairs] == [
        (p.pair_id, p.left_title, p.right_title) for p in b.pairs
    ]
    assert {p.category for p in a.pairs} == {"laptop", "smartphone", "earbuds"}

    code, out = _run([EXAMPLE_A, EXAMPLE_B, "--min", "0.8"], capsys)
    assert code == 0
    found = re.search(r"Four-class kappa: (\d\.\d{3})", out)
    assert found is not None
    assert 0.80 <= float(found.group(1)) <= 0.95
    assert "Disagreements (5):" in out


def test_kappa_runs_as_a_script() -> None:
    # The way people run it: the script's own folder makes "import check_labels" work, with no packages needed.
    done = subprocess.run(
        [sys.executable, str(ML / "scripts" / "kappa.py"), str(EXAMPLE_A), str(EXAMPLE_B), "--min", "0.8"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=60,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "Four-class kappa: 0.859" in done.stdout
    assert "PASS: four-class kappa 0.859 is at least 0.800." in done.stdout
