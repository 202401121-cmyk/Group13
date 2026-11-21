"""US-06: the memory service never raises and never holds up the interview. Every failure (the model, its sha256, the
database, the 1.5 s share) gives an empty list and one log line naming the reason (DECIDED 5)."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.contracts import CriteriaInput, Exclusion, Intent, InterviewState
from app.criteria import fallback_set
from app.interview.rubric_builder import build_rubric
from app.memory.embedder import EmbedderHolder, MemoryUnavailable, load_embedder
from app.memory.lasting import Lasting
from app.memory.service import MemoryService
from app.memory.settings import MemorySettings, load_memory_settings, memory_switch
from app.memory.store import InMemoryPreferenceStore, StoredPreference
from tests.unit.memory.fakes import WordEmbedder, ready_holder
from tests.unit.memory.test_text_and_lasting import answered

A = uuid.UUID("aaaaaaaa-0000-4000-8000-000000000001")
B = uuid.UUID("bbbbbbbb-0000-4000-8000-000000000002")
NOW = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)


def state_for(category: str = "laptop", **intent: Any) -> InterviewState:
    criteria = fallback_set(CriteriaInput(category=category, category_label=category.replace("_", " ").capitalize()))
    made = Intent(
        raw_query=f"a made-up {category} request",
        category=category,
        category_label=category.capitalize(),
        detected_by="rules",
        **intent,
    )
    return InterviewState(session_id="sess_memorytest02", intent=made, criteria=criteria)


def service(
    store: Any = None, *, holder: Any = None, settings: MemorySettings | None = None, now: datetime = NOW
) -> MemoryService:
    return MemoryService(
        settings or MemorySettings(),
        holder if holder is not None else ready_holder(WordEmbedder()),
        store if store is not None else InMemoryPreferenceStore(clock=lambda: now),
        clock=lambda: now,
    )


def remember(memory: MemoryService, user: uuid.UUID, state: InterviewState) -> int:
    return memory.remember(user, state, build_rubric(state))


def one_warning(caplog: pytest.LogCaptureFixture) -> str:
    lines = [r.getMessage() for r in caplog.records if r.name.startswith("app.memory") and r.levelno >= logging.WARNING]
    assert len(lines) == 1, lines
    return lines[0]


# --- saving and recall ------------------------------------------------------------------------------------------------


def test_a_saved_weight_comes_back_as_a_remembered_answer_in_a_new_session() -> None:
    store = InMemoryPreferenceStore(clock=lambda: NOW - timedelta(days=7))
    assert remember(service(store, now=NOW - timedelta(days=7)), A, answered(state_for(), "processor_speed", 8.0)) == 1
    [item] = service(store).recall(A, state_for())
    remembered = item.preference
    assert (remembered.kind, remembered.criterion_key, remembered.weight) == ("weight", "processor_speed", 8.0)
    assert remembered.age_days == 7 and remembered.similarity == pytest.approx(1.0)
    assert remembered.text.endswith(": high importance")


def test_saving_the_same_preference_again_updates_it_and_its_time() -> None:
    clock = {"now": NOW - timedelta(days=10)}
    store = InMemoryPreferenceStore(clock=lambda: clock["now"])
    memory = MemoryService(MemorySettings(), ready_holder(WordEmbedder()), store, clock=lambda: clock["now"])
    remember(memory, A, answered(state_for(), "processor_speed", 8.0))
    clock["now"] = NOW
    remember(memory, A, answered(state_for(), "processor_speed", 2.0))
    [stored] = store.rows(A)
    assert (stored.weight, stored.text.endswith("low importance")) == (2.0, True)
    assert stored.updated_at == NOW and stored.created_at == NOW - timedelta(days=10)


def test_user_b_never_sees_user_as_rows() -> None:
    store = InMemoryPreferenceStore(clock=lambda: NOW)
    remember(service(store), A, answered(state_for(), "processor_speed", 8.0))
    assert service(store).recall(B, state_for()) == []
    assert len(service(store).recall(A, state_for())) == 1


def test_a_laptop_weight_never_fills_a_headphone_criterion() -> None:
    store = InMemoryPreferenceStore(clock=lambda: NOW)
    laptop = state_for("laptop")
    shared = {c.key for c in laptop.criteria.criteria} & {c.key for c in state_for("headphones").criteria.criteria}
    key = sorted(shared)[0] if shared else laptop.criteria.criteria[0].key
    remember(service(store), A, answered(laptop, key, 8.0))
    assert service(store).recall(A, state_for("headphones")) == []


def test_a_brand_left_out_with_always_fills_another_category() -> None:
    store = InMemoryPreferenceStore(clock=lambda: NOW)
    remember(service(store), A, state_for("laptop", exclusions=[Exclusion(kind="brand", value="Acme", always=True)]))
    [item] = service(store).recall(A, state_for("headphones"))
    assert item.preference.kind == "exclusion" and item.exclusion == Exclusion(kind="brand", value="Acme", always=True)


def test_a_model_left_out_with_always_stays_in_its_category() -> None:
    store = InMemoryPreferenceStore(clock=lambda: NOW)
    remember(
        service(store), A, state_for("laptop", exclusions=[Exclusion(kind="model", value="Acme Book", always=True)])
    )
    assert service(store).recall(A, state_for("headphones")) == []
    assert len(service(store).recall(A, state_for("laptop"))) == 1


def test_a_guest_has_no_user_so_nothing_is_saved_or_recalled() -> None:
    store = InMemoryPreferenceStore(clock=lambda: NOW)
    memory = service(store)
    assert memory.remember(None, answered(state_for(), "processor_speed", 8.0), build_rubric(state_for())) == 0
    assert memory.recall(None, state_for()) == []
    assert store.rows(A) == [] and store.count() == 0


# --- every failure: an empty list and one log line --------------------------------------------------------------------


class FailingStore(InMemoryPreferenceStore):
    def candidates(self, user_id: uuid.UUID, category: str) -> list[StoredPreference]:
        raise MemoryUnavailable("the database could not be read")

    def save(self, user_id: uuid.UUID, items: Sequence[tuple[Lasting, Sequence[float]]]) -> int:
        raise MemoryUnavailable("the database could not be written")


class SlowStore(InMemoryPreferenceStore):
    def __init__(self, release: threading.Event) -> None:
        super().__init__(clock=lambda: NOW)
        self.release = release

    def candidates(self, user_id: uuid.UUID, category: str) -> list[StoredPreference]:
        self.release.wait(5)
        return super().candidates(user_id, category)


def test_the_database_down_gives_an_empty_list_and_one_log_line(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="app.memory")
    assert service(FailingStore(clock=lambda: NOW)).recall(A, state_for()) == []
    assert "the database could not be read" in one_warning(caplog)


def test_a_failed_save_never_raises(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="app.memory")
    assert remember(service(FailingStore(clock=lambda: NOW)), A, answered(state_for(), "processor_speed", 8.0)) == 0
    assert "could not be written" in one_warning(caplog)


def test_the_model_failing_gives_an_empty_list_and_one_log_line(caplog: pytest.LogCaptureFixture) -> None:
    def broken(settings: MemorySettings) -> Any:
        raise MemoryUnavailable("the model could not be loaded (RuntimeError)")

    holder = EmbedderHolder(MemorySettings(), broken)
    with pytest.raises(MemoryUnavailable):
        holder.get(5)  # the load runs in its own thread and fails; the failure is held
    caplog.set_level(logging.WARNING, logger="app.memory")
    store = InMemoryPreferenceStore(clock=lambda: NOW)
    remember(service(store), A, answered(state_for(), "processor_speed", 8.0))
    assert service(store, holder=holder).recall(A, state_for()) == []
    assert "the model could not be loaded" in one_warning(caplog)


def test_a_model_whose_sha256_is_wrong_is_never_used(tmp_path: Path) -> None:
    model, tokenizer = tmp_path / "model.onnx", tmp_path / "tokenizer.json"
    model.write_bytes(b"not the pinned model")
    tokenizer.write_bytes(b"{}")
    pinned = load_memory_settings(env={})
    assert pinned.pinned

    def download(**asked: Any) -> str:
        return str(model if asked["filename"] == pinned.model_file else tokenizer)

    with pytest.raises(MemoryUnavailable, match="sha256"):
        load_embedder(pinned, tmp_path, download=download)


def test_the_wrong_sha256_through_the_service_gives_an_empty_list(
    caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    model = tmp_path / "model.onnx"
    model.write_bytes(b"not the pinned model")
    pinned = load_memory_settings(env={})
    holder = EmbedderHolder(pinned, lambda s: load_embedder(s, tmp_path, download=lambda **asked: str(model)))
    caplog.set_level(logging.WARNING, logger="app.memory")
    store = InMemoryPreferenceStore(clock=lambda: NOW)
    remember(service(store), A, answered(state_for(), "processor_speed", 8.0))
    assert service(store, holder=holder).recall(A, state_for()) == []
    assert "sha256" in one_warning(caplog)


def test_recall_past_its_share_gives_an_empty_list_at_once(caplog: pytest.LogCaptureFixture) -> None:
    release = threading.Event()
    caplog.set_level(logging.WARNING, logger="app.memory")
    started = time.monotonic()
    try:
        found = service(SlowStore(release), settings=MemorySettings(time_share_s=0.2)).recall(A, state_for())
    finally:
        release.set()
    assert found == [] and time.monotonic() - started < 1.0
    assert "0.2 s share" in one_warning(caplog)


def test_memory_off_recalls_and_saves_nothing_and_logs_nothing(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="app.memory")
    store = InMemoryPreferenceStore(clock=lambda: NOW)
    off = service(store, settings=MemorySettings(enabled=False))
    assert remember(off, A, answered(state_for(), "processor_speed", 8.0)) == 0
    assert off.recall(A, state_for()) == [] and store.count() == 0
    assert [r for r in caplog.records if r.name.startswith("app.memory")] == []


@pytest.mark.parametrize(
    ("raw", "on"), [("", True), ("on", True), ("ON", True), ("off", False), (" Off ", False), ("maybe", True)]
)
def test_the_memory_switch(raw: str, on: bool) -> None:
    assert memory_switch({"MEMORY": raw} if raw else {}) is on
    assert load_memory_settings(env={"MEMORY": raw}).enabled is on


def test_an_unreadable_config_gives_no_model(tmp_path: Path) -> None:
    broken = tmp_path / "memory.yaml"
    broken.write_text("repo: [", encoding="utf-8")
    settings = load_memory_settings(broken, env={})
    assert not settings.pinned and settings.enabled
