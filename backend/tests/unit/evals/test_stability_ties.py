"""US-19 criterion 4 on close products (S5-15, P-45): a second Spearman beside today's, with tie groups as one position.

Today's number compares every shown product's place. The second gives each group the app marks "too close to call"
(linked through tied_with in the unchanged order) one shared place, the average of its members' places, so a change
that only reorders a group's members moves nothing while any product crossing into or out of a group counts (Spearman
for tied places: Pearson's r on the places). Both come from the same changes, seed and method, with US-19's
target of 0.9 beside each. Today's close median, its floor and its "not met" stay as they were; the second number gets
its own floor at its measured value less 0.05, never below 0.9 while it is met. Every brief here is made up.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from itertools import permutations
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from pydantic import ValidationError

from app.contracts import DecideInput
from app.decide import decide
from evals import stability, thresholds
from tests.unit.decide.build import decide_input, known, product, rubric

OLD = "decide.stability.close_median"
NEW = stability.UNTIED_MEASURE
A, B = "prd_alpha_one", "prd_bravo_two"


@pytest.fixture(scope="module")
def measured() -> dict[str, Any]:
    """One measurement for the tests that need one: each costs 600 decide runs."""
    return stability.measure()


def _record() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(stability.STABILITY_OUT.read_text(encoding="utf-8"))
    return data


def _all_tied(folder: Path) -> Path:
    """A made-up brief whose two products have the same values, so the app marks them too close to call."""
    values = [known(pid, key, score=7.0) for pid in (A, B) for key in ("battery_life", "cpu_performance")]
    inp = decide_input(
        rubric(weights=(("battery_life", 5.0), ("cpu_performance", 5.0))), [product(A), product(B)], values
    )
    ranked = decide(inp).ranked
    assert [r.tied_with for r in ranked] == [[ranked[1].product_id], [ranked[0].product_id]]  # the premise
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "all_tied.json"
    path.write_text(json.dumps({"input": inp.model_dump(mode="json")}), encoding="utf-8")
    return path


# --- tie groups as one position -------------------------------------------------------------------------------------


def test_a_tie_group_shares_one_place_the_average_of_its_members() -> None:
    heads = stability.tie_heads(["a", "b", "c", "d"], {"b": ["c"], "c": ["b"]})
    assert heads == {"a": "a", "b": "b", "c": "b", "d": "d"}
    assert stability.places(["a", "b", "c", "d"], heads) == {"a": 1, "b": 2.5, "c": 2.5, "d": 4}
    assert stability.places(["a", "c", "b", "d"], heads) == {"a": 1, "b": 2.5, "c": 2.5, "d": 4}  # nothing moved
    assert stability.places(["c", "a", "d", "b"], heads) == {"a": 2, "b": 2.5, "c": 2.5, "d": 3}  # split up: it moved


def test_ties_link_through_one_another_into_one_group() -> None:
    """a and c aren't marked tied with each other, but each is with b. The app marks ties in pairs; pairs that share
    a product join one group, as a group can't be split to hold a and c apart (S5-15, Decisions)."""
    heads = stability.tie_heads(["a", "b", "c", "d"], {"a": ["b"], "b": ["a", "c"], "c": ["b"]})
    assert heads == {"a": "a", "b": "a", "c": "a", "d": "d"}


def test_a_brief_with_no_ties_gives_the_same_number_both_ways() -> None:
    before = ["a", "b", "c", "d"]
    afters = [["a", "b", "c", "d"], ["b", "a", "c", "d"], ["a", "b", "d", "c"], ["d", "c", "b", "a"]]
    block, rhos = stability.untied(before, {}, afters)
    assert rhos == pytest.approx([stability.spearman(before, after) for after in afters])
    assert rhos == pytest.approx([1.0, 0.8, 0.8, -1.0])
    assert block == {"positions": 4, "tie_groups": [], "median": 0.8, "lowest": -1.0, "note": None}


