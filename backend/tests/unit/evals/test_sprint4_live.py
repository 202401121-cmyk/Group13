"""S4-2 (#45 follow-up): the five live briefs, measured the way the demo runs them, and the live floors from them.

The record was made once by hand (`python -m evals.live_briefs --out evals/results/sprint-4-live.json ...`, see
docs/evaluation/sprint-4-live.md); these tests check what it holds and that the floors come from it.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
import yaml

from app.contracts import SEARCH_TIME_LIMIT_S
from evals import live_briefs, thresholds
from evals.run_baselines import BACKEND, RESULTS_DIR

SPRINT_4 = RESULTS_DIR / "sprint-4-live.json"
BUDGET = {"llm": 200, "serper": 30, "reddit": 50, "jina": 30, "firecrawl": 10}
ORDER = ["smartphone", "laptop", "earbuds", "air_fryer", "smartwatch"]


@pytest.fixture(scope="module")
def record() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(SPRINT_4.read_text(encoding="utf-8"))
    return data


def measures_of(record: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {m["id"]: m for m in record["measures"]}


def test_the_record_keeps_numbers_and_fixed_labels_only(record: dict[str, Any]) -> None:
    assert live_briefs.text_in(record) == []  # no request, quote, product name, link or username (D-53)


def test_it_ran_at_the_normal_source_cap_inside_the_stated_budget(record: dict[str, Any]) -> None:
    from app.extract.settings import load_extract_settings

    cap = load_extract_settings(env={}).max_sources_per_search
    assert re.search(r"EXTRACT_MAX_SOURCES=(\d+)", record["budget_note"])[1] == str(cap) == "40"  # type: ignore[index]
    assert f"limits {BUDGET}" in record["budget_note"]
    assert all(record["calls_used"].get(service, 0) <= limit for service, limit in BUDGET.items())
    assert record["runner"] == "python -m evals.live_briefs (live)"


def test_it_ran_the_five_briefs_in_order(record: dict[str, Any]) -> None:
    assert [b["category"] for b in record["briefs"]] == ORDER


def test_every_finished_brief_stayed_inside_the_search_time_limit(record: dict[str, Any]) -> None:
    finished = [b for b in record["briefs"] if "search_seconds" in b]
    assert finished
    for brief in finished:
        assert brief["search_seconds"] < SEARCH_TIME_LIMIT_S, brief["category"]
    assert measures_of(record)["live.search_seconds_max"]["value"] < SEARCH_TIME_LIMIT_S


def test_both_live_safety_measures_are_recorded_now(record: dict[str, Any]) -> None:
    found = measures_of(record)
    for measure_id in ("live.unsourced_shown", "live.most_mentioned_worst_fit_wins", "live.hard_rule_violations_shown"):
        assert found[measure_id]["status"] == "measured", measure_id
        assert found[measure_id]["value"] == 0, measure_id  # none at all: the floor is exact


# --- the floors -----------------------------------------------------------------------------------------------------


def test_the_live_floors_come_from_the_sprint_4_record() -> None:
    assert thresholds.LIVE_OUT == SPRINT_4
    loaded = thresholds.load(thresholds.THRESHOLDS).measures
    live = {k: v for k, v in loaded.items() if k.startswith("live.")}
    assert live
    assert all("evals/results/sprint-4-live.json" in v.source for v in live.values())


def test_the_three_live_safety_floors_are_exact_zero() -> None:
    loaded = thresholds.load(thresholds.THRESHOLDS).measures
    for measure_id in ("live.unsourced_shown", "live.most_mentioned_worst_fit_wins", "live.hard_rule_violations_shown"):
        floor = loaded[measure_id]
        assert (floor.kind, floor.direction, floor.floor) == ("safety", "at_most", 0.0), measure_id


def test_the_committed_floors_are_what_write_gives_now() -> None:
    records = []
    for path in thresholds.RECORDS:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            records.append({**data, "file": path.relative_to(BACKEND.parent).as_posix()})
    committed = yaml.safe_load(thresholds.THRESHOLDS.read_text(encoding="utf-8"))
    assert thresholds.build(records) == committed
