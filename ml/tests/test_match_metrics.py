"""Matching metrics (ML-0, #13): counts, 0/0 as undefined, the threshold sweep and the dev-only threshold choice."""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest
from match_metrics import (
    THRESHOLDS,
    Choice,
    Counts,
    Sweep,
    bucket,
    choose,
    evaluate,
    evaluate_at,
    show,
    sweep_cross,
    sweep_pairs,
)
from pairsets import SPLITS, Pair, PairScorer, Split, load_leipzig

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@dataclass(frozen=True)
class TableScorer:
    """A made-up PairScorer: a fixed score per pair_id."""

    name: str
    table: dict[str, float]

    def score_pairs(self, pairs: Sequence[Pair]) -> list[float]:
        return [self.table[pair.pair_id] for pair in pairs]


@dataclass(frozen=True)
class LengthScorer:
    """A made-up PairScorer that needs no table: closer text lengths score higher."""

    name: str = "length"

    def score_pairs(self, pairs: Sequence[Pair]) -> list[float]:
        return [1 - min(1.0, abs(len(p.left_text) - len(p.right_text)) / 40) for p in pairs]


def _pairs(labels: Sequence[bool], split: Split) -> list[Pair]:
    return [Pair(f"{split}{i}", "a", "b", label, "made-up", split) for i, label in enumerate(labels)]


def test_hand_worked_counts_at_one_threshold() -> None:
    # Scores 0.9 0.8 0.6 0.4 0.3 0.1 with labels T F T T F F. At 0.50 the first three are called a match:
    # TP = 2 (0.9, 0.6), FP = 1 (0.8), FN = 1 (0.4), TN = 2. P = R = 2/3, F1 = 2*2 / (2*2 + 1 + 1) = 2/3.
    sweep = Sweep.from_scores([0.9, 0.8, 0.6, 0.4, 0.3, 0.1], [True, False, True, True, False, False])
    counts = sweep.counts(50)
    assert counts == Counts(tp=2, fp=1, fn=1, tn=2)
    assert counts.precision == pytest.approx(2 / 3)
    assert counts.recall == pytest.approx(2 / 3)
    assert counts.f1 == pytest.approx(2 / 3)
    assert sweep.counts(0) == Counts(tp=3, fp=3, fn=0, tn=0)  # at 0.00 everything is a match


def test_zero_over_zero_is_undefined_never_zero() -> None:
    nothing_called = Counts(tp=0, fp=0, fn=0, tn=5)  # no matches exist and none are called
    assert nothing_called.precision is None
    assert nothing_called.recall is None
    assert nothing_called.f1 is None
    assert show(nothing_called.f1) == "undefined"
    only_false_matches = Counts(tp=0, fp=3, fn=0, tn=2)
    assert only_false_matches.precision == 0.0
    assert only_false_matches.recall is None  # there are no true matches to find
    assert only_false_matches.f1 == 0.0  # 0 / (0 + 3 + 0): a real zero, not 0/0
    missed_everything = Counts(tp=0, fp=0, fn=4, tn=1)
    assert missed_everything.precision is None
    assert missed_everything.recall == 0.0


def test_a_score_exactly_at_the_threshold_is_a_match() -> None:
    assert all(bucket(t) == k for k, t in enumerate(THRESHOLDS))
    sweep = Sweep.from_scores([0.5], [True])
    assert sweep.counts(50).tp == 1
    assert sweep.counts(51).tp == 0


@pytest.mark.parametrize("score", [-0.01, 1.01, math.nan])
def test_scores_outside_0_to_1_are_rejected(score: float) -> None:
    with pytest.raises(ValueError, match="scores must be from 0 to 1"):
        bucket(score)


def test_the_sweep_matches_brute_force_at_every_threshold() -> None:
    rng = random.Random(13)
    scores = [rng.random() for _ in range(400)] + [k / 100 for k in range(101)] + [0.0, 1.0]
    labels = [rng.random() < 0.3 for _ in scores]
    hard = [None if label else rng.random() < 0.5 for label in labels]
    sweep = Sweep.from_scores(scores, labels, hard)
    for k, t in enumerate(THRESHOLDS):
        called = [s >= t for s in scores]
        tp = sum(c and lab for c, lab in zip(called, labels, strict=True))
        fp = sum(c and not lab for c, lab in zip(called, labels, strict=True))
        assert sweep.counts(k) == Counts(tp, fp, sum(labels) - tp, len(labels) - sum(labels) - fp)
        assert sweep.hard_at[k] == sum(c and bool(h) for c, h in zip(called, hard, strict=True))


