"""A dry run of a whole labelling session on the synthetic examples (ML-1), offline.

The listings come from titles already in ml/examples (fictional brands; D-55: none invented). The two labellers are
the examples' placeholders "Alex" and "Sam", and their five answers each are scripted key presses, not anyone's
judgement. Run with -s to see the status report: python -m pytest ml/tests/test_labelling_dry_run.py -s
"""

from __future__ import annotations

import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest
from check_labels import check_file
from check_listings import main as check_listings
from label_pairs import default_output, run_session
from labelling_status import status_report
from make_pairs import make_batch


class Scripted:
    def __init__(self, keys: str, lines: list[str]) -> None:
        self.keys, self.lines = list(keys), lines

    def key(self) -> str:
        return self.keys.pop(0) if self.keys else ""

    def line(self, prompt: str) -> str:
        return self.lines.pop(0) if self.lines else ""


def test_a_dry_run_on_the_synthetic_examples(
    example_listings: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert check_listings([str(example_listings)]) == 0
    batches = tmp_path / "labelling" / "batches"
    batch = make_batch([example_listings], "agree-1", seed=101, out_dir=batches)
    assert len(batch) == 50
    capsys.readouterr()

    pairs_file = batches / "agree-1.pairs.csv"
    script = {"Alex": ("12343", ["synthetic note"]), "Sam": ("12433", ["synthetic note"])}
    for labeller, (keys, lines) in script.items():
        code = run_session(
            pairs_file,
            labeller,
            default_output(pairs_file, labeller),
            Scripted(keys, lines),
            today=date(2026, 11, 8),
            opener=lambda url: None,
            write=lambda text: None,
        )
        assert code == 0
        result = check_file(default_output(pairs_file, labeller))
        assert result.ok and len(result.pairs) == 5

    lines, code = status_report(tmp_path / "labelling")
    assert code == 0
    report = "\n".join(lines)
    assert "agree-1  50 pairs  alex 5/50  sam 5/50" in report
    assert "Next step: label agree-1 (alex has 45 pairs left, sam has 45 pairs left)" in report
    with capsys.disabled():
        print("\n--- dry run: make_pairs agree-1 (seed 101), five scripted answers each for Alex and Sam ---")
        print("\n".join(lines))


def test_the_four_tools_run_as_scripts_with_piped_keys(example_listings: Path, tmp_path: Path) -> None:
    """The way people run them: each script on its own, keys piped in one per line, UTF-8 out on every platform."""
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    labelling = tmp_path / "labelling"
    batches = labelling / "batches"

    def run(script: str, *args: str, keys: str = "") -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            [sys.executable, str(scripts / script), *args], input=keys.encode(), capture_output=True, timeout=120
        )

    assert run("check_listings.py", str(example_listings), "--fix").returncode == 0
    made = run("make_pairs.py", str(example_listings), "--batch", "agree-1", "--seed", "101", "--dir", str(batches))
    assert made.returncode == 0, made.stdout.decode("utf-8")
    assert "Made agree-1: 50 pairs (50 new, 0 repeated" in made.stdout.decode("utf-8")
    labelled = run("label_pairs.py", str(batches / "agree-1.pairs.csv"), "--labeller", "Alex", keys="1\n3\nq\n")
    assert labelled.returncode == 0, labelled.stdout.decode("utf-8")
    assert "Pair 1 of 50 (50 to go)" in labelled.stdout.decode("utf-8")
    assert len(check_file(labelling / "agree-1.alex.csv").pairs) == 2
    status = run("labelling_status.py", str(labelling))
    assert status.returncode == 0
    assert "agree-1  50 pairs  alex 2/50" in status.stdout.decode("utf-8")
