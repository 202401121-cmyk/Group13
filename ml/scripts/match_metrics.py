"""Confusion counts, precision, recall and F1 at every threshold, and the threshold choice on dev (ML-0, #13).

Standard library only, and independent of any scorer: it sees scores from 0 to 1 and labels, never rapidfuzz. So the
trained matcher in Sprint 3 is measured exactly like the fuzzy baseline. A pair is called a match when its score is
at or above the threshold. The thresholds are 0.00 to 1.00 in steps of 0.01.

Rules that keep the numbers honest:
- 0/0 is undefined (None, printed "undefined"), never 0.
- The threshold is chosen on the dev split only. The test split is scored once, at that choice.
- On equal dev F1 the higher threshold wins (fewer false matches), then the earlier scorer in the given order.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from pairsets import CrossPairSet, Pair, PairScorer, Split

THRESHOLDS: tuple[float, ...] = tuple(k / 100 for k in range(101))


def ratio(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else numerator / denominator


def show(value: float | None, digits: int = 3) -> str:
    return "undefined" if value is None else f"{value:.{digits}f}"


@dataclass(frozen=True)
class Counts:
    tp: int
    fp: int
    fn: int
    tn: int

    @property
    def precision(self) -> float | None:
        return ratio(self.tp, self.tp + self.fp)

    @property
    def recall(self) -> float | None:
        return ratio(self.tp, self.tp + self.fn)

    @property
    def f1(self) -> float | None:
        """2TP / (2TP + FP + FN): equal to 2PR / (P + R), and undefined only when there is nothing to count."""
        return ratio(2 * self.tp, 2 * self.tp + self.fp + self.fn)


def bucket(score: float) -> int:
    """The highest threshold index the score reaches: the pair is a match there and at every lower threshold."""
    if not 0.0 <= score <= 1.0:  # also rejects NaN
        raise ValueError(f"scores must be from 0 to 1, got {score!r}")
    return bisect_right(THRESHOLDS, score) - 1


def _tail_sums(histogram: Sequence[int]) -> tuple[int, ...]:
    """at[k] = how many items reach threshold k = the sum of the histogram from k up."""
    out = [0] * len(histogram)
    running = 0
    for k in range(len(histogram) - 1, -1, -1):
        running += histogram[k]
        out[k] = running
    return tuple(out)


@dataclass(frozen=True)
class Sweep:
    """How many pairs are called a match at each of the 101 thresholds, split by their true label."""

    positives: int
    negatives: int
    hard_negatives: int
    pos_at: tuple[int, ...]
    neg_at: tuple[int, ...]
    hard_at: tuple[int, ...]
    tracks_hard: bool = False  # whether the source marks hard negatives at all

    @classmethod
    def from_histograms(
        cls, pos_hist: Sequence[int], neg_hist: Sequence[int], hard_hist: Sequence[int] | None = None
    ) -> Sweep:
        """hist[k] = how many pairs have bucket k. Lets a fast scorer count without making a Pair per item."""
        hard = [0] * len(THRESHOLDS) if hard_hist is None else hard_hist
        if not len(pos_hist) == len(neg_hist) == len(hard) == len(THRESHOLDS):
            raise ValueError(f"histograms must have {len(THRESHOLDS)} buckets")
        return cls(
            sum(pos_hist),
            sum(neg_hist),
            sum(hard),
            _tail_sums(pos_hist),
            _tail_sums(neg_hist),
            _tail_sums(hard),
            hard_hist is not None,
        )

    @classmethod
    def from_scores(
        cls, scores: Sequence[float], labels: Sequence[bool], hard: Sequence[bool | None] | None = None
    ) -> Sweep:
        if len(scores) != len(labels) or (hard is not None and len(hard) != len(labels)):
            raise ValueError("scores, labels and hard-negative flags must have the same length")
        pos, neg, hard_hist = [0] * len(THRESHOLDS), [0] * len(THRESHOLDS), [0] * len(THRESHOLDS)
        tracks = hard is not None and any(flag is not None for flag in hard)
        for i, (score, label) in enumerate(zip(scores, labels, strict=True)):
            k = bucket(score)
            if label:
                pos[k] += 1
            else:
                neg[k] += 1
                if hard is not None and hard[i]:
                    hard_hist[k] += 1
        return cls.from_histograms(pos, neg, hard_hist if tracks else None)

    def counts(self, k: int) -> Counts:
        tp, fp = self.pos_at[k], self.neg_at[k]
        return Counts(tp, fp, self.positives - tp, self.negatives - fp)

    def __add__(self, other: Sweep) -> Sweep:
        return Sweep(
            self.positives + other.positives,
            self.negatives + other.negatives,
            self.hard_negatives + other.hard_negatives,
            tuple(a + b for a, b in zip(self.pos_at, other.pos_at, strict=True)),
            tuple(a + b for a, b in zip(self.neg_at, other.neg_at, strict=True)),
            tuple(a + b for a, b in zip(self.hard_at, other.hard_at, strict=True)),
            self.tracks_hard or other.tracks_hard,
        )


EMPTY_SWEEP = Sweep.from_histograms([0] * len(THRESHOLDS), [0] * len(THRESHOLDS))


@dataclass(frozen=True)
class Choice:
    scorer: str
    k: int
    dev_f1: float

    @property
    def threshold(self) -> float:
        return THRESHOLDS[self.k]


def choose(dev: Mapping[str, Sweep], order: Sequence[str]) -> Choice | None:
    """The scorer and threshold with the best dev F1. None when no threshold gives a defined F1."""
    best: Choice | None = None
    for name in order:
        sweep = dev[name]
        for k in range(len(THRESHOLDS)):
            f1 = sweep.counts(k).f1
            if f1 is None:
                continue
            # Strictly better F1, or the same F1 at a higher threshold. A later scorer never wins a full tie.
            if best is None or f1 > best.dev_f1 or (f1 == best.dev_f1 and k > best.k):
                best = Choice(name, k, f1)
    return best


def sweep_pairs(scorer: PairScorer, pairs: Sequence[Pair]) -> Sweep:
    scores = scorer.score_pairs(pairs)
    if len(scores) != len(pairs):
        raise ValueError(f"{scorer.name} returned {len(scores)} scores for {len(pairs)} pairs")
    return Sweep.from_scores(scores, [pair.label for pair in pairs], [pair.is_hard_negative for pair in pairs])


def sweep_cross(scorer: PairScorer, cross: CrossPairSet, split: Split, rows_per_chunk: int = 50) -> Sweep:
    """Any PairScorer on an all-pairs set, a few left records at a time, so memory stays small."""
    total = EMPTY_SWEEP
    left = cross.left_in(split)
    for start in range(0, len(left), rows_per_chunk):
        chunk = CrossPairSet(cross.source, left[start : start + rows_per_chunk], cross.right, cross.matches)
        total = total + sweep_pairs(scorer, list(chunk.iter_pairs(split)))
    return total


@dataclass(frozen=True)
class Evaluation:
    choice: Choice
    test: Counts
    test_pairs: int
    hard_negatives: int | None
    hard_called_match: int | None

    @property
    def hard_share(self) -> float | None:
        if self.hard_negatives is None or self.hard_called_match is None:
            return None
        return ratio(self.hard_called_match, self.hard_negatives)


def evaluate_at(choice: Choice, test: Sweep) -> Evaluation:
    tracks = test.tracks_hard
    return Evaluation(
        choice,
        test.counts(choice.k),
        test.positives + test.negatives,
        test.hard_negatives if tracks else None,
        test.hard_at[choice.k] if tracks else None,
    )


def choose_on_dev(scorers: Sequence[PairScorer], dev_sweep: Callable[[PairScorer], Sweep]) -> Choice | None:
    return choose({scorer.name: dev_sweep(scorer) for scorer in scorers}, [scorer.name for scorer in scorers])


def evaluate(
    scorers: Sequence[PairScorer],
    dev_sweep: Callable[[PairScorer], Sweep],
    test_sweep: Callable[[PairScorer], Sweep],
) -> Evaluation | None:
    """Choose on dev, then score test once with the chosen scorer. Test labels are not read until the choice is made."""
    choice = choose_on_dev(scorers, dev_sweep)
    if choice is None:
        return None
    chosen = next(scorer for scorer in scorers if scorer.name == choice.scorer)
    return evaluate_at(choice, test_sweep(chosen))


def evaluate_pairs(scorers: Sequence[PairScorer], dev: Sequence[Pair], test: Sequence[Pair]) -> Evaluation | None:
    return evaluate(scorers, lambda scorer: sweep_pairs(scorer, dev), lambda scorer: sweep_pairs(scorer, test))
