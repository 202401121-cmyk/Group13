"""US-07 criterion 2 (S4-32): every definition in the fixed fallback criteria is plain, by the same check the interview
uses (app/interview/terms.py: grade 9 or under, no technical word but the label's own). Only definitions changed: every
other field still matches main as of S4-32.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]

from app.contracts import MAX_EXPLANATION_WORDS, Criterion
from app.criteria.fallback import DEFAULT_FALLBACK_PATH
from app.explain.readability import grade
from app.interview.questions import question_for
from app.interview.terms import is_plain, is_technical, jargon_in, label_terms

SHAPE = Path(__file__).resolve().parents[2] / "fixtures" / "criteria" / "fallback_shape.json"


def _sets() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(DEFAULT_FALLBACK_PATH.read_text(encoding="utf-8"))
    return data


def _every_criterion() -> list[tuple[str, dict[str, Any]]]:
    data = _sets()
    return [
        (f"{part}.{name}.{raw['key']}", raw)
        for part in ("families", "categories")
        for name, group in data[part].items()
        for raw in group["criteria"]
    ]


CRITERIA = _every_criterion()


def _criterion(raw: dict[str, Any]) -> Criterion:
    return Criterion(key=raw["key"], label=raw["label"], definition=raw["definition"], default_weight=5)


@pytest.mark.parametrize("raw", [raw for _, raw in CRITERIA], ids=[name for name, _ in CRITERIA])
def test_every_fallback_definition_is_plain(raw: dict[str, Any]) -> None:
    c = _criterion(raw)
    own = label_terms(c)
    assert is_plain(c.definition, own), (round(grade(c.definition), 1), jargon_in(c.definition, own), c.definition)


def test_only_definitions_changed() -> None:
    """Keys, labels, weights, units, directions, the value-for-money flag and the order are main's (S4-32 snapshot)."""
    shape = json.loads(SHAPE.read_text(encoding="utf-8"))
    data = _sets()
    now = {
        part: {
            name: [{k: v for k, v in c.items() if k != "definition"} for c in group["criteria"]]
            for name, group in data[part].items()
        }
        for part in ("families", "categories")
    }
    assert now == {part: shape[part] for part in ("families", "categories")}


def test_every_fallback_question_explains_with_its_own_definition_in_40_words_or_fewer() -> None:
    linked = 0
    for name, raw in CRITERIA:
        c = _criterion(raw)
        for term in question_for(c).terms:
            linked += 1
            assert term.explanation == c.definition, name  # plain, so it is shown as it is (P-25 item 4, step 1)
            assert len(term.explanation.split()) <= MAX_EXPLANATION_WORDS, name
        assert bool(question_for(c).terms) == is_technical(c), name
    assert linked >= 100  # most fallback questions are technical by default (P-25 item 1)
