"""Sign-in (US-30): checking a Supabase access token from the browser, and the routes' view of who is asking.

Supabase Auth (Google) signs the shopper in, in the browser, with the publishable key (D-32). The browser sends the
access token as `Authorization: Bearer <token>`; the backend checks it against the project's public signing keys
(P-24) and holds no secret. No token, or a bad one, is a guest (D-36); only the user routes (app/api/account.py)
answer a bad token with 401.
"""

from .http import Caller, OptionalUser, get_token_verifier, who_is_asking
from .keys import AuthUnavailable, KeySet, UnknownKey, http_key_fetch
from .settings import AuthSettings, auth_settings_from, load_auth_settings
from .verify import InvalidToken, SignedInUser, TokenVerifier

__all__ = [
    "AuthSettings",
    "AuthUnavailable",
    "Caller",
    "InvalidToken",
    "KeySet",
    "OptionalUser",
    "SignedInUser",
    "TokenVerifier",
    "UnknownKey",
    "auth_settings_from",
    "get_token_verifier",
    "http_key_fetch",
    "load_auth_settings",
    "who_is_asking",
]
