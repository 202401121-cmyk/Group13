"""A signed-in shopper's own data (US-30): their profile and their saved searches, read through the backend.

Every read names the user, and every query filters on that user's id, as well as row level security doing so for the
Data API (S4-9). A guest has no account here: their rows keep user_id null and are never read by these (D-36).

- **ensure_profile:** made at first sign-in, an insert that does nothing if the profile exists; answers the
  display name. UserGone when the token's user is no longer in auth.users (deleted since the token was issued).
- **saved_searches:** a page of summaries, newest first (by created_at, then id), after a cursor.
- **saved_search:** one saved result, only the owner's; None for any other id, so an id is never confirmed.

Each raises AccountsUnavailable when the database is off, down, slow or skipped by the circuit breaker, and the user
routes answer 503 then. A search never needs any of this (build rule 10).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

JsonObject = dict[str, Any]


class AccountsUnavailable(Exception):
    """The database is off, down or slow: the user routes answer 503. Never raised by a search."""


class UserGone(Exception):
    """The token's user id is not in auth.users: the account was deleted after the token was issued."""


@dataclass(frozen=True)
class PageAfter:
    """Where the last page ended: the next page starts after this search, newest first."""

    created_at: datetime
    id: UUID


@dataclass(frozen=True)
class SavedSearchRow:
    """What the list needs from one saved search, before the route shapes it into a SavedSearchSummary."""

    id: UUID
    created_at: datetime
    request_text: str | None
    category: str | None
    top_product: str | None  # the first-ranked product's name, only when the ranking scored it


class AccountStore(Protocol):
    def ensure_profile(self, user_id: UUID) -> str | None: ...

    def saved_searches(self, user_id: UUID, after: PageAfter | None, limit: int) -> list[SavedSearchRow]: ...

    def saved_search(self, user_id: UUID, search_id: UUID) -> JsonObject | None: ...


class NullAccountStore:
    """The database is off (no DATABASE_URL): there is nowhere to keep an account, so every read is unavailable."""

    def ensure_profile(self, user_id: UUID) -> str | None:
        raise AccountsUnavailable("the database is off")

    def saved_searches(self, user_id: UUID, after: PageAfter | None, limit: int) -> list[SavedSearchRow]:
        raise AccountsUnavailable("the database is off")

    def saved_search(self, user_id: UUID, search_id: UUID) -> JsonObject | None:
        raise AccountsUnavailable("the database is off")


def _at(value: Any, *path: str | int) -> Any:
    """value[path...], or None as soon as a step is missing or of the wrong kind."""
    for step in path:
        if isinstance(step, int):
            value = value[step] if isinstance(value, list) and len(value) > step else None
        else:
            value = value.get(step) if isinstance(value, dict) else None
    return value


def saved_search_row(search_id: UUID, created_at: datetime, result: Any, rubric: Any) -> SavedSearchRow:
    """The summary fields of one saved search, read the way the Postgres store's query reads them."""
    text, category = _at(result, "request", "raw_query"), _at(rubric, "category")
    first = _at(result, "decision", "ranked", 0)
    score = _at(first, "score")
    top: str | None = None
    if isinstance(score, int | float) and not isinstance(score, bool):  # an unscored list ranked nothing (1.8.0)
        wanted = _at(first, "product_id")
        products = _at(result, "products")
        for summary in products if isinstance(products, list) else []:
            if _at(summary, "product", "id") == wanted:
                name = _at(summary, "product", "name")
                top = name if isinstance(name, str) else None
                break
    return SavedSearchRow(
        id=search_id,
        created_at=created_at,
        request_text=text if isinstance(text, str) else None,
        category=category if isinstance(category, str) else None,
        top_product=top,
    )


def newest_first(rows: list[SavedSearchRow], after: PageAfter | None) -> list[SavedSearchRow]:
    """Newest first by (created_at, id), as Postgres orders them, starting after `after`."""
    ordered = sorted(rows, key=lambda row: (row.created_at, row.id), reverse=True)
    if after is None:
        return ordered
    return [row for row in ordered if (row.created_at, row.id) < (after.created_at, after.id)]
