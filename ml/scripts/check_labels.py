"""Check labelled product-pair CSV files before anyone trains on them or compares labellers (ML-0).

Usage: python ml/scripts/check_labels.py FILE [FILE ...]

The format is in ml/DATA.md, section 2. Every error prints as FILE:ROW: message, with rows numbered the way a
spreadsheet shows them (the header is row 1), so a labeller can go straight to the bad cell. The exit code is 0
when every file is clean and 1 otherwise. Standard library only, so it runs without the backend's environment.
"""

from __future__ import annotations

import argparse
import csv
import io
import re
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

HEADER = (
    "pair_id",
    "left_title",
    "right_title",
    "category",
    "product_family",
    "label",
    "labeller",
    "labelled_at",
    "notes",
)
LABELS = ("same", "variant", "different", "unsure")
MAX_TITLE_LENGTH = 300

# [0-9] rather than \d, which would also accept digits from other scripts such as "٣".
PAIR_ID = re.compile(r"p[0-9]{4}")
SLUG = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+)*")
ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


@dataclass(frozen=True)
class Pair:
    """One labelled row that passed every check."""

    row: int
    pair_id: str
    left_title: str
    right_title: str
    category: str
    product_family: str
    label: str
    labeller: str
    labelled_at: date
    notes: str


@dataclass
class CheckResult:
    """What one file held: the rows that passed, and every error found."""

    path: str
    rows_checked: int = 0
    pairs: list[Pair] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def add_error(self, row: int, message: str) -> None:
        self.errors.append(f"{self.path}:{row}: {message}")


def check_file(path: str | Path) -> CheckResult:
    """Read one labelling CSV and check the header and every row.

    Problems with the file itself (missing, not UTF-8, empty) are reported against row 1, where reading starts.
    """
    result = CheckResult(path=str(path))
    try:
        # utf-8-sig drops the byte-order mark that Excel adds; newline="" lets csv handle CRLF and quoted newlines.
        with open(path, encoding="utf-8-sig", newline="") as file:
            records = list(csv.reader(file))
    except FileNotFoundError:
        result.add_error(1, "file not found")
        return result
    except UnicodeDecodeError:
        result.add_error(1, 'the file is not UTF-8 text; save it as "CSV UTF-8"')
        return result
    except (OSError, csv.Error) as exc:
        result.add_error(1, f"cannot read the file ({exc})")
        return result

    if not records:
        result.add_error(1, "the file is empty; the first row must be the header")
        return result
    problem = _header_problem(records[0])
    if problem:
        # Without the right columns no row can be read safely, so stop at the header.
        result.add_error(1, f"{problem}. The header must be exactly: {','.join(HEADER)}")
        return result

    first_row_of: dict[str, int] = {}
    for row, record in enumerate(records[1:], start=2):
        if not any(cell.strip() for cell in record):
            continue  # a blank line, often left at the end by spreadsheets
        result.rows_checked += 1
        pair = _check_row(result, row, record, first_row_of)
        if pair is not None:
            result.pairs.append(pair)
    return result


def _header_problem(header: Sequence[str]) -> str | None:
    """Name the first wrong header cell, which is easier to fix than a whole-line comparison."""
    if tuple(header) == HEADER:
        return None
    if len(header) != len(HEADER):
        return f"the header has {len(header)} columns, expected {len(HEADER)}"
    for number, (expected, found) in enumerate(zip(HEADER, header, strict=True), start=1):
        if expected != found:
            return f"header column {number} is {found!r}, expected {expected!r}"
    return None  # unreachable: the tuples differ, so some column differs


