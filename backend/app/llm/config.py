"""Provider settings and model limits, loaded from config/models.yaml (US-26, US-46).

The file is checked when it loads, so a bad config stops the app at startup rather than at the first call.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date
from pathlib import Path

import yaml  # type: ignore[import-untyped]  # PyYAML ships no type stubs; adding types-PyYAML is a dependency
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.contracts import DataClass, Difficulty, ModelLimits, Provider

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "models.yaml"


class LLMConfigError(ValueError):
    """config/models.yaml is missing, unreadable or breaks a rule."""


class ProviderSettings(BaseModel):
    """How to reach one provider. Not a contract: it never leaves the LLM wrapper."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Provider
    base_url: str
    api_key_env: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$", description="Environment variable holding the key")
    request_timeout_s: float = Field(default=30, gt=0)
    # Build rule 8 (US-46): may this provider, on the plan we use, train on what we send? True means public data
    # only. Set from the provider's own terms page, with the date it was read, so a reviewer can check it again.
    trains_on_inputs: bool
    terms_url: str
    terms_checked: date
    # Limits we hold ourselves to (US-46). None = no limit of our own; the provider's limits still apply.
    reasoning_effort: str | None = Field(
        default=None,
        description="Sent as is. Thinking counts against a model's output limit, so a long think can cut off",
    )
    json_mode: bool = Field(default=False, description="Send response_format json_object (Mistral supports it)")
    requests_per_second: float | None = Field(default=None, gt=0)
    tokens_per_minute: int | None = Field(default=None, gt=0)
    daily_tokens: int | None = Field(default=None, gt=0, description="Tokens per UTC day, counted in memory")
    # False = skip it with no call, for example while the account answers HTTP 402. Its row and flags stay.
    available: bool = True
    unavailable_reason: str | None = None

    @field_validator("base_url", "terms_url")
    @classmethod
    def _https_only(cls, value: str) -> str:
        if not re.match(r"^https://[^\s/]+", value):
            raise ValueError(f"URLs must start with https://, got {value!r}")
        return value.rstrip("/")

    @model_validator(mode="after")
    def _says_why_unavailable(self) -> ProviderSettings:
        if not self.available and not self.unavailable_reason:
            raise ValueError(f"{self.name.value} is not available, so it needs an unavailable_reason")
        return self


class PoolMember(BaseModel):
    """One model's own limits (US-46). Not on ModelLimits, which is a contract and holds window sizes only."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: Provider
    model: str = Field(min_length=1)
    requests_per_minute: int = Field(gt=0)
    tokens_per_minute: int = Field(gt=0)
    tokens_per_day: int = Field(gt=0)
    requests_per_day: int = Field(gt=0, description="Recorded to check the API's headers; tokens run out first")


class PoolSettings(BaseModel):
    """The members of the model pool, and where their limits came from, so a reviewer can check them again."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    limits_url: str
    limits_checked: date
    reddit_tokens_per_search: int = Field(gt=0, description="What a search's Reddit extraction may plan to use")
    extract_reserve_tokens_per_minute: int = Field(
        default=0, ge=0, description="What bulk work must leave of each member's minute, for calls a shopper waits on"
    )
    members: list[PoolMember] = Field(min_length=1)

    @property
    def combined_tokens_per_minute(self) -> int:
        return sum(m.tokens_per_minute for m in self.members)

    def member(self, provider: Provider, model: str) -> PoolMember | None:
        return next((m for m in self.members if (m.provider, m.model) == (provider, model)), None)


# Which providers a call may use, in order, by what its prompt carries and how big the job is (US-46).
Routes = dict[DataClass, dict[Difficulty, list[Provider]]]


