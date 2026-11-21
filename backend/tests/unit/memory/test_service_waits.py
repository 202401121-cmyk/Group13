"""US-06: memory never makes anyone wait for nothing. With no database the model is never loaded; a shopper with
nothing remembered never waits for the model."""

from __future__ import annotations

import uuid
from typing import Any

from app.interview.rubric_builder import build_rubric
from app.memory import InMemoryPreferenceStore, MemoryService, MemorySettings, NullPreferenceStore
from tests.unit.memory.fakes import WordEmbedder
from tests.unit.memory.test_service import state_for
from tests.unit.memory.test_text_and_lasting import answered

A = uuid.UUID("aaaaaaaa-0000-4000-8000-000000000003")


class CountingHolder:
    def __init__(self) -> None:
        self.started = 0
        self.asked = 0

    def start(self) -> None:
        self.started += 1

    def get(self, timeout: float) -> Any:
        self.asked += 1
        return WordEmbedder()


def test_with_no_database_the_model_is_never_loaded_and_nothing_waits() -> None:
    holder = CountingHolder()
    memory = MemoryService(MemorySettings(), holder, NullPreferenceStore())
    memory.warm()
    state = answered(state_for(), "processor_speed", 8.0)
    assert memory.recall(A, state) == []
    assert memory.remember(A, state, build_rubric(state)) == 0
    assert (holder.started, holder.asked) == (0, 0)


def test_a_shopper_with_nothing_remembered_never_waits_for_the_model() -> None:
    holder = CountingHolder()
    memory = MemoryService(MemorySettings(), holder, InMemoryPreferenceStore())
    assert memory.recall(A, state_for()) == []
    assert holder.asked == 0
    memory.warm()
    assert holder.started == 1  # with a database, signing in starts the load early
