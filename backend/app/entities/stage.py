"""The match stage (US-15, DR-02, DR-06): raw names in, one product per real product out.

MatchStage is the Sprint 1 stub, kept as it was (replace, never rewrite: D-20). FuzzyMatchStage replaced it (S3-3).
ModelMatchStage (S3-15) runs in the pipeline: the trained matcher settles the pairs the rules leave open, and
FuzzyMatchStage is its baseline, for MATCHER=fuzzy and for every fallback.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from time import monotonic

from app.contracts import STAGE_TIME_LIMITS_S, MatchInput, MatchResult, StageContext, StageName, examples

from .clustering import match_names, unmerged
from .model import ModelHolder, ModelUnavailable, ShareSpent, shared_model
from .scorer import FuzzyScorer, PairScorer
from .settings import MatcherSettings, load_matcher_settings

log = logging.getLogger(__name__)

# Of the match stage's 5 seconds, what the fuzzy rerun keeps after the model's share: it took 0.14 s on S3-3's worst
# case of 1,000 names, so 2 seconds is a wide margin, and the runner's limit is never reached. The share is cut to fit,
# whatever config/matcher.yaml says.
FUZZY_ROOM_S = 2.0
MAX_SHARE_S = STAGE_TIME_LIMITS_S[StageName.MATCH] - FUZZY_ROOM_S


class FuzzyMatchStage:
    """Matches names by rules and the fuzzy scorer (US-15 criteria 1, 3, 4 and 6). The scorer is swappable (S3-15)."""

    name = StageName.MATCH
    is_placeholder = False

    def __init__(self, scorer: PairScorer | None = None) -> None:
        self.scorer: PairScorer = scorer if scorer is not None else FuzzyScorer()

    def run(self, inp: MatchInput, ctx: StageContext) -> MatchResult:
        return match_names(inp, self.scorer)

    def fallback(self, inp: MatchInput, ctx: StageContext, error: Exception) -> MatchResult:
        """Any error in run(): every name kept apart, method "fuzzy", and one log line (criterion 6)."""
        log.warning("match fell back: every name is kept apart (%s)", type(error).__name__)
        return unmerged(inp)


class _TimedScorer:
    """The model for one run, fed one batch at a time and stopped once the run's share of time is spent."""

    def __init__(self, inner: PairScorer, deadline: float, batch_size: int, clock: Callable[[], float]) -> None:
        self.inner = inner
        self.threshold = inner.threshold
        self.deadline = deadline
        self.batch_size = batch_size
        self.clock = clock

    def score(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
        bounded = getattr(self.inner, "score_until", None)  # the ONNX scorer: its wait for the model is bounded too
        scores: list[float] = []
        for start in range(0, len(pairs), self.batch_size):
            left = self.deadline - self.clock()
            if left <= 0:
                raise ShareSpent
            chunk = pairs[start : start + self.batch_size]
            scores += bounded(chunk, left) if bounded is not None else self.inner.score(chunk)
        return scores


class ModelMatchStage:
    """The trained matcher decides each pair the rules leave open, and only pairs at or above its threshold merge
    (US-15 criterion 2). The rules, the chaining guard and the variants are FuzzyMatchStage's, unchanged.

    With MATCHER=fuzzy it is FuzzyMatchStage. Otherwise, a model that can't be had or used in time (no revision, a
    failed download or load, an error, scoring past its time share, MATCHER_FORCE_FAIL=1) gives FuzzyMatchStage's run
    instead, whole, with method "fuzzy" and one log line naming the reason (criterion 6).
    """

    name = StageName.MATCH
    is_placeholder = False

    def __init__(
        self,
        settings: MatcherSettings | None = None,
        model: ModelHolder | None = None,
        baseline: FuzzyMatchStage | None = None,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self.settings = settings if settings is not None else load_matcher_settings()
        self.baseline = baseline if baseline is not None else FuzzyMatchStage()
        self._model = model
        self._clock = clock
        if self.settings.use == "model" and not self.settings.force_fail:
            self.model.start()  # loads while research runs, so match rarely waits

    @property
    def model(self) -> ModelHolder:
        if self._model is None:
            self._model = shared_model(self.settings)
        return self._model

    def run(self, inp: MatchInput, ctx: StageContext) -> MatchResult:
        if self.settings.use == "fuzzy":
            return self.baseline.run(inp, ctx)
        started = self._clock()
        share = min(self.settings.time_share_s, MAX_SHARE_S, max(0.0, ctx.seconds_left - FUZZY_ROOM_S))
        try:
            if self.settings.force_fail:
                raise ModelUnavailable("MATCHER_FORCE_FAIL=1 made it fail on purpose")
            scorer = _TimedScorer(self.model.get(share), started + share, self.settings.batch_size, self._clock)
            return match_names(inp, scorer, method="model")
        except ModelUnavailable as exc:
            reason = exc.reason
        except ShareSpent:
            reason = f"scoring took longer than its {share:g} s share of the stage's time"
        except Exception as exc:  # the type only: an error's text can hold names a shopper's search found
            reason = f"scoring failed ({type(exc).__name__})"
        log.warning("match: fuzzy matching ran instead of the model: %s", reason)
        return self.baseline.run(inp, ctx)

    def fallback(self, inp: MatchInput, ctx: StageContext, error: Exception) -> MatchResult:
        """The runner's fallback (a timeout or an error run() let through): fuzzy matching, else every name apart."""
        try:
            return self.baseline.run(inp, ctx)
        except Exception as exc:
            return self.baseline.fallback(inp, ctx, exc)


class MatchStage:
    """Returns the five example products and their aliases."""

    name = StageName.MATCH
    is_placeholder = True

    def run(self, inp: MatchInput, ctx: StageContext) -> MatchResult:
        return examples.match()

    def fallback(self, inp: MatchInput, ctx: StageContext, error: Exception) -> MatchResult:
        return MatchResult.model_validate({**examples.match().model_dump(), "method": "fuzzy"})
