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

from app.contracts import ModelLimits, Provider

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
    json_mode: bool = Field(default=False, description="Send response_format json_object (Mistral supports it)")
    requests_per_second: float | None = Field(default=None, gt=0)
    tokens_per_minute: int | None = Field(default=None, gt=0)
    daily_tokens: int | None = Field(default=None, gt=0, description="Tokens per UTC day, counted in memory")

    @field_validator("base_url", "terms_url")
    @classmethod
    def _https_only(cls, value: str) -> str:
        if not re.match(r"^https://[^\s/]+", value):
            raise ValueError(f"URLs must start with https://, got {value!r}")
        return value.rstrip("/")


class LLMConfig(BaseModel):
    """Providers in fallback order, and the limits of every model."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    providers: list[ProviderSettings] = Field(min_length=1)
    models: list[ModelLimits] = Field(min_length=1)

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
        return self

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
        return LLMConfig.model_validate({"providers": providers, "models": models})
    except ValidationError as exc:
        used = sorted(var for variables in ENV_OVERRIDES.values() for var in variables if env.get(var, "").strip())
        raise LLMConfigError(f"invalid LLM settings from the environment ({', '.join(used)}):\n{exc}") from exc
