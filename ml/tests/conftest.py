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


EXAMPLE_PAIRS = Path(__file__).resolve().parents[1] / "examples" / "pairs_labeller_a.csv"
EXAMPLE_SHOPS = ("shop-a", "shop-b", "shop-c")
LISTINGS_HEADER = ("listing_id", "kind", "text", "source", "url", "product_family", "category", "added_by")


def example_listing_rows() -> list[dict[str, str]]:
    """Listings made only from the synthetic titles already in ml/examples (fictional brands; D-55: none invented).

    Each family's titles go round the three fictional shops in turn, so a family has titles from several shops and
    from the same shop. Each family also gets one short mention: the start of its first title, cut before "(" or
    " - ", the way people name a product in a forum post.
    """
    titles: dict[str, list[str]] = {}
    category_of: dict[str, str] = {}
    with EXAMPLE_PAIRS.open(encoding="utf-8", newline="") as file:
        for row in csv.DictReader(file):
            family = row["product_family"]
            category_of[family] = row["category"]
            for title in (row["left_title"], row["right_title"]):
                if title not in titles.setdefault(family, []):
                    titles[family].append(title)
    rows: list[dict[str, str]] = []

    def add(kind: str, text: str, source: str, family: str) -> None:
        listing_id = f"l{len(rows) + 1:04d}"
        url = f"https://{source}.example/p/{listing_id}"
        rows.append(
            {
                "listing_id": listing_id,
                "kind": kind,
                "text": text,
                "source": source,
                "url": url,
                "product_family": family,
                "category": category_of[family],
                "added_by": "Alex",
            }
        )

    for family, family_titles in titles.items():
        for number, title in enumerate(family_titles):
            add("title", title, EXAMPLE_SHOPS[number % len(EXAMPLE_SHOPS)], family)
        mention = family_titles[0].split("(")[0].split(" - ")[0].strip()
        add("mention", mention, "forum-x", family)
    return rows


def write_csv(path: Path, header: Sequence[str], rows: Sequence[Mapping[str, str]]) -> Path:
    """Write a UTF-8 CSV with LF line endings, the way the tools write theirs."""
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file, lineterminator="\n")
        writer.writerow(header)
        for row in rows:
            writer.writerow([row[column] for column in header])
    return path


@pytest.fixture
def example_listings(tmp_path: Path) -> Path:
    """The synthetic listings above, written to tmp_path/listings.csv."""
    return write_csv(tmp_path / "listings.csv", LISTINGS_HEADER, example_listing_rows())


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
