"""Who is asking, for every route (US-30): a signed-in user, or a guest.

Every route may take `OptionalUser`: the user when a good bearer token came with the request, else None. No token, a
bad one, or sign-in being unavailable all mean a guest there (D-36), so a search never fails over a token. The user
routes (app/api/account.py) take `Caller` instead, to answer a bad token with 401 and unavailable sign-in with 503.

Both are plain `def` dependencies, so FastAPI runs them in its thread pool: a key fetch never blocks the event loop.
"""

from __future__ import annotations

import functools
import logging
from dataclasses import dataclass
from typing import Annotated, Literal

from fastapi import Depends, Header

from .keys import AuthUnavailable, KeySet, http_key_fetch
from .settings import load_auth_settings
from .verify import InvalidToken, SignedInUser, TokenVerifier

log = logging.getLogger(__name__)

Problem = Literal["invalid", "unavailable"]


@dataclass(frozen=True)
class Caller:
    """The user a good token named, or None. `problem` says why a token that came named nobody."""

    user: SignedInUser | None = None
    token_given: bool = False
    problem: Problem | None = None


@functools.cache
def get_token_verifier() -> TokenVerifier | None:
    """The app's verifier, built on first use from SUPABASE_URL. None when sign-in is off. Tests override it."""
    settings = load_auth_settings()
    if settings is None:
        return None
    return TokenVerifier(settings, KeySet(http_key_fetch(settings.jwks_url)))


Verifier = Annotated[TokenVerifier | None, Depends(get_token_verifier)]


def who_is_asking(verifier: Verifier, authorization: Annotated[str | None, Header()] = None) -> Caller:
    """Read `Authorization: Bearer <token>`. Never raises; the token and the user's id are never logged."""
    if authorization is None or not authorization.strip():
        return Caller()
    scheme, _, token = authorization.strip().partition(" ")
    if scheme.casefold() != "bearer" or not token.strip():
        return Caller(token_given=True, problem="invalid")
    if verifier is None:
        log.warning("a sign-in could not be checked: SUPABASE_URL is not set")
        return Caller(token_given=True, problem="unavailable")
    try:
        return Caller(user=verifier.verify(token.strip()), token_given=True)
    except InvalidToken as exc:
        log.info("a sign-in was not accepted: %s", exc)  # a plain reason, never the token
        return Caller(token_given=True, problem="invalid")
    except AuthUnavailable:
        return Caller(token_given=True, problem="unavailable")


def optional_user(caller: Annotated[Caller, Depends(who_is_asking)]) -> SignedInUser | None:
    return caller.user


OptionalUser = Annotated[SignedInUser | None, Depends(optional_user)]
