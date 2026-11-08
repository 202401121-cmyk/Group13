"""Build synthetic conflicts files for the trust evaluation tests. Every product and site here is made up."""

from __future__ import annotations

import csv
import io
from collections.abc import Mapping, Sequence
from pathlib import Path

from check_conflicts import HEADER

# A valid number conflict; each test overrides only the cells it is about.
CONFLICT = {
    "conflict_id": "syn001",
    "category": "laptop",
    "product": "Nova Book 14",
    "criterion": "battery_life",
    "unit": "h",
    "known_answer": "10 h",
    "tolerance": "0.5",
    "how_established": "https://nova.example/book-14/specs",
    "added_by": "Alex",
}
TEXT_CONFLICT = {
    "product": "Kestrel Buds",
    "criterion": "charging_port",
    "unit": "",
    "known_answer": "USB-C",
    "tolerance": "",
}


def claim_rows(
    claims: Sequence[tuple[str, str]] = (("manufacturer", "10 h"), ("editorial", "12 h")),
    /,
    **overrides: str,
) -> list[dict[str, str]]:
    """One row for each (tier, value) claim, each from its own made-up site. Overrides apply to every row."""
    rows: list[dict[str, str]] = []
    for number, (tier, value) in enumerate(claims, start=1):
        site = f"site{number}.example"
        rows.append(
            {
                **CONFLICT,
                "claim_url": f"https://{site}/page",
                "claim_site": site,
                "claim_tier": tier,
                "claim_value": value,
                "claim_retrieved_at": "2026-11-01",
                "claim_quote": "",
                **overrides,
            }
        )
    return rows


def text_rows(values: Sequence[str] = ("USB-C", "Micro-USB"), **overrides: str) -> list[dict[str, str]]:
    """A text conflict (blank unit): one editorial claim per value."""
    return claim_rows([("editorial", value) for value in values], **{**TEXT_CONFLICT, **overrides})


def as_real(rows: Sequence[Mapping[str, str]], conflict_id: str = "c001") -> list[dict[str, str]]:
    """The same rows under a real id, off the sites reserved for examples: for testing what a real run does."""
    moved = ("claim_url", "claim_site", "how_established")
    return [
        {**row, "conflict_id": conflict_id, **{column: row[column].replace(".example", ".in") for column in moved}}
        for row in rows
    ]


def csv_lines(rows: Sequence[Mapping[str, str]]) -> list[str]:
    """Each row as one CSV line with its line ending."""
    lines: list[str] = []
    for row in rows:
        buffer = io.StringIO(newline="")
        csv.writer(buffer, lineterminator="\n").writerow([row[column] for column in HEADER])
        lines.append(buffer.getvalue())
    return lines


def header_line() -> str:
    return ",".join(HEADER) + "\n"


def write_conflicts(path: Path, rows: Sequence[Mapping[str, str]]) -> Path:
    path.write_text(header_line() + "".join(csv_lines(rows)), encoding="utf-8", newline="\n")
    return path
