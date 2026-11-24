"""The user routes (US-30): who is signed in, and their own saved searches.

- GET /api/me: MeResponse. No token is a guest (signed_in false); a token that isn't accepted answers 401. At first
  sign-in the profile row is made (an insert that does nothing if it exists).
- GET /api/me/searches: SavedSearchList, newest first, PAGE_SIZE a page, with an opaque cursor for the next.
- GET /api/me/searches/{search_id}: the saved SearchResult, only the owner's. Any other id answers 404, never 403, so
  an id is never confirmed.

Both search routes need a signed-in user (401 without one). Every query filters on the token's user id, as well as row
level security doing so for the Data API (S4-9). The database being off, down or slow answers 503, and searches still
finish (build rule 10). Messages are plain sentences; nothing logs a token, a user id or an email.

What the app remembers, and the account itself (US-11, P-43), also from the token's user only:
- GET /api/me/preferences: PreferenceList, newest first. GET /api/me/preferences/export: the same as a PreferenceExport
  JSON download. Neither ever holds an embedding, a user id or an email.
- PATCH /api/me/preferences/{id}: PreferenceEdit. DELETE /api/me/preferences/{id}: one, with its embedding (the same
  row). DELETE /api/me/preferences: everything. Another user's id answers 404, never 403.
- DELETE /api/me: the account, with the word "delete" in the body. Supabase's Auth admin API deletes the user with the
  backend's secret key (D-32), the foreign keys remove the user's rows, and 204 comes only after a read-back finds none.
"""

from __future__ import annotations

import base64
import binascii
import functools
import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app import db
from app.api.evidence import EvidenceUnavailable, saved_evidence_for
from app.auth import Caller, SignedInUser, who_is_asking
from app.auth.settings import load_admin_settings
from app.contracts import (
    EvidenceResponse,
    MeResponse,
    PreferenceEdit,
    PreferenceExport,
    PreferenceItem,
    PreferenceList,
    SavedSearchList,
    SavedSearchSummary,
    SearchRequest,
    SearchResult,
)
from app.db.account_admin import AdminUnavailable, AuthAdmin, SupabaseAuthAdmin
from app.memory import EditConflict, EditRefused, MemoryService, MemoryUnavailable, StoredPreference, default_memory

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
# US-11: what the app remembers, and deleting the account
SIGN_IN_FOR_MEMORY = "Please sign in to see what ShopSense remembers about you."
SIGN_IN_TO_DELETE = "Please sign in to delete your account."
PREFERENCE_CHANGED = "This preference changed while you were editing it. Please look at it again and retry."
PREFERENCE_NOT_FOUND = "We couldn't find that remembered preference."
MEMORY_NOTE_HEADER = "X-Memory-Note"
EMBEDDING_KEPT = (
    "Your new words are saved, but we couldn't work out their meaning just now, so later searches still match the "
    "old words."
)
EXPORT_FILE = "shopsense-memory.json"
CONFIRM_WORD = "delete"
CONFIRM_FIRST = 'To delete your account, type "delete" to confirm. This can\'t be undone.'
DELETE_UNAVAILABLE = "Deleting an account isn't available right now. Nothing was deleted. Please try again later."
DELETE_FAILED = "We couldn't delete your account just now. Nothing was deleted. Please try again in a minute."
DELETE_NOT_FINISHED = (
    "Your account was deleted, but we couldn't yet confirm that all your saved data is gone. Please try again in a "
    "minute."
)

router = APIRouter(prefix="/api/me", tags=["account"])


def get_account_store() -> db.AccountStore:
    return db.default_account_store()


def get_memory_controls() -> MemoryService:
    return default_memory()


@functools.cache
def get_auth_admin() -> AuthAdmin | None:
    """Supabase's Auth admin API, built on first use from SUPABASE_URL and SUPABASE_SECRET_KEY. None without them:
    deleting an account then answers 503, and everything else works. Tests override it."""
    settings = load_admin_settings()
    return SupabaseAuthAdmin(settings) if settings is not None else None


Accounts = Annotated[db.AccountStore, Depends(get_account_store)]
Asking = Annotated[Caller, Depends(who_is_asking)]
Memory = Annotated[MemoryService, Depends(get_memory_controls)]
Admin = Annotated[AuthAdmin | None, Depends(get_auth_admin)]


