"""S4-3 (ML-1, prep for task #47): the interview simulator.

Seeded shoppers with hidden weights answer the real interview turn by turn, with no LLM call and no network. The fixed
order's accuracy (the best match is the one the true weights give) has a floor in evals/thresholds.yaml, and any
planner US-04 adds runs beside it on the same shoppers.
"""

from __future__ import annotations

import csv
import json
import socket
import statistics
from pathlib import Path
from typing import Any

import pytest

from app.contracts import (
    MAX_QUESTIONS,
    CriteriaSet,
    Criterion,
    InterviewState,
    InterviewStep,
    InterviewTurn,
    RoughLevel,
    StageContext,
)
from app.interview import InterviewStage
from app.interview.rubric_builder import build_rubric
from evals import interview_sim as sim
from evals import thresholds
from evals.run_baselines import REPO

LOW, GOOD_ENOUGH, MEDIUM, HIGH = RoughLevel.LOW, RoughLevel.GOOD_ENOUGH, RoughLevel.MEDIUM, RoughLevel.HIGH


@pytest.fixture(scope="module")
def fixed() -> dict[str, Any]:
    """The fixed order on the full population, at the default seed: run once for the whole module."""
    return sim.simulate("fixed")


def defaults(criteria: CriteriaSet) -> dict[str, float]:
    return {c.key: c.default_weight for c in criteria.criteria}


# Made-up criteria with no topic or premium words, so only the plan's length and the question limit stop the interview.
_MADE_UP = [
    ("hinge_strength", "Hinge strength"),
    ("fan_speed", "Fan speed"),
    ("port_choice", "Port choice"),
    ("speaker_volume", "Speaker volume"),
    ("webcam_clarity", "Webcam clarity"),
    ("trackpad_feel", "Trackpad feel"),
    ("lid_stiffness", "Lid stiffness"),
    ("bezel_width", "Bezel width"),
    ("key_travel", "Key travel"),
]


def made_up_set(n: int) -> CriteriaSet:
    chosen = _MADE_UP[: n - 1]
    criteria = [
        Criterion(key=key, label=label, definition=f"How good the {label.lower()} is.", default_weight=9 - i * 0.5)
        for i, (key, label) in enumerate(chosen)
    ]
    criteria.append(
        Criterion(
            key="value_for_money",
            label="Value for money",
            definition="How much you get for the price.",
            default_weight=4,
            is_value_for_money=True,
        )
    )
    return CriteriaSet(category="laptop", criteria=criteria, source="fallback")


# --- same seed, same output -------------------------------------------------------------------------------------------


def test_the_same_seed_gives_the_same_output() -> None:
    first = sim.simulate("fixed", seed=7, shoppers=3)
    assert first == sim.simulate("fixed", seed=7, shoppers=3)
    assert first != sim.simulate("fixed", seed=8, shoppers=3)


def test_a_shopper_is_fixed_by_the_seed_and_answers_every_criterion() -> None:
    criteria = sim.criteria_for("laptop")
    shopper = sim.make_shopper(criteria, 4)
    assert shopper == sim.make_shopper(criteria, 4)
    assert shopper != sim.make_shopper(criteria, 5)
    assert set(shopper.true_weights) == set(shopper.answers) == {c.key for c in criteria.criteria}


def test_the_brief_does_not_change_with_the_shoppers_seed() -> None:
    criteria = sim.criteria_for("earbuds")
    assert sim.brief_for(criteria) == sim.brief_for(criteria)
    assert sim.simulate("fixed", seed=7, shoppers=1)["briefs"] == sim.simulate("fixed", seed=8, shoppers=1)["briefs"]


def test_the_simulator_never_touches_the_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_: object, **__: object) -> socket.socket:
        raise AssertionError("the simulator opened a socket")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    assert sim.simulate("fixed", shoppers=1)["summary"]["overall"]["shoppers"] == len(sim.CATEGORIES)


