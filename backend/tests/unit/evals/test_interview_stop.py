"""S5-14 (US-04, D-75): the product-aware stop, tried on the interview simulator only.

After at least 3 answers, decide's seeded simulations score the brief's products with weights drawn from the interview's
beliefs, and the interview stops when one product is first in at least a set share of them. The share is tuned on one
seed and reported on another. The bar to ship it: the fixed order's accuracy with 30% fewer questions.
"""

from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path
from typing import Any

import pytest

from app.contracts import (
    AskedQuestion,
    CriteriaSet,
    InterviewState,
    InterviewTurn,
    Product,
    ResolvedValue,
    StageContext,
    WeightBelief,
)
from app.interview import AdaptiveInterviewStage
from evals import interview_sim as sim
from evals import interview_stop as stop

CTX = StageContext(search_id="srch_interviewstop")


def laptop() -> CriteriaSet:
    return sim.criteria_for("laptop")


def defaults(criteria: CriteriaSet) -> dict[str, float]:
    return {c.key: c.default_weight for c in criteria.criteria}


def one_product(criteria: CriteriaSet) -> sim.Brief:
    """The category's brief with its first product only."""
    brief = sim.brief_for(criteria)
    first = brief.products[0]
    return sim.Brief([first], [v for v in brief.values if v.product_id == first.id], {first.id: brief.scores[first.id]})


def twins(criteria: CriteriaSet, confidence: float | None = None) -> sim.Brief:
    """Two products with the very same scores: the brief's first product and a copy of it under another id."""
    brief = one_product(criteria)
    first = brief.products[0]
    twin = Product(id=f"{first.id}_twin", name=f"{first.name} twin", brand="Sim")
    values: list[ResolvedValue] = []
    for value in brief.values:
        sure = value.confidence if confidence is None else confidence
        values.append(ResolvedValue.model_validate({**value.model_dump(), "confidence": sure}))
        values.append(ResolvedValue.model_validate({**value.model_dump(), "product_id": twin.id, "confidence": sure}))
    return sim.Brief([first, twin], values, {first.id: brief.scores[first.id], twin.id: brief.scores[first.id]})


def smart_questions(criteria: CriteriaSet, shopper: sim.Shopper) -> int:
    return sim.interview(AdaptiveInterviewStage(), criteria, shopper, "smart").questions_asked


# --- the edge cases D-75 names ----------------------------------------------------------------------------------------


def test_the_same_seed_gives_the_same_record() -> None:
    first = stop.simulate_stop(0.9, seed=7, shoppers=3, categories=("laptop", "earbuds"))
    assert first == stop.simulate_stop(0.9, seed=7, shoppers=3, categories=("laptop", "earbuds"))
    assert first["planner"] == "stop" and first["ordering"] == ["information_gain"]
    brief, beliefs = (
        sim.brief_for(laptop()),
        {key: WeightBelief(mean=w, sd=2.5) for key, w in defaults(laptop()).items()},
    )
    assert stop.first_shares(brief, beliefs, "laptop", 7) == stop.first_shares(brief, beliefs, "laptop", 7)


def test_a_brief_with_one_product_stops_at_three_answers() -> None:
    criteria = laptop()
    shopper = sim.shopper_with(criteria, defaults(criteria))
    step = sim.interview(stop.ProductAwareStage(one_product(criteria)), criteria, shopper, "stop")
    assert step.kind == "finished" and step.rubric is not None
    assert step.questions_asked == 3 < smart_questions(criteria, shopper)
    assert step.ordering == "information_gain"


def test_a_tie_between_two_products_never_stops_early() -> None:
    criteria = laptop()
    shopper = sim.shopper_with(criteria, defaults(criteria))
    for share in (stop.START_SHARE, 0.6):
        step = sim.interview(stop.ProductAwareStage(twins(criteria), share), criteria, shopper, "stop")
        assert step.questions_asked == smart_questions(criteria, shopper)  # it asks all the smart interview asks
        assert step.ordering == "information_gain"  # the rule ran each turn: no turn fell back to the fixed order
    exact = twins(criteria, confidence=1.0)  # no noise on the scores: the two totals are level in every simulation
    beliefs = {key: WeightBelief(mean=w, sd=0.5) for key, w in defaults(criteria).items()}
    assert sorted(stop.first_shares(exact, beliefs, "laptop", 47).values()) == [0.5, 0.5]
    step = sim.interview(stop.ProductAwareStage(exact, 0.51), criteria, shopper, "stop")
    assert step.questions_asked == smart_questions(criteria, shopper)
    assert step.ordering == "information_gain"


