"""The one LLM client every stage uses (US-26, US-46). Implements app.contracts.LLMClient.

complete() tries the providers that the routes table in models.yaml gives for the call's data class and
difficulty, skipping any that may not see that class or are unavailable, and re-fits the input to each model.
Bulk tasks rotate through those models (app/llm/pool.py). Before every call it checks the daily token budget,
health (cool-downs and dead keys) and the rate limit. A pool member with no room is left to come back to, so a
free member goes first; if all are busy the call waits for a slot, up to its stage's time limit. It retries
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
from .limits import DailyTokenBudget, RateLimiter
from .output import build_system_prompt, parse_output
from .pool import ATTEMPT_SHARE, BULK_TASKS, CALL_MARGIN_S, ModelPool, time_limit_s
from .providers import Adapter, OpenAICompatibleAdapter, build_http_client, note_no_client

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


def planned_tokens(system: str, fitted: FitResult, reserve: int) -> int:
    """The most one attempt may use: every chunk's input plus the output kept free for its answer."""
    return sum(estimate_tokens(system) + estimate_tokens(text) + reserve for text in fitted.texts)


def default_limiters(config: LLMConfig) -> dict[Provider, RateLimiter]:
    """One rate limiter for each provider that has a limit of our own in models.yaml (or .env)."""
    return {
        p.name: RateLimiter(p.requests_per_second, p.tokens_per_minute)
        for p in config.providers
        if p.requests_per_second is not None or p.tokens_per_minute is not None
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
    ) -> None:
        self.config = config
        self.adapters = dict(adapters)
        self.call_log: CallLog = call_log if call_log is not None else InMemoryCallLog()
        self.health = health if health is not None else ProviderHealth()
        if budget is None:
            budget = DailyTokenBudget({p.name: p.daily_tokens for p in config.providers})
        self.budget = budget
        self.limiters = dict(limiters) if limiters is not None else default_limiters(config)
        self.pool = pool if pool is not None else ModelPool(config.pool)
        self._reported_missing: set[Provider] = set()

    def candidates(self, data_class: DataClass, difficulty: Difficulty) -> list[ModelLimits]:
        """The models to try, in order. The class filter runs again here, whatever the routes table says."""
        routed = route_models(self.config, data_class, difficulty)
        allowed = allowed_for_class(self.config, routed, data_class)
        return self._with_keys([m for m in allowed if self.config.provider(m.provider).available])

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
        order = self.pool.order(self.candidates(data_class, difficulty), data_class, rotate=task in BULK_TASKS)
        first = order[0] if order else None
        call = _Call(task, data_class, full_system, schema, search_id, limit_s, self.pool.now() + limit_s, first)
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
        """
        reserve = reserve_output(model, max_output_tokens)
        try:
            budget = model.input_budget(reserve_output=reserve)
            ceiling = self.pool.token_ceiling(model)
            limited_by_rate = ceiling is not None and ceiling - reserve < budget
            if ceiling is not None and limited_by_rate:
                budget = ceiling - reserve
            fitted = fit(user, call.system, budget, call.task in CHUNKABLE_TASKS and not limited_by_rate)
        except ValueError as exc:
            log.info("llm %s skipped for %s: %s", model.provider.value, call.task.value, exc)
            return None
        return _Plan(model, fitted, reserve, planned_tokens(call.system, fitted, reserve))

    def _try_model(self, call: _Call[T], plan: _Plan, retries: dict[str, int]) -> tuple[T | None, ModelState]:
        """One model, retrying invalid output once. A busy model is left to come back to."""
        name = plan.model.model
        while True:
            state = self._may_call(call, plan)
            if state != "go":
                return None, state
            result, outcome = self._attempt(call, plan)
            if result is not None:
                return result, "go"
            if not should_retry(outcome, retries.get(name, 0)):
                return None, "no"
            retries[name] = retries.get(name, 0) + 1

    def _wait_for_room(self, call: _Call[T], busy: Sequence[_Plan]) -> bool:
        """Sleep until the soonest slot opens. False if nothing can open in time for a call to run."""
        waits = [self.pool.seconds_until_room(p.model, p.planned) for p in busy]
        soonest = min((w for w in waits if w is not None), default=None)
        if soonest is None or self.pool.now() + soonest > call.latest_start:
            for plan in busy:
                log.info("llm %s skipped: no room before the time limit", plan.model.model)
                self._record(call, plan, "rate_limited")
            return False
        self.pool.pause(soonest)
        return True

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

    def _may_call(self, call: _Call[T], plan: _Plan) -> ModelState:
        """Time, budgets, health and rate limit, checked before every call. A call that fails one is never sent."""
        model, planned = plan.model, plan.planned
        provider, member = model.provider, self._member(model)
        if self.pool.now() > call.latest_start:
            log.info("llm %s skipped: too close to the stage's time limit", model.model)
            return "no"
        if not self._reserve(model, planned):
            log.info("llm %s skipped: today's token budget is spent", model.model)
            self._record(call, plan, "skipped_budget")
            return "no"
        if not self.health.allow(provider, member):
            self._release(model, planned)
            log.info("llm %s skipped: %s", model.model, self.health.state(provider, member))
            return "no"
        state = self._take_slot(call, plan)
        if state == "go" and call.task in BULK_TASKS and model != call.first_choice:
            self.pool.served(model, call.data_class)  # the claimed member was skipped: the turn moved on
        if state != "go":
            self._release(model, planned)
            self.health.release(provider, member)
        return state

    def _reserve(self, model: ModelLimits, tokens: int) -> bool:
        """Hold today's tokens before the call runs, so calls running together can't pass a budget."""
        if not self.budget.reserve(model.provider, tokens):
            return False
        if self.pool.reserve(model, tokens):
            return True
        self.budget.settle(model.provider, tokens, 0)
        return False

    def _release(self, model: ModelLimits, tokens: int) -> None:
        self.budget.settle(model.provider, tokens, 0)
        self.pool.release(model, tokens)

    def _take_slot(self, call: _Call[T], plan: _Plan) -> ModelState:
        model = plan.model
        if self.pool.has(model):
            return "go" if self.pool.try_take(model, plan.planned) else "busy"
        limiter = self.limiters.get(model.provider)
        if limiter is not None and not limiter.acquire(plan.planned):
            log.info("llm %s skipped: no room in its rate limit within %gs", model.provider.value, limiter.max_wait_s)
            self._record(call, plan, "rate_limited")
            return "no"
        return "go"

    def _full_timeout_s(self, call: _Call[T], model: ModelLimits) -> float:
        """The longest one attempt may take, which leaves another model time to answer if this one hangs."""
        return min(self.config.provider(model.provider).request_timeout_s, call.limit_s * ATTEMPT_SHARE)

    def _timeout_s(self, call: _Call[T], model: ModelLimits) -> float:
        left = max(call.stage_ends_at - self.pool.now(), CALL_MARGIN_S)
        return min(self._full_timeout_s(call, model), left)

    def _attempt(self, call: _Call[T], plan: _Plan) -> tuple[T | None, AttemptOutcome]:
        """One attempt on one model: every chunk is sent and parsed. Always writes one call record."""
        model, fitted, system = plan.model, plan.fitted, call.system
        provider, member = model.provider, self._member(model)
        adapter = self.adapters[provider]
        timeout_s = self._timeout_s(call, model)
        started = monotonic()
        tokens_in = tokens_out = 0
        unanswered = 0  # estimated input of a chunk whose call failed: it was still sent
        result: T | None = None
        outcome: AttemptOutcome = "ok"
        error: Exception | None = None
        try:
            answers: list[T] = []
            for text in fitted.texts:
                unanswered = estimate_tokens(system) + estimate_tokens(text)
                raw = adapter.call(model.model, system, text, plan.reserve, timeout_s)
                unanswered = 0
                tokens_in += raw.input_tokens
                tokens_out += raw.output_tokens
                answers.append(parse_output(raw.text, call.schema))
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
        self.budget.settle(provider, plan.planned, used)
        self.pool.settle(model, plan.planned, used)
        duration_ms = int((monotonic() - started) * 1000)
        self._record(call, plan, outcome, tokens_in + unanswered, tokens_out, duration_ms)
        return result, outcome

    def _record(
        self,
        call: _Call[T],
        plan: _Plan,
        outcome: AttemptOutcome,
        input_tokens: int = 0,
        output_tokens: int = 0,
        duration_ms: int = 0,
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
                at=datetime.now(UTC),
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
                settings.name, settings.base_url, key, http, json_mode=settings.json_mode
            )
    return WrapperLLMClient(config, adapters, call_log)


def satisfies_contract(client: WrapperLLMClient) -> LLMClient:
    """mypy fails here if WrapperLLMClient ever stops matching the LLMClient contract."""
    return client
