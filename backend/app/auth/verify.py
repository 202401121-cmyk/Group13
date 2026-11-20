"""Checking a Supabase access token (US-30, P-24): who is signed in, or InvalidToken.

The bearer token is a JWT signed with one of the project's asymmetric signing keys. The backend checks:
- the signature, with the published key the token's "kid" names, and only ES256 or RS256 (never "none", never HS256);
- the expiry ("exp") and the issue time ("iat"), with a few seconds' leeway for clock drift;
- the issuer ("iss") is this project's `<SUPABASE_URL>/auth/v1`, and the audience ("aud") is "authenticated";
- it is a signed-in shopper: "role" is "authenticated", "is_anonymous" is not true, and "sub" is a user id (a UUID).

A refusal's message is a plain reason, and never holds the token, a claim or the user's id. Nothing here logs them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import jwt

from .keys import ALGORITHMS, AuthUnavailable, KeySet, UnknownKey
from .settings import AuthSettings

LEEWAY_S = 10
MAX_TOKEN_CHARS = 8192  # a Supabase access token is far shorter; anything longer is not parsed at all
REQUIRED_CLAIMS = ["exp", "iat", "sub", "iss", "aud"]


class InvalidToken(Exception):
    """The token was not accepted. The message is a plain reason, never the token."""


@dataclass(frozen=True)
class SignedInUser:
    """Who the token belongs to: the Supabase user id, and nothing else (no email, D-36)."""

    id: UUID

    def __repr__(self) -> str:
        return "SignedInUser(id=<hidden>)"  # an id is personal data: never in a log line or a traceback


class TokenVerifier:
    def __init__(self, settings: AuthSettings, keys: KeySet) -> None:
        self.settings = settings
        self.keys = keys

    def verify(self, token: str) -> SignedInUser:
        """The signed-in user. Raises InvalidToken, or AuthUnavailable when the signing keys can't be had."""
        if not token or len(token) > MAX_TOKEN_CHARS:
            raise InvalidToken("the token is missing or too long")
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            raise InvalidToken("the token is not a JWT") from None
        algorithm, kid = header.get("alg"), header.get("kid")
        if not isinstance(algorithm, str) or algorithm not in ALGORITHMS:
            raise InvalidToken("the token is not signed with one of the project's signing keys")
        if not isinstance(kid, str) or not kid:
            raise InvalidToken("the token names no signing key")
        try:
            key = self.keys.key_for(kid)
        except UnknownKey:
            raise InvalidToken("the token's signing key is not one of the project's") from None
        except AuthUnavailable:
            raise
        if key.algorithm_name != algorithm:
            raise InvalidToken("the token's algorithm does not match its signing key")
        claims = self._decode(token, key.key, algorithm)
        return SignedInUser(id=_signed_in_user(claims))

    def _decode(self, token: str, key: Any, algorithm: str) -> dict[str, Any]:
        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                key,
                algorithms=[algorithm],
                audience=self.settings.audience,
                issuer=self.settings.issuer,
                leeway=LEEWAY_S,
                options={"require": REQUIRED_CLAIMS, "verify_signature": True},
            )
        except jwt.ExpiredSignatureError:
            raise InvalidToken("the token has expired") from None
        except jwt.InvalidIssuerError:
            raise InvalidToken("the token is from another project") from None
        except jwt.InvalidAudienceError:
            raise InvalidToken("the token is not for a signed-in user") from None
        except jwt.MissingRequiredClaimError:
            raise InvalidToken("the token lacks a required claim") from None
        except jwt.InvalidSignatureError:
            raise InvalidToken("the token's signature does not match") from None
        except jwt.PyJWTError:
            raise InvalidToken("the token was not accepted") from None
        return claims


def _signed_in_user(claims: dict[str, Any]) -> UUID:
    if claims.get("role") != "authenticated":
        raise InvalidToken("the token is not a signed-in user's")
    if claims.get("is_anonymous") is True:
        raise InvalidToken("an anonymous session is not an account")
    try:
        return UUID(str(claims["sub"]))
    except ValueError:
        raise InvalidToken("the token names no user id") from None
