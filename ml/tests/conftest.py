"""Shared test setup for ml/: import the scripts, and write labelling files and raw-data trees into tmp_path."""

from __future__ import annotations

import csv
import shutil
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

import pytest

# The scripts are plain files, not a package. Put their folder on sys.path, just as running one as a script does.
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from check_labels import HEADER  # noqa: E402  (needs the sys.path line above)
from pairsets import wdc_subsets  # noqa: E402

# Tiny made-up files in the formats of our labels and of the three public datasets (see fixtures/.gitattributes).
FIXTURES = Path(__file__).resolve().parent / "fixtures"

# A valid row; each test overrides only the cells it is about.
VALID_ROW = {
    "pair_id": "p0001",
    "left_title": "Nova Book 14 (16GB RAM, 512GB SSD, Silver)",
    "right_title": "Nova Book 14 Laptop - 16 GB / 512 GB SSD - Silver",
    "category": "laptop",
    "product_family": "nova-book",
    "label": "same",
    "labeller": "Alex",
    "labelled_at": "2026-10-25",
    "notes": "",
}

WriteLabels = Callable[..., Path]


@pytest.fixture
def write_labels(tmp_path: Path) -> WriteLabels:
    """Return a function that writes a labelling CSV into tmp_path: one row per dict of overrides."""

    def write(name: str, rows: Sequence[Mapping[str, str]], header: Sequence[str] = HEADER) -> Path:
        path = tmp_path / name
        with path.open("w", encoding="utf-8", newline="") as file:
            writer = csv.writer(file, lineterminator="\n")
            writer.writerow(header)
            for overrides in rows:
                row = {**VALID_ROW, **overrides}
                writer.writerow([row[column] for column in HEADER])
        return path

    return write


@pytest.fixture
def raw_tree(tmp_path: Path) -> Path:
    """A stand-in for ml/data/raw: the Leipzig fixtures, and the one WDC fixture under all 18 file names a run reads."""
    raw = tmp_path / "raw"
    shutil.copytree(FIXTURES / "public", raw)
    (raw / "wdc_products").mkdir()
    for subset in wdc_subsets(raw):
        shutil.copyfile(FIXTURES / "wdc_pairs.json.gz", subset.dev_file)
        shutil.copyfile(FIXTURES / "wdc_pairs.json.gz", subset.test_file)
    return raw
