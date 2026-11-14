"""The extract stage's settings (US-14): config/extract.yaml, one environment switch, and the pool's Reddit budget.

What kind of site each domain is comes from the one table of known sites, config/trust.yaml (US-16, S3-7), which
verify reads too, so the two stages always agree on a site's tier.
"""

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
_KEY = re.compile(r"[a-z][a-z0-9_]{1,59}")
PRICE_DEFINITION = "The price in Indian rupees that a store in India asks for it, as a number."


class ExtractSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    reddit_output_tokens: int = Field(default=1200, ge=100, le=3500)
    public_output_tokens: int = Field(default=2000, ge=100, le=3500)
    max_sources_per_search: int = Field(default=40, ge=1, le=100)
    reddit_tokens_per_search: int = Field(default=18_000, gt=0)
    book_real_use: bool = Field(default=False, description="Count real use in the Reddit budget, as the pool does")
    # What a public prompt may carry besides fixed criteria (US-14, US-46). Only these keys and our own definitions go:
    # never a generated key or the shopper's words. Empty tables keep public prompts to the fixed criteria alone.
    public_synonyms: dict[str, list[str]] = Field(
        default_factory=dict,
        description="A generated criterion key, then the fixed criteria it may stand for, in order",
    )
    public_spec_keys: dict[str, str] = Field(
        default_factory=dict, description="Spec keys a hard rule may name, each with our own definition"
    )
    price_definition: str = Field(default=PRICE_DEFINITION, min_length=10, max_length=200)

    @field_validator("public_synonyms")
    @classmethod
    def _synonyms_are_keys(cls, table: dict[str, list[str]]) -> dict[str, list[str]]:
        for key, targets in table.items():
            if not targets or not all(_KEY.fullmatch(k) for k in [key, *targets]):
                raise ValueError(f"public_synonyms: {key} needs one or more lowercase keys")
        return table

    @field_validator("public_spec_keys")
    @classmethod
    def _spec_keys_are_defined(cls, table: dict[str, str]) -> dict[str, str]:
        for key, definition in table.items():
            if not _KEY.fullmatch(key) or not 10 <= len(definition) <= 200:
                raise ValueError(
                    f"public_spec_keys: {key} needs a lowercase key and a definition of 10 to 200 characters"
                )
        return table

    def output_tokens(self, data_class: DataClass) -> int:
        return self.reddit_output_tokens if data_class is DataClass.REDDIT else self.public_output_tokens

    @property
    def domain_tiers(self) -> dict[str, SourceTier]:
        """Each known site's tier, from config/trust.yaml. With that file broken, none: research's tier is used."""
        from app.verify import trust  # here, because app.verify.trust imports this package's tiers

        return trust.default_trust_table().domain_tiers()


def load_extract_settings(path: Path | str | None = None, env: Mapping[str, str] | None = None) -> ExtractSettings:
    """Read the settings. With no `env`, python-dotenv loads .env first (existing environment variables win)."""
    if env is None:
        load_dotenv()
        env = os.environ
    pool = load_llm_config().pool
    budget = {"reddit_tokens_per_search": pool.reddit_tokens_per_search, "book_real_use": pool.book_real_use}
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
