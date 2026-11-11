"""Choosing the matcher's threshold on the dev part (S3-4, DECIDED 5). Standard library only.

The threshold is the highest score that gives the best recall among those where precision is still 0.95 or more: going
lower is worth it only if recall really rises. Pairs with the same score are called together, so a tie is
never split. The target is never lowered:
when no threshold reaches it, `met` is False and the numbers are those of the most precise threshold.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

TARGET_PRECISION = 0.95


@dataclass(frozen=True)
class ThresholdChoice:
    threshold: float
    precision: float
    recall: float
    met: bool  # precision reached the target on dev
    called: int  # pairs called a match at this threshold on dev


def choose_threshold(
    scores: Sequence[float], labels: Sequence[bool], target: float = TARGET_PRECISION
) -> ThresholdChoice:
    if len(scores) != len(labels):
        raise ValueError("scores and labels must have the same length")
    positives = sum(labels)
    by_score: dict[float, list[int]] = {}
    for score, label in zip(scores, labels, strict=True):
        by_score.setdefault(score, [0, 0])[0 if label else 1] += 1  # [matches, non-matches] at this exact score
    tp = fp = 0
    best: ThresholdChoice | None = None
    most_precise: ThresholdChoice | None = None
    for score in sorted(by_score, reverse=True):
        tp += by_score[score][0]
        fp += by_score[score][1]
        precision = tp / (tp + fp)
        recall = tp / positives if positives else 0.0
        met = precision >= target
        here = ThresholdChoice(score, precision, recall, met, tp + fp)
        if met and (best is None or recall > best.recall):  # only a real recall gain justifies a lower threshold
            best = here
        if tp > 0 and (most_precise is None or precision > most_precise.precision):
            most_precise = here
    if best is not None:
        return best
    return most_precise or ThresholdChoice(1.0, 0.0, 0.0, False, 0)
