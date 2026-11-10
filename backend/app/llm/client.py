"""The one LLM client every stage uses (US-26, US-46). Implements app.contracts.LLMClient.

complete() tries the providers that the routes table in models.yaml gives for the call's data class and
difficulty, skipping any that may not see that class or are unavailable, and re-fits the input to each model.
Bulk tasks rotate through those models (app/llm/pool.py). Before every call it checks the daily token budget,
health (cool-downs and dead keys) and the rate limit. A pool member with no room is left to come back to, so a
free member goes first; if all are busy the call waits for a slot, up to its stage's time limit. A public page that
reaches the pool (Groq, the last resort) never waits for one: the next provider is tried at once (F-6). It retries
invalid output once, moves on after any provider failure, and raises LLMUnavailable when no model answered. It
never invents an answer: the calling stage's fallback decides what happens next.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Generic, Literal, Protocol, TypeVar

from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError

from app.contracts import (
    PERSONAL_TASKS,
    DataClass,
    Difficulty,
    LLMCallRecord,
    LLMClient,
    LLMTask,
    LLMUnavailable,
    ModelLimits,
    Provider,
)

from .config import LLMConfig, load_llm_config, with_env_overrides
from .errors import AttemptOutcome, InvalidOutput, ProviderFailure, ProviderTimeout
from .fitting import CHUNKABLE_TASKS, FitResult, estimate_tokens, fit, merge_chunk_results, reserve_output
from .health import ProviderHealth
from .limits import Booking, DailyTokenBudget, RateLimiter
from .output import RETRIEVED_TEXT_TASKS, build_system_prompt, parse_output
from .pool import ATTEMPT_SHARE, BULK_TASKS, CALL_MARGIN_S, RUNNING_CALL_LOOK_S, ModelPool, time_limit_s
from .providers import Adapter, OpenAICompatibleAdapter, build_http_client, note_no_client
from .usage import record_use, time_already_spent

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

MAX_INVALID_OUTPUT_RETRIES = 1  # retry the same model once, then move on

ModelState = Literal["go", "busy", "no"]  # send it; no room yet, come back; or leave this model for this call


# --- the call log -------------------------------------------------------------------------------


class CallLog(Protocol):
    """Where LLMCallRecords go. In memory for now; the llm_calls table comes with the database stories."""

    def record(self, rec: LLMCallRecord) -> None: ...


class InMemoryCallLog:
    """Keeps every record and logs one line per call. Prompt and reply text are never logged."""

    def __init__(self) -> None:
        self.records: list[LLMCallRecord] = []

    def record(self, rec: LLMCallRecord) -> None:
        self.records.append(rec)
        log.info(
            "llm call task=%s class=%s provider=%s model=%s outcome=%s fitted=%s in=%d out=%d ms=%d",
            rec.task.value,
            rec.data_class.value,
            rec.provider.value,
            rec.model,
            rec.outcome,
            rec.fitted,
            rec.input_tokens,
            rec.output_tokens,
            rec.duration_ms,
        )


# --- decisions -----------------------------------------------------------------------------------


def route_models(config: LLMConfig, data_class: DataClass, difficulty: Difficulty) -> list[ModelLimits]:
    """The models of each provider in the route, in route order (a provider's models in models.yaml order)."""
    return [m for provider in config.chain(data_class, difficulty) for m in config.models if m.provider is provider]


def check_data_class(task: LLMTask, data_class: DataClass) -> None:
    """A task whose prompt always carries the shopper's words can only be private. Anything else is a bug."""
    if task in PERSONAL_TASKS and data_class is not DataClass.PRIVATE:
        raise ValueError(f"{task.value} always carries the shopper's words, so its data class must be private")


def allowed_for_class(config: LLMConfig, models: Sequence[ModelLimits], data_class: DataClass) -> list[ModelLimits]:
    """Build rule 8: private and Reddit text never go to a provider that may train on what we send."""
    if data_class is DataClass.PUBLIC:
        return list(models)
    return [m for m in models if not m.trains_on_free_tier and not config.provider(m.provider).trains_on_inputs]


def should_retry(outcome: AttemptOutcome, retries_done: int) -> bool:
    """Only invalid output is retried on the same model, and only once."""
    return outcome == "invalid_output" and retries_done < MAX_INVALID_OUTPUT_RETRIES


def _real_use(error: Exception | None, reported: int, usage_reported: bool) -> int | None:
    """What a finished call counts for in this minute. None keeps what was held, because the real use isn't known."""
    if error is not None:
        return error.used_tokens if isinstance(error, ProviderFailure) else 0
    return reported if usage_reported else None


def planned_tokens(system: str, fitted: FitResult, reserve: int) -> int:
    """The most one attempt may use: every chunk's input plus the output kept free for its answer."""
    return sum(estimate_tokens(system) + estimate_tokens(text) + reserve for text in fitted.texts)


def default_limiters(config: LLMConfig) -> dict[Provider, RateLimiter]:
    """One rate limiter for each provider that has a limit of our own in models.yaml (or .env)."""
    return {
        p.name: RateLimiter(p.requests_per_second, p.tokens_per_minute, requests_per_minute=p.requests_per_minute)
        for p in config.providers
        if p.requests_per_second is not None or p.tokens_per_minute is not None or p.requests_per_minute is not None
    }


# --- the client ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Call(Generic[T]):
    """What stays the same for every model tried during one complete()."""

    task: LLMTask
    data_class: DataClass
    system: str
    schema: type[T]
    search_id: str | None
    limit_s: float
    stage_ends_at: float  # the pool's clock
    first_choice: ModelLimits | None  # whose turn this call claimed
    keep_free: int = 0  # tokens of a pool member's minute this call must leave for shoppers

    @property
    def latest_start(self) -> float:
        return self.stage_ends_at - CALL_MARGIN_S


@dataclass(frozen=True)
class _Plan:
    """One model, with the input fitted to it."""

    model: ModelLimits
    fitted: FitResult
    reserve: int
    planned: int

    @property
    def requests(self) -> int:
        """The HTTP requests this plan sends: one per chunk."""
        return len(self.fitted.texts)


class WrapperLLMClient:
    """Routing by data class, the model pool, fallback, health, budgets, context fitting, one retry, a call log."""

    def __init__(
        self,
        config: LLMConfig,
        adapters: Mapping[Provider, Adapter],
        call_log: CallLog | None = None,
        *,
        health: ProviderHealth | None = None,
        budget: DailyTokenBudget[Provider] | None = None,
        limiters: Mapping[Provider, RateLimiter] | None = None,
        pool: ModelPool | None = None,
        requests: DailyTokenBudget[Provider] | None = None,
    ) -> None:
        self.config = config
        self.adapters = dict(adapters)
        self.call_log: CallLog = call_log if call_log is not None else InMemoryCallLog()
        self.health = health if health is not None else ProviderHealth()
        if budget is None:
            budget = DailyTokenBudget({p.name: p.daily_tokens for p in config.providers})
        self.budget = budget
        # Requests a day, where a provider sets one (OpenRouter's free limit): each attempt counts one.
        if requests is None:
            requests = DailyTokenBudget({p.name: p.requests_per_day for p in config.providers})
        self.requests = requests
        self.limiters = dict(limiters) if limiters is not None else default_limiters(config)
        self.pool = pool if pool is not None else ModelPool(config.pool)
        self._reported_missing: set[Provider] = set()

    def candidates(self, data_class: DataClass, difficulty: Difficulty) -> list[ModelLimits]:
        """The models to try, in order. The class filter runs again here, whatever the routes table says."""
        routed = route_models(self.config, data_class, difficulty)
        allowed = allowed_for_class(self.config, routed, data_class)
        return self._with_keys([m for m in allowed if self.config.provider(m.provider).available])

    def order(self, task: LLMTask, data_class: DataClass, difficulty: Difficulty) -> list[ModelLimits]:
        """The models to try, in order. Any task but bulk work keeps the route's order.

        Bulk work takes turns across the models that take turns. For public pages, Groq's models (the private and
        Reddit pool) come next, as the last resort, in their fixed order: taking no turn, they move no rotation. A
        provider that doesn't rotate (OpenRouter) follows them all as the fallback.
        """
        models = self.candidates(data_class, difficulty)
        if task not in BULK_TASKS:
            return self.pool.order(models, data_class, rotate=False)
        turns = [m for m in models if self._takes_turns(m, task, data_class)]
        last_resort = [m for m in models if self._last_resort(m, task, data_class)]
        fallback = [m for m in models if not self._rotates(m)]
        return [*self.pool.order(turns, data_class, rotate=True), *last_resort, *fallback]

    def _rotates(self, model: ModelLimits) -> bool:
        return self.config.provider(model.provider).rotates

    def _last_resort(self, model: ModelLimits, task: LLMTask, data_class: DataClass) -> bool:
        """A pool member asked for a public page's bulk work: it takes no turn, and is never waited for (F-6)."""
        return task in BULK_TASKS and data_class is DataClass.PUBLIC and self.pool.has(model)

    def _takes_turns(self, model: ModelLimits, task: LLMTask, data_class: DataClass) -> bool:
        return self._rotates(model) and not self._last_resort(model, task, data_class)

    def complete(
        self,
        task: LLMTask,
        system: str,
        user: str,
        schema: type[T],
        *,
        data_class: DataClass,
        difficulty: Difficulty,
        search_id: str | None = None,
        max_output_tokens: int = 1024,
    ) -> T:
        check_data_class(task, data_class)
        full_system = build_system_prompt(system, schema)
        limit_s = time_limit_s(task)
        order = self.order(task, data_class, difficulty)
        first = order[0] if order else None
        ends_at = self.pool.now() + limit_s - time_already_spent()
        call = _Call(
            task, data_class, full_system, schema, search_id, limit_s, ends_at, first, self.pool.keep_free(task)
        )
        pending = [plan for model in order if (plan := self._plan(call, model, user, max_output_tokens))]
        retries: dict[str, int] = {}  # per model, for the whole call: coming back to a busy model is not a new retry
        while pending:
            busy: list[_Plan] = []
            for plan in pending:
                result, state = self._try_model(call, plan, retries)
                if result is not None:
                    return result
                if state == "busy":
                    busy.append(plan)
            if not self._wait_for_room(call, busy):
                break
            pending = busy
        raise LLMUnavailable(f"no LLM provider could complete the {task.value} task")

    def _plan(self, call: _Call[T], model: ModelLimits, user: str, max_output_tokens: int) -> _Plan | None:
        """Fit the input to this model, or None if it can't take it. Each model is fitted again, as limits differ.

        A pool member can't take a call over a minute's tokens, and chunks over a minute's tokens couldn't all go out
        before the stage's limit. So when its rate is the tighter limit, the input is trimmed to one call instead:
        callers put the most important text first, so the lowest-ranked comments go.

        A public page that a pool member reads as the last resort asks for the pool's public answer size, and is never
        trimmed: a trimmed page could lose its data block's closing marker, so that member is skipped instead (F-6).
        """
        last_resort = self._last_resort(model, call.task, call.data_class)
        reserve = reserve_output(model, max_output_tokens)
        if last_resort and self.config.pool.public_output_tokens is not None:
            reserve = min(reserve, self.config.pool.public_output_tokens)
        try:
            budget = model.input_budget(reserve_output=reserve)
            ceiling = self.pool.token_ceiling(model, call.keep_free)
            limited_by_rate = ceiling is not None and ceiling - reserve < budget
            if ceiling is not None and limited_by_rate:
                budget = ceiling - reserve
            fitted = fit(user, call.system, budget, call.task in CHUNKABLE_TASKS and not limited_by_rate)
        except ValueError as exc:
            log.info("llm %s skipped for %s: %s", model.provider.value, call.task.value, exc)
            return None
        if last_resort and fitted.mode == "trimmed":
            log.info("llm %s skipped: a public page is never trimmed to fit it", model.model)
            return None
        return _Plan(model, fitted, reserve, planned_tokens(call.system, fitted, reserve))

    def _try_model(self, call: _Call[T], plan: _Plan, retries: dict[str, int]) -> tuple[T | None, ModelState]:
        """One model, retrying invalid output once. A busy model is left to come back to."""
        name = plan.model.model
        while True:
            state, booking = self._may_call(call, plan)
            if state != "go":
                return None, state
            result, outcome = self._attempt(call, plan, booking)
            if result is not None:
                return result, "go"
            if not should_retry(outcome, retries.get(name, 0)):
                return None, "no"
            retries[name] = retries.get(name, 0) + 1

    def _wait_for_room(self, call: _Call[T], busy: Sequence[_Plan]) -> bool:
        """Sleep until the soonest slot opens. False if nothing can open in time for a call to run."""
        waits = [self.pool.seconds_until_room(p.model, p.planned, call.keep_free) for p in busy]
        soonest = min((w for w in waits if w is not None), default=None)
        if soonest is not None and self._a_running_call_may_free_room(call, busy):
            look = min(soonest, RUNNING_CALL_LOOK_S)
            if self.pool.now() + look <= call.latest_start:
                self.pool.pause(look)
                return True
        if soonest is None or self.pool.now() + soonest > call.latest_start:
            for plan in busy:
                log.info("llm %s skipped: no room before the time limit", plan.model.model)
                self._record(call, plan, "rate_limited")
            return False
        self.pool.pause(soonest)
        return True

    def _a_running_call_may_free_room(self, call: _Call[T], busy: Sequence[_Plan]) -> bool:
        """A reservation shrinks when its call ends, and the minute's count can't know when that will be.

        Only bulk work waits for that. A shopper's call has a quicker fallback, and a late start could only time out.
        """
        if not self.config.pool.book_real_use or call.task not in BULK_TASKS:
            return False
        return any(self.pool.running_calls_may_free_room(p.model, p.planned, call.keep_free) for p in busy)

    def _with_keys(self, models: Sequence[ModelLimits]) -> list[ModelLimits]:
        """Drop models whose provider has no API key, saying so once (the variable name, never a value)."""
        kept: list[ModelLimits] = []
        for model in models:
            if model.provider in self.adapters:
                kept.append(model)
            elif model.provider not in self._reported_missing:
                self._reported_missing.add(model.provider)
                env_var = self.config.provider(model.provider).api_key_env
                log.info("llm provider %s skipped: %s is not set", model.provider.value, env_var)
        return kept

    def _member(self, model: ModelLimits) -> str | None:
        """The name health tracks this model under: its own for a pool member, its provider's for the rest."""
        return model.model if self.pool.has(model) else None

    def _may_call(self, call: _Call[T], plan: _Plan) -> tuple[ModelState, Booking | None]:
        """Time, budgets, health and rate limit, checked before every call. A call that fails one is never sent."""
        model, planned = plan.model, plan.planned
        provider, member = model.provider, self._member(model)
        if self.pool.now() > call.latest_start:
            log.info("llm %s skipped: too close to the stage's time limit", model.model)
            return "no", None
        if not self._reserve(model, planned):
            log.info("llm %s skipped: today's token budget is spent", model.model)
            self._record(call, plan, "skipped_budget")
            return "no", None
        if not self.requests.reserve(provider, plan.requests):
            self._release_tokens(model, planned)
            log.info("llm %s skipped: today's requests are used up", model.model)
            self._record(call, plan, "skipped_budget")
            return "no", None
        if not self.health.allow(provider, member):
            self._release(plan)
            log.info("llm %s skipped: %s", model.model, self.health.state(provider, member))
            return "no", None
        state, booking = self._take_slot(call, plan)
        takes_turns = self._takes_turns(model, call.task, call.data_class)
        if state == "go" and call.task in BULK_TASKS and model != call.first_choice and takes_turns:
            self.pool.served(model, call.data_class)  # the claimed member was skipped: the turn moved on
        if state != "go":
            self._release(plan)
            self.health.release(provider, member)
        return state, booking

    def _reserve(self, model: ModelLimits, tokens: int) -> bool:
        """Hold today's tokens before the call runs, so calls running together can't pass a budget."""
        if not self.budget.reserve(model.provider, tokens):
            return False
        if self.pool.reserve(model, tokens):
            return True
        self.budget.settle(model.provider, tokens, 0)
        return False

    def _release_tokens(self, model: ModelLimits, tokens: int) -> None:
        self.budget.settle(model.provider, tokens, 0)
        self.pool.release(model, tokens)

    def _release(self, plan: _Plan) -> None:
        """A call that was held for but not sent gives back its tokens and its requests."""
        self._release_tokens(plan.model, plan.planned)
        self.requests.settle(plan.model.provider, plan.requests, 0)

    def _take_slot(self, call: _Call[T], plan: _Plan) -> tuple[ModelState, Booking | None]:
        model = plan.model
        if self.pool.has(model):
            booking = self.pool.try_take(model, plan.planned, call.keep_free)  # keep_free: the shopper's reserve
            if booking is not None:
                return "go", booking
            if self._last_resort(model, call.task, call.data_class):
                log.info("llm %s skipped: no room right now, and a public page never waits for it", model.model)
                self._record(call, plan, "rate_limited")
                return "no", None
            return "busy", None
        limiter = self.limiters.get(model.provider)
        if limiter is None:
            return "go", None
        room = self._wait_for_pace(call, plan, limiter) if call.task in BULK_TASKS else limiter.acquire(plan.planned)
        if not room:
            log.info("llm %s skipped: no room in its rate limit in time", model.provider.value)
            self._record(call, plan, "rate_limited")
            return "no", None
        return "go", None

    def _wait_for_pace(self, call: _Call[T], plan: _Plan, limiter: RateLimiter) -> bool:
        """Bulk work waits for a provider's own limit, if that still leaves a full attempt's time. Else the next model.

        Sources run in parallel, so this never holds the limiter's lock while it waits: each keeps its own time limit.
        """
        leaves_an_attempt = call.stage_ends_at - self._full_timeout_s(call, plan.model) - CALL_MARGIN_S
        provider, member = plan.model.provider, self._member(plan.model)
        while limiter.try_book(plan.planned) is None:
            wait = limiter.seconds_until_room(plan.planned)
            if wait is None:
                return False
            start = self.pool.now() + wait
            short = wait <= limiter.max_wait_s and start <= call.latest_start
            if not short and start > leaves_an_attempt:
                return False
            self.pool.pause(wait)
            if self.health.state(provider, member) == "cooling":  # another source just earned a 429 here
                return False
        return True

    def _full_timeout_s(self, call: _Call[T], model: ModelLimits) -> float:
        """The longest one attempt may take, which leaves another model time to answer if this one hangs."""
        return min(self.config.provider(model.provider).request_timeout_s, call.limit_s * ATTEMPT_SHARE)

    def _timeout_s(self, call: _Call[T], model: ModelLimits) -> float:
        left = max(call.stage_ends_at - self.pool.now(), CALL_MARGIN_S)
        return min(self._full_timeout_s(call, model), left)

    def _attempt(self, call: _Call[T], plan: _Plan, booking: Booking | None = None) -> tuple[T | None, AttemptOutcome]:
        """One attempt on one model: every chunk is sent and parsed. Always writes one call record."""
        model, fitted, system = plan.model, plan.fitted, call.system
        provider, member = model.provider, self._member(model)
        adapter = self.adapters[provider]
        timeout_s = self._timeout_s(call, model)
        started = monotonic()
        started_at = datetime.now(UTC)
        tokens_in = tokens_out = 0
        usage_reported = True
        unanswered = 0  # estimated input of a chunk whose call failed: it was still sent
        sent = 0  # requests that went out: a chunked attempt stops at the first chunk that fails
        result: T | None = None
        outcome: AttemptOutcome = "ok"
        error: Exception | None = None
        try:
            answers: list[T] = []
            for text in fitted.texts:
                unanswered = estimate_tokens(system) + estimate_tokens(text)
                extra = self.config.request_fields(call.task, model.model)
                sent += 1
                raw = adapter.call(model.model, system, text, plan.reserve, timeout_s, extra)
                unanswered = 0
                tokens_in += raw.input_tokens
                tokens_out += raw.output_tokens
                usage_reported = usage_reported and raw.usage_reported
                answers.append(parse_output(raw.text, call.schema, strict=call.task in RETRIEVED_TEXT_TASKS))
            result = merge_chunk_results(answers, call.schema) if fitted.mode == "chunked" else answers[0]
        except (InvalidOutput, ValidationError):
            outcome = "invalid_output"  # the provider did answer, so its health is fine
        except ProviderFailure as exc:
            outcome, error = exc.outcome, exc
        except Exception as exc:  # an adapter bug must not crash the search (build rule 9)
            log.warning("llm %s raised %s; trying the next model", provider.value, type(exc).__name__)
            outcome, error = "error", exc
        cut_short = timeout_s < self._full_timeout_s(call, model)
        if error is None:
            self.health.record_success(provider, member)
        elif isinstance(error, ProviderTimeout) and cut_short:
            self.health.release(provider, member)  # we gave it less than a fair time: that says nothing about it
        else:
            self.health.record_failure(provider, error, member)
        used = tokens_in + unanswered + tokens_out
        real_use = _real_use(error, tokens_in + tokens_out, usage_reported)
        if booking is not None and self.config.pool.book_real_use:
            self.pool.book_real_use(model, booking, real_use)
        record_use(plan.planned if real_use is None else real_use)
        self.budget.settle(provider, plan.planned, used)
        self.pool.settle(model, plan.planned, used)
        self.requests.settle(provider, plan.requests, sent)  # each request that went out counts, whatever came back
        duration_ms = int((monotonic() - started) * 1000)
        self._record(call, plan, outcome, tokens_in + unanswered, tokens_out, duration_ms, started_at)
        return result, outcome

    def _record(
        self,
        call: _Call[T],
        plan: _Plan,
        outcome: AttemptOutcome,
        input_tokens: int = 0,
        output_tokens: int = 0,
        duration_ms: int = 0,
        started_at: datetime | None = None,
    ) -> None:
        self.call_log.record(
            LLMCallRecord(
                search_id=call.search_id,
                task=call.task,
                data_class=call.data_class,
                provider=plan.model.provider,
                model=plan.model.model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                duration_ms=duration_ms,
                outcome=outcome,
                fitted=plan.fitted.mode,
                at=started_at or datetime.now(UTC),  # when the attempt began: the end is at + duration_ms
            )
        )


def build_llm_client(
    config_path: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    call_log: CallLog | None = None,
) -> WrapperLLMClient:
    """The real client: models.yaml, any overrides from the environment (such as MISTRAL_MODEL), and the keys.

    With no `env`, python-dotenv loads .env if there is one (existing environment variables win), then
    os.environ is used. Providers without a key get no adapter and are skipped.
    """
    if env is None:
        load_dotenv()
        env = os.environ
    config = with_env_overrides(load_llm_config(config_path), env)
    try:
        http = build_http_client(config.force_ipv4)
    except Exception as exc:  # a proxy httpx can't use must not crash a request: stages use their fallbacks (rule 9)
        note_no_client(type(exc).__name__)
        client = WrapperLLMClient(config, {}, call_log)
        client._reported_missing.update(Provider)  # the keys may be fine, so nothing should say they are missing
        return client
    adapters: dict[Provider, Adapter] = {}
    for settings in config.providers:
        key = env.get(settings.api_key_env, "").strip()
        if key:
            adapters[settings.name] = OpenAICompatibleAdapter(
                settings.name,
                settings.base_url,
                key,
                http,
                json_mode=settings.json_mode,
                reasoning_effort=settings.reasoning_effort,
            )
    return WrapperLLMClient(config, adapters, call_log)


def satisfies_contract(client: WrapperLLMClient) -> LLMClient:
    """mypy fails here if WrapperLLMClient ever stops matching the LLMClient contract."""
    return client
