"""US-41 (S5-23, D-102 item 4): the coverage record also says which products were looked up, ranked or "can't tell",
their coverage before and after, and the products ranked with a score before and after. Offline: a recorded gate run
replayed with the stub enrich, so nothing is looked up and no call is made."""

from __future__ import annotations

from app.enrich import EnrichStage
from evals import enrich_coverage, live_briefs
from evals.gate import recordings


def test_a_replay_records_the_products_looked_up_and_the_products_ranked() -> None:
    folder = next(f for f in recordings() if f.name == "gate_laptop")
    run = enrich_coverage.replay_coverage(folder, EnrichStage)
    assert run["looked_up"] > 0
    assert run["looked_up_before"] == run["looked_up_after"]  # the stub looks nothing up
    assert isinstance(run["ranked_before"], int) and run["ranked_before"] == run["ranked_after"]
    assert live_briefs.text_in({"runs": {folder.name: run}}) == []  # numbers and fixed labels only


def test_the_measures_add_the_ranked_products_and_average_the_looked_up_coverage() -> None:
    runs = {
        "gate_a": {
            "before": 0.2,
            "after": 0.4,
            "ranked_before": 3,
            "ranked_after": 4,
            "looked_up": 5,
            "looked_up_before": 0.1,
            "looked_up_after": 0.3,
        },
        "brief_b": {
            "top": 0,
            "ranked_before": 0,
            "ranked_after": 2,
            "looked_up": 5,
            "looked_up_before": 0.0,
            "looked_up_after": 0.2,
        },
        "brief_c": {"top": 0, "ranked_before": 0, "ranked_after": 0, "looked_up": 0, "looked_up_before": None},
        "brief_d": {"top": 0},  # from before S5-23: counted in neither
    }
    found = {m["id"]: m for m in enrich_coverage.measures(runs, "made-up source")}
    assert (found["enrich.ranked_before"]["value"], found["enrich.ranked_after"]["value"]) == (3, 6)
    assert found["enrich.ranked_after"]["unit"] == "count"
    assert found["enrich.looked_up_evidence_before"]["value"] == 0.05
    assert found["enrich.looked_up_evidence_after"]["value"] == 0.25
    assert "enrich.top5_evidence_gain" in found  # the first ranking's top 5, as before
    assert "over 3 runs" in found["enrich.ranked_before"]["detail"]


def test_runs_without_the_new_fields_give_only_the_top_five_measures() -> None:
    runs = {"gate_a": {"before": 0.2, "after": 0.4}}
    assert [m["id"] for m in enrich_coverage.measures(runs, "made-up source")] == [
        "enrich.top5_evidence_before",
        "enrich.top5_evidence_after",
        "enrich.top5_evidence_gain",
    ]
    only_new = {"brief_a": {"top": 0, "ranked_before": 0, "ranked_after": 1}}
    assert [m["id"] for m in enrich_coverage.measures(only_new, "made-up source")] == [
        "enrich.ranked_before",
        "enrich.ranked_after",
    ]