class AccountDelete(BaseModel):
    """DELETE /api/me's body: the word "delete", typed by the shopper, so a stray call can't delete an account."""

    model_config = ConfigDict(extra="forbid")

    confirm: str | None = Field(default=None, max_length=40, description='The word "delete"')


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


@router.get(
    "/searches/{search_id}/evidence",
    response_model=EvidenceResponse,
    responses={**_ERRORS, 404: {"description": f"{NOT_FOUND} Or that value's sources can't be shown"}},
)
def my_search_evidence(
    search_id: str,
    caller: Asking,
    accounts: Accounts,
    product_id: Annotated[str, Query(min_length=1, description="One of the saved result's products")],
    criterion_key: Annotated[
        str, Query(min_length=1, description="A criterion or hard-rule attribute with a value for it")
    ],
) -> EvidenceResponse:
    """The claims behind one value of a saved result, from its saved row alone (US-29): the same answer as the run's
    evidence route, with no Reddit words (D-53). Only the owner's: another user's id, a guest's or a made-up one all
    answer 404, as the saved result does. A search saved before 1.11.0 answers 404 for a value, saying why."""
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
    try:
        return saved_evidence_for(result, product_id, criterion_key)
    except EvidenceUnavailable as unavailable:
        raise HTTPException(unavailable.status_code, unavailable.detail) from None


# --- what the app remembers (US-11) -------------------------------------------------------------------------------

_MEMORY_ERRORS: dict[int | str, dict[str, str]] = {
    **_ERRORS,
    404: {"description": PREFERENCE_NOT_FOUND},
    409: {"description": PREFERENCE_CHANGED},
}


@router.get("/preferences", response_model=PreferenceList, responses=_ERRORS)
def my_preferences(caller: Asking, memory: Memory) -> PreferenceList:
    """Everything remembered for the signed-in shopper, newest first. Never an embedding, a user id or an email."""
    user = _signed_in(caller, SIGN_IN_FOR_MEMORY)
    with _memory_errors():
        rows = memory.listing(user.id)
    return PreferenceList(items=[item for item in map(preference_of, rows) if item is not None])


@router.get("/preferences/export", response_model=PreferenceExport, responses=_ERRORS)
def export_preferences(caller: Asking, memory: Memory) -> JSONResponse:
    """Everything remembered, as a JSON file to download: the same items as the list, and when it was made."""
    user = _signed_in(caller, SIGN_IN_FOR_MEMORY)
    with _memory_errors():
        rows = memory.listing(user.id)
    exported = PreferenceExport(
        exported_at=datetime.now(UTC), items=[item for item in map(preference_of, rows) if item is not None]
    )
    return JSONResponse(
        exported.model_dump(mode="json"),
        headers={"Content-Disposition": f'attachment; filename="{EXPORT_FILE}"'},
    )


@router.patch("/preferences/{preference_id}", response_model=PreferenceItem, responses=_MEMORY_ERRORS)
def edit_preference(
    preference_id: str, change: PreferenceEdit, caller: Asking, memory: Memory, response: Response
) -> PreferenceItem:
    """Change one remembered preference: its words, a weight's weight, or a must-have on or off. New words get a new
    embedding; if that can't be made now, the row keeps its old one and the X-Memory-Note header says so."""
    user = _signed_in(caller, SIGN_IN_FOR_MEMORY)
    wanted = _preference_id(preference_id)
    with _memory_errors():
        edited = memory.edit(user.id, wanted, change)
    item = preference_of(edited.row) if edited is not None else None
    if edited is None or item is None:
        raise HTTPException(404, PREFERENCE_NOT_FOUND)
    if edited.embedding_kept:
        response.headers[MEMORY_NOTE_HEADER] = EMBEDDING_KEPT
    return item


@router.delete("/preferences/{preference_id}", status_code=204, responses=_MEMORY_ERRORS)
def delete_preference(preference_id: str, caller: Asking, memory: Memory) -> Response:
    """Delete one remembered preference, and its embedding with it (the same row)."""
    user = _signed_in(caller, SIGN_IN_FOR_MEMORY)
    wanted = _preference_id(preference_id)
    with _memory_errors():
        deleted = memory.delete(user.id, wanted)
    if not deleted:
        raise HTTPException(404, PREFERENCE_NOT_FOUND)
    return Response(status_code=204)