def test_a_reorder_inside_a_tie_group_moves_nothing_but_one_past_it_does() -> None:
    before = ["a", "b", "c", "d"]
    ties = {"a": ["b"], "b": ["a"]}
    block, rhos = stability.untied(before, ties, [["b", "a", "c", "d"], ["c", "a", "b", "d"]])
    # places a, b, c, d: (1.5, 1.5, 3, 4) before; (1.5, 1.5, 3, 4), then (2.5, 2.5, 1, 4)
    assert rhos == pytest.approx([1.0, 1 / 3])
    assert block["positions"] == 3 and block["tie_groups"] == [["a", "b"]]


@pytest.mark.parametrize(
    "after",
    [
        pytest.param(["p", "f", "w", "l", "m", "k"], id="an-outsider-moves-into-the-middle-of-a-group"),
        pytest.param(["p", "w", "l", "f", "m", "k"], id="one-member-drops-below-an-outsider"),
    ],
)
def test_any_product_crossing_into_or_out_of_a_group_counts(after: list[str]) -> None:
    """The app claims every member of p, w, l and m beats f: a change that breaks that for one member is counted.
    (With only two positions any rho is 1 or -1, as for two products, so k stands below them.)"""
    group = {"p": ["w", "l", "m"], "w": ["p", "l", "m"], "l": ["p", "w", "m"], "m": ["p", "w", "l"]}
    _, (rho,) = stability.untied(["p", "w", "l", "m", "f", "k"], group, [after])
    assert rho < 1.0


def test_a_change_that_levels_every_place_keeps_no_order() -> None:
    """a and b tied, c below them; c moving between a and b puts the group and c at the same average place."""
    heads = stability.tie_heads(["a", "b", "c"], {"a": ["b"], "b": ["a"]})
    assert stability.tied_spearman(["a", "b", "c"], ["a", "c", "b"], heads) == 0.0
    assert stability.tied_spearman(["a", "b", "c"], ["c", "a", "b"], heads) == pytest.approx(-1.0)


def test_the_tied_rho_needs_the_same_products() -> None:
    with pytest.raises(ValueError):
        stability.tied_spearman(["a", "b"], ["a", "c"], {"a": "a", "b": "b", "c": "c"})


def test_a_group_level_with_the_rest_before_any_change_has_no_ordered_products() -> None:
    """a and c tied with b between them, tied with neither: the group's average place is b's, so nothing is ordered."""
    block, rhos = stability.untied(["a", "b", "c"], {"a": ["c"], "c": ["a"]}, [["b", "a", "c"]])
    assert rhos == [] and block["positions"] == 2 and block["tie_groups"] == [["a", "c"]]
    assert (block["median"], block["lowest"], block["note"]) == (None, None, stability.NO_ORDER)


def test_the_five_briefs_have_no_ties_so_both_numbers_agree(measured: dict[str, Any]) -> None:
    counted = {name: row for name, row in measured["per_brief"].items() if row["counted"]}
    assert counted
    for row in counted.values():
        assert row["untied"]["tie_groups"] == [] and row["untied"]["positions"] == row["shown"]
        assert (row["untied"]["median"], row["untied"]["lowest"]) == (row["median"], row["lowest"])
    untied = measured["untied"]
    assert (untied["median"], untied["lowest"]) == (measured["median"], measured["lowest"])
    assert untied["target_median"] == stability.TARGET == 0.9
    assert untied["meets_target"] is True and untied["note"] is None


# --- every product tied ---------------------------------------------------------------------------------------------


def test_a_brief_where_every_product_is_tied_reports_no_ordered_products(tmp_path: Path) -> None:
    path = _all_tied(tmp_path / "close")
    close = stability.close_brief(path)
    assert close["brief"] == path.as_posix()  # outside the repository: its own path
    assert close["median"] is not None  # today's number still compares the two products' places
    untied = close["untied"]
    assert untied["positions"] == 1 and len(untied["tie_groups"]) == 1
    assert (untied["median"], untied["lowest"], untied["meets_target"]) == (None, None, None)  # never a 1.0
    assert untied["note"] == stability.NO_ORDER and untied["note"].startswith("no ordered products")
    assert untied["target_median"] == 0.9


