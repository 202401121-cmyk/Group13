"""Count the Sprint 1 survey export (task #15). Standard library only.

Run from the repository root:  python scripts/elicitation/count_survey.py <path to the Google Form CSV>

Prints the number of responses and, for each question, the count and share of each answer, in the form the
elicitation kit asks for: "21 of 34 (62%)". Free-text answers are counted but never printed, so a name or a story
someone typed cannot reach the output. Keep the CSV outside the repository.

A column that looks like it identifies a person stops the run before any answer is read. Remove it from the file
first. Exit codes: 0 counted, 2 the file could not be used.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

OTHER = "Other"
IDENTIFYING_COLUMN = re.compile(r"\b(name|e-?mail|phone|mobile|contact|roll|student id)\b", re.IGNORECASE)
IGNORED_COLUMNS = {"timestamp"}


class SurveyFileError(Exception):
    """The file cannot be counted. The message names columns only, never an answer."""


@dataclass(frozen=True)
class Question:
    number: int
    text: str
    kind: Literal["single", "multi", "text"]
    options: tuple[str, ...] = ()


# The questions in docs/elicitation-kit.md, section 1. Google Forms uses the question text as the column header.
QUESTIONS = (
    Question(
        1,
        "How often do you research before buying electronics or other products?",
        "single",
        ("Always", "Usually", "Sometimes", "Rarely"),
    ),
    Question(
        2,
        "Where do you look?",
        "multi",
        (
            "Google search",
            "Reddit",
            "YouTube",
            "Store reviews (Amazon, Flipkart)",
            "Friends or family",
            "Tech websites",
            OTHER,
        ),
    ),
    Question(
        3,
        "What frustrates you most when researching?",
        "multi",
        (
            "Reviews disagree",
            "Fake or paid reviews",
            "Too many options",
            "Outdated information",
            "Jargon I don't understand",
            "It takes too long",
        ),
    ),
    Question(
        4,
        "How many questions would you answer before getting recommendations?",
        "single",
        ("0 to 3", "4 to 6", "7 to 10", "More than 10"),
    ),
    Question(5, "How important is it to see the source of each claim?", "single", ("1", "2", "3", "4", "5")),
    Question(
        6,
        "Should the app remember your preferences for next time?",
        "single",
        ("Yes", "No", "Only if I can see and delete them"),
    ),
    Question(
        7,
        "If two products are nearly tied, would you rather",
        "single",
        ("The app picks one for me", "See both and the one difference between them"),
    ),
    Question(8, "What would make you trust a recommendation?", "text"),
    Question(9, "Tell us about the last product you researched", "text"),
)


def normalised(text: str) -> str:
    return " ".join(text.lower().split())


def question_for(header: str) -> Question | None:
    """The kit's question for a column. Forms may add or drop trailing words, so the start of the text decides."""
    wanted = normalised(header)
    for question in QUESTIONS:
        start = normalised(question.text).rstrip("?")
        if wanted.startswith(start):
            return question
    return None


def split_choices(cell: str, options: tuple[str, ...]) -> list[str]:
    """Split one checkbox answer. Forms joins choices with ", ", and an option can hold a comma of its own.

    Anything typed instead of a listed option counts as Other, once per respondent, and is never kept.
    """
    longest_first = sorted(options, key=len, reverse=True)
    chosen: list[str] = []
    rest = cell.strip()
    while rest:
        for option in longest_first:
            if rest == option or rest.startswith(f"{option}, "):
                chosen.append(option)
                rest = rest[len(option) :].removeprefix(", ")
                break
        else:
            chosen.append(OTHER)
            _, _, rest = rest.partition(", ")
    return list(dict.fromkeys(chosen))


def share(count: int, answered: int) -> str:
    return f"{count} of {answered} ({round(count * 100 / answered)}%)"


def read_rows(path: Path) -> tuple[list[str], list[list[str]]]:
    """The headers and the non-empty rows. Refuses a file with an identifying column before reading any answer."""
    try:
        with path.open(encoding="utf-8-sig", newline="") as file:
            reader = csv.reader(file)
            headers = [header.strip() for header in next(reader, [])]
            if not headers:
                raise SurveyFileError("the file has no header row")
            identifying = [
                header for header in headers if question_for(header) is None and IDENTIFYING_COLUMN.search(header)
            ]
            if identifying:
                raise SurveyFileError(
                    f"remove the column(s) that identify a person, then run this again: {', '.join(identifying)}"
                )
            rows = [row for row in reader if any(cell.strip() for cell in row)]
    except OSError as error:
        raise SurveyFileError(f"could not read the file: {error.strerror}") from error
    return headers, rows


def answers_in(rows: list[list[str]], column: int) -> list[str]:
    cells = (row[column].strip() if column < len(row) else "" for row in rows)
    return [cell for cell in cells if cell]


def count_question(question: Question, answers: list[str]) -> list[str]:
    lines = [f"{question.number}. {question.text} (answered: {len(answers)})"]
    if question.kind == "text":
        return lines
    counts: Counter[str] = Counter()
    for answer in answers:
        if question.kind == "multi":
            counts.update(split_choices(answer, question.options))
        else:
            counts[answer if answer in question.options else OTHER] += 1
    listed = question.options if OTHER in question.options or not counts[OTHER] else (*question.options, OTHER)
    lines += [f"  {option}: {share(counts[option], len(answers))}" for option in listed if answers]
    return lines


def report(path: Path) -> str:
    headers, rows = read_rows(path)
    lines = [f"Responses: {len(rows)}"]
    found: dict[int, int] = {}
    extra: list[int] = []
    for column, header in enumerate(headers):
        question = question_for(header)
        if question is not None:
            found[question.number] = column
        elif normalised(header) not in IGNORED_COLUMNS:
            extra.append(column)
    for question in QUESTIONS:
        lines.append("")
        if question.number in found:
            lines += count_question(question, answers_in(rows, found[question.number]))
        else:
            lines.append(f"{question.number}. {question.text} (not in this file)")
    for column in extra:
        lines += ["", f"Other column: {headers[column]} (answered: {len(answers_in(rows, column))})"]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Count the Sprint 1 survey export.")
    parser.add_argument("csv_path", type=Path, help="the Google Form export, kept outside the repository")
    args = parser.parse_args(argv)
    try:
        print(report(args.csv_path))
    except SurveyFileError as error:
        print(f"Stopped: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
