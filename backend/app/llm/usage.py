"""What a block of LLM calls really used, for a stage that keeps its own budget (US-14).

The wrapper adds each attempt as it ends. A context variable keeps sources running in parallel apart, and leaves the
LLMClient contract as it is: a fake client simply reports nothing, and `attempts` stays 0.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

_current: ContextVar[TokenMeter | None] = ContextVar("llm_token_meter", default=None)


@dataclass
class TokenMeter:
    attempts: int = 0
    tokens: int = 0
    already_spent_s: float = 0.0  # of the stage's time limit, before these calls began

    @contextmanager
    def counting(self) -> Iterator[None]:
        token = _current.set(self)
        try:
            yield
        finally:
            _current.reset(token)


def time_already_spent() -> float:
    """Seconds of the stage's limit gone before this call began: complete() can't take its own time-limit argument."""
    meter = _current.get()
    return meter.already_spent_s if meter is not None else 0.0


def record_use(tokens: int) -> None:
    meter = _current.get()
    if meter is not None:
        meter.attempts += 1
        meter.tokens += tokens