def test_no_ordered_products_leaves_the_new_measure_missing_so_the_gate_notices(tmp_path: Path) -> None:
    path = _all_tied(tmp_path / "briefs")
    record = stability.measure(tmp_path / "briefs", close=path)
    row = record["per_brief"]["all_tied"]
    assert row["counted"] is True and row["untied"]["note"] == stability.NO_ORDER
    assert row["untied"]["median"] is None
    assert record["untied"]["median"] is None and record["untied"]["note"] == stability.NO_ORDER
    assert record["untied"]["meets_target"] is None
    new = next(m for m in record["measures"] if m["id"] == NEW)
    assert (new["value"], new["status"]) == (None, "missing")
    old = next(m for m in record["measures"] if m["id"] == OLD)
    assert old["status"] == "measured"
    problems = thresholds.check(thresholds.load(thresholds.THRESHOLDS), record["measures"])
    assert f"{NEW} is missing from the results" in problems


# --- the close brief ------------------------------------------------------------------------------------------------


def test_the_close_brief_counts_its_two_tie_groups_as_one_position_each() -> None:
    close = _record()["close"]
    names = {
        p.id: p.name
        for p in DecideInput.model_validate(json.loads(stability.CLOSE_BRIEF.read_text("utf-8"))["input"]).products
    }
    groups = [[names[pid] for pid in group] for group in close["untied"]["tie_groups"]]
    assert groups == [["Pike Air", "Wren Buds 2", "Lark Fit", "Moss Quiet"], ["Kite Lite", "Opal Mini"]]
    assert close["untied"]["positions"] == 3  # the four, Fern Talk, the two
    assert close["untied"]["target_median"] == 0.9
    assert close["untied"]["meets_target"] is (close["untied"]["median"] >= 0.9)


def test_in_the_close_brief_every_pair_in_a_group_is_marked_tied_directly() -> None:
    """So joining pairs into groups (a chain) changes nothing on the close brief: each group is tied pair by pair."""
    ranked = decide(DecideInput.model_validate(json.loads(stability.CLOSE_BRIEF.read_text("utf-8"))["input"])).ranked
    ties = {r.product_id: set(r.tied_with) for r in ranked}
    for group in _record()["close"]["untied"]["tie_groups"]:
        for pid in group:
            assert ties[pid] == set(group) - {pid}


def test_every_change_to_the_close_brief_keeps_each_group_in_its_own_places() -> None:
    """What model card 4 says: on the close brief, every product a change moved stayed inside its group."""
    inp = DecideInput.model_validate(json.loads(stability.CLOSE_BRIEF.read_text("utf-8"))["input"])
    before = [r.product_id for r in decide(inp).ranked]
    first, second = _record()["close"]["untied"]["tie_groups"]
    afters = stability._afters(inp, np.random.default_rng(stability.CHANGE_SEED))
    assert any(after != before for after in afters)  # changes do reorder it
    for after in afters:
        assert set(after[:4]) == set(first) and after[4] == "prd_fern_talk" and set(after[5:]) == set(second)


def test_with_only_3_positions_one_product_slipping_out_of_its_group_still_scores_above_0_9() -> None:
    """Model card 4: on the close brief the number is coarse, so its 1.0 is coarse evidence."""
    ranked = decide(DecideInput.model_validate(json.loads(stability.CLOSE_BRIEF.read_text("utf-8"))["input"])).ranked
    before = [r.product_id for r in ranked]
    assert before[3:5] == ["prd_moss_quiet", "prd_fern_talk"]  # the premise
    heads = stability.tie_heads(before, {r.product_id: list(r.tied_with) for r in ranked})
    slipped = [*before[:3], "prd_fern_talk", "prd_moss_quiet", *before[5:]]  # Moss Quiet one place down
    rho = stability.tied_spearman(before, slipped, heads)
    assert round(rho, 2) == 0.97 and stability.TARGET < rho < 1.0


