"""The matcher's config for S3-15 (backend/config/matcher.yaml) agrees with the recorded results (US-15). No network."""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml  # type: ignore[import-untyped]  # PyYAML ships no type stubs; the CI ml job installs it with backend[dev]

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "backend" / "config" / "matcher.yaml"
RESULTS = ROOT / "ml" / "results"
FULL_REVISION = re.compile(r"[0-9a-f]{40}")


def _config() -> dict[str, object]:
    data = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _results(name: str) -> dict[str, object]:
    data = json.loads((RESULTS / name).read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def test_the_revision_is_empty_or_a_full_commit_hash_never_a_branch() -> None:
    revision = _config()["revision"]
    assert isinstance(revision, str), revision  # an empty value must be "", not false, 0 or a missing key
    assert revision != "main"
    assert revision == "" or FULL_REVISION.fullmatch(revision), revision


def test_a_set_revision_has_a_repository() -> None:
    config = _config()
    if config["revision"]:
        repo = config["repo"]
        assert isinstance(repo, str) and repo.strip(), repo


def test_the_threshold_is_the_one_chosen_on_dev() -> None:
    threshold = _results("matcher_v1.json")["threshold"]
    assert isinstance(threshold, dict)
    assert _config()["threshold"] == threshold["value"]


def test_the_onnx_record_names_the_same_revision() -> None:
    recorded = _results("matcher_v1_onnx.json")["huggingface_revision"]
    assert (recorded or "") == (_config().get("revision") or "")