@router.delete("/preferences", status_code=204, responses=_ERRORS)
def delete_preferences(caller: Asking, memory: Memory) -> Response:
    """Forget everything remembered for the signed-in shopper."""
    user = _signed_in(caller, SIGN_IN_FOR_MEMORY)
    with _memory_errors():
        memory.delete_all(user.id)
    return Response(status_code=204)


# --- deleting the account (US-11) ---------------------------------------------------------------------------------


@router.delete(
    "",
    status_code=204,
    responses={**_ERRORS, 422: {"description": CONFIRM_FIRST}, 503: {"description": DELETE_FAILED}},
)
def delete_account(
    caller: Asking, accounts: Accounts, admin: Admin, body: Annotated[AccountDelete | None, Body()] = None
) -> Response:
    """Delete the signed-in shopper's account and everything saved with it. Needs the word "delete" in the body.

    The database must answer first: without it nothing is deleted (503), since the result couldn't be checked. Then the
    user is deleted through Supabase's Auth admin API with the backend's secret key, and the foreign keys remove every
    request, search, profile and preference of theirs. 204 only after a read-back finds none of their rows; any left
    means the user wasn't really deleted, so it is 503, never 204. No secret key: 503, and nothing is deleted."""
    user = _signed_in(caller, SIGN_IN_TO_DELETE)
    if body is None or (body.confirm or "").strip().casefold() != CONFIRM_WORD:
        raise HTTPException(422, CONFIRM_FIRST)
    if admin is None:
        raise HTTPException(503, DELETE_UNAVAILABLE)
    try:
        accounts.account_rows(user.id)  # the database answers before anything is deleted
    except (db.AccountsUnavailable, db.UserGone):
        raise HTTPException(503, DELETE_FAILED) from None
    try:
        admin.delete_user(user.id)
    except AdminUnavailable as exc:
        log.warning("account delete: the user could not be deleted: %s", exc.reason)
        raise HTTPException(503, DELETE_FAILED) from None
    try:
        left = accounts.account_rows(user.id)
    except (db.AccountsUnavailable, db.UserGone):
        raise HTTPException(503, DELETE_NOT_FINISHED) from None
    if any(left.values()):
        log.warning(
            "account delete: rows were left in %s after the user's delete", sorted(t for t, n in left.items() if n)
        )
        raise HTTPException(503, DELETE_NOT_FINISHED)
    log.info("account delete: an account and all its rows were deleted")
    return Response(status_code=204)


def preference_of(row: StoredPreference) -> PreferenceItem | None:
    """The contract's item for one stored row, or None for a row that no longer fits it (left out, its kind logged)."""
    try:
        return PreferenceItem.model_validate(
            {
                "id": row.id,
                "kind": row.kind,
                "category": row.category,
                "criterion_key": row.criterion_key,
                "text": row.text,
                "weight": row.weight,
                "rule": row.rule if row.kind == "hard_rule" else None,
                "exclusion": row.rule if row.kind == "exclusion" else None,
                "always": row.always,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
            }
        )
    except ValidationError:
        log.warning("a remembered %s could not be read back as a preference", row.kind)
        return None


def _preference_id(text: str) -> UUID:
    try:
        return UUID(text)
    except ValueError:
        raise HTTPException(404, PREFERENCE_NOT_FOUND) from None


@contextmanager
def _memory_errors() -> Iterator[None]:
    try:
        yield
    except MemoryUnavailable:
        raise HTTPException(503, ACCOUNT_UNAVAILABLE) from None
    except EditRefused as exc:
        raise HTTPException(422, str(exc)) from None
    except EditConflict:
        raise HTTPException(409, PREFERENCE_CHANGED) from None


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


def _signed_in(caller: Caller, first: str = SIGN_IN_FIRST) -> SignedInUser:
    if caller.user is not None:
        return caller.user
    if caller.problem == "unavailable":
        raise HTTPException(503, SIGN_IN_UNAVAILABLE)
    detail = SIGN_IN_AGAIN if caller.token_given else first
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
