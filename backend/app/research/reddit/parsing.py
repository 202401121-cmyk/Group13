"""Lenient models for what the Reddit archives send back (US-12). Never contracts: they are cleaned into Sources.

Archive data is uneven (missing fields, numbers as strings, nulls), so these accept what they can and an item that
still does not fit is skipped, never a crash.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Annotated, Any, TypeVar

from pydantic import BaseModel, BeforeValidator, ConfigDict, ValidationError

M = TypeVar("M", bound=BaseModel)

MAX_COUNT = 2**63  # no real count is near this, and a bigger one breaks the sums made on it later


def _whole_number(value: Any) -> int | None:
    """A count we can trust, or None. An odd one must not lose the whole post."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return int(value) if abs(value) < MAX_COUNT else None  # also refuses NaN and 1e999, which JSON readers accept


LenientInt = Annotated[int | None, BeforeValidator(_whole_number)]


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
    score: LenientInt = None
    num_comments: LenientInt = None


class RawComment(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    link_id: str
    parent_id: str | None = None
    subreddit: str
    body: str | None = None
    author: str | None = None
    created_utc: float | None = None
    score: LenientInt = None


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