def test_histograms_and_scores_give_the_same_sweep() -> None:
    scores = [0.05, 0.5, 0.5, 0.73, 1.0]
    labels = [False, True, False, True, True]
    pos, neg = [0] * 101, [0] * 101
    for s, lab in zip(scores, labels, strict=True):
        (pos if lab else neg)[bucket(s)] += 1
    assert Sweep.from_histograms(pos, neg) == Sweep.from_scores(scores, labels)


def test_ties_go_to_the_higher_threshold() -> None:
    # One match at 0.90 and one non-match at 0.20: F1 is 1.0 at every threshold from 0.21 to 0.90.
    sweep = Sweep.from_scores([0.9, 0.2], [True, False])
    choice = choose({"only": sweep}, ["only"])
    assert choice == Choice("only", 90, 1.0)
    assert choice is not None and choice.threshold == 0.9


def test_a_full_tie_goes_to_the_earlier_scorer_and_better_f1_always_wins() -> None:
    same = Sweep.from_scores([0.9, 0.2], [True, False])
    assert choose({"first": same, "second": same}, ["first", "second"]) == Choice("first", 90, 1.0)
    weaker = Sweep.from_scores([0.9, 0.8], [False, True])  # its best F1 is 2/3, at 0.80
    choice = choose({"weaker": weaker, "better": same}, ["weaker", "better"])
    assert choice is not None and choice.scorer == "better"


def test_an_undefined_f1_is_never_chosen() -> None:
    empty = Sweep.from_scores([], [])
    assert choose({"empty": empty}, ["empty"]) is None


def test_the_threshold_is_chosen_on_dev_only() -> None:
    dev = _pairs([True, True, False, False], "dev")
    test = _pairs([True, True, True, False], "test")
    table = {"dev0": 0.9, "dev1": 0.7, "dev2": 0.6, "dev3": 0.1}
    table |= {"test0": 0.8, "test1": 0.75, "test2": 0.3, "test3": 0.2}
    scorers: list[PairScorer] = [TableScorer("a", table), TableScorer("b", {key: v / 2 for key, v in table.items()})]
    calls: list[str] = []

    def test_sweep(pairs: list[Pair]) -> Callable[[PairScorer], Sweep]:
        def run(scorer: PairScorer) -> Sweep:
            calls.append(scorer.name)
            return sweep_pairs(scorer, pairs)

        return run

    flipped = [Pair(p.pair_id, p.left_text, p.right_text, not p.label, p.source, p.split) for p in test]
    first = evaluate(scorers, lambda s: sweep_pairs(s, dev), test_sweep(test))
    second = evaluate(scorers, lambda s: sweep_pairs(s, dev), test_sweep(flipped))
    assert first is not None and second is not None
    assert first.choice == second.choice  # changing every test label does not move the threshold
    assert first.choice == Choice("a", 70, 1.0)
    assert first.test != second.test
    assert calls == ["a", "a"]  # test is scored once per run, only for the chosen scorer


def test_hard_negatives_called_a_match_are_counted_at_the_chosen_threshold() -> None:
    test = Sweep.from_scores([0.9, 0.8, 0.4, 0.95], [True, False, False, False], [None, True, True, False])
    result = evaluate_at(Choice("x", 50, 1.0), test)
    assert result.hard_negatives == 2
    assert result.hard_called_match == 1  # the 0.8 one; the 0.95 one is a non-match but not a hard negative
    assert result.hard_share == 0.5
    no_flags = evaluate_at(Choice("x", 50, 1.0), Sweep.from_scores([0.9], [True]))
    assert no_flags.hard_negatives is None and no_flags.hard_share is None


def test_sweeps_add_up_like_one_sweep() -> None:
    a = Sweep.from_scores([0.9, 0.1], [True, False])
    b = Sweep.from_scores([0.6], [True], [None])
    assert a + b == Sweep.from_scores([0.9, 0.1, 0.6], [True, False, True])


def test_the_chunked_all_pairs_path_equals_scoring_every_pair_at_once() -> None:
    cross = load_leipzig(FIXTURES / "public", "abt_buy")
    scorer = LengthScorer()
    for split in SPLITS:
        whole = sweep_pairs(scorer, list(cross.iter_pairs(split)))
        assert sweep_cross(scorer, cross, split, rows_per_chunk=1) == whole
