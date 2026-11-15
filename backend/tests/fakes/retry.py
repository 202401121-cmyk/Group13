"""Randomness for the one retry's jitter (US-31) that always picks the shortest wait, so tests can name exact waits."""

from __future__ import annotations

import random


class NoJitter(random.Random):
    """uniform(a, b) is always a: a 429 waits exactly its reset time, and a 5xx's retry the shortest jitter."""

    def uniform(self, a: float, b: float) -> float:
        return a
