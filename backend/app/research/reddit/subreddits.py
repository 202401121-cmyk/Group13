"""The subreddits we search for each category (US-12), from config/reddit_subreddits.yaml.

Checked when it loads, so a typo stops the app at startup rather than sending a bad request mid-search.
"""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from pathlib import Path

import yaml  # type: ignore[import-untyped]  # PyYAML ships no type stubs
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.intake.lexicon import load_lexicon

from .base import valid_subreddit

DEFAULT_SUBREDDITS_PATH = Path(__file__).resolve().parents[3] / "config" / "reddit_subreddits.yaml"
MAX_PER_SEARCH = 4  # one request each, out of the search's budget of 14 (US-12)


class SubredditConfigError(ValueError):
    """config/reddit_subreddits.yaml is missing, unreadable or breaks a rule."""


def _names(values: list[str]) -> list[str]:
    bad = [v for v in values if not valid_subreddit(v)]
    if bad:
        raise ValueError(f"not a subreddit name (2-21 letters, digits or _ and no r/): {bad}")
    if len({v.lower() for v in values}) != len(values):
        raise ValueError(f"a subreddit is listed twice: {values}")
    return values


class SharedGroup(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    subreddits: list[str] = Field(min_length=1)
    families: list[str] = Field(min_length=1)

    @field_validator("subreddits")
    @classmethod
    def _check_names(cls, value: list[str]) -> list[str]:
        return _names(value)


class SubredditConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    checked: date
    categories: dict[str, list[str]]
    shared: list[SharedGroup] = Field(default_factory=list)

    @field_validator("categories")
    @classmethod
    def _check_category_names(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        for names in value.values():
            if not names:
                raise ValueError("every category needs at least one subreddit")
            _names(names)
        return value

    @model_validator(mode="after")
    def _check_against_lexicon(self) -> SubredditConfig:
        lexicon = load_lexicon()
        known = set(lexicon.categories)
        unknown = sorted(set(self.categories) - known)
        if unknown:
            raise ValueError(f"not a category in config/categories.yaml: {unknown}")
        missing = sorted(known - set(self.categories))
        if missing:
            raise ValueError(f"every category needs subreddits; missing: {missing}")
        families = {entry.family for entry in lexicon.data.categories}
        bad = sorted({f for g in self.shared for f in g.families} - families)
        if bad:
            raise ValueError(f"not a product family in config/categories.yaml: {bad}")
        return self

    def for_category(self, category: str) -> list[str]:
        """The category's own subreddits, then the shared ones for its family, each once, in that order."""
        family = load_lexicon().family_of(category)
        names = list(self.categories.get(category, []))
        for group in self.shared:
            if family in group.families:
                names.extend(group.subreddits)
        unique: list[str] = []
        for name in names:
            if name.lower() not in {u.lower() for u in unique}:
                unique.append(name)
        return unique


def load_subreddits(path: Path | str | None = None) -> SubredditConfig:
    """Read and check config/reddit_subreddits.yaml (or `path`). Raises SubredditConfigError with the reason."""
    return _load(str(path) if path is not None else str(DEFAULT_SUBREDDITS_PATH))


@lru_cache(maxsize=4)
def _load(path: str) -> SubredditConfig:
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise SubredditConfigError(f"cannot read {path}: {exc}") from exc
    try:
        return SubredditConfig.model_validate(raw)
    except ValidationError as exc:
        raise SubredditConfigError(f"invalid subreddit list {path}:\n{exc}") from exc


def subreddits_for(category: str, limit: int = MAX_PER_SEARCH) -> list[str]:
    """The first `limit` subreddits to search for a category; none for a category we have no list for."""
    return load_subreddits().for_category(category)[:limit]
