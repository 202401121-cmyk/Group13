"""The user routes (US-30): who is signed in, and their own saved searches.

- GET /api/me: MeResponse. No token is a guest (signed_in false); a token that isn't accepted answers 401. At first
  sign-in the profile row is made (an insert that does nothing if it exists).
- GET /api/me/searches: SavedSearchList, newest first, PAGE_SIZE a page, with an opaque cursor for the next.
- GET /api/me/searches/{search_id}: the saved SearchResult, only the owner's. Any other id answers 404, never 403, so
  an id is never confirmed.

Both search routes need a signed-in user (401 without one). Every query filters on the token's user id, as well as row
level security doing so for the Data API (S4-9). The database being off, down or slow answers 503, and searches still
finish (build rule 10). Messages are plain sentences; nothing logs a token, a user id or an email.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import ValidationError

from app import db
from app.auth import Caller, SignedInUser, who_is_asking
from app.contracts import MeResponse, SavedSearchList, SavedSearchSummary, SearchRequest, SearchResult

log = logging.getLogger(__name__)

PAGE_SIZE = 20
REQUEST_TEXT_CHARS = 500  # SavedSearchSummary.request_text; a saved request holds up to 1,000
TOP_PRODUCT_CHARS = 150  # SavedSearchSummary.top_product
MAX_CURSOR_CHARS = 200  # SavedSearchList.next_cursor

SIGN_IN_AGAIN = "Your sign-in has expired or isn't valid. Please sign in again."
SIGN_IN_FIRST = "Please sign in to see your saved searches."
SIGN_IN_UNAVAILABLE = "We can't check sign-ins right now. Please try again in a minute."
ACCOUNT_UNAVAILABLE = "Your account can't be reached right now. Searching still works; please try again in a minute."
NOT_FOUND = "We couldn't find that saved search."
BAD_CURSOR = "That page link doesn't work. Please start again from the first page."

router = APIRouter(prefix="/api/me", tags=["account"])


def get_account_store() -> db.AccountStore:
    return db.default_account_store()


Accounts = Annotated[db.AccountStore, Depends(get_account_store)]
Asking = Annotated[Caller, Depends(who_is_asking)]
_ERRORS: dict[int | str, dict[str, str]] = {
    401: {"description": SIGN_IN_AGAIN},
    503: {"description": ACCOUNT_UNAVAILABLE},
}


# --- routes -------------------------------------------------------------------------------------------------------


@router.get("", response_model=MeResponse, responses=_ERRORS)
def me(caller: Asking, accounts: Accounts) -> MeResponse:
    """Who the browser is signed in as. A guest needs no database; a signed-in user's profile is made once."""
    if not caller.token_given:
        return MeResponse(signed_in=False)
    user = _signed_in(caller)
    with _account_errors():
        name = accounts.ensure_profile(user.id)
    return MeResponse(signed_in=True, user_id=user.id, display_name=name)


@router.get("/searches", response_model=SavedSearchList, responses={**_ERRORS, 422: {"description": BAD_CURSOR}})
def my_searches(
    caller: Asking,
    accounts: Accounts,
    cursor: Annotated[str | None, Query(max_length=MAX_CURSOR_CHARS)] = None,
) -> SavedSearchList:
    """One page of the user's saved searches, newest first. Ask again with next_cursor for the next page."""
    user = _signed_in(caller)
    after = read_cursor(cursor) if cursor else None
    with _account_errors():
        rows = accounts.saved_searches(user.id, after, PAGE_SIZE + 1)  # one more says whether a next page exists
    page = rows[:PAGE_SIZE]
    items = [summary for summary in map(summary_of, page) if summary is not None]
    more = len(rows) > PAGE_SIZE and bool(page)
    return SavedSearchList(items=items, next_cursor=write_cursor(page[-1]) if more else None)


