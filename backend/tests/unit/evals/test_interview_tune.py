"""Tuning the smart interview's three numbers (US-04, D-69 #5) on the simulator, on a tuning seed.

The rule is accuracy first (Om, 17 Nov 2026): only settings at least as accurate as the fixed order on the tuning seed
count, then the fewest questions, then the settings nearest D-69's. The final run is on another seed.
"""

from __future__ import annotations

import json
from dataclasses import asdict

import pytest

from app.interview.beliefs import BeliefSettings
from evals import interview_sim as sim
from evals import interview_tune as tune


def row(prior: float, exact: float, settled: float, questions: float, accuracy: float) -> tune.Row:
    return tune.Row(BeliefSettings(prior, exact, settled), questions=questions, accuracy=accuracy, mae=1.0)


# --- the rule -------------------------------------------------------------------------------------------------------


def test_only_settings_as_accurate_as_the_fixed_order_count_then_the_fewest_questions() -> None:
    rows = [row(2.5, 1.5, 1.2, 4.5, 0.44), row(2.5, 1.5, 0.9, 6.0, 0.60), row(2.5, 1.5, 0.7, 8.0, 0.61)]
    chosen = tune.choose(rows, fixed_accuracy=0.60)
    assert chosen.settings == BeliefSettings(2.5, 1.5, 0.9)  # 4.5 questions is fewer, but it loses accuracy


def test_a_tie_goes_to_the_settings_nearest_d69() -> None:
    # distances from (2.5, 1.5, 1.2): 0.2 + 0.333 + 0.583 = 1.117; 0 + 0 + 0.417 = 0.417; 0.2 + 0.333 + 0.5 = 1.033
    rows = [row(2.0, 1.0, 0.5, 8.0, 0.6), row(2.5, 1.5, 0.7, 8.0, 0.6), row(3.0, 2.0, 0.6, 8.0, 0.6)]
    assert tune.choose(rows, fixed_accuracy=0.6).settings == BeliefSettings(2.5, 1.5, 0.7)
    assert tune.distance(BeliefSettings(2.5, 1.5, 0.7)) == pytest.approx(0.5 / 1.2)
    assert tune.distance(tune.D69) == 0


def test_with_nothing_as_accurate_the_most_accurate_is_taken() -> None:
    rows = [row(2.5, 1.5, 1.2, 4.5, 0.44), row(2.5, 1.5, 0.9, 6.0, 0.55), row(2.0, 1.5, 0.9, 5.0, 0.55)]
    assert tune.choose(rows, fixed_accuracy=0.6).settings == BeliefSettings(2.0, 1.5, 0.9)  # 0.55, fewer questions


def test_no_rows_is_an_error() -> None:
    with pytest.raises(ValueError):
        tune.choose([], fixed_accuracy=0.6)


# --- the grid and the seed ------------------------------------------------------------------------------------------


def test_the_grid_holds_d69_and_keeps_the_settled_sd_below_the_prior() -> None:
    grid = tune.grid()
    assert tune.D69 in grid
    assert all(s.settled_sd < s.prior_sd for s in grid)
    assert len(grid) == len(set(grid))
    assert {s.prior_sd for s in grid} == set(tune.PRIOR_SDS)
    assert {s.settled_sd for s in grid} == set(tune.SETTLED_SDS)


def test_the_tuning_seed_is_never_the_report_seed() -> None:
    assert tune.TUNING_SEED != sim.SEED


def test_a_small_tuning_run_records_every_row_and_its_choice() -> None:
    grid = [BeliefSettings(2.5, 1.5, 1.2), BeliefSettings(2.5, 1.5, 0.7)]
    record = tune.tune(seed=tune.TUNING_SEED, shoppers=1, candidates=grid)
    assert [(r["prior_sd"], r["exact_sd"], r["settled_sd"]) for r in record["rows"]] == [
        (2.5, 1.5, 1.2),
        (2.5, 1.5, 0.7),
    ]
    assert record["chosen"] in record["rows"]
    assert record["fixed"]["shoppers"] == len(sim.CATEGORIES)
    assert record["settings"]["seed"] == tune.TUNING_SEED
    assert record["d69"]["settled_sd"] == 1.2
    assert record == tune.tune(seed=tune.TUNING_SEED, shoppers=1, candidates=grid)  # same seed, same output


# --- the committed record -------------------------------------------------------------------------------------------


def test_the_committed_choice_is_the_interviews_default_and_measures_the_same_now() -> None:
    committed = json.loads(tune.TUNE_OUT.read_text(encoding="utf-8"))
    chosen = committed["chosen"]
    assert {k: chosen[k] for k in ("prior_sd", "exact_sd", "settled_sd")} == asdict(BeliefSettings())
    assert committed["settings"]["seed"] == tune.TUNING_SEED
    assert committed["settings"]["shoppers_per_category"] == sim.SHOPPERS
    assert len(committed["rows"]) == len(tune.grid())
    assert chosen["accuracy"] >= committed["fixed"]["accuracy"]
    assert committed["keeps_fixed_accuracy"] is True
    assert tune.measure(BeliefSettings(), tune.TUNING_SEED, sim.SHOPPERS).as_dict() == chosen
    assert tune.measure(tune.D69, tune.TUNING_SEED, sim.SHOPPERS).as_dict() == committed["d69"]
    assert sim.simulate("fixed", tune.TUNING_SEED)["summary"]["overall"] == committed["fixed"]


def test_the_committed_rows_are_the_grid_and_the_rule_still_picks_the_committed_choice() -> None:
    committed = json.loads(tune.TUNE_OUT.read_text(encoding="utf-8"))
    rows = [
        tune.Row(
            BeliefSettings(r["prior_sd"], r["exact_sd"], r["settled_sd"]),
            questions=r["questions"],
            accuracy=r["accuracy"],
            mae=r["mae"],
        )
        for r in committed["rows"]
    ]
    assert [r.settings for r in rows] == tune.grid()
    assert tune.choose(rows, committed["fixed"]["accuracy"]).as_dict() == committed["chosen"]
    assert committed["d69"] in committed["rows"]


def test_another_seed_or_size_never_replaces_the_committed_record() -> None:
    before = tune.TUNE_OUT.read_bytes()
    with pytest.raises(SystemExit):
        tune.main(["--shoppers", "2"])
    assert tune.TUNE_OUT.read_bytes() == before
