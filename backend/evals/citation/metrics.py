"""How the citation check is measured on the hand-written pairs (US-22, S5-4 DECIDED 6): plain code, no model.

- A pair's verdict is the check's: its entailment probability at or above the threshold AND every number in the
  sentence is in the quote (check.py's rule; these pairs have no scores, so the quote is the only place a number may
  come from).
- Caught: the share of the deliberately wrong sentences the check rejects (target 80%).
- Wrongly rejected: the share of the right sentences the check rejects (target at most 15%).
- The threshold is tuned on the 40 tuning pairs only: of the thresholds that reject at most 15% of their right
  sentences, the one that catches the most wrong ones (then the fewest wrongly rejected, then the middle of the tied
  run, so it sits between two pairs rather than on one).
- Each rate on the 60 test pairs comes with a 95% bootstrap interval: the pairs resampled with replacement, seeded.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise

from app.explain.numbers import numbers_in, unmatched

MAX_WRONGLY_REJECTED = 0.15
CAUGHT_TARGET = 0.80
RESAMPLES = 10_000
SEED = 22


@dataclass(frozen=True)
class Scored:
    """One pair as the check sees it."""

    id: str
    supported: bool  # the person's label
    entailment: float  # the highest over the quote's chunks
    numbers_ok: bool  # every number in the sentence is in the quote


def numbers_in_quote(sentence: str, quote: str) -> bool:
    return not unmatched(sentence, [n.value for n in numbers_in(quote)])


def passes(pair: Scored, threshold: float) -> bool:
    return pair.entailment >= threshold and pair.numbers_ok


def rates(pairs: Sequence[Scored], threshold: float) -> tuple[float, float]:
    """(caught, wrongly rejected): the shares of the wrong sentences rejected and of the right ones rejected."""
    wrong = [p for p in pairs if not p.supported]
    right = [p for p in pairs if p.supported]
    caught = sum(not passes(p, threshold) for p in wrong) / len(wrong) if wrong else 0.0
    rejected = sum(not passes(p, threshold) for p in right) / len(right) if right else 0.0
    return caught, rejected


def misjudged(pairs: Sequence[Scored], threshold: float) -> dict[str, list[dict[str, object]]]:
    """The pairs the check got wrong, by id, with what decided each: right sentences rejected, wrong ones passed."""

    def row(p: Scored) -> dict[str, object]:
        return {"id": p.id, "entailment": round(p.entailment, 4), "numbers_ok": p.numbers_ok}

    return {
        "rejected_right": [row(p) for p in pairs if p.supported and not passes(p, threshold)],
        "passed_wrong": [row(p) for p in pairs if not p.supported and passes(p, threshold)],
    }


def candidates(pairs: Sequence[Scored]) -> list[float]:
    """Every threshold that changes a verdict: 0, the midpoints between the pairs' probabilities, and just over 1."""
    values = sorted({p.entailment for p in pairs})
    middles = [(a + b) / 2 for a, b in pairwise(values)]
    return [0.0, *middles, 1.0 + 1e-9]


def tune(pairs: Sequence[Scored], limit: float = MAX_WRONGLY_REJECTED) -> float:
    """The threshold for these pairs, by the rule above. Raises ValueError when none keeps within `limit`."""
    allowed = [(t, *rates(pairs, t)) for t in candidates(pairs)]
    allowed = [(t, caught, rejected) for t, caught, rejected in allowed if rejected <= limit + 1e-12]
    if not allowed:
        raise ValueError(f"no threshold rejects at most {limit:.0%} of the right sentences")
    best_caught = max(caught for _, caught, _ in allowed)
    best = [row for row in allowed if row[1] == best_caught]
    fewest = min(rejected for _, _, rejected in best)
    tied = [t for t, _, rejected in best if rejected == fewest]
    return round(tied[(len(tied) - 1) // 2], 4)


def bootstrap(
    pairs: Sequence[Scored], threshold: float, resamples: int = RESAMPLES, seed: int = SEED
) -> dict[str, tuple[float, float]]:
    """95% intervals for caught and wrongly rejected: the pairs resampled with replacement, `resamples` times."""
    rng = random.Random(seed)
    caught: list[float] = []
    rejected: list[float] = []
    for _ in range(resamples):
        sample = [pairs[rng.randrange(len(pairs))] for _ in pairs]
        if not any(p.supported for p in sample) or all(p.supported for p in sample):
            continue  # a sample with only one kind measures nothing
        c, r = rates(sample, threshold)
        caught.append(c)
        rejected.append(r)
    return {"caught": interval(caught), "wrongly_rejected": interval(rejected)}


def interval(values: list[float]) -> tuple[float, float]:
    ordered = sorted(values)
    low = ordered[int(0.025 * (len(ordered) - 1))]
    high = ordered[round(0.975 * (len(ordered) - 1))]
    return round(low, 4), round(high, 4)