@router.get(
    "/searches/{search_id}", response_model=SearchResult, responses={**_ERRORS, 404: {"description": NOT_FOUND}}
)
def my_search(search_id: str, caller: Asking, accounts: Accounts) -> SearchResult:
    """One saved result, only the owner's. Another user's id, a guest's or a made-up one all answer 404."""
    user = _signed_in(caller)
    try:
        wanted = UUID(search_id)
    except ValueError:
        raise HTTPException(404, NOT_FOUND) from None
    with _account_errors():
        saved = accounts.saved_search(user.id, wanted)
    if saved is None:
        raise HTTPException(404, NOT_FOUND)
    try:
        result = SearchResult.model_validate(saved)
    except ValidationError:
        log.warning("a saved search could not be read back as a result")  # the type only: never its text
        raise HTTPException(404, NOT_FOUND) from None
    return SearchResult.model_validate({**result.model_dump(), "saved": True})  # it was read from its saved row


# --- helpers ------------------------------------------------------------------------------------------------------


def owned_by(request: SearchRequest, user: SignedInUser | None) -> SearchRequest:
    """The request with its owner set from the checked token: the user's id, or None for a guest.

    Whatever user_id the body held is replaced, so a guest's search is never linked to an account (D-36) and nobody
    can save a search under someone else's id.
    """
    owner = str(user.id) if user is not None else None
    if request.user_id == owner:
        return request
    return SearchRequest.model_validate({**request.model_dump(), "user_id": owner})


def _signed_in(caller: Caller) -> SignedInUser:
    if caller.user is not None:
        return caller.user
    if caller.problem == "unavailable":
        raise HTTPException(503, SIGN_IN_UNAVAILABLE)
    detail = SIGN_IN_AGAIN if caller.token_given else SIGN_IN_FIRST
    raise HTTPException(401, detail, headers={"WWW-Authenticate": "Bearer"})


@contextmanager
def _account_errors() -> Iterator[None]:
    """Turns the account store's errors into the route's answers."""
    try:
        yield
    except db.AccountsUnavailable:
        raise HTTPException(503, ACCOUNT_UNAVAILABLE) from None
    except db.UserGone:  # the account was deleted after the token was issued
        raise HTTPException(401, SIGN_IN_AGAIN, headers={"WWW-Authenticate": "Bearer"}) from None


def shorten(text: str, limit: int) -> str:
    """At most `limit` characters: a longer text keeps limit - 1 and ends with "…"."""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def summary_of(row: db.SavedSearchRow) -> SavedSearchSummary | None:
    """The list's view of one saved search. None for a row with no request text, which a summary must have."""
    text = (row.request_text or "").strip()
    if not text:
        return None
    fields = {
        "id": row.id,
        "created_at": row.created_at,
        "request_text": shorten(text, REQUEST_TEXT_CHARS),
        "top_product": shorten(row.top_product, TOP_PRODUCT_CHARS) if row.top_product else None,
    }
    try:
        return SavedSearchSummary.model_validate({**fields, "category": row.category})
    except ValidationError:
        return SavedSearchSummary.model_validate(fields)  # a category that isn't a slug is left out


def write_cursor(row: db.SavedSearchRow) -> str:
    raw = json.dumps({"t": row.created_at.isoformat(), "i": str(row.id)}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode("utf-8")).rstrip(b"=").decode("ascii")


def read_cursor(cursor: str) -> db.PageAfter:
    """The place a cursor names. Anything this route didn't make answers 422."""
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        data = json.loads(raw.decode("utf-8"))
        created_at = datetime.fromisoformat(data["t"])
        search_id = UUID(data["i"])
    except (binascii.Error, UnicodeDecodeError, ValueError, TypeError, KeyError, AttributeError):
        raise HTTPException(422, BAD_CURSOR) from None
    if created_at.tzinfo is None:
        raise HTTPException(422, BAD_CURSOR)
    return db.PageAfter(created_at=created_at, id=search_id)