def test_the_simulator_never_builds_or_asks_an_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """US-39 (DECIDED 5): its answers are rough levels and the word "Skip", read in code, so InterviewStage() with no
    client never reaches for the shared one."""

    def refuse() -> object:
        raise AssertionError("the simulator asked for an LLM client")

    monkeypatch.setattr("app.presearch.default_llm", refuse)
    assert sim.simulate("fixed", shoppers=2)["summary"]["overall"]["shoppers"] == 2 * len(sim.CATEGORIES)


# --- the edge cases ---------------------------------------------------------------------------------------------------


def test_a_shopper_who_skips_every_question_keeps_the_defaults() -> None:
    criteria = sim.criteria_for("smartwatch")
    hidden = sim.make_shopper(criteria, 0).true_weights
    shopper = sim.shopper_with(criteria, hidden, skip_all=True)
    step = sim.interview(InterviewStage(), criteria, shopper)
    assert step.kind == "finished" and step.rubric is not None
    assert [e.status for e in step.state.history] == ["skipped"] * 8
    assert step.questions_asked == 8
    assert {w.key: w.weight for w in step.rubric.weights} == defaults(criteria)
    assert {w.origin for w in step.rubric.weights} == {"skipped"}
    result = sim.score(criteria, shopper, step, sim.brief_for(criteria))
    expected = statistics.fmean(abs(c.default_weight - hidden[c.key]) for c in criteria.criteria)
    assert result.mae == pytest.approx(expected)


def test_true_weights_all_equal_are_found_exactly() -> None:
    criteria = sim.criteria_for("headphones")
    shopper = sim.shopper_with(criteria, {c.key: 5.0 for c in criteria.criteria})
    assert set(shopper.answers.values()) == {MEDIUM}
    step = sim.interview(InterviewStage(), criteria, shopper)
    result = sim.score(criteria, shopper, step, sim.brief_for(criteria))
    assert result.mae == 0
    # Equal weights tie every brief product (each has the same scores), so decide's tie-break picks the same one on
    # both sides; test_the_best_match_follows_the_weights shows the comparison is not always this easy.
    assert result.top3
    assert result.same_best
    assert result.true_best == result.final_best


def test_the_best_match_follows_the_weights() -> None:
    criteria = sim.criteria_for("smartwatch")
    brief = sim.brief_for(criteria)
    step = sim.interview(InterviewStage(), criteria, sim.shopper_with(criteria, defaults(criteria)))
    assert step.rubric is not None
    for c in criteria.criteria:
        only = {k.key: (10.0 if k.key == c.key else 0.0) for k in criteria.criteria}
        best = max(brief.scores, key=lambda pid: brief.scores[pid][c.key])
        assert sim.best_match(sim.true_rubric(step.rubric, only), brief) == best, c.key


def test_a_category_with_fewer_than_10_criteria_asks_each_once_and_stops() -> None:
    criteria = made_up_set(6)
    step = sim.interview(InterviewStage(), criteria, sim.shopper_with(criteria, defaults(criteria)))
    assert step.kind == "finished"
    assert step.questions_asked == 6
    assert sorted(e.question.criterion_key for e in step.state.history) == sorted(defaults(criteria))


def test_every_listings_category_asks_at_most_its_8_criteria(fixed: dict[str, Any]) -> None:
    assert all(len(sim.criteria_for(category).criteria) == 8 for category in sim.CATEGORIES)
    assert min(row["questions"] for row in fixed["shoppers"]) >= 1
    assert max(row["questions"] for row in fixed["shoppers"]) <= 8


# --- the stage's own stop rule ----------------------------------------------------------------------------------------


def test_a_low_answer_settles_its_topic_so_the_stage_asks_fewer() -> None:
    criteria = sim.criteria_for("earbuds")
    shopper = sim.shopper_with(criteria, defaults(criteria) | {"sound_quality": 1.0})
    assert shopper.answers["sound_quality"] is LOW
    step = sim.interview(InterviewStage(), criteria, shopper)
    assert step.rubric is not None
    assert "noise_cancellation" not in [e.question.criterion_key for e in step.state.history]
    assert step.questions_asked == 7
    assert {w.key: w.weight for w in step.rubric.weights}["noise_cancellation"] == 2.0