@pytest.mark.parametrize("share", [0.5, 0.3, 0.0, 1.01])
def test_a_share_of_one_half_or_less_is_refused_as_a_tie_would_stop_it(share: float) -> None:
    with pytest.raises(ValueError, match="above one half"):
        stop.ProductAwareStage(one_product(laptop()), share)
    assert all(0.5 < s <= 1 for s in stop.SHARES) and stop.START_SHARE in stop.SHARES


# --- the rule ---------------------------------------------------------------------------------------------------------


def test_fewer_than_three_answers_never_stop_and_a_skip_is_no_answer() -> None:
    criteria = laptop()
    skipper = sim.shopper_with(criteria, defaults(criteria), skip_all=True)
    step = sim.interview(stop.ProductAwareStage(one_product(criteria)), criteria, skipper, "stop")
    assert step.questions_asked == smart_questions(criteria, skipper)
    assert stop.answers_given(step.state) == 0
    two = stop.ProductAwareStage(one_product(criteria), min_answers=2)
    assert sim.interview(two, criteria, sim.shopper_with(criteria, defaults(criteria)), "stop").questions_asked == 2


def test_a_remembered_answer_counts_and_a_pending_question_does_not() -> None:
    criteria = laptop()
    smart = AdaptiveInterviewStage()
    step = smart.run(InterviewTurn(state=sim.state_for(criteria)), CTX)
    for _ in range(3):
        step = smart.run(InterviewTurn(state=step.state, level=sim.nearest_level(5.0)), CTX)
    assert step.question is not None and stop.answers_given(step.state) == 3  # 3 answered, 1 on screen
    remembered = [
        AskedQuestion.model_validate({**e.model_dump(), "status": "remembered"}) if e.status == "answered" else e
        for e in step.state.history
    ]
    state = InterviewState.model_validate({**step.state.model_dump(), "history": [e.model_dump() for e in remembered]})
    assert stop.answers_given(state) == 3


def test_a_question_on_screen_is_asked_again_never_cut_off() -> None:
    criteria = laptop()
    smart = AdaptiveInterviewStage()
    step = smart.run(InterviewTurn(state=sim.state_for(criteria)), CTX)
    for _ in range(3):
        step = smart.run(InterviewTurn(state=step.state, level=sim.nearest_level(5.0)), CTX)
    assert step.question is not None
    again = stop.ProductAwareStage(one_product(criteria))._smart_next(step.state)  # one product: always a clear leader
    assert again.kind == "question" and again.question is not None and again.question.id == step.question.id


def test_the_draws_follow_the_beliefs() -> None:
    criteria = laptop()
    brief = sim.brief_for(criteria)
    keys = [c.key for c in criteria.criteria]

    def alone_on_top(k: str) -> bool:
        first, second = sorted((scores[k] for scores in brief.scores.values()), reverse=True)[:2]
        return first > second

    key = next(k for k in keys if alone_on_top(k))  # two products can share a top score: take one led by one product
    sure = {k: WeightBelief(mean=10.0 if k == key else 0.0, sd=0.01) for k in keys}  # only the first criterion counts
    shares = stop.first_shares(brief, sure, "laptop", 47)
    best = max(brief.scores, key=lambda pid: brief.scores[pid][key])
    assert max(shares, key=lambda pid: shares[pid]) == best and shares[best] > 0.9
    assert sum(shares.values()) == pytest.approx(1.0)
    unsure = {k: WeightBelief(mean=w, sd=2.5) for k, w in defaults(criteria).items()}
    assert max(stop.first_shares(brief, unsure, "laptop", 47).values()) < shares[best]


# --- the tuning, the bar and the report -------------------------------------------------------------------------------


def row(share: float, questions: float, accuracy: float) -> stop.Row:
    return stop.Row(share, questions=questions, accuracy=accuracy, mae=1.0)