def test_on_the_close_briefs_shape_the_number_is_1_only_when_no_product_leaves_its_group() -> None:
    """Model card 4: the claim the 1.0 supports. Every order of groups of 4, 1 and 2, as in the close brief."""
    before = ["p", "w", "l", "m", "f", "k", "o"]
    four, two = {"p", "w", "l", "m"}, {"k", "o"}
    ties = {pid: sorted(four - {pid}) for pid in four} | {pid: sorted(two - {pid}) for pid in two}
    heads = stability.tie_heads(before, ties)
    for after in permutations(before):
        kept = set(after[:4]) == four and after[4] == "f"
        assert (stability.tied_spearman(before, list(after), heads) > 1 - 1e-9) is kept


def test_the_old_number_its_floor_and_not_met_stay_as_they_were() -> None:
    close = _record()["close"]
    assert (close["median"], close["lowest"], close["meets_target"]) == (0.8929, 0.7143, False)
    assert close["top3_reordered"] == 87
    assert "below US-19's target of 0.9" in close["note"]
    floor = thresholds.load(thresholds.THRESHOLDS).measures[OLD]
    assert (floor.floor, floor.measured, floor.margin) == (0.8429, 0.8929, "0.05 below the measured median")
    assert floor.target is not None and (floor.target.value, floor.target.note) == (0.9, "not met")


def test_the_new_floor_is_the_measured_median_less_0_05_never_below_0_9_while_met() -> None:
    median = _record()["close"]["untied"]["median"]
    floor = thresholds.load(thresholds.THRESHOLDS).measures[NEW]
    assert floor.measured == median and floor.unit == "rho" and floor.direction == "at_least"
    met = median >= 0.9
    assert floor.floor == (max(0.9, round(median - 0.05, 4)) if met else round(median - 0.05, 4))
    assert floor.target is not None and floor.target.value == 0.9
    assert floor.target.note == ("met" if met else "not met")
    assert floor.source.startswith("backend/evals/results/us19-stability.json; ")


@pytest.mark.parametrize(
    ("value", "floor", "margin"),
    [
        pytest.param(1.0, 0.95, "0.05 below the measured median, never below the met target of 0.9", id="met-high"),
        pytest.param(0.92, 0.9, "0.05 below the measured median, never below the met target of 0.9", id="met-near"),
        pytest.param(0.9, 0.9, "0.05 below the measured median, never below the met target of 0.9", id="met-exactly"),
        pytest.param(0.8929, 0.8429, "0.05 below the measured median", id="not-met"),
    ],
)
def test_a_met_target_is_never_given_back_by_the_floor(value: float, floor: float, margin: str) -> None:
    for measure_id in (OLD, NEW):
        rule = thresholds.rule_for({"id": measure_id, "unit": "rho", "value": value, "detail": "x"})
        assert rule.margin == margin
        assert thresholds.floor_for(rule, value) == floor


def test_a_rho_with_no_target_keeps_the_plain_margin() -> None:
    rule = thresholds.rule_for({"id": "decide.stability.other", "unit": "rho", "value": 0.97, "detail": "x"})
    assert (rule.margin, rule.least) == ("0.05 below the measured median", None)
    assert thresholds.floor_for(rule, 0.97) == 0.92


def test_the_new_floor_check_passes_on_the_record_and_fails_below_it() -> None:
    loaded = thresholds.load(thresholds.THRESHOLDS)
    measures = thresholds.all_measures()
    assert not [p for p in thresholds.check(loaded, measures) if NEW in p]
    below = round(loaded.measures[NEW].floor - 0.01, 4)
    lower = [{**m, "value": below} if m["id"] == NEW else m for m in measures]
    assert [p for p in thresholds.check(loaded, lower) if NEW in p] == [
        f"{NEW} is below its floor: {below} < {loaded.measures[NEW].floor}"
    ]