def test_ten_criteria_ask_ten_and_stop() -> None:
    criteria = made_up_set(10)
    step = sim.interview(InterviewStage(), criteria, sim.shopper_with(criteria, defaults(criteria)))
    assert step.kind == "finished"
    assert step.questions_asked == MAX_QUESTIONS == 10


def test_the_stages_question_limit_ends_the_interview(monkeypatch: pytest.MonkeyPatch) -> None:
    # A criteria set holds at most 10, so the limit is lowered to show the stage's own cut, not the simulator's.
    monkeypatch.setattr("app.interview.stage.MAX_QUESTIONS", 4)
    criteria = sim.criteria_for("laptop")
    step = sim.interview(InterviewStage(), criteria, sim.make_shopper(criteria, 0))
    assert step.kind == "finished"
    assert step.questions_asked == 4


class StopsAfterTwo:
    """A planner that decides it knows enough after two answers."""

    def __init__(self) -> None:
        self._stage = InterviewStage()

    def run(self, inp: InterviewTurn, ctx: StageContext) -> InterviewStep:
        step = self._stage.run(inp, ctx)
        if step.kind != "question" or step.questions_asked <= 2:
            return step
        history = [e.model_dump() for e in step.state.history[:2]]
        state = InterviewState.model_validate({**step.state.model_dump(), "history": history})
        return InterviewStep(
            kind="finished",
            message="Done.",
            rubric=build_rubric(state),
            state=state,
            questions_asked=2,
            ordering="information_gain",
        )


class NeverStops:
    """A broken planner: it asks the same question again whatever the reply."""

    def run(self, inp: InterviewTurn, ctx: StageContext) -> InterviewStep:
        return InterviewStage().run(InterviewTurn(state=inp.state), ctx)


def test_the_simulator_takes_the_planners_word_that_it_is_finished() -> None:
    criteria = sim.criteria_for("laptop")
    step = sim.interview(StopsAfterTwo(), criteria, sim.make_shopper(criteria, 0), planner="early")
    assert step.kind == "finished"
    assert step.questions_asked == 2
    assert step.ordering == "information_gain"


def test_a_planner_that_never_finishes_is_an_error_not_a_silent_cut() -> None:
    criteria = sim.criteria_for("laptop")
    with pytest.raises(RuntimeError, match="never_stops"):
        sim.interview(NeverStops(), criteria, sim.make_shopper(criteria, 0), planner="never_stops")


class RephrasesOnce:
    """A planner that asks each question again once, in other words, before it takes the answer."""

    def __init__(self) -> None:
        self._stage = InterviewStage()
        self._rephrased: set[str] = set()

    def run(self, inp: InterviewTurn, ctx: StageContext) -> InterviewStep:
        pending = next((e for e in inp.state.history if e.status == "pending"), None)
        replied = inp.level is not None or inp.reply_text is not None
        if pending is None or not replied or pending.question.id in self._rephrased:
            return self._stage.run(inp, ctx)
        self._rephrased.add(pending.question.id)
        return InterviewStep(
            kind="rephrase",
            message="Please pick one of the buttons.",
            question=pending.question,
            state=inp.state,
            questions_asked=len(inp.state.history),
        )


class UnderCounts:
    """A planner whose count leaves out the skipped questions."""

    def run(self, inp: InterviewTurn, ctx: StageContext) -> InterviewStep:
        step = InterviewStage().run(inp, ctx)
        skipped = sum(e.status == "skipped" for e in step.state.history)
        return InterviewStep.model_validate({**step.model_dump(), "questions_asked": step.questions_asked - skipped})


def test_a_rephrased_question_is_answered_again_and_counted_once() -> None:
    criteria = sim.criteria_for("earbuds")
    shopper = sim.make_shopper(criteria, 3)
    plain = sim.interview(InterviewStage(), criteria, shopper)
    again = sim.interview(RephrasesOnce(), criteria, shopper, planner="rephrases")
    assert again.questions_asked == plain.questions_asked == 8
    assert again.rubric == plain.rubric


def test_a_planner_whose_count_differs_from_the_questions_shown_is_an_error() -> None:
    criteria = sim.criteria_for("laptop")
    shopper = sim.shopper_with(criteria, defaults(criteria), skip_all=True)
    with pytest.raises(RuntimeError, match="under_counts"):
        sim.interview(UnderCounts(), criteria, shopper, planner="under_counts")


