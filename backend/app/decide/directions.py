"""Which way is better on each criterion, for scoring stated values (US-18, P-20). Plain code, read from our config.

A fixed criterion has a direction when its entry in config/criteria_fallback.yaml states `direction:` (higher_is_better
or lower_is_better): a stated number or yes/no means something there, such as hours of battery or kilograms.
Criteria that are only opinions (build quality, comfort) leave it out, and stated values never score them. The
Criterion contract defaults the field to higher_is_better, so this file is read as written, not through the contract.

A generated criterion takes the direction of the fixed criterion it stands for: the first of its public_synonyms
targets (config/extract.yaml) that the rubric's category set has, as extract picks it. Anything else has none. The
category's set is found as app/criteria/fallback.py finds it (its own, else its family's from categories.yaml, else
generic), from the files themselves: decide imports no other stage.
A missing or unreadable file means no directions, never a failed search (build rule 9).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml  # type: ignore[import-untyped]  # PyYAML ships no type stubs

log = logging.getLogger(__name__)

Better = Literal["higher", "lower"]
CONFIG = Path(__file__).resolve().parents[2] / "config"
CRITERIA_PATH = CONFIG / "criteria_fallback.yaml"
SYNONYMS_PATH = CONFIG / "extract.yaml"
LEXICON_PATH = CONFIG / "categories.yaml"
GENERIC = "generic"  # the set for a category the lexicon does not know
_WORDS: dict[str, Better] = {"higher_is_better": "higher", "lower_is_better": "lower"}


def _read(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        log.warning("decide: %s could not be read (%s), so no directions", path.name, type(exc).__name__)
        return None


def _entries(section: object) -> Iterator[tuple[str, Mapping[str, Any]]]:
    """(set name, criterion entry) for each entry in a section of criteria_fallback.yaml, as written."""
    for name, criteria_set in section.items() if isinstance(section, Mapping) else ():
        items = criteria_set.get("criteria") if isinstance(criteria_set, Mapping) else None
        for item in items if isinstance(items, list) else ():
            if isinstance(item, Mapping) and isinstance(item.get("key"), str):
                yield str(name), item


def load_directions(path: Path | None = None) -> dict[str, Better]:
    """Each fixed criterion's stated direction. A key stated two ways in different sets gets none: it can't be told."""
    raw = _read(path or CRITERIA_PATH)
    found: dict[str, set[Better]] = {}
    for section in ("families", "categories"):
        for _, item in _entries(raw.get(section) if isinstance(raw, Mapping) else None):
            if item.get("direction") in _WORDS:
                found.setdefault(item["key"], set()).add(_WORDS[item["direction"]])
    mixed = sorted(key for key, ways in found.items() if len(ways) > 1)
    if mixed:
        log.warning("decide: criteria stated both ways get no direction: %s", ", ".join(mixed))
    return {key: next(iter(ways)) for key, ways in sorted(found.items()) if len(ways) == 1}


def load_synonyms(path: Path | None = None) -> dict[str, list[str]]:
    """config/extract.yaml's public_synonyms: a generated key, then the fixed criteria it may stand for, in order."""
    raw = _read(path or SYNONYMS_PATH)
    table = raw.get("public_synonyms") if isinstance(raw, Mapping) else None
    if not isinstance(table, Mapping):
        return {}
    return {str(k): [str(t) for t in v] for k, v in table.items() if isinstance(v, list)}


@lru_cache(maxsize=1)
def stated_directions() -> dict[str, Better]:
    return load_directions()


@lru_cache(maxsize=1)
def _synonyms() -> dict[str, list[str]]:
    return load_synonyms()


def _set_keys(section: object) -> dict[str, frozenset[str]]:
    """Each named set's criterion keys."""
    out: dict[str, set[str]] = {}
    for name, item in _entries(section):
        out.setdefault(name, set()).add(item["key"])
    return {name: frozenset(keys) for name, keys in out.items()}


@lru_cache(maxsize=1)
def _sets() -> tuple[dict[str, frozenset[str]], dict[str, frozenset[str]], dict[str, str]]:
    """The family sets, the category sets and each known category's family, read as written (decide imports no
    other stage, so this follows app/criteria/fallback.py's lookup itself; a test holds the two equal)."""
    raw = _read(CRITERIA_PATH)
    families = _set_keys(raw.get("families")) if isinstance(raw, Mapping) else {}
    categories = _set_keys(raw.get("categories")) if isinstance(raw, Mapping) else {}
    lexicon = _read(LEXICON_PATH)
    rows = lexicon.get("categories") if isinstance(lexicon, Mapping) else None
    family_of = {
        str(row["slug"]): str(row["family"])
        for row in (rows if isinstance(rows, list) else ())
        if isinstance(row, Mapping) and "slug" in row and "family" in row
    }
    return families, categories, family_of


def fixed_keys(category: str) -> frozenset[str]:
    """The keys of the fixed set this category uses: its own, else its family's, else generic's."""
    families, categories, family_of = _sets()
    if category in categories:
        return categories[category]
    family = family_of.get(category)
    return families.get(family if family in families else GENERIC, frozenset())


def direction_of(key: str, category: str) -> Better | None:
    """Which way is better on this criterion, or None when a stated value can't score it."""
    stated = stated_directions()
    if key in stated:
        return stated[key]
    fixed = fixed_keys(category)
    stand_in = next((target for target in _synonyms().get(key, ()) if target in fixed), None)
    return stated.get(stand_in) if stand_in is not None else None


def directions_for(keys: list[str], category: str) -> dict[str, Better]:
    """The rubric's criteria that stated values can score, with their direction."""
    out: dict[str, Better] = {}
    for key in keys:
        better = direction_of(key, category)
        if better is not None:
            out[key] = better
    return out