def _check_row(result: CheckResult, row: int, record: Sequence[str], first_row_of: dict[str, int]) -> Pair | None:
    """Record every problem in one row; return the row as a Pair only if it has none."""
    if len(record) != len(HEADER):
        result.add_error(
            row, f"expected {len(HEADER)} columns, found {len(record)}; put quotes around titles that contain commas"
        )
        return None
    values = dict(zip(HEADER, record, strict=True))
    problems: list[str] = []

    pair_id = values["pair_id"]
    if not PAIR_ID.fullmatch(pair_id):
        problems.append(f"pair_id {pair_id!r} must be p and 4 digits, such as p0001")
    elif pair_id in first_row_of:
        problems.append(f"duplicate pair_id {pair_id} (first used on row {first_row_of[pair_id]})")
    else:
        first_row_of[pair_id] = row

    for column in ("left_title", "right_title"):
        title = values[column]
        if not title.strip():
            problems.append(f"{column} is empty")
        elif len(title) > MAX_TITLE_LENGTH:
            problems.append(f"{column} has {len(title)} characters; the limit is {MAX_TITLE_LENGTH}")

    for column in ("category", "product_family"):
        if not SLUG.fullmatch(values[column]):
            problems.append(f"{column} {values[column]!r} is not a lowercase slug such as nova-book")

    if values["label"] not in LABELS:
        problems.append(f"label {values['label']!r} must be one of: {', '.join(LABELS)}")
    if not values["labeller"].strip():
        problems.append("labeller is empty")
    labelled_at = _parse_date(values["labelled_at"])
    if labelled_at is None:
        problems.append(f"labelled_at {values['labelled_at']!r} is not a real date written as YYYY-MM-DD")

    for problem in problems:
        result.add_error(row, problem)
    if problems or labelled_at is None:
        return None
    return Pair(
        row=row,
        pair_id=pair_id,
        left_title=values["left_title"],
        right_title=values["right_title"],
        category=values["category"],
        product_family=values["product_family"],
        label=values["label"],
        labeller=values["labeller"],
        labelled_at=labelled_at,
        notes=values["notes"],
    )


def _parse_date(text: str) -> date | None:
    """Accept only YYYY-MM-DD that names a real day; fromisoformat alone also takes forms like 20261025."""
    if not ISO_DATE.fullmatch(text):
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:  # such as 2026-02-30
        return None


def summary_lines(result: CheckResult) -> list[str]:
    """Rows checked, label counts and product families per category, for one file."""
    labels = Counter(pair.label for pair in result.pairs)
    families: dict[str, set[str]] = {}
    for pair in result.pairs:
        families.setdefault(pair.category, set()).add(pair.product_family)

    errors = len(result.errors)
    lines = [f"{result.path}: {result.rows_checked} rows checked, {errors} error{'' if errors == 1 else 's'}"]
    counted = "  labels" if result.ok else "  labels (rows with errors not counted)"
    lines.append(f"{counted}: " + ", ".join(f"{label} {labels[label]}" for label in LABELS))
    if families:
        per_category = ", ".join(f"{category} {len(names)}" for category, names in sorted(families.items()))
        lines.append(f"  families per category: {per_category}")
    return lines


def use_utf8_stdout() -> None:
    """Windows consoles default to a legacy code page; print UTF-8 so no title can crash the output."""
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main(argv: Sequence[str] | None = None) -> int:
    use_utf8_stdout()
    parser = argparse.ArgumentParser(
        prog="check_labels.py", description="Check labelling CSV files against the format in ml/DATA.md."
    )
    parser.add_argument("files", nargs="+", metavar="FILE", help="a labelling CSV file")
    args = parser.parse_args(argv)
    paths: list[str] = args.files

    results = [check_file(path) for path in paths]
    errors = [error for result in results for error in result.errors]
    for error in errors:
        print(error)
    if errors:
        print()
    for result in results:
        for line in summary_lines(result):
            print(line)

    rows = sum(result.rows_checked for result in results)
    files = f"{len(results)} file{'' if len(results) == 1 else 's'}"
    if errors:
        print(f"FAILED: {len(errors)} error{'' if len(errors) == 1 else 's'} in {files}, {rows} rows checked.")
        return 1
    print(f"OK: {files}, {rows} rows checked, no errors.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