def test_the_fixed_order_reports_its_ordering(fixed: dict[str, Any]) -> None:
    assert fixed["ordering"] == ["fixed_order"]


# --- answers ----------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("weight", "level"),
    [
        (0.0, LOW),
        (2.0, LOW),
        (2.5, LOW),  # halfway between low and good enough: the lower level
        (2.51, GOOD_ENOUGH),
        (4.0, GOOD_ENOUGH),
        (4.01, MEDIUM),
        (6.5, MEDIUM),
        (6.51, HIGH),
        (10.0, HIGH),
    ],
)
def test_an_answer_is_the_nearest_rough_level(weight: float, level: RoughLevel) -> None:
    assert sim.nearest_level(weight) is level


@pytest.mark.parametrize(
    ("level", "weight", "neighbour"),
    [
        (LOW, 1.0, GOOD_ENOUGH),  # an end has one neighbour
        (HIGH, 9.5, MEDIUM),
        (MEDIUM, 5.5, HIGH),  # the neighbour on the true weight's side
        (MEDIUM, 4.5, GOOD_ENOUGH),
        (GOOD_ENOUGH, 3.2, MEDIUM),
        (GOOD_ENOUGH, 2.8, LOW),
        (MEDIUM, 5.0, GOOD_ENOUGH),  # exactly on the level: the nearer neighbour (3 is 2 away, 8 is 3)
        (GOOD_ENOUGH, 3.0, LOW),
    ],
)
def test_a_wrong_answer_is_the_neighbouring_level(level: RoughLevel, weight: float, neighbour: RoughLevel) -> None:
    assert sim.neighbour_level(level, weight) is neighbour


def test_about_15_percent_of_answers_are_the_neighbouring_level() -> None:
    answers = flips = 0
    for category in sim.CATEGORIES:
        criteria = sim.criteria_for(category)
        for index in range(sim.SHOPPERS):
            shopper = sim.make_shopper(criteria, index)
            answers += len(shopper.answers)
            flips += len(shopper.flipped)
    assert answers == 8 * sim.SHOPPERS * len(sim.CATEGORIES)
    assert 0.10 <= flips / answers <= 0.20


def test_true_weights_stay_within_0_and_10_and_answers_follow_them() -> None:
    for category in sim.CATEGORIES:
        criteria = sim.criteria_for(category)
        for index in range(sim.SHOPPERS):
            shopper = sim.make_shopper(criteria, index)
            for key, weight in shopper.true_weights.items():
                assert 0 <= weight <= 10
                nearest = sim.nearest_level(weight)
                expected = sim.neighbour_level(nearest, weight) if key in shopper.flipped else nearest
                assert shopper.answers[key] is expected


def test_top3_ties_follow_the_criteria_order() -> None:
    order = ["a", "b", "c", "d"]
    assert sim.top3({"a": 5, "b": 5, "c": 5, "d": 5}, order) == ["a", "b", "c"]
    assert sim.top3({"a": 1, "b": 5, "c": 9, "d": 5}, order) == ["c", "b", "d"]


# --- briefs -----------------------------------------------------------------------------------------------------------


def test_no_brief_product_dominates_another() -> None:
    for category in sim.CATEGORIES:
        criteria = sim.criteria_for(category)
        brief = sim.brief_for(criteria)
        assert len(brief.products) == sim.PRODUCTS
        rows = [sorted(scores.values()) for scores in brief.scores.values()]
        assert all(row == rows[0] for row in rows), category  # the same scores, in another order
        assert len({tuple(scores[c.key] for c in criteria.criteria) for scores in brief.scores.values()}) == 6
        assert all(p.name.startswith("Sim ") and p.brand == "Sim" for p in brief.products)
        assert {v.criterion_key for v in brief.values} == {c.key for c in criteria.criteria}


def test_the_best_match_depends_on_the_weights(fixed: dict[str, Any]) -> None:
    for category in sim.CATEGORIES:
        winners = {row["true_best"] for row in fixed["shoppers"] if row["category"] == category}
        assert len(winners) >= 2, category


