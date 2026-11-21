"""The 100 hand-written citation pairs (US-22, S5-4 DECIDED 5), read and checked before anything is measured on them.

A pair is a quote drawn from a recorded run's public source and one sentence a person wrote about it, labelled
supported or unsupported. A sheet is used only when every row is complete and its quote is still the one drawn (so a
quote edited after the fact can't make a sentence look supported), and every row names who wrote it: one of the two
people the decision names (D-55), never a tool.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path

from . import sheet

LABELS = {"supported": True, "unsupported": False}
WRITERS = ("Rushiraj Navar", "Jhanvi Barot")
SENTENCE_MAX = 400  # as long as an explanation sentence may be (ExplanationSentence.text)


class SheetProblem(Exception):
    """The sheet can't be used yet. `problems` names each row and what is wrong with it, never its words."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class LabelledPair:
    id: str
    quote: str
    source_address: str
    sentence: str
    supported: bool
    written_by: str


def _writer(name: str) -> str | None:
    """The full name of the person a written_by names (their full name or first name, any case), or None."""
    words = name.strip().casefold().split()
    for person in WRITERS:
        if words and (words == person.casefold().split() or words == [person.split()[0].casefold()]):
            return person
    return None


def load_pairs(path: Path = sheet.SHEET, drawn: list[sheet.Quote] | None = None) -> list[LabelledPair]:
    """The sheet's pairs, in id order, or SheetProblem naming every row that isn't ready."""
    expected = drawn if drawn is not None else _drawn()
    quotes = {f"c{i:03d}": q.quote for i, q in enumerate(expected, start=1)}
    with path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        if tuple(reader.fieldnames or ()) != sheet.COLUMNS:
            raise SheetProblem([f"the columns are {reader.fieldnames}, not {list(sheet.COLUMNS)}"])
        rows = list(reader)
    problems: list[str] = []
    pairs: list[LabelledPair] = []
    seen: set[str] = set()
    for row in rows:
        id_ = (row.get("id") or "").strip()
        where = id_ or "a row with no id"
        if id_ not in quotes or id_ in seen:
            problems.append(f"{where}: not one of the drawn ids, or there twice")
            continue
        seen.add(id_)
        sentence = (row.get("sentence") or "").strip()
        label = (row.get("label") or "").strip().casefold()
        writer = _writer(row.get("written_by") or "")
        wrong = [
            why
            for bad, why in (
                ((row.get("quote") or "").strip() != quotes[id_].strip(), "its quote is not the one drawn"),
                (not sentence or len(sentence) > SENTENCE_MAX, f"no sentence, or one over {SENTENCE_MAX} characters"),
                (label not in LABELS, "the label is not supported or unsupported"),
                (writer is None, f"written_by is not {' or '.join(WRITERS)}"),
            )
            if bad
        ]
        problems += [f"{id_}: {why}" for why in wrong]
        if not wrong and writer is not None:
            pairs.append(
                LabelledPair(id_, quotes[id_], row.get("source_address") or "", sentence, LABELS[label], writer)
            )
    missing = sorted(set(quotes) - seen)
    if missing:
        problems.append(f"{len(missing)} drawn ids are missing: {', '.join(missing[:10])}")
    if problems:
        raise SheetProblem(problems)
    return sorted(pairs, key=lambda p: p.id)


def _drawn() -> list[sheet.Quote]:
    found = sheet.candidates()
    return sheet.draw(found, sheet.passages(taken={sheet.key(q.quote) for q in found}))


def load_split(path: Path = sheet.SPLIT) -> tuple[list[str], list[str]]:
    """The tuning ids and the test ids, as split when the sheet was drawn."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data["tune"]), list(data["test"])
