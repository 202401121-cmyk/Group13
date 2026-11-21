"""Writes measured values into config/nli.yaml in place (US-22): one key at a time, its comments and the rest of the
file as they were, so a diff shows only what was measured."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]  # PyYAML ships no type stubs

from app.explain.nli_settings import DEFAULT_CONFIG_PATH


def set_keys(text: str, values: Mapping[str, Any]) -> str:
    """The YAML text with each top-level key's value replaced. A scalar keeps the line's own comment; a list becomes
    a block under its key. Raises KeyError for a key the file doesn't have."""
    lines = text.split("\n")
    for key, value in values.items():
        at = next((i for i, line in enumerate(lines) if re.match(rf"{re.escape(key)}:(\s|$)", line)), None)
        if at is None:
            raise KeyError(key)
        end = at + 1
        while end < len(lines) and lines[end].startswith(("  ", "- ")) and lines[end].strip():
            end += 1  # an earlier block under this key
        comment = ""
        if "  #" in lines[at]:
            comment = "  #" + lines[at].split("  #", 1)[1]
        if isinstance(value, list):
            block = yaml.safe_dump(value, allow_unicode=True, sort_keys=False, width=110, default_flow_style=False)
            body = ["  " + row for row in block.rstrip("\n").split("\n")] if value else []
            head = f"{key}:{comment}" if value else f"{key}: []{comment}"
            lines[at:end] = [head, *body]
        else:
            lines[at:end] = [f"{key}: {json.dumps(value, ensure_ascii=False)}{comment}"]
    return "\n".join(lines)


def write_config(values: Mapping[str, Any], path: Path = DEFAULT_CONFIG_PATH) -> None:
    text = set_keys(path.read_text(encoding="utf-8"), values)
    yaml.safe_load(text)  # still YAML, or nothing is written
    path.write_text(text, encoding="utf-8", newline="\n")
