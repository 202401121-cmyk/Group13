"""Lenient models for what the Reddit archives send back (US-12). Never contracts: they are cleaned into Sources.

Archive data is uneven (missing fields, numbers as strings, nulls), so these accept what they can and an item that
still does not fit is skipped, never a crash.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, ValidationError

M = TypeVar("M", bound=BaseModel)


class RawPost(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    subreddit: str
    title: str = ""
    selftext: str | None = None
    author: str | None = None
    created_utc: float | None = None
    over_18: bool | None = None
    removed_by_category: str | None = None


class RawComment(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    link_id: str
    parent_id: str | None = None
    subreddit: str
    body: str | None = None
    author: str | None = None
    created_utc: float | None = None


def parse_items(items: Iterable[Any], model: type[M]) -> list[M]:
    """Every item that fits `model`; the rest are dropped."""
    parsed: list[M] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            parsed.append(model.model_validate(item))
        except ValidationError:
            continue
    return parsed
