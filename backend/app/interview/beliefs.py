"""What the interview believes about each criterion's weight (US-04, D-69): a normal belief, updated in code.

- Every criterion starts at its category default, with sd `prior_sd`.
- An answer is a noisy look at the true weight:
  - an exact number has noise sd `exact_sd`;
  - a rough level (US-08) has its own spread, worked out from the one table, ROUGH_LEVEL_WEIGHTS. A level stands for
    the weights nearer to it than to any other level, and its spread is how far those weights sit from the level's
    weight, as a root mean square with the weights spread evenly. No new number is needed.
- The update is the normal-normal rule: the precisions (1 / sd squared) add, and the mean is the precision-weighted
  mean of the belief and the answer.
- The beliefs are worked out again from the answers on every turn, never carried from the last one. Going back,
  correcting an answer or editing the rubric always leaves them as the answers give them. The shopper's rubric edits
  are not answers, so they never move a belief.

The three numbers were tuned on the simulator, on a tuning seed (evals/interview_tune.py, D-69 #5). The goal was
accuracy first (Om, 17 Nov 2026).

What that means in practice: the beliefs are independent, so a criterion not yet asked always sits at its default
with the prior sd. Ranking those by mean x sd is ranking by default weight, which is the fixed order. Only the stop
can differ. With the tuned settled sd of 0.7, only a "good enough" answer settles a criterion (sd 0.49, against 0.73
for "medium", 0.96 for "low" or "high" and 1.29 for a typed number). So the interview stops early only when the 3
highest means are all "good enough" answers. Every faster stop the simulator found cost accuracy.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

from app.contracts import ROUGH_LEVEL_WEIGHTS, AskedQuestion, InterviewState, RoughLevel, WeightBelief

from .answers import is_number_reply

MAX_SD = 5.0  # WeightBelief.sd limit
SETTLED_TOP = 3  # the stop rule looks at the 3 criteria with the highest means (D-69 #3)


@dataclass(frozen=True)
class BeliefSettings:
    """The three numbers: how unsure a default is, how noisy an exact answer is, and how sure counts as settled.

    The defaults are the tuned numbers, from evals/results/interview-tune.json. D-69 started from 2.5, 1.5 and 1.2.
    """

    prior_sd: float = 2.5
    exact_sd: float = 1.5
    settled_sd: float = 0.7

    def __post_init__(self) -> None:
        for name in ("prior_sd", "exact_sd", "settled_sd"):
            value = getattr(self, name)
            if not 0 < value <= MAX_SD:
                raise ValueError(f"{name} must be above 0 and at most {MAX_SD}")
        if self.settled_sd >= self.prior_sd:
            raise ValueError("settled_sd must be below prior_sd, or every default would count as settled")


def level_spread(level: RoughLevel) -> float:
    """How far the weights a rough level stands for sit from its weight: the root mean square over its range."""
    weights = sorted(set(ROUGH_LEVEL_WEIGHTS.values()))
    at = ROUGH_LEVEL_WEIGHTS[level]
    i = weights.index(at)
    low = 0.0 if i == 0 else (weights[i - 1] + at) / 2
    high = 10.0 if i == len(weights) - 1 else (weights[i + 1] + at) / 2
    return math.sqrt(((high - at) ** 3 - (low - at) ** 3) / (3 * (high - low)))


# D-69's starting numbers: the reference the tuning starts from and the simulator reports beside the tuned ones.
D69_SETTINGS = BeliefSettings(prior_sd=2.5, exact_sd=1.5, settled_sd=1.2)

LEVEL_SPREADS: Mapping[RoughLevel, float] = {level: level_spread(level) for level in RoughLevel}


def rough_level(entry: AskedQuestion) -> RoughLevel | None:
    """The rough level an answer was, or None for an exact one.

    It is a level when its weight is a level's weight in the US-08 table and its words aren't a number: the High
    button is rough, and a typed "8" is exact.
    """
    weight = entry.classification.weight if entry.classification is not None else None
    if weight is None or (entry.reply_text is not None and is_number_reply(entry.reply_text)):
        return None
    return next((level for level, at in ROUGH_LEVEL_WEIGHTS.items() if at == weight), None)


def answer_sd(entry: AskedQuestion, settings: BeliefSettings) -> float:
    """How noisy an answer is: its level's spread, or the exact noise."""
    level = rough_level(entry)
    return LEVEL_SPREADS[level] if level is not None else settings.exact_sd


def updated(belief: WeightBelief, weight: float, noise_sd: float) -> WeightBelief:
    """The belief after one answer, by the normal-normal rule."""
    prior = 1 / belief.sd**2
    seen = 1 / noise_sd**2
    variance = 1 / (prior + seen)
    mean = variance * (belief.mean * prior + weight * seen)
    return WeightBelief(mean=min(10.0, max(0.0, mean)), sd=math.sqrt(variance))


def beliefs_for(state: InterviewState, settings: BeliefSettings | None = None) -> dict[str, WeightBelief]:
    """Every criterion's belief, from its default and the answers in the history. Skipped and pending questions add
    nothing; a corrected answer counts as its new weight only."""
    settings = settings or BeliefSettings()
    beliefs = {c.key: WeightBelief(mean=c.default_weight, sd=settings.prior_sd) for c in state.criteria.criteria}
    for entry in state.history:
        key = entry.question.criterion_key
        weight = entry.classification.weight if entry.classification is not None else None
        if entry.status == "answered" and weight is not None and key in beliefs:
            beliefs[key] = updated(beliefs[key], weight, answer_sd(entry, settings))
    return beliefs
