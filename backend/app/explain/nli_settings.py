"""The citation check's settings (US-22, S5-4): config/nli.yaml, and NLI=on|off in the environment.

The model is on unless NLI=off (S5-4b DECIDED 7: on 20 labelled evidence sentences it kept 15 of 16 supported and
caught 4 of 4 unsupported). A host without the memory for it sets NLI=off, as staging does (render.yaml, P-10). Off,
it never loads, and an evidence sentence stays only when it quotes a cited quote word for word, as when the model is
missing, failing or out of time (S5-4b DECIDED 5). A file that can't be read gives settings with no revision, so the
model is never loaded and that rule is used, with one log line (build rule 9).
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

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "nli.yaml"
_COMMIT = re.compile(r"[0-9a-f]{40}")
_SHA256 = re.compile(r"[0-9a-f]{64}")


class ReferencePair(BaseModel):
    """A premise and hypothesis, with the full model's entailment probability for them (onnx/model.onnx)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    premise: str = Field(min_length=1)
    hypothesis: str = Field(min_length=1)
    entailment: float = Field(ge=0.0, le=1.0)


class NliSettings(BaseModel):
    """config/nli.yaml, plus what the environment chose (`enabled`)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    repo: str = ""
    revision: str = ""  # an exact commit hash, never "main" (ml/MODELS.md card 6); anything else means no model
    model_file: str = ""
    model_sha256: str = ""
    tokenizer_file: str = "tokenizer.json"
    tokenizer_sha256: str = ""
    labels: int = Field(default=3, ge=2)  # the model's outputs: 0 contradiction, 1 entailment, 2 neutral
    entailment_index: int = Field(default=1, ge=0)
    max_length: int = Field(default=512, ge=16, le=512)  # tokens per premise and hypothesis together
    premise_tokens: int = Field(default=400, ge=8)  # a quote longer than this is split into overlapping chunks
    premise_overlap: int = Field(default=50, ge=0)
    threshold: float | None = Field(default=None, ge=0.0, le=1.0)  # tuned on the 40 tuning pairs; None: not yet
    time_limit_s: float = Field(default=5.0, gt=0.0, le=15.0)  # the check itself, inside explain's 15 s
    quoted_words: int = Field(default=6, ge=2)  # the fallback keeps a sentence holding this many quote words in a row
    reference: tuple[ReferencePair, ...] = ()  # checked on each machine before the model is used
    reference_tolerance: float = Field(default=0.02, gt=0.0, le=1.0)
    enabled: bool = True  # on unless NLI=off (S5-4b DECIDED 7)

    @property
    def pinned(self) -> bool:
        """An exact revision, a file, both files' sha256 and a tuned threshold: only then is the model used."""
        return bool(
            _COMMIT.fullmatch(self.revision)
            and self.model_file
            and _SHA256.fullmatch(self.model_sha256)
            and _SHA256.fullmatch(self.tokenizer_sha256)
            and self.threshold is not None
            and self.entailment_index < self.labels
            and self.premise_overlap < self.premise_tokens
        )


def nli_switch(env: Mapping[str, str]) -> bool:
    """NLI: "off" switches the check off; unset or "on" keeps it on (S5-4b DECIDED 7). Anything else keeps it on, with
    a log line."""
    raw = env.get("NLI", "").strip().lower()
    if raw in ("", "on"):
        return True
    if raw == "off":
        return False
    log.warning("explain: NLI=%r is not on or off; the citation check stays on", raw[:20])
    return True


def load_nli_settings(path: Path | str | None = None, env: Mapping[str, str] | None = None) -> NliSettings:
    """Read the settings. With no `env`, python-dotenv loads .env first (existing environment variables win)."""
    if env is None:
        load_dotenv()
        env = os.environ
    enabled = nli_switch(env)
    where = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    try:
        values = yaml.safe_load(where.read_text(encoding="utf-8"))
        if not isinstance(values, dict):
            raise ValueError("not a mapping")
        return NliSettings.model_validate({**values, "enabled": enabled})
    except (OSError, ValueError, yaml.YAMLError, ValidationError) as exc:
        log.warning(
            "explain: %s could not be read (%s); the citation check is not used", where.name, type(exc).__name__
        )
        return NliSettings(enabled=enabled)
