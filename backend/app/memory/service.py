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
import re
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from app.contracts import (
    Exclusion,
    HardRule,
    InterviewState,
    PreferenceEdit,
    PreferenceItem,
    RememberedPreference,
    Rubric,
)

from .embedder import Embedder, MemoryUnavailable
from .lasting import lasting_items
from .recall import Ranked, rank
from .settings import MemorySettings
from .store import Change, NullPreferenceStore, PreferenceStore, StoredPreference
from .text import clean_text, exclusion_text, round_weight, rule_text, weight_text

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

    # --- the shopper's own controls (US-11) --------------------------------------------------------------------------
    # These work whether memory is on or off: what is stored is the shopper's to see and remove. They raise
    # MemoryUnavailable when the database can't be reached, so the routes can answer 503.

    def listing(self, user_id: uuid.UUID) -> list[StoredPreference]:
        """Every stored preference of the user, newest first, without embeddings."""
        return self._store.listing(user_id)

    def edit(self, user_id: uuid.UUID, preference_id: uuid.UUID, change: PreferenceEdit) -> Edited | None:
        """The preference changed, or None if the user has no preference with this id. A changed text gets a new
        embedding; if the model can't make one, the row keeps its old one and `embedding_kept` says so. Raises
        EditRefused (plain words) for a change that doesn't fit the preference, and EditConflict when the preference
        changed while the edit was made (it is never overwritten with what was read before)."""
        row = self._store.one(user_id, preference_id)
        if row is None:
            return None
        text, weight, rule, new_words = _changed(row, change)
        embedding: tuple[float, ...] | None = None
        kept = False
        if new_words:  # the shopper's own new words: what they mean now
            embedding = self._embedding_of(text)
            kept = embedding is None
        _check_contract(row, text, weight, rule)
        updated = self._store.update(user_id, preference_id, Change(text, weight, rule, embedding, row.updated_at))
        if updated is None:
            if self._store.one(user_id, preference_id) is not None:
                raise EditConflict
            return None
        return Edited(updated, kept)

    def delete(self, user_id: uuid.UUID, preference_id: uuid.UUID) -> bool:
        """Delete one preference, its embedding with it. False if the user has no preference with this id."""
        return self._store.delete(user_id, preference_id)

    def delete_all(self, user_id: uuid.UUID) -> int:
        """Forget everything remembered for the user; the number deleted."""
        return self._store.delete_all(user_id)

    def _embedding_of(self, text: str) -> tuple[float, ...] | None:
        if not self.settings.enabled:
            return None
        try:
            return tuple(self._holder.get(EDIT_WAIT_S).embed([text])[0])
        except MemoryUnavailable as exc:
            log.warning("memory: an edited preference kept its old embedding: %s", exc.reason)
        except Exception as exc:
            log.warning("memory: an edited preference kept its old embedding (%s)", type(exc).__name__)
        return None

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


# --- editing one preference (US-11) -------------------------------------------------------------------------------

EDIT_WAIT_S = 3.0  # an edit waits this long for the model; past it the row keeps its old embedding
_AUTO_WORDS = re.compile(
    r"^(?P<label>.+): (?:low importance|good enough will do|medium importance|high importance|"
    r"\d+(?:\.\d+)? out of 10)$"
)


class EditRefused(ValueError):
    """A change that doesn't fit the preference. The message is one plain sentence for the shopper."""


@dataclass(frozen=True)
class Edited:
    row: StoredPreference
    embedding_kept: bool  # the text changed, but the model couldn't embed it: recall still uses the old words


class EditConflict(Exception):
    """The preference changed between reading it and writing the edit (say, a search saved it again)."""


def _changed(row: StoredPreference, change: PreferenceEdit) -> tuple[str, float | None, dict[str, Any] | None, bool]:
    """The row's text, weight and rule after the change, and whether the shopper gave new words. Raises EditRefused.

    Only a weight's words can change: a must-have's and a leave-out's come from the rule itself, which is what the
    interview uses, so new words there would say one thing and do another."""
    text, weight = row.text, row.weight
    rule = dict(row.rule) if row.rule is not None else None
    if change.always is not None and change.always != row.always:
        if row.kind == "exclusion":
            raise EditRefused("A remembered leave-out is kept for every later search. To stop it, delete it.")
        raise EditRefused("Only a leave-out can be kept for every later search.")
    if change.active is not None:
        if row.kind != "hard_rule" or rule is None:
            raise EditRefused("Only a must-have can be switched on or off.")
        rule["active"] = change.active
    new_words: str | None = None
    if change.text is not None:
        if row.kind != "weight":
            raise EditRefused(
                "Only a remembered weight's words can be changed. To change a must-have or a leave-out, delete it; "
                "your next search can set it again."
            )
        new_words = clean_text(change.text)
        if not new_words:
            raise EditRefused("The words can't be empty.")
        if new_words == row.text:
            new_words = None  # the same words sent back with a new weight: not new words
    if change.weight is not None:
        if row.kind != "weight":
            raise EditRefused("Only a remembered weight has a weight to change.")
        weight = round_weight(change.weight)
        matched = _AUTO_WORDS.match(row.text)
        if new_words is None and matched:  # the words the app wrote follow the new weight
            text = weight_text(matched["label"], weight)
    if new_words is not None:
        text = new_words
    return text, weight, rule, new_words is not None


def _check_contract(row: StoredPreference, text: str, weight: float | None, rule: dict[str, Any] | None) -> None:
    """The changed row must still read back as a PreferenceItem, as the table's checks also hold it."""
    try:
        PreferenceItem.model_validate(
            {
                "id": row.id,
                "kind": row.kind,
                "category": row.category,
                "criterion_key": row.criterion_key,
                "text": text,
                "weight": weight,
                "rule": rule if row.kind == "hard_rule" else None,
                "exclusion": rule if row.kind == "exclusion" else None,
                "always": row.always,
                "created_at": row.created_at,
                "updated_at": max(row.updated_at, row.created_at),
            }
        )
    except ValueError:
        raise EditRefused("That change doesn't fit this preference.") from None
