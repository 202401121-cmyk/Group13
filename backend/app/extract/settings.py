"""The extract stage's settings (US-14): config/extract.yaml, one environment switch, and the pool's Reddit budget."""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping
from pathlib import Path

import yaml  # type: ignore[import-untyped]  # PyYAML ships no type stubs
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.contracts import DataClass, SourceTier
from app.llm import load_llm_config

log = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "extract.yaml"
MAX_SOURCES_SWITCH = "EXTRACT_MAX_SOURCES"
_DOMAIN = re.compile(r"[a-z0-9-]+(\.[a-z0-9-]+)+")


class ExtractSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    reddit_output_tokens: int = Field(default=1200, ge=100, le=3500)
    public_output_tokens: int = Field(default=2000, ge=100, le=3500)
    max_sources_per_search: int = Field(default=40, ge=1, le=100)
    reddit_tokens_per_search: int = Field(default=18_000, gt=0)
    tiers: dict[str, list[str]] = Field(default_factory=dict)

    @field_validator("tiers")
    @classmethod
    def _known_tiers(cls, tiers: dict[str, list[str]]) -> dict[str, list[str]]:
        for name, domains in tiers.items():
            if name.upper() not in SourceTier.__members__:
                raise ValueError(f"unknown tier: {name}")
            if not all(_DOMAIN.fullmatch(domain) for domain in domains):
                raise ValueError(f"a domain under {name} is not a plain lowercase domain")
        return tiers

    def output_tokens(self, data_class: DataClass) -> int:
        return self.reddit_output_tokens if data_class is DataClass.REDDIT else self.public_output_tokens

    @property
    def domain_tiers(self) -> dict[str, SourceTier]:
        return {domain: SourceTier[name.upper()] for name, domains in self.tiers.items() for domain in domains}


def load_extract_settings(path: Path | str | None = None, env: Mapping[str, str] | None = None) -> ExtractSettings:
    """Read the settings. With no `env`, python-dotenv loads .env first (existing environment variables win)."""
    if env is None:
        load_dotenv()
        env = os.environ
    budget = {"reddit_tokens_per_search": load_llm_config().pool.reddit_tokens_per_search}
    where = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    try:
        values = yaml.safe_load(where.read_text(encoding="utf-8"))
        if not isinstance(values, dict):
            raise ValueError("not a mapping")
        settings = ExtractSettings.model_validate({**values, **budget})
    except (OSError, yaml.YAMLError, ValueError, TypeError) as exc:  # ValidationError is a ValueError
        log.warning("%s is not usable (%s); using the defaults", where.name, type(exc).__name__)
        settings = ExtractSettings.model_validate(budget)
    return _with_switch(settings, env)


def _with_switch(settings: ExtractSettings, env: Mapping[str, str]) -> ExtractSettings:
    raw = env.get(MAX_SOURCES_SWITCH, "").strip()
    if not raw:
        return settings
    try:
        return ExtractSettings.model_validate({**settings.model_dump(), "max_sources_per_search": int(raw)})
    except (ValueError, ValidationError):
        log.warning("%s is not usable, so it is ignored", MAX_SOURCES_SWITCH)
        return settings