def test_only_shares_as_accurate_as_the_fixed_order_count_then_the_fewest_questions() -> None:
    rows = [row(0.5, 6.1, 0.58), row(0.6, 7.1, 0.60), row(0.7, 7.6, 0.61)]
    assert stop.choose(rows, fixed_accuracy=0.60).share == 0.6  # 6.1 questions is fewer, but it loses accuracy


def test_a_tie_goes_to_the_share_nearest_09_then_the_higher() -> None:
    assert stop.choose([row(0.7, 8.0, 0.6), row(0.95, 8.0, 0.6)], fixed_accuracy=0.6).share == 0.95
    assert stop.choose([row(0.85, 8.0, 0.6), row(0.95, 8.0, 0.6)], fixed_accuracy=0.6).share == 0.95


def test_with_nothing_as_accurate_the_most_accurate_is_taken() -> None:
    rows = [row(0.4, 4.7, 0.53), row(0.5, 6.1, 0.58), row(0.55, 6.5, 0.58)]
    assert stop.choose(rows, fixed_accuracy=0.6).share == 0.5


def test_no_rows_is_an_error() -> None:
    with pytest.raises(ValueError):
        stop.choose([], fixed_accuracy=0.6)


def overall(questions: float, accuracy: float) -> dict[str, float]:
    return {"questions_mean": questions, "accuracy": accuracy}


def test_the_bar_is_the_fixed_orders_accuracy_with_30_percent_fewer_questions() -> None:
    fixed = overall(8.0, 0.6)
    met = stop.verdict(overall(5.6, 0.6), fixed)
    assert (met["met"], met["says"], met["fewer_questions"]) == (True, "met", 0.3)
    assert stop.verdict(overall(5.61, 0.6), fixed)["says"] == "not met"  # 29.9% fewer
    almost = stop.verdict(overall(4.6833, 0.6), overall(6.69, 0.6))  # 29.995% fewer: rounds to 0.3, still short
    assert (almost["met"], almost["fewer_questions"]) == (False, 0.3)
    assert stop.verdict(overall(4.0, 0.5967), fixed)["says"] == "not met"  # half the questions, one shopper less right
    assert stop.verdict(overall(7.1, 0.6033), fixed)["met"] is False


def test_the_tuning_measures_every_share_and_the_start_share_beside_them() -> None:
    tuned = stop.tune(seed=7, shoppers=1, shares=[0.6, 0.9])
    assert [r["share"] for r in tuned["rows"]] == [0.6, 0.9]
    assert tuned["start"] == tuned["rows"][1] and tuned["settings"]["min_answers"] == 3
    assert tuned["chosen"] in tuned["rows"] and tuned["rule"] == stop.RULE
    rows = [stop.Row(**r) for r in tuned["rows"]]
    assert tuned["chosen"] == stop.choose(rows, tuned["fixed"]["accuracy"]).as_dict()
    alone = stop.tune(seed=7, shoppers=1, shares=[0.6])
    assert alone["start"] == tuned["start"]  # measured on its own when the grid leaves it out


def test_the_report_carries_the_fixed_order_and_the_bar() -> None:
    reported = stop.report(0.9, seed=7, shoppers=1, tuned_on=11)
    assert reported["planner"] == "stop" and reported["stop"]["share"] == 0.9
    assert reported["stop"]["tuned_on_seed"] == 11 and reported["live_limit"] == stop.LIVE_LIMIT
    assert reported["fixed"] == sim.simulate("fixed", 7, 1)["summary"]["overall"]
    assert reported["bar"] == stop.verdict(reported["summary"]["overall"], reported["fixed"])
    assert reported["may_ship"] is reported["bar"]["met"]


# --- the command ------------------------------------------------------------------------------------------------------