def test_the_new_target_can_never_be_lowered_or_dropped() -> None:
    content = thresholds.load(thresholds.THRESHOLDS).model_dump(mode="json")
    content["measures"][NEW]["target"]["value"] = 0.85
    with pytest.raises(ValidationError):
        thresholds.Thresholds.model_validate(content)
    content["measures"][NEW]["target"] = None
    with pytest.raises(ValidationError):
        thresholds.Thresholds.model_validate(content)


# --- the record and the command -------------------------------------------------------------------------------------


def test_the_record_says_in_one_sentence_each_what_the_two_numbers_count() -> None:
    record = _record()
    assert record["what_counts"] == stability.WHAT_COUNTS
    assert set(stability.WHAT_COUNTS) == {"median", "untied"}
    for sentence in stability.WHAT_COUNTS.values():
        assert sentence.endswith(".") and ". " not in sentence  # one sentence
    card = (stability.BACKEND.parent / "ml" / "MODELS.md").read_text(encoding="utf-8").split("## 4. ")[1]
    card = card.split("\n## ")[0]
    for sentence in stability.WHAT_COUNTS.values():
        assert sentence in card  # model card 4 says the same


def test_the_committed_record_holds_both_numbers_as_measures() -> None:
    record = _record()
    ids = [m["id"] for m in record["measures"]]
    assert ids == [OLD, NEW]
    new = record["measures"][1]
    assert (new["value"], new["unit"], new["status"]) == (record["close"]["untied"]["median"], "rho", "measured")


def test_the_same_seed_gives_the_same_file_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class Clock:
        @staticmethod
        def now(tz: Any = None) -> datetime:
            return datetime(2026, 11, 24, 12, 0, tzinfo=UTC)

    monkeypatch.setattr(stability, "datetime", Clock)
    files = []
    for n in (1, 2):
        out = tmp_path / f"run{n}.json"
        monkeypatch.setattr(stability, "STABILITY_OUT", out)
        assert stability.main() == 0
        files.append(out.read_bytes())
    assert files[0] == files[1]
    written = json.loads(files[0])
    assert written["generated"] == "2026-11-24"
    printed = capsys.readouterr().out.splitlines()
    record = _record()
    close = record["close"]
    assert printed[1] == (
        f"close brief: median {close['median']}, lowest {close['lowest']}, target met: {close['meets_target']}"
    )
    untied, close_untied = record["untied"], close["untied"]
    assert printed[2] == (
        f"tie groups as one position (P-45): median {untied['median']}, lowest {untied['lowest']} (target 0.9); "
        f"close brief: median {close_untied['median']}, lowest {close_untied['lowest']}, "
        f"target met: {close_untied['meets_target']}"
    )
    assert printed[3:5] == [stability.WHAT_COUNTS["median"], stability.WHAT_COUNTS["untied"]]


def test_no_ordered_products_is_said_in_words_not_as_a_number() -> None:
    assert stability.said(None) == "no ordered products"
    assert stability.said(0.8929) == "0.8929"
    assert stability.said(True) == "True"


def test_the_command_says_no_ordered_products_in_words_when_every_product_is_tied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _all_tied(tmp_path / "briefs")
    real = stability.measure
    monkeypatch.setattr(stability, "measure", lambda: real(tmp_path / "briefs", close=path))
    monkeypatch.setattr(stability, "STABILITY_OUT", tmp_path / "out.json")
    assert stability.main() == 0
    line = capsys.readouterr().out.splitlines()[2]
    assert line == (
        "tie groups as one position (P-45): median no ordered products, lowest no ordered products (target 0.9); "
        "close brief: median no ordered products, lowest no ordered products, target met: no ordered products"
    )
    assert "None" not in line
