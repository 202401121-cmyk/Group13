"""How long each model's answers take, measured in this process (D-101).

A "fastest" task (models.yaml `fastest`) sends one call with a short window: it goes to the model whose recent answers
were quickest, and its caller leaves it the 90th percentile of that model's answer times. Only real answers count: one
that came back, valid or not, and a timeout at the attempt's full length (it took at least that long). A failure before
any answer says nothing about speed, and neither does a timeout we cut short.

Each model keeps its last KEEP times, with the task each was for; a model with fewer than MIN_TIMES for the tasks asked
about is not measured yet. Thread-safe: calls run in parallel.
"""

from __future__ import annotations

import math
import threading
from collections import deque
from collections.abc import Collection, Sequence

from app.contracts import LLMTask, Provider

KEEP = 20  # a model's recent answers: the speed it has now, not last week's
MIN_TIMES = 3  # fewer than this and a model's median says too little to rank it by

ModelKey = tuple[Provider, str]


def percentile(times: Sequence[float], share: float) -> float:
    """The nearest-rank percentile: the smallest time with at least `share` of the times at or below it."""
    ordered = sorted(times)
    return ordered[max(0, math.ceil(share * len(ordered)) - 1)]


class AnswerTimes:
    def __init__(self, keep: int = KEEP, least: int = MIN_TIMES) -> None:
        self._keep = keep
        self._least = least
        self._lock = threading.Lock()
        self._times: dict[ModelKey, deque[tuple[LLMTask, float]]] = {}

    def note(self, model: ModelKey, task: LLMTask, seconds: float) -> None:
        with self._lock:
            self._times.setdefault(model, deque(maxlen=self._keep)).append((task, max(0.0, seconds)))

    def times(self, model: ModelKey, tasks: Collection[LLMTask]) -> list[float]:
        """The model's kept times for these tasks, oldest first."""
        with self._lock:
            return [seconds for task, seconds in self._times.get(model, ()) if task in tasks]

    def median(self, model: ModelKey, tasks: Collection[LLMTask]) -> float | None:
        """None until the model has MIN_TIMES answers for these tasks."""
        return self._share(model, tasks, 0.5)

    def p90(self, model: ModelKey, tasks: Collection[LLMTask]) -> float | None:
        return self._share(model, tasks, 0.9)

    def _share(self, model: ModelKey, tasks: Collection[LLMTask], share: float) -> float | None:
        times = self.times(model, tasks)
        return percentile(times, share) if len(times) >= self._least else None
