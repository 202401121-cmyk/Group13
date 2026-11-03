"""The one LLM client every stage uses (US-26). Implements app.contracts.LLMClient.

complete() tries the configured models in provider order, re-fitting the input to each model's window. It
retries invalid output once, moves on after any provider failure, and raises LLMUnavailable when every
model failed. It never invents an answer: the calling stage's fallback decides what happens next.

The ordering, filtering and retry decisions are small functions, so US-46 can add the circuit breaker,
round-robin rotation and daily token budgets around them without rewriting complete().
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

from .config import LLMConfig, load_llm_config
from .errors import AttemptOutcome, InvalidOutput, ProviderFailure
from .fitting import CHUNKABLE_TASKS, FitResult, estimate_tokens, fit, merge_chunk_results, reserve_output
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


# --- decisions (the seams US-46 builds on) ------------------------------------------------------


def ordered_models(config: LLMConfig) -> list[ModelLimits]:
    """Every model, in provider fallback order (the order of `providers` in models.yaml)."""
    order = [p.name for p in config.providers]
    return sorted(config.models, key=lambda m: order.index(m.provider))


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


# --- the client ---------------------------------------------------------------------------------


class WrapperLLMClient:
    """Fallback across providers, routing by data class, context fitting, one retry, and a call log."""

    def __init__(
        self,
        config: LLMConfig,
        adapters: Mapping[Provider, Adapter],
        call_log: CallLog | None = None,
    ) -> None:
        self.config = config
        self.adapters = dict(adapters)
        self.call_log: CallLog = call_log if call_log is not None else InMemoryCallLog()
        self._reported_missing: set[Provider] = set()

    def candidates(self, data_class: DataClass) -> list[ModelLimits]:
        """The models that may see a prompt of this class, in order."""
        return self._with_keys(allowed_for_class(self.config, ordered_models(self.config), data_class))

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
        for model in self.candidates(data_class):
            reserve = reserve_output(model, max_output_tokens)
            try:
                # Re-fit for every model, so a fallback to a smaller window trims or chunks again first.
                fitted = fit(user, full_system, model.input_budget(reserve_output=reserve), chunkable)
            except ValueError as exc:
                log.info("llm %s skipped for %s: %s", model.provider.value, task.value, exc)
                continue
            retries = 0
            while True:
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
            outcome = "invalid_output"
        except ProviderFailure as exc:
            outcome = exc.outcome
        except Exception as exc:  # an adapter bug must not crash the search (build rule 9)
            log.warning("llm %s raised %s; trying the next model", model.provider.value, type(exc).__name__)
            outcome = "error"
        self.call_log.record(
            LLMCallRecord(
                search_id=search_id,
                task=task,
                data_class=data_class,
                provider=model.provider,
                model=model.model,
                input_tokens=tokens_in + unanswered,
                output_tokens=tokens_out,
                duration_ms=int((monotonic() - started) * 1000),
                outcome=outcome,
                fitted=fitted.mode,
                at=datetime.now(UTC),
            )
        )
        return result, outcome


def build_llm_client(
    config_path: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    call_log: CallLog | None = None,
) -> WrapperLLMClient:
    """The real client: models.yaml plus keys from the environment.

    With no `env`, python-dotenv loads .env if there is one (existing environment variables win), then
    os.environ is used. Providers without a key get no adapter and are skipped.
    """
    config = load_llm_config(config_path)
    if env is None:
        load_dotenv()
        env = os.environ
    http = httpx.Client()
    adapters: dict[Provider, Adapter] = {}
    for settings in config.providers:
        key = env.get(settings.api_key_env, "").strip()
        if key:
            adapters[settings.name] = OpenAICompatibleAdapter(settings.name, settings.base_url, key, http)
    return WrapperLLMClient(config, adapters, call_log)


def satisfies_contract(client: WrapperLLMClient) -> LLMClient:
    """mypy fails here if WrapperLLMClient ever stops matching the LLMClient contract."""
    return client
