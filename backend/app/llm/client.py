"""The one LLM client every stage uses (US-26, US-46). Implements app.contracts.LLMClient.

complete() tries the providers that the routes table in models.yaml gives for the call's data class and
difficulty, skipping any that may not see that class, and re-fits the input to each model's window.
Before every call it checks the provider's daily token budget, its health (cool-downs and dead keys) and its
rate limit. It retries invalid output once, moves on after any provider failure, and raises LLMUnavailable
when no model answered. It never invents an answer: the calling stage's fallback decides what happens next.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Protocol, TypeVar

import httpx
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
from .errors import AttemptOutcome, InvalidOutput, ProviderFailure
from .fitting import CHUNKABLE_TASKS, FitResult, estimate_tokens, fit, merge_chunk_results, reserve_output
from .health import ProviderHealth
from .limits import DailyTokenBudget, RateLimiter
from .output import build_system_prompt, parse_output
from .providers import Adapter, OpenAICompatibleAdapter

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

MAX_INVALID_OUTPUT_RETRIES = 1  # retry the same model once, then move on


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


class WrapperLLMClient:
    """Routing by data class, fallback, provider health, token budgets, context fitting, one retry, a call log."""

    def __init__(
        self,
        config: LLMConfig,
        adapters: Mapping[Provider, Adapter],
        call_log: CallLog | None = None,
        *,
        health: ProviderHealth | None = None,
        budget: DailyTokenBudget | None = None,
        limiters: Mapping[Provider, RateLimiter] | None = None,
    ) -> None:
        self.config = config
        self.adapters = dict(adapters)
        self.call_log: CallLog = call_log if call_log is not None else InMemoryCallLog()
        self.health = health if health is not None else ProviderHealth()
        if budget is None:
            budget = DailyTokenBudget({p.name: p.daily_tokens for p in config.providers})
        self.budget = budget
        self.limiters = dict(limiters) if limiters is not None else default_limiters(config)
        self._reported_missing: set[Provider] = set()

    def candidates(self, data_class: DataClass, difficulty: Difficulty) -> list[ModelLimits]:
        """The models to try, in order. The class filter runs again here, whatever the routes table says."""
        routed = route_models(self.config, data_class, difficulty)
        return self._with_keys(allowed_for_class(self.config, routed, data_class))

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
        chunkable = task in CHUNKABLE_TASKS
        for model in self.candidates(data_class, difficulty):
            reserve = reserve_output(model, max_output_tokens)
            try:
                # Re-fit for every model, so a fallback to a smaller window trims or chunks again first.
                fitted = fit(user, full_system, model.input_budget(reserve_output=reserve), chunkable)
            except ValueError as exc:
                log.info("llm %s skipped for %s: %s", model.provider.value, task.value, exc)
                continue
            planned = planned_tokens(full_system, fitted, reserve)
            retries = 0
            while self._may_call(task, data_class, model, fitted, planned, search_id):
                result, outcome = self._attempt(
                    task, data_class, model, full_system, fitted, schema, reserve, search_id
                )
                if result is not None:
                    return result
                if not should_retry(outcome, retries):
                    break
                retries += 1
        raise LLMUnavailable(f"no LLM provider could complete the {task.value} task")

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

    def _may_call(
        self,
        task: LLMTask,
        data_class: DataClass,
        model: ModelLimits,
        fitted: FitResult,
        planned: int,
        search_id: str | None,
    ) -> bool:
        """Budget, health and rate limit, checked before every call. A call that fails one is never sent."""
        provider = model.provider
        if not self.budget.allows(provider, planned):
            log.info("llm %s skipped: today's token budget is spent", provider.value)
            self._record(task, data_class, model, "skipped_budget", fitted, search_id)
            return False
        if not self.health.allow(provider):
            log.info("llm %s skipped: %s", provider.value, self.health.state(provider))
            return False
        limiter = self.limiters.get(provider)
        if limiter is not None and not limiter.acquire(planned):
            self.health.release(provider)
            log.info("llm %s skipped: no room in its rate limit within %gs", provider.value, limiter.max_wait_s)
            self._record(task, data_class, model, "rate_limited", fitted, search_id)
            return False
        return True

    def _attempt(
        self,
        task: LLMTask,
        data_class: DataClass,
        model: ModelLimits,
        system: str,
        fitted: FitResult,
        schema: type[T],
        reserve: int,
        search_id: str | None,
    ) -> tuple[T | None, AttemptOutcome]:
        """One attempt on one model: every chunk is sent and parsed. Always writes one call record."""
        adapter = self.adapters[model.provider]
        timeout_s = self.config.provider(model.provider).request_timeout_s
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
                raw = adapter.call(model.model, system, text, reserve, timeout_s)
                unanswered = 0
                tokens_in += raw.input_tokens
                tokens_out += raw.output_tokens
                answers.append(parse_output(raw.text, schema))
            result = merge_chunk_results(answers, schema) if fitted.mode == "chunked" else answers[0]
        except (InvalidOutput, ValidationError):
            outcome = "invalid_output"  # the provider did answer, so its health is fine
        except ProviderFailure as exc:
            outcome, error = exc.outcome, exc
        except Exception as exc:  # an adapter bug must not crash the search (build rule 9)
            log.warning("llm %s raised %s; trying the next model", model.provider.value, type(exc).__name__)
            outcome, error = "error", exc
        if error is None:
            self.health.record_success(model.provider)
        else:
            self.health.record_failure(model.provider, error)
        self.budget.spend(model.provider, tokens_in + unanswered + tokens_out)
        duration_ms = int((monotonic() - started) * 1000)
        self._record(
            task, data_class, model, outcome, fitted, search_id, tokens_in + unanswered, tokens_out, duration_ms
        )
        return result, outcome

    def _record(
        self,
        task: LLMTask,
        data_class: DataClass,
        model: ModelLimits,
        outcome: AttemptOutcome,
        fitted: FitResult,
        search_id: str | None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        duration_ms: int = 0,
    ) -> None:
        self.call_log.record(
            LLMCallRecord(
                search_id=search_id,
                task=task,
                data_class=data_class,
                provider=model.provider,
                model=model.model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                duration_ms=duration_ms,
                outcome=outcome,
                fitted=fitted.mode,
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
    http = httpx.Client()
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
