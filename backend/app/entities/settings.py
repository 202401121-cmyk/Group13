"""The trained matcher's settings (US-15, S3-15): config/matcher.yaml, and the MATCHER switches in the environment.

MATCHER=model or fuzzy picks the scorer; unset, the file's `default` decides. MATCHER_FORCE_FAIL=1 makes the model
scorer fail on purpose, so a review can watch the fallback (criterion 6). A file that can't be read gives settings with
no revision, so the model is never used: fuzzy matching runs, as it did before S3-15.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

import yaml  # type: ignore[import-untyped]  # PyYAML ships no type stubs
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, ValidationError

log = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "matcher.yaml"
_COMMIT = re.compile(r"[0-9a-f]{40}")

Matcher = Literal["model", "fuzzy"]


class MatcherSettings(BaseModel):
    """config/matcher.yaml, plus what the environment chose (`use`, `force_fail`)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    repo: str = ""
    revision: str = ""  # an exact commit hash, never "main" (ml/MODELS.md); anything else means no model
    threshold: float = Field(default=1.0, ge=0.0, le=1.0)
    model_sha256: str = ""
    default: Matcher = "fuzzy"
    time_share_s: float = Field(default=3.0, gt=0.0, le=5.0)  # of the match stage's 5 seconds
    batch_size: int = Field(default=32, ge=1, le=512)
    max_length: int = Field(default=128, ge=8, le=512)
    use: Matcher = "fuzzy"
    force_fail: bool = False

    @property
    def pinned(self) -> bool:
        """Is the revision an exact commit? Only then can the model be downloaded and used."""
        return bool(_COMMIT.fullmatch(self.revision))


def load_matcher_settings(path: Path | str | None = None, env: Mapping[str, str] | None = None) -> MatcherSettings:
    """Read the settings. With no `env`, python-dotenv loads .env first (existing environment variables win)."""
    if env is None:
        load_dotenv()
        env = os.environ
    where = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    try:
        values = yaml.safe_load(where.read_text(encoding="utf-8"))
        if not isinstance(values, dict):
            raise ValueError("not a mapping")
        found = MatcherSettings.model_validate(values)
    except (OSError, yaml.YAMLError, ValueError, ValidationError, TypeError) as exc:
        log.warning("%s is not usable (%s); the matcher model is off", where.name, type(exc).__name__)
        found = MatcherSettings()
    use = matcher_choice(env.get("MATCHER"), found.default)
    force_fail = (env.get("MATCHER_FORCE_FAIL") or "").strip() == "1"
    return MatcherSettings.model_validate({**found.model_dump(), "use": use, "force_fail": force_fail})


def matcher_choice(raw: str | None, default: Matcher) -> Matcher:
    """MATCHER's value, or the default when it is unset or neither "model" nor "fuzzy" (said once, in the log)."""
    value = (raw or "").strip().lower()
    if value == "model":
        return "model"
    if value == "fuzzy":
        return "fuzzy"
    if value:
        log.warning('MATCHER is neither "model" nor "fuzzy"; using the default, %s', default)
    return default
