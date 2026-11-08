"""The research stage's settings (US-12, US-27), from config/research.yaml and two environment switches.

RESEARCH_CACHE=off   read and keep nothing in the 24-hour research cache
RESEARCH_PAGES=off   fetch no review pages; their search results stay as snippets

A bad file never stops a search (build rule 9): it is logged by name and the defaults here are used. The Serper key
is not a setting: app/research/stage.py reads it from the environment and hands it only to the Serper source.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping
from pathlib import Path

import yaml  # type: ignore[import-untyped]  # PyYAML ships no type stubs
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

log = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "research.yaml"
MAX_SOURCES = 60  # ResearchResult.sources may hold no more than this

DEFAULT_REVIEW_DOMAINS = (
    "91mobiles.com",
    "smartprix.com",
    "digit.in",
    "gadgets360.com",
    "notebookcheck.net",
    "gsmarena.com",
    "rtings.com",
)
# The only uses a query may hold (config/research.yaml can change them). Intake keeps no list: its use case is free
# text.
DEFAULT_KNOWN_USES = (
    "coding",
    "programming",
    "gaming",
    "travel",
    "office",
    "student",
    "students",
    "video editing",
    "photo editing",
    "music",
    "running",
    "gym",
    "calls",
)
_KNOWN_USE = re.compile(r"[a-z]+(?: [a-z]+)*")


class ProviderCap(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    per_search: int = Field(ge=0)
    per_day: int = Field(ge=0)
    total: int | None = Field(default=None, ge=0)


def _default_caps() -> dict[str, ProviderCap]:
    return {
        "serper": ProviderCap(per_search=6, per_day=60, total=2400),
        "reddit_archive": ProviderCap(per_search=10, per_day=500),
        "pages": ProviderCap(per_search=24, per_day=200),
    }


class ResearchSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    pool_size: int = Field(default=6, ge=1, le=32)
    deadline_s: float = Field(default=36.0, gt=0, lt=40)  # the runner gives the stage 40 seconds
    max_reddit_threads: int = Field(default=20, ge=0)
    max_web_results: int = Field(default=20, ge=0)
    max_review_pages: int = Field(default=12, ge=0)
    max_pages_per_domain: int = Field(default=3, ge=0)
    threads_with_comments: int = Field(default=6, ge=0)
    min_page_chars: int = Field(default=500, ge=1)
    max_page_bytes: int = Field(default=1_500_000, ge=1)
    serper_interval_s: float = Field(default=0.2, ge=0)
    page_interval_s: float = Field(default=2.0, ge=0)
    max_crawl_delay_s: float = Field(default=10.0, ge=0)
    caps: dict[str, ProviderCap] = Field(default_factory=_default_caps)
    review_domains: tuple[str, ...] = DEFAULT_REVIEW_DOMAINS
    known_uses: tuple[str, ...] = DEFAULT_KNOWN_USES
    cache_on: bool = True
    pages_on: bool = True

    @model_validator(mode="after")
    def _check(self) -> ResearchSettings:
        total = self.max_reddit_threads + self.max_web_results + self.max_review_pages
        if total > MAX_SOURCES:
            raise ValueError(f"the source caps add up to {total}, over the contract's limit of {MAX_SOURCES}")
        if not all(_KNOWN_USE.fullmatch(use) for use in self.known_uses):
            raise ValueError("a known use is lowercase words only, so it can hold no search operator")
        return self


def load_research_settings(path: Path | str | None = None, env: Mapping[str, str] | None = None) -> ResearchSettings:
    """Read the settings. With no `env`, python-dotenv loads .env first (existing environment variables win)."""
    if env is None:
        load_dotenv()
        env = os.environ
    switches = {"cache_on": _on(env.get("RESEARCH_CACHE")), "pages_on": _on(env.get("RESEARCH_PAGES"))}
    where = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    try:
        values = yaml.safe_load(where.read_text(encoding="utf-8"))
        if not isinstance(values, dict):
            raise ValueError("not a mapping")
        return ResearchSettings.model_validate({**values, **switches})
    except (OSError, yaml.YAMLError, ValueError, ValidationError, TypeError) as exc:
        log.warning("%s is not usable (%s); using the defaults", where.name, type(exc).__name__)
        return ResearchSettings.model_validate(switches)


def _on(raw: str | None) -> bool:
    return (raw or "").strip().lower() != "off"