def test_a_rubric_of_the_true_weights_keeps_the_final_rubrics_frame() -> None:
    criteria = sim.criteria_for("air_fryer")
    shopper = sim.make_shopper(criteria, 1)
    step = sim.interview(InterviewStage(), criteria, shopper)
    assert step.rubric is not None
    truth = sim.true_rubric(step.rubric, shopper.true_weights)
    assert {w.key: w.weight for w in truth.weights} == shopper.true_weights
    assert sum(w.normalized for w in truth.weights) == pytest.approx(1)
    assert truth.category == step.rubric.category


# --- planners side by side --------------------------------------------------------------------------------------------


def test_a_second_planner_runs_beside_the_fixed_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setitem(sim.PLANNERS, "early", StopsAfterTwo)
    assert sim.main(["--planners", "fixed,early", "--shoppers", "2", "--out-dir", str(tmp_path)]) == 0
    header = capsys.readouterr().out.splitlines()[0]
    assert "fixed" in header and "early" in header
    fixed_record = json.loads((tmp_path / "interview-sim-fixed.json").read_text(encoding="utf-8"))
    early_record = json.loads((tmp_path / "interview-sim-early.json").read_text(encoding="utf-8"))
    assert fixed_record["ordering"] == ["fixed_order"]
    assert early_record["ordering"] == ["information_gain"]
    assert {r["questions"] for r in early_record["shoppers"]} == {2}
    assert early_record["measures"][0]["id"] == "interview.early.accuracy"
    # the same shoppers on both sides
    assert [(r["category"], r["index"]) for r in fixed_record["shoppers"]] == [
        (r["category"], r["index"]) for r in early_record["shoppers"]
    ]


def test_an_unknown_planner_is_refused() -> None:
    with pytest.raises(SystemExit):
        sim.main(["--planners", "nope"])


@pytest.mark.parametrize("change", [["--seed", "8"], ["--shoppers", "2"]])
def test_another_seed_or_size_never_replaces_the_committed_record(change: list[str]) -> None:
    before = sim.FIXED_OUT.read_bytes()
    with pytest.raises(SystemExit):
        sim.main(change)
    assert sim.FIXED_OUT.read_bytes() == before


# --- the record and its floor -----------------------------------------------------------------------------------------


def test_the_six_categories_are_the_listings_categories() -> None:
    path = REPO / "ml" / "labelling" / "listings.csv"
    with path.open(encoding="utf-8", newline="") as f:
        assert {row["category"] for row in csv.DictReader(f)} == set(sim.CATEGORIES)


def test_the_committed_record_is_what_the_simulator_gives_now(fixed: dict[str, Any]) -> None:
    committed = json.loads(sim.FIXED_OUT.read_text(encoding="utf-8"))
    assert committed["runner"] == "python -m evals.interview_sim (offline)"
    assert {k: v for k, v in committed.items() if k not in ("generated", "commit", "runner")} == fixed


def test_the_record_has_one_measured_measure_the_accuracy(fixed: dict[str, Any]) -> None:
    [accuracy] = fixed["measures"]
    assert accuracy["id"] == "interview.fixed.accuracy"
    assert accuracy["status"] == "measured" and accuracy["unit"] == "rate"
    rows = fixed["shoppers"]
    assert len(rows) == sim.SHOPPERS * len(sim.CATEGORIES)
    assert accuracy["value"] == round(sum(r["same_best"] for r in rows) / len(rows), 4)
    assert fixed["summary"]["overall"]["accuracy"] == accuracy["value"]


def test_the_fixed_orders_accuracy_has_a_floor_005_below_it(fixed: dict[str, Any]) -> None:
    floor = thresholds.load(thresholds.THRESHOLDS).measures["interview.fixed.accuracy"]
    measured = fixed["measures"][0]["value"]
    assert floor.measured == measured
    assert floor.floor == round(max(0.0, measured - 0.05), 4)
    assert (floor.kind, floor.direction, floor.measured_on) == ("quality", "at_least", "offline")
    assert "US-04" in floor.rule
    assert "interview.fixed.accuracy" in {m["id"] for m in thresholds.all_measures()}
