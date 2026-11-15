"""Which way is better on each criterion, for scoring stated values (US-18, P-20). Plain code, read from our config.

A fixed criterion has a direction when its entry in config/criteria_fallback.yaml states `direction:` (higher_is_better
or lower_is_better): a stated number or yes/no means something there, such as hours of battery or kilograms.
Criteria that are only opinions (build quality, comfort) leave it out, and stated values never score them. The
Criterion contract defaults the field to higher_is_better, so this file is read as written, not through the contract.

A generated criterion takes the direction of the fixed criterion it stands for, found by stand_in(): the same match
extract uses to ask public pages about it (US-14), so the two stages always agree. The category's set is found as
app/criteria/fallback.py finds it (its own, else its family's from categories.yaml, else generic), from the files
themselves: decide imports no other stage. A missing or unreadable file means no directions, never a failed search
(build rule 9).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Collection, Iterator, Mapping, Sequence
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
_TABLE_KEY = re.compile(r"[a-z][a-z0-9_]{1,59}")  # app/extract/settings.py's rule for a public_synonyms key
# Words that only name a unit: "capacity_litres" names capacity, "power_watts" names power. ("min" is not one: it is
# as often "minimum", which turns a criterion around.)
UNIT_WORDS = frozenset(
    {"l", "litre", "litres", "liter", "liters", "ml", "w", "watt", "watts", "kw", "mah", "v", "volt", "volts"}
    | {"h", "hr", "hrs", "hour", "hours", "mins", "minute", "minutes", "rpm"}
    | {"gb", "tb", "mb", "kg", "g", "gram", "grams", "inch", "inches", "cm", "mm", "db", "hz"}
    | {"c", "celsius", "degree", "degrees", "day", "days", "week", "weeks", "month", "months", "year", "years"}
)
# Words that turn a criterion around or point at something else: "power_consumption" is not power (less is better),
# "compact_size" is not capacity, "exterior_temperature" is not how hot it cooks, "max_child_weight" is a load limit,
# "cost_effectiveness" is value for money. A key holding one never matches by its words, unless the word is part of the
# fixed criterion's own key ("after_sales_service", "running_cost").
BLOCKING_WORDS = frozenset(
    {"off", "consumption", "saving", "saver", "compact", "small", "mini", "exterior", "outer", "outside", "surface"}
    | {"body", "min", "minimum", "lowest", "child", "cost", "price", "ratio", "customer", "after", "sales"}
)
# One-word table entries too broad to read inside a longer key: "grinding_performance" is not processor speed. They
# still match a key that is exactly the word.
BROAD_ENTRIES = frozenset({"performance", "value", "comfort", "fit", "durability", "display", "multitasking"})
BROAD_ENTRIES |= {"portability"}


def _words(key: str) -> list[str]:
    return [word for word in key.casefold().split("_") if word and word not in UNIT_WORDS]


def _only(candidates: Mapping[str, int]) -> str | None:
    """The one candidate with the most matching words, or None when none or a tie: never a guess."""
    if not candidates:
        return None
    most = max(candidates.values())
    best = [key for key, count in candidates.items() if count == most]
    return best[0] if len(best) == 1 else None


def _same_words_in_set(named: Mapping[str, int], fixed: Collection[str]) -> str | None:
    """Of keys tied on the same words in another order (comfort_and_fit, fit_and_comfort), the one this set has."""
    if not named:
        return None
    most = max(named.values())
    tied = [key for key, count in named.items() if count == most]
    if len({frozenset(_words(key)) for key in tied}) != 1:
        return None  # different criteria, named equally
    in_set = [key for key in tied if key in fixed]
    return in_set[0] if len(in_set) == 1 else None


def stand_in(
    key: str,
    fixed: Collection[str],
    synonyms: Mapping[str, Sequence[str]],
    vocabulary: Collection[str] | None = None,
) -> str | None:
    """The fixed criterion of this set a generated key stands for, matched in code (US-14, D-51), or None.

    1. An exact public_synonyms entry wins: its first target the set has, else none, as the table named the key.
    2. Else the fixed criterion whose words all appear among the key's words, unit words left out ("capacity_litres"
       -> capacity, "cooking_temperature_range" -> temperature_range), looked for among every set's criteria
       (`vocabulary`, by default all of them), the one with the most words. "storage_space" names storage_space, a
       smartphone criterion, so a laptop search doesn't stretch it to memory_and_storage.
    3. Else the one-word table entries among the key's words ("basket_volume" -> volume -> capacity).
    It stays out, rather than being guessed, when two criteria are named equally, when a one-word entry points
    elsewhere than the criterion the words name, when a word turns it around (BLOCKING_WORDS), or when what it names
    isn't in this set.
    """
    if key in synonyms:
        return next((target for target in synonyms[key] if target in fixed), None)
    words = _words(key)
    if not words:
        return None
    known = set(fixed) | set(all_fixed_keys() if vocabulary is None else vocabulary)
    named = {f: len(_words(f)) for f in sorted(known) if _words(f) and set(_words(f)) <= set(words)}
    best = _only(named) or _same_words_in_set(named, fixed)
    if named and best is None:
        return None  # two criteria named equally
    own = set(_words(best)) if best is not None else set()
    if any(word in BLOCKING_WORDS and word not in own for word in words):
        return None
    entries = [synonyms[w] for w in words if w in synonyms and w not in BROAD_ENTRIES and w not in own]
    if best is not None:
        if any(best not in targets for targets in entries):
            return None  # its words name two things
        return best if best in fixed else None
    picks = {next((target for target in targets if target in fixed), None) for targets in entries}
    return picks.pop() if len(picks) == 1 and None not in picks else None


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
    """config/extract.yaml's public_synonyms: a generated key, then the fixed criteria it may stand for, in order.

    Checked as app/extract/settings.py checks it: one bad entry and extract runs with no table at all, so decide reads
    none either, and the two stages still map every key the same way."""
    raw = _read(path or SYNONYMS_PATH)
    table = raw.get("public_synonyms") if isinstance(raw, Mapping) else None
    if not isinstance(table, Mapping):
        return {}
    out: dict[str, list[str]] = {}
    for key, targets in table.items():
        names = [key, *targets] if isinstance(targets, list) and targets else None
        if names is None or not all(isinstance(n, str) and _TABLE_KEY.fullmatch(n) for n in names):
            log.warning("decide: public_synonyms has a bad entry, so no synonyms, as extract")
            return {}
        out[str(key)] = [str(t) for t in targets]
    return out


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


@lru_cache(maxsize=1)
def all_fixed_keys() -> frozenset[str]:
    """Every fixed criterion of every set: what a generated key's words may name."""
    families, categories, _ = _sets()
    return frozenset().union(*families.values(), *categories.values())


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
    fixed = fixed_keys(category)
    if key in fixed:
        return stated.get(key)
    found = stand_in(key, fixed, _synonyms())  # as extract asks public pages about it
    if found is not None:
        return stated.get(found)
    return stated.get(key)  # a fixed key of another set, carried as written to Reddit only


def directions_for(keys: list[str], category: str) -> dict[str, Better]:
    """The rubric's criteria that stated values can score, with their direction."""
    out: dict[str, Better] = {}
    for key in keys:
        better = direction_of(key, category)
        if better is not None:
            out[key] = better
    return out