class LLMConfig(BaseModel):
    """How to reach each provider, the limits of every model, and the provider chain for every kind of call."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    providers: list[ProviderSettings] = Field(min_length=1)
    models: list[ModelLimits] = Field(min_length=1)
    routes: Routes
    pool: PoolSettings
    force_ipv4: bool = Field(default=True, description="Connect to every provider over IPv4 only (docs/api-setup.md)")

    @model_validator(mode="after")
    def _check(self) -> LLMConfig:
        names = [p.name for p in self.providers]
        duplicates = sorted({n.value for n in names if names.count(n) > 1})
        if duplicates:
            raise ValueError(f"each provider may appear only once: {duplicates}")
        missing = sorted(p.value for p in Provider if p not in names)
        if missing:
            raise ValueError(f"no provider entry for: {missing}")

        pairs = [(m.provider, m.model) for m in self.models]
        repeated = sorted({f"{p.value}/{m}" for p, m in pairs if pairs.count((p, m)) > 1})
        if repeated:
            raise ValueError(f"each model may appear only once: {repeated}")
        without_model = sorted(p.value for p in Provider if not any(m.provider == p for m in self.models))
        if without_model:
            raise ValueError(f"every provider needs at least one model: {without_model}")

        # Build rule 8: Gemini's free tier may train on prompts, so personal tasks must never be routed there.
        unsafe = [m.model for m in self.models if m.provider == Provider.GEMINI and not m.trains_on_free_tier]
        if unsafe:
            raise ValueError(f"Gemini models must set trains_on_free_tier: true (build rule 8): {unsafe}")
        trains = {p.name: p.trains_on_inputs for p in self.providers}
        mismatched = sorted(
            f"{m.provider.value}/{m.model}" for m in self.models if m.trains_on_free_tier != trains[m.provider]
        )
        if mismatched:
            raise ValueError(f"trains_on_free_tier must match the provider's trains_on_inputs: {mismatched}")
        self._check_routes(trains)
        self._check_pool(set(pairs))
        return self

    def _check_routes(self, trains: dict[Provider, bool]) -> None:
        """Every class and difficulty has a chain; private and Reddit chains never name a training provider."""
        missing = sorted(f"{c.value}/{d.value}" for c in DataClass for d in Difficulty if not self.chain(c, d))
        if missing:
            raise ValueError(f"every data class and difficulty needs a route with at least one provider: {missing}")
        available = {p.name: p.available for p in self.providers}
        for data_class, by_difficulty in self.routes.items():
            for difficulty, chain in by_difficulty.items():
                where = f"{data_class.value}/{difficulty.value}"
                if len(set(chain)) != len(chain):
                    raise ValueError(f"route {where} names a provider twice: {[p.value for p in chain]}")
                training = [p.value for p in chain if trains[p]]
                if data_class is not DataClass.PUBLIC and training:
                    raise ValueError(
                        f"route {where} may not use providers that train on inputs (build rule 8): {training}"
                    )
                unavailable = [p.value for p in chain if not available[p]]
                if data_class is not DataClass.PUBLIC and unavailable:
                    raise ValueError(f"route {where} names an unavailable provider: {unavailable}")

    def _check_pool(self, known_models: set[tuple[Provider, str]]) -> None:
        """Every member is a real model, listed once. The reserve leaves room, and the Reddit budget fits above it."""
        members = [(m.provider, m.model) for m in self.pool.members]
        unknown = sorted(f"{p.value}/{m}" for p, m in members if (p, m) not in known_models)
        if unknown:
            raise ValueError(f"every pool member needs a row in models: {unknown}")
        if len(set(members)) != len(members):
            raise ValueError("each pool member may appear only once")
        if self.pool.reddit_tokens_per_search > self.pool.combined_tokens_per_minute:
            raise ValueError("reddit_tokens_per_search must fit in one minute of the pool's combined token rate")
        reserve = self.pool.extract_reserve_tokens_per_minute
        if any(m.tokens_per_minute <= reserve for m in self.pool.members):
            raise ValueError("extract_reserve_tokens_per_minute must leave every pool member room for bulk work")
        if self.pool.reddit_tokens_per_search > sum(m.tokens_per_minute - reserve for m in self.pool.members):
            raise ValueError("reddit_tokens_per_search must fit in the pool's minute above the reserve")

    def chain(self, data_class: DataClass, difficulty: Difficulty) -> list[Provider]:
        """The providers to try, in order, for one kind of call."""
        return list(self.routes.get(data_class, {}).get(difficulty, []))

    def provider(self, name: Provider) -> ProviderSettings:
        return next(p for p in self.providers if p.name == name)


def load_llm_config(path: Path | str | None = None) -> LLMConfig:
    """Read and check config/models.yaml (or `path`). Raises LLMConfigError with the reason."""
    where = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    try:
        raw = yaml.safe_load(where.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise LLMConfigError(f"cannot read LLM config {where}: {exc}") from exc
    try:
        return LLMConfig.model_validate(raw)
    except ValidationError as exc:
        raise LLMConfigError(f"invalid LLM config {where}:\n{exc}") from exc


# Settings a member may change in .env without editing models.yaml (US-46). Unset or empty keeps the file's value.
ENV_OVERRIDES: dict[Provider, dict[str, str]] = {
    Provider.MISTRAL: {
        "MISTRAL_MODEL": "model",
        "MISTRAL_RPS": "requests_per_second",
        "MISTRAL_TPM": "tokens_per_minute",
        "MISTRAL_DAILY_TOKENS": "daily_tokens",
    },
}


def with_env_overrides(config: LLMConfig, env: Mapping[str, str]) -> LLMConfig:
    """The config with any ENV_OVERRIDES values from `env` applied, checked again. Raises LLMConfigError.

    The model override renames the provider's first model and keeps its limits: if you pick a model with a
    smaller window, change its row in models.yaml too.
    """
    providers = [p.model_dump() for p in config.providers]
    models = [m.model_dump() for m in config.models]
    for provider, variables in ENV_OVERRIDES.items():
        for var, field in variables.items():
            text = env.get(var, "").strip()
            if not text:
                continue
            if field == "model":
                row = next((m for m in models if m["provider"] == provider), None)
                if row is not None:
                    row["model"] = text
                continue
            settings = next(p for p in providers if p["name"] == provider)
            try:
                settings[field] = float(text) if field == "requests_per_second" else int(text)
            except ValueError:
                raise LLMConfigError(f"{var} must be a number") from None
    try:
        return LLMConfig.model_validate(
            {
                "providers": providers,
                "models": models,
                "routes": config.routes,
                "pool": config.pool,
                "force_ipv4": config.force_ipv4,
            }
        )
    except ValidationError as exc:
        used = sorted(var for variables in ENV_OVERRIDES.values() for var in variables if env.get(var, "").strip())
        raise LLMConfigError(f"invalid LLM settings from the environment ({', '.join(used)}):\n{exc}") from exc
