"""Preference memory's settings (US-06): config/memory.yaml, and MEMORY=on|off in the environment.

MEMORY=off switches memory off (staging can, P-40): nothing is recalled or saved, and the interview runs as it did
before US-06. Unset, or "on", memory is on. A file that can't be read gives settings with no revision, so the model is
never loaded and every recall is empty, with one log line (build rule 10).
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping
from pathlib import Path

import yaml  # type: ignore[import-untyped]  # PyYAML ships no type stubs
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, ValidationError

log = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "memory.yaml"
_COMMIT = re.compile(r"[0-9a-f]{40}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
MAX_REMEMBERED = 5  # InterviewState.remembered holds at most 5


class MemorySettings(BaseModel):
    """config/memory.yaml, plus what the environment chose (`enabled`)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    repo: str = ""
    revision: str = ""  # an exact commit hash, never "main" (ml/MODELS.md); anything else means no model
    model_file: str = "onnx/model.onnx"
    model_sha256: str = ""
    tokenizer_file: str = "tokenizer.json"
    tokenizer_sha256: str = ""
    reference_file: str = "memory_reference.json"  # in config/: the full model's vectors for a few sentences
    dimensions: int = Field(default=384, ge=1)
    max_length: int = Field(default=256, ge=8, le=512)  # tokens per text, as the model was trained
    check_min_cosine: float = Field(default=0.99, gt=0.0, le=1.0)
    top_k: int = Field(default=MAX_REMEMBERED, ge=1, le=MAX_REMEMBERED)
    min_score: float = Field(default=0.7, gt=0.0, le=1.0)
    half_life_days: float = Field(default=90.0, gt=0.0)
    time_share_s: float = Field(default=1.5, gt=0.0, le=5.0)  # of the interview turn's 5 seconds
    save_wait_s: float = Field(default=30.0, gt=0.0, le=120.0)  # a save, after the reply has gone, waits this long
    enabled: bool = True

    @property
    def pinned(self) -> bool:
        """An exact revision and both files' sha256: only then is the model downloaded and used."""
        return bool(
            _COMMIT.fullmatch(self.revision)
            and _SHA256.fullmatch(self.model_sha256)
            and _SHA256.fullmatch(self.tokenizer_sha256)
        )


def memory_switch(env: Mapping[str, str]) -> bool:
    """MEMORY: "off" switches memory off; unset or "on" keeps it on. Anything else keeps it on, with a log line."""
    raw = env.get("MEMORY", "").strip().lower()
    if raw in ("", "on"):
        return True
    if raw == "off":
        return False
    log.warning("memory: MEMORY=%r is not on or off; memory stays on", raw[:20])
    return True


def load_memory_settings(path: Path | str | None = None, env: Mapping[str, str] | None = None) -> MemorySettings:
    """Read the settings. With no `env`, python-dotenv loads .env first (existing environment variables win)."""
    if env is None:
        load_dotenv()
        env = os.environ
    enabled = memory_switch(env)
    where = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    try:
        values = yaml.safe_load(where.read_text(encoding="utf-8"))
        if not isinstance(values, dict):
            raise ValueError("not a mapping")
        return MemorySettings.model_validate({**values, "enabled": enabled})
    except (OSError, ValueError, yaml.YAMLError, ValidationError) as exc:
        log.warning("memory: %s could not be read (%s); nothing is recalled", where.name, type(exc).__name__)
        return MemorySettings(enabled=enabled)
