"""US-06's measurement (S5-3 DECIDED 6): 20 seeded returning shoppers, made by code from the S4-3 simulator; repeated
questions and accuracy with and without memory. Also the model check's sentences and the committed records."""

from __future__ import annotations

import json
from typing import Any

from app.memory import MemorySettings
from evals import memory_model_check as check
from evals import memory_sim as sim
from evals.thresholds import PLAN_TARGETS, all_measures, load


def quick(shoppers: int = 6) -> dict[str, Any]:
    return sim.simulate(sim.WordEmbedder(), MemorySettings(), shoppers=shoppers)


def test_the_same_seed_gives_the_same_record() -> None:
    assert quick(3) == quick(3)


def test_each_category_gets_returning_shoppers_and_memory_spares_repeated_questions() -> None:
    record = quick()
    rows = record["shoppers"]
    assert {r["category"] for r in rows} == set(sim.CATEGORIES)
    for r in rows:
        assert r["remembered"] <= 5
        assert r["repeated_with"] <= r["repeated_without"]
        assert r["questions_with"] <= r["questions_without"]
    summary = record["summary"]
    assert summary["repeated_with"] < summary["repeated_without"]
    assert summary["repeated_reduction"] == round(1 - summary["repeated_with"] / summary["repeated_without"], 4)


def test_without_memory_the_second_session_repeats_every_answered_question() -> None:
    """The baseline: with no memory, everything answered in the first session is asked again."""
    record = quick(2)
    for r in record["shoppers"]:
        assert r["repeated_without"] == r["questions_without"]


def test_the_measures_are_the_ones_thresholds_floor() -> None:
    record = quick(2)
    ids = {m["id"] for m in record["measures"]}
    assert ids == {"memory.repeated_reduction", "memory.accuracy", "memory.accuracy_change"}
    assert {"memory.repeated_reduction", "memory.accuracy_change"} <= set(PLAN_TARGETS)
    assert PLAN_TARGETS["memory.repeated_reduction"][0] == 0.5  # the story's target, never lowered


def test_the_committed_record_used_the_pinned_model_on_20_shoppers_and_its_floors_hold() -> None:
    record = json.loads(sim.MEMORY_OUT.read_text(encoding="utf-8"))
    settings = sim.load_memory_settings(env={})
    assert record["model"]["embedder"] == "model"
    assert (record["model"]["revision"], record["model"]["model_file"]) == (settings.revision, settings.model_file)
    assert record["settings"]["shoppers"] == sim.SHOPPERS == 20 and len(record["shoppers"]) == 20
    thresholds = load(sim.RESULTS_DIR.parent / "thresholds.yaml")
    measured = {m["id"]: m for m in all_measures()}
    for measure_id in ("memory.repeated_reduction", "memory.accuracy", "memory.accuracy_change"):
        assert measured[measure_id]["value"] >= thresholds.measures[measure_id].floor


def test_the_model_check_uses_200_distinct_sentences_from_our_own_criteria() -> None:
    sentences = check.sentences()
    assert len(sentences) == len(set(sentences)) == 200
    assert all(": " in s and len(s) <= 1000 for s in sentences)
