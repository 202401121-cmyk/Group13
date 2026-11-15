"""Who is signed in, and their saved searches (US-30, Sprint 4).

A guest is never linked to an account (D-36). No contract here, or anywhere in app/contracts/, ever holds an email:
the account is known by its id and an optional display name only.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import Field, model_validator

from .common import AwareDatetime, Contract, Slug

MAX_SAVED_SEARCHES_PAGE = 50


class MeResponse(Contract):
    """Who the browser is signed in as. A guest has no id and no name."""

    signed_in: bool
    user_id: UUID | None = None
    display_name: str | None = Field(default=None, min_length=1, max_length=80)

    @model_validator(mode="after")
    def _check(self) -> MeResponse:
        if self.signed_in and self.user_id is None:
            raise ValueError("a signed-in answer names the user's id")
        if not self.signed_in and (self.user_id is not None or self.display_name is not None):
            raise ValueError("a guest has no user id and no display name (D-36)")
        return self


class SavedSearchSummary(Contract):
    """One saved search in a signed-in shopper's list: what they asked, when, and the product that came first."""

    id: UUID
    created_at: AwareDatetime
    request_text: str = Field(min_length=1, max_length=500, description="What the shopper typed")
    category: Slug | None = None
    top_product: str | None = Field(
        default=None, min_length=1, max_length=150, description="The best match's name; None when nothing ranked"
    )


class SavedSearchList(Contract):
    """One page of saved searches, newest first."""

    items: list[SavedSearchSummary] = Field(default_factory=list, max_length=MAX_SAVED_SEARCHES_PAGE)
    next_cursor: str | None = Field(
        default=None, min_length=1, max_length=200, description="Ask with it for the next page; None on the last"
    )