def test_the_command_writes_both_records_and_says_met_or_not(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert stop.main(["--shoppers", "1", "--shares", "0.6,0.9", "--tune-seed", "11", "--out-dir", str(tmp_path)]) == 0
    tuned = json.loads((tmp_path / "interview-stop-tune.json").read_text(encoding="utf-8"))
    reported = json.loads((tmp_path / "interview-sim-stop.json").read_text(encoding="utf-8"))
    assert reported["stop"]["share"] == tuned["chosen"]["share"]
    assert reported["stop"]["tuned_on_seed"] == tuned["settings"]["seed"] == 11
    assert tuned["runner"] == reported["runner"] == "python -m evals.interview_stop (offline)"
    out = capsys.readouterr().out
    assert f"US-04 criterion 4 (the fixed order's accuracy with 30% fewer questions): {reported['bar']['says']}" in out
    assert stop.LIVE_LIMIT in out


def test_the_report_seed_must_differ_from_the_tuning_seed(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        stop.main(["--shoppers", "1", "--seed", "7", "--tune-seed", "7", "--out-dir", str(tmp_path)])
    assert not any(tmp_path.iterdir())


@pytest.mark.parametrize("change", [["--shoppers", "1"], ["--seed", "7"], ["--tune-seed", "7"], ["--shares", "0.9"]])
def test_another_run_never_replaces_the_committed_records(change: list[str]) -> None:
    before = stop.TUNE_OUT.read_bytes(), stop.REPORT_OUT.read_bytes()
    with pytest.raises(SystemExit):
        stop.main(change)
    assert (stop.TUNE_OUT.read_bytes(), stop.REPORT_OUT.read_bytes()) == before


@pytest.mark.parametrize("shares", ["x", "0.5", "1.5", ","])
def test_shares_must_be_numbers_above_one_half_and_at_most_1(shares: str, tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        stop.main(["--shares", shares, "--out-dir", str(tmp_path)])


def test_it_runs_as_a_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    argv = ["evals.interview_stop", "--shoppers", "1", "--shares", "0.9", "--out-dir", str(tmp_path)]
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.delitem(sys.modules, "evals.interview_stop")  # run afresh as __main__, put back after the test
    with pytest.raises(SystemExit) as done:
        runpy.run_module("evals.interview_stop", run_name="__main__")
    assert done.value.code == 0
    assert (tmp_path / "interview-sim-stop.json").exists()


# --- the committed records --------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def committed() -> tuple[dict[str, Any], dict[str, Any]]:
    return (
        json.loads(stop.TUNE_OUT.read_text(encoding="utf-8")),
        json.loads(stop.REPORT_OUT.read_text(encoding="utf-8")),
    )


def test_the_committed_tuning_chose_by_its_rule_on_the_tuning_seed(committed: tuple[dict[str, Any], ...]) -> None:
    tuned, _ = committed
    assert tuned["runner"] == stop.RUNNER and tuned["rule"] == stop.RULE
    assert tuned["settings"]["seed"] == 4747 and tuned["settings"]["shares"] == list(stop.SHARES)
    assert tuned["settings"]["min_answers"] == 3 and tuned["start"]["share"] == stop.START_SHARE
    rows = [stop.Row(**r) for r in tuned["rows"]]
    assert tuned["chosen"] == stop.choose(rows, tuned["fixed"]["accuracy"]).as_dict()
    assert tuned["keeps_fixed_accuracy"] is (tuned["chosen"]["accuracy"] >= tuned["fixed"]["accuracy"])


def test_the_committed_report_is_on_the_report_seed_with_the_chosen_share(
    committed: tuple[dict[str, Any], ...],
) -> None:
    tuned, reported = committed
    assert reported["settings"]["seed"] == sim.SEED != tuned["settings"]["seed"]
    assert reported["stop"]["share"] == tuned["chosen"]["share"]
    assert reported["stop"]["tuned_on_seed"] == tuned["settings"]["seed"]
    assert reported["live_limit"] == stop.LIVE_LIMIT
    fixed = json.loads(sim.FIXED_OUT.read_text(encoding="utf-8"))["summary"]["overall"]
    assert reported["fixed"] == fixed  # the same shoppers the fixed order's record is on
    assert reported["bar"] == stop.verdict(reported["summary"]["overall"], fixed)
    assert reported["may_ship"] is reported["bar"]["met"]


def test_the_committed_report_is_what_the_rule_gives_now(committed: tuple[dict[str, Any], ...]) -> None:
    """A slice, to keep the test quick: the first laptop shoppers, run again, give the committed rows."""
    _, reported = committed
    fresh = stop.simulate_stop(reported["stop"]["share"], sim.SEED, shoppers=4, categories=("laptop",))
    rows = [r for r in reported["shoppers"] if r["category"] == "laptop" and r["index"] < 4]
    assert fresh["shoppers"] == rows
