"""One table for rough answers (US-08): the numbers live in ROUGH_LEVEL_WEIGHTS and nowhere else in the backend.

The search finds tables: a dict, or dict(), that gives a level name a number. A number hidden in an if statement is
still a job for a reviewer.
"""

from __future__ import annotations

import ast
from pathlib import Path

import app.interview.answers as answers
from app.contracts import ROUGH_LEVEL_WEIGHTS, RoughLevel
from app.interview.plan import load_interview_rules

BACKEND = Path(__file__).resolve().parents[3]
THE_ONE_TABLE = "app/contracts/interview.py"
LEVEL_WORDS = {word.replace("_", mark) for word in RoughLevel for mark in ("_", " ", "-")}


def _names_a_level(key: ast.expr | None) -> bool:
    if isinstance(key, ast.Constant) and isinstance(key.value, str):
        return key.value.lower() in LEVEL_WORDS
    return isinstance(key, ast.Attribute) and key.attr.lower() in LEVEL_WORDS


def _is_number(value: ast.expr) -> bool:
    return isinstance(value, ast.Constant) and type(value.value) in (int, float)


def copies_in(source: str) -> list[int]:
    """The lines where a dict gives a level name a number."""
    lines: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Dict):
            pairs = zip(node.keys, node.values, strict=True)
            lines += [key.lineno for key, value in pairs if key and _names_a_level(key) and _is_number(value)]
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "dict":
            lines += [k.value.lineno for k in node.keywords if k.arg in LEVEL_WORDS and _is_number(k.value)]
    return lines


def code_files() -> list[Path]:
    return [path for folder in ("app", "scripts") for path in (BACKEND / folder).rglob("*.py")]


def test_the_search_finds_a_copy_when_there_is_one() -> None:
    """Otherwise the test below could pass by finding nothing at all."""
    assert copies_in('TABLE = {"low": 2.0}') == [1]
    assert copies_in('TABLE = {\n    "Good Enough": 3,\n}') == [2]
    assert copies_in("TABLE = {RoughLevel.MEDIUM: 5.0}") == [1]
    assert copies_in('TABLE = {"good-enough": 3}') == [1]
    assert copies_in("TABLE = dict(high=8.0)") == [1]
    assert copies_in('NOT_A_TABLE = {"low": "a word", "battery": 5.0}') == []


def test_no_second_copy_of_the_table_exists() -> None:
    found = {path.relative_to(BACKEND).as_posix(): len(copies_in(path.read_text("utf-8"))) for path in code_files()}
    assert {path: count for path, count in found.items() if count} == {THE_ONE_TABLE: len(RoughLevel)}


def test_the_old_table_is_gone() -> None:
    assert not hasattr(answers, "REPLY_WEIGHTS")
    assert [path.name for path in code_files() if "REPLY_WEIGHTS" in path.read_text("utf-8")] == []


def test_the_low_answer_rule_matches_the_low_level() -> None:
    """A threshold in config/interview_rules.yaml. Changing one without the other fails here."""
    assert load_interview_rules().low_answer_weight == ROUGH_LEVEL_WEIGHTS[RoughLevel.LOW]
