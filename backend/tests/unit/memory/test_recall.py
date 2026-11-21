"""US-06 recall (DECIDED 3): the shopper's own rows, the same category plus brands left out with Always; each scored
cosine x 0.5 ^ (age_days / 90); the 5 best at or above 0.7, ties to the newer."""

from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.memory.recall import decay, rank
from app.memory.settings import MemorySettings
from app.memory.store import StoredPreference

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
SETTINGS = MemorySettings()


def unit(*values: float) -> list[float]:
    size = math.sqrt(sum(v * v for v in values))
    return [v / size for v in values]


def at_cosine(c: float) -> list[float]:
    """A unit vector whose cosine with (1, 0) is c."""
    return [c, math.sqrt(max(0.0, 1 - c * c))]


def row(kind: str = "weight", *, days: float = 0, vector: list[float] | None = None, **fields: Any) -> StoredPreference:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "kind": kind,
        "category": "laptop",
        "criterion_key": "battery_life" if kind == "weight" else None,
        "text": "Battery life: high importance",
        "weight": 8.0 if kind == "weight" else None,
        "rule": None,
        "always": False,
        "created_at": NOW - timedelta(days=days),
        "updated_at": NOW - timedelta(days=days),
        "embedding": tuple(vector or unit(1, 0)),
    }
    if kind == "exclusion":
        values.update(
            {
                "category": None,
                "rule": {"kind": "brand", "value": "Acme", "always": True},
                "always": True,
                "text": "Never Acme",
            }
        )
    values.update(fields)
    return StoredPreference(**values)


CRITERIA = [("battery_life", unit(1, 0)), ("display", unit(0, 1))]


def test_decay_halves_every_90_days() -> None:
    assert decay(0, 90) == 1.0
    assert decay(90, 90) == pytest.approx(0.5)
    assert decay(180, 90) == pytest.approx(0.25)


def test_an_item_200_days_old_at_cosine_09_scores_below_07_and_drops() -> None:
    old = row(days=200, vector=at_cosine(0.9))
    expected = 0.9 * 0.5 ** (200 / 90)
    assert expected < 0.7
    assert rank([old], CRITERIA, NOW, SETTINGS) == []
    kept = rank([old], CRITERIA, NOW, MemorySettings(min_score=0.1))
    assert kept[0].score == pytest.approx(expected) and kept[0].similarity == pytest.approx(0.9)
    assert kept[0].age_days == pytest.approx(200)


def test_a_fresh_item_at_cosine_09_is_kept_and_fills_its_criterion() -> None:
    [found] = rank([row(vector=at_cosine(0.9))], CRITERIA, NOW, SETTINGS)
    assert found.criterion_key == "battery_life"
    assert found.score == pytest.approx(0.9)


def test_a_weight_fills_the_new_criterion_it_is_closest_to() -> None:
    reworded = [("battery_hours", unit(1, 0.05)), ("display", unit(0, 1))]
    [found] = rank([row(criterion_key="battery_life")], reworded, NOW, SETTINGS)
    assert found.criterion_key == "battery_hours"


def test_one_remembered_weight_per_criterion_the_better_one() -> None:
    better, worse = row(days=1), row(days=30)
    found = rank([worse, better], CRITERIA, NOW, SETTINGS)
    assert [f.row.id for f in found] == [better.id]


def test_ties_go_to_the_newer() -> None:
    rules = [
        row(
            "hard_rule",
            rule={"id": f"r{i}"},
            text=f"Must have: {i}",
            updated_at=NOW - timedelta(hours=hours),
            created_at=NOW - timedelta(hours=hours),
        )
        for i, hours in ((1, 3), (2, 1))
    ]
    found = rank(rules, CRITERIA, NOW, MemorySettings(half_life_days=1e9))  # all but no decay: the scores tie
    assert round(found[0].score, 6) == round(found[1].score, 6)
    assert [f.row.rule["id"] for f in found if f.row.rule] == ["r2", "r1"]
    again = rank(list(reversed(rules)), CRITERIA, NOW, MemorySettings(half_life_days=1e9))
    assert [f.row.id for f in again] == [f.row.id for f in found]  # the order never depends on how rows were read


def test_at_most_five_the_best_first() -> None:
    criteria = [(f"c{i}", unit(*[1.0 if j == i else 0.0 for j in range(8)])) for i in range(8)]
    rows = [row(criterion_key=f"c{i}", vector=criteria[i][1], days=i) for i in range(8)]
    found = rank(rows, criteria, NOW, SETTINGS)
    assert len(found) == 5
    assert [f.criterion_key for f in found] == ["c0", "c1", "c2", "c3", "c4"]
    assert [f.score for f in found] == sorted((f.score for f in found), reverse=True)


def test_a_brand_left_out_with_always_holds_by_the_shoppers_word_not_by_closeness() -> None:
    far = row("exclusion", vector=[0.0, 1.0])
    [found] = rank([far], CRITERIA, NOW, SETTINGS)
    assert (found.similarity, found.criterion_key) == (1.0, None)


def test_a_leave_out_too_old_drops_like_any_other() -> None:
    assert rank([row("exclusion", days=100)], CRITERIA, NOW, SETTINGS) == []


def test_a_rule_is_kept_only_when_it_is_close_to_one_of_the_new_criteria() -> None:
    criteria = [("battery_life", [1.0, 0.0, 0.0]), ("display", [0.0, 1.0, 0.0])]
    near = row("hard_rule", rule={"id": "near"}, vector=[0.95, 0.0, math.sqrt(1 - 0.95**2)], text="Must have: near")
    far = row("hard_rule", rule={"id": "far"}, vector=[0.2, 0.0, math.sqrt(1 - 0.2**2)], text="Must have: far")
    found = rank([near, far], criteria, NOW, SETTINGS)
    assert [f.row.rule["id"] for f in found if f.row.rule] == ["near"]
    assert found[0].similarity == pytest.approx(0.95)


def test_a_row_from_the_future_counts_as_new() -> None:
    [found] = rank([row(days=-3)], CRITERIA, NOW, SETTINGS)
    assert found.age_days == 0 and found.score == pytest.approx(1.0)


def test_nothing_to_compare_gives_nothing() -> None:
    assert rank([row()], [], NOW, SETTINGS) == []
    assert rank([], CRITERIA, NOW, SETTINGS) == []


def test_leave_outs_fill_only_the_places_answers_leave() -> None:
    """Five brands left out with Always score on age alone (1.0 when new); they never push a remembered answer out."""
    leave_outs = [
        row("exclusion", rule={"kind": "brand", "value": f"Acme {i}", "always": True}, text=f"Never Acme {i}")
        for i in range(5)
    ]
    answer = row(vector=at_cosine(0.9), days=10)
    found = rank([*leave_outs, answer], CRITERIA, NOW, SETTINGS)
    assert len(found) == 5 and found[0].row.id == answer.id
    assert [f.row.kind for f in found] == ["weight", "exclusion", "exclusion", "exclusion", "exclusion"]
