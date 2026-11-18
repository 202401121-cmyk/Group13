"""Saved criteria, so each category and use case is generated only once (US-03, build rule 7).

A local file cache for now; US-30 can move it to Supabase behind the same CriteriaStore interface. Only
generated sets are saved: a fallback set is never stored, so the next search tries the LLM again. A broken
file is ignored and logged, never a crash, and a failed write never stops a search (build rule 10).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from app.contracts import CriteriaSet

log = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = Path(__file__).resolve().parents[2] / ".cache" / "criteria"
_FORMAT = 2  # 2 since S4-22: definitions checked for plain words; a set saved before it is made again

StoreKey = tuple[str, str | None]


def normalise_use_case(use_case: str | None) -> str | None:
    """Lowercase, trimmed, single spaces; an empty use case means none ("Gaming  " == "gaming")."""
    if use_case is None:
        return None
    return " ".join(use_case.lower().split()) or None


def store_key(category: str, use_case: str | None) -> StoreKey:
    return category, normalise_use_case(use_case)


class CriteriaStore(Protocol):
    """Where generated criteria sets are kept between searches."""

    def get(self, category: str, use_case: str | None) -> CriteriaSet | None: ...

    def put(self, criteria_set: CriteriaSet) -> None: ...


class InMemoryCriteriaStore:
    """For tests: the same rules as the file store, kept in a dict."""

    def __init__(self) -> None:
        self.sets: dict[StoreKey, CriteriaSet] = {}

    def get(self, category: str, use_case: str | None) -> CriteriaSet | None:
        return self.sets.get(store_key(category, use_case))

    def put(self, criteria_set: CriteriaSet) -> None:
        if criteria_set.source == "generated":
            self.sets[store_key(criteria_set.category, criteria_set.use_case)] = criteria_set


class FileCriteriaStore:
    """One JSON file per category and use case under backend/.cache/criteria/ (UTF-8, LF line endings)."""

    def __init__(self, root: Path | str = DEFAULT_CACHE_DIR) -> None:
        self.root = Path(root)

    def path_for(self, category: str, use_case: str | None) -> Path:
        _, use = store_key(category, use_case)
        if use is None:
            return self.root / f"{category}.json"
        # The readable part is for people browsing the folder; the hash keeps different use cases apart.
        readable = re.sub(r"[^a-z0-9]+", "-", use).strip("-")[:40] or "use"
        digest = hashlib.sha256(use.encode("utf-8")).hexdigest()[:8]
        return self.root / f"{category}--{readable}-{digest}.json"

    def get(self, category: str, use_case: str | None) -> CriteriaSet | None:
        path = self.path_for(category, use_case)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("format") != _FORMAT or data.get("key") != list(store_key(category, use_case)):
                raise ValueError("the file is for a different key or format")
            saved = CriteriaSet.model_validate(data["criteria_set"])
            if saved.source != "generated":
                raise ValueError(f"only generated sets are stored, found {saved.source!r}")
        except (OSError, ValueError, KeyError, TypeError, AttributeError, ValidationError) as exc:
            # json.JSONDecodeError and ValidationError are ValueErrors; the cause is logged, never raised.
            log.warning("criteria cache: ignoring %s (%s)", path.name, type(exc).__name__)
            return None
        return saved

    def put(self, criteria_set: CriteriaSet) -> None:
        if criteria_set.source != "generated":
            return  # a fallback set is never stored, so the next search tries the LLM again
        path = self.path_for(criteria_set.category, criteria_set.use_case)
        envelope = {
            "format": _FORMAT,
            "key": list(store_key(criteria_set.category, criteria_set.use_case)),
            "criteria_set": criteria_set.model_dump(mode="json"),
        }
        text = json.dumps(envelope, indent=2, ensure_ascii=False) + "\n"
        tmp = path.with_name(path.name + ".tmp")
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            tmp.write_text(text, encoding="utf-8", newline="\n")
            os.replace(tmp, path)  # atomic: a reader never sees half a file
        except OSError as exc:
            log.warning("criteria cache: could not save %s (%s)", path.name, type(exc).__name__)
