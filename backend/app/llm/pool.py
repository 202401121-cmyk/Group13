"""The model pool (US-46): each listed model is a member with its own limits, so free quotas add up.

Groq's limits are per model, so three models give about three times the room of one. The pool:
- paces each member by requests and tokens a minute, so a call waits for a free slot instead of earning a 429;
- keeps each member under its daily token budget, holding a call's tokens before it runs, and public pages within
  their share of that day (public_daily_share), so private and Reddit work keep the rest;
- rotates bulk work (BULK_TASKS) across every model its route allows, so it spreads across members and providers. A
  shopper's calls keep the route's order, with the other members as their backups;
- keeps part of each member's minute free of bulk work (US-14), so a shopper's next call always finds room;
- holds a call's input and whole requested output while it runs, then books what it really used (US-14), because Groq
  doesn't charge the requested output in full. A failed call gives its tokens back, less any its reply says it used.

The client decides who may see a prompt and who is healthy; the pool only answers "is there room?". All state is in
memory and per process (build rule 10), and shared by every search.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence
from datetime import date

from app.contracts import STAGE_TIME_LIMITS_S, DataClass, LLMTask, ModelLimits, Provider, StageName

from .config import PoolSettings
from .fitting import CHUNKABLE_TASKS
from .limits import Booking, DailyTokenBudget, RateLimiter, utc_today

MemberKey = tuple[Provider, str]

BULK_TASKS = CHUNKABLE_TASKS
"""Work that goes out in many calls at once. The rest has a shopper waiting, so the first model is tried first."""

CALL_MARGIN_S = 1.0  # no call starts later than this before the stage's limit
# One attempt gets at most this share of the stage's limit, so another model has time to answer if it hangs. Real
# Groq calls in Sprint 1 took at most 1.8 s (detect_intent, limit 5 s) and 5.1 s (generate_criteria, limit 10 s).
ATTEMPT_SHARE = 0.6
MIN_PAUSE_S = 0.05  # a wake-up can land a moment early: never spin
RUNNING_CALL_LOOK_S = 0.5  # how often a waiting call looks again while another runs: a call's end wakes nobody

# Which stage's time limit each task runs under. complete() has no time-limit argument, so the task says it.
TASK_STAGES: dict[LLMTask, StageName] = {
    LLMTask.DETECT_INTENT: StageName.INTAKE,
    LLMTask.GENERATE_CRITERIA: StageName.CRITERIA,
    LLMTask.CLASSIFY_REPLY: StageName.INTERVIEW,
    LLMTask.ANSWER_USER_QUESTION: StageName.INTERVIEW,
    LLMTask.WRITE_QUESTION: StageName.INTERVIEW,
    LLMTask.EXTRACT_CLAIMS: StageName.EXTRACT,
    LLMTask.DISCOVER_ALIASES: StageName.MATCH,
    LLMTask.ENRICH_EXTRACT: StageName.ENRICH,
    LLMTask.WRITE_EXPLANATION: StageName.EXPLAIN,
}


def time_limit_s(task: LLMTask) -> float:
    """How long the stage running this task may take."""
    return STAGE_TIME_LIMITS_S[TASK_STAGES[task]]


def _key(model: ModelLimits) -> MemberKey:
    return (model.provider, model.model)


class ModelPool:
    def __init__(
        self,
        settings: PoolSettings,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        today: Callable[[], date] = utc_today,
    ) -> None:
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._last_served: dict[DataClass, MemberKey] = {}
        self._ceilings = {(m.provider, m.model): m.tokens_per_minute for m in settings.members}
        self._bulk_keeps_free = settings.extract_reserve_tokens_per_minute
        self._pacers = {
            (m.provider, m.model): RateLimiter(
                tokens_per_minute=m.tokens_per_minute,
                requests_per_minute=m.requests_per_minute,
                clock=clock,
                sleep=sleep,
            )
            for m in settings.members
        }
        self._budget = DailyTokenBudget({(m.provider, m.model): m.tokens_per_day for m in settings.members}, today)
        # Public pages may use only this share of a member's day (US-46), so private and Reddit work keep the rest.
        share = settings.public_daily_share
        self._public = DailyTokenBudget(
            {(m.provider, m.model): int(m.tokens_per_day * share) for m in settings.members}, today
        )

    def now(self) -> float:
        return self._clock()

    def has(self, model: ModelLimits) -> bool:
        return _key(model) in self._pacers

    def keep_free(self, task: LLMTask) -> int:
        """Tokens of a member's minute this task must leave alone. Checked before a call, and never booked to it."""
        return self._bulk_keeps_free if task in BULK_TASKS else 0

    def token_ceiling(self, model: ModelLimits, keep_free: int = 0) -> int | None:
        """The most one call may use: more than a minute's tokens can never fit. None for a model outside the pool."""
        ceiling = self._ceilings.get(_key(model))
        return None if ceiling is None else ceiling - keep_free

    def order(self, models: Sequence[ModelLimits], data_class: DataClass, *, rotate: bool) -> list[ModelLimits]:
        """The models to try. Rotating starts after the last one served, so a skipped member gives nobody two turns.

        Bulk work rotates across every model its route allows (F-3): Groq's models for private and Reddit text, and
        every allowed provider's for public text. Each keeps its own pacing. The next turn is claimed here, under the
        lock, so calls starting together each begin on a different member.
        """
        if not rotate or len(models) < 2:
            return list(models)
        keys = [_key(m) for m in models]
        with self._lock:
            last = self._last_served.get(data_class)
            start = keys.index(last) + 1 if last in keys else 0
            self._last_served[data_class] = keys[start % len(keys)]
        start %= len(keys)
        return [*models[start:], *models[:start]]

    def served(self, model: ModelLimits, data_class: DataClass) -> None:
        """A member other than the one whose turn was claimed took the call, so the next turn follows it."""
        with self._lock:
            self._last_served[data_class] = _key(model)

    def try_take(self, model: ModelLimits, tokens: int, keep_free: int = 0) -> Booking | None:
        """Take a slot now if there is room. Only for a member of the pool."""
        return self._pacers[_key(model)].try_book(tokens, keep_free)

    def book_real_use(self, model: ModelLimits, booking: Booking, tokens: int | None) -> None:
        """A slot's call is over, and used this many tokens. None keeps what was held for it."""
        self._pacers[_key(model)].rebook(booking, tokens)

    def running_calls_may_free_room(self, model: ModelLimits, tokens: int, keep_free: int = 0) -> bool:
        return self._pacers[_key(model)].running_calls_may_free_room(tokens, keep_free)

    def seconds_until_room(self, model: ModelLimits, tokens: int, keep_free: int = 0) -> float | None:
        return self._pacers[_key(model)].seconds_until_room(tokens, keep_free)

    def pause(self, seconds: float) -> None:
        self._sleep(max(seconds, MIN_PAUSE_S))

    def tokens_this_minute(self, model: ModelLimits) -> int:
        return self._pacers[_key(model)].tokens_this_minute()

    def tokens_left_today(self, model: ModelLimits) -> int | None:
        return self._budget.remaining(_key(model))

    def public_tokens_left_today(self, model: ModelLimits) -> int | None:
        return self._public.remaining(_key(model))

    def reserve(self, model: ModelLimits, tokens: int, data_class: DataClass) -> bool:
        """Hold today's tokens for a call. A public call must also fit the public share. True outside the pool."""
        if not self.has(model):
            return True
        key = _key(model)
        if data_class is DataClass.PUBLIC and not self._public.reserve(key, tokens):
            return False
        if self._budget.reserve(key, tokens):
            return True
        if data_class is DataClass.PUBLIC:
            self._public.settle(key, tokens, 0)
        return False

    def release(self, model: ModelLimits, tokens: int, data_class: DataClass) -> None:
        """Give a reservation back: the call was not sent."""
        self.settle(model, tokens, 0, data_class)

    def settle(self, model: ModelLimits, reserved: int, actual: int, data_class: DataClass) -> None:
        if self.has(model):
            self._budget.settle(_key(model), reserved, actual)
            if data_class is DataClass.PUBLIC:
                self._public.settle(_key(model), reserved, actual)
