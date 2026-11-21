"""Preference memory for the interview (US-06): recall at its start, save when its rubric is final.

Never raises, and never holds up the interview (build rules 9 and 10):
- recall runs within a `time_share_s` share of the interview turn (1.5 s of its 5), the model's load and the database
  included. Past it, or on any failure (the model, its sha256, the database), the remembered list is empty, with one
  log line naming the reason, and the interview runs exactly as it did before US-06;
- a save runs after the reply has gone; a failure is one log line, and nothing else changes.
A guest (no user id) gets neither (D-36). MEMORY=off switches both off.
Everything stays on this server: the model runs here, and no text is ever sent to any provider (P-40, D-51).
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from app.contracts import Exclusion, HardRule, InterviewState, RememberedPreference, Rubric

from .embedder import Embedder, MemoryUnavailable
from .lasting import lasting_items
from .recall import Ranked, rank
from .settings import MemorySettings
from .store import NullPreferenceStore, PreferenceStore
from .text import exclusion_text, rule_text

log = logging.getLogger(__name__)


class Holder(Protocol):
    """What the service needs of an EmbedderHolder."""

    def start(self) -> None: ...

    def get(self, timeout: float) -> Embedder: ...


@dataclass(frozen=True)
class RememberedItem:
    """One remembered preference for the interview: what the shopper sees, and the rule or leave-out to add."""

    preference: RememberedPreference
    rule: HardRule | None = None
    exclusion: Exclusion | None = None


class MemoryService:
    def __init__(
        self,
        settings: MemorySettings,
        holder: Holder,
        store: PreferenceStore,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.settings = settings
        self._holder = holder
        self._store = store
        self._clock = clock
        self._workers = ThreadPoolExecutor(max_workers=2, thread_name_prefix="memory")
        self._save_lock = threading.Lock()

    @property
    def usable(self) -> bool:
        """On, and with a database to keep preferences in. Without one nothing is loaded, recalled or saved."""
        return self.settings.enabled and not isinstance(self._store, NullPreferenceStore)

    def warm(self) -> None:
        """Start loading the model, so it is ready when a signed-in shopper's interview starts."""
        if self.usable:
            self._holder.start()

    def recall(self, user_id: uuid.UUID | None, state: InterviewState) -> list[RememberedItem]:
        """The remembered preferences for this interview, the best first. Empty for a guest, with memory off, or on
        any failure (one log line). Never raises, never waits past the share."""
        if user_id is None or not self.settings.enabled:
            return []
        if not self.usable:
            log.info("memory: nothing recalled: the database is off (no DATABASE_URL)")
            return []
        share = self.settings.time_share_s
        deadline = time.monotonic() + share
        try:
            future = self._workers.submit(self._recall, user_id, state, deadline)
        except RuntimeError:  # the service was closed
            return []
        try:
            return future.result(timeout=share)
        except FutureTimeout:
            future.cancel()  # if it hasn't started, it never will: a slow database never builds a queue of recalls
            log.warning("memory: nothing recalled: it ran past its %.2g s share of the interview turn", share)
        except MemoryUnavailable as exc:
            log.warning("memory: nothing recalled: %s", exc.reason)
        except Exception as exc:  # a bug must never stop the interview (build rule 9)
            log.warning("memory: nothing recalled: recall failed (%s)", type(exc).__name__)
        return []

    def remember(self, user_id: uuid.UUID | None, state: InterviewState, rubric: Rubric) -> int:
        """Save the finished rubric's lasting preferences; the number written. Never raises."""
        if user_id is None or not self.usable:
            return 0
        try:
            items = lasting_items(state, rubric)
            if not items:
                return 0
            embedder = self._holder.get(self.settings.save_wait_s)
            vectors = embedder.embed([item.topic for item in items])
            with self._save_lock:
                return self._store.save(user_id, list(zip(items, vectors, strict=True)))
        except MemoryUnavailable as exc:
            log.warning("memory: nothing saved: %s", exc.reason)
        except Exception as exc:
            log.warning("memory: nothing saved: the save failed (%s)", type(exc).__name__)
        return 0

    def close(self) -> None:
        self._workers.shutdown(wait=False, cancel_futures=True)

    def _recall(self, user_id: uuid.UUID, state: InterviewState, deadline: float) -> list[RememberedItem]:
        # The rows first: a shopper with nothing remembered never waits for the model.
        rows = self._store.candidates(user_id, state.criteria.category)
        if not rows:
            return []
        embedder = self._holder.get(max(0.0, deadline - time.monotonic()))
        criteria = state.criteria.criteria
        vectors = embedder.embed([f"{c.label}: {c.definition}" for c in criteria])
        ranked = rank(rows, list(zip((c.key for c in criteria), vectors, strict=True)), self._clock(), self.settings)
        return [item for item in (_item(r) for r in ranked) if item is not None]


def _item(ranked: Ranked) -> RememberedItem | None:
    """The interview's form of one ranked row, or None if the row no longer fits the contract (it is left out)."""
    row = ranked.row
    try:
        rule = HardRule.model_validate(row.rule) if row.kind == "hard_rule" and row.rule is not None else None
        exclusion = Exclusion.model_validate(row.rule) if row.kind == "exclusion" and row.rule is not None else None
        if (row.kind == "hard_rule" and rule is None) or (row.kind == "exclusion" and exclusion is None):
            return None
        # A must-have's and a leave-out's words are made again from the rule itself, so the interview can find the rule
        # these words stand for when the shopper forgets it (app/interview/remembered.py).
        text = rule_text(rule.source_text) if rule else _leave_out_text(exclusion) if exclusion else row.text
        preference = RememberedPreference(
            preference_id=row.id,
            kind=row.kind,  # the table's check holds it to the three kinds
            text=text,
            criterion_key=ranked.criterion_key,
            weight=row.weight if row.kind == "weight" else None,
            similarity=min(1.0, max(0.0, ranked.similarity)),
            age_days=int(ranked.age_days),
        )
    except ValueError:
        return None
    return RememberedItem(preference, rule, exclusion)


def _leave_out_text(exclusion: Exclusion) -> str:
    return exclusion_text(exclusion.value, exclusion.kind)
