"""Locally signed test tokens and a fake key set (US-30). Never a real account, a real key or a real project.

TestSigner makes an ES256 key pair (the algorithm Supabase recommends for its JWT signing keys), publishes the public
half as a JWKS document, and signs access tokens shaped like Supabase Auth's: iss, aud "authenticated", exp, iat, sub
(the user's id), role "authenticated". FakeKeyFetch serves that document and counts how often it was asked.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Mapping
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from app.auth import AuthSettings, KeySet, TokenVerifier

PROJECT_URL = "https://testproject.supabase.test"  # .test: never a real host (RFC 2606)
ISSUER = f"{PROJECT_URL}/auth/v1"
SETTINGS = AuthSettings(issuer=ISSUER)


class TestSigner:
    """One signing key with its key id. kty "EC" (ES256) by default, or "RSA" (RS256)."""

    __test__ = False  # not a test class, despite the name

    def __init__(self, kid: str = "test-key-1", kty: str = "EC") -> None:
        self.kid = kid
        self.algorithm = "ES256" if kty == "EC" else "RS256"
        self.private_key: Any = (
            ec.generate_private_key(ec.SECP256R1())
            if kty == "EC"
            else rsa.generate_private_key(public_exponent=65537, key_size=2048)
        )

    def public_jwk(self) -> dict[str, Any]:
        algorithm = jwt.get_algorithm_by_name(self.algorithm)
        jwk: dict[str, Any] = json.loads(algorithm.to_jwk(self.private_key.public_key()))
        return {**jwk, "kid": self.kid, "alg": self.algorithm, "use": "sig", "key_ops": ["verify"]}

    def token(self, user: uuid.UUID | str | None = None, *, kid: str | None = None, **claims: Any) -> str:
        """A signed access token. Claims passed here replace the defaults; a claim set to None is left out."""
        now = int(time.time())
        payload: dict[str, Any] = {
            "iss": ISSUER,
            "aud": "authenticated",
            "exp": now + 3600,
            "iat": now,
            "sub": str(user or uuid.uuid4()),
            "role": "authenticated",
            "aal": "aal1",
            "session_id": str(uuid.uuid4()),
            "is_anonymous": False,
        }
        payload.update(claims)
        payload = {key: value for key, value in payload.items() if value is not None}
        return jwt.encode(payload, self.private_key, algorithm=self.algorithm, headers={"kid": kid or self.kid})


def jwks(*signers: TestSigner) -> dict[str, Any]:
    return {"keys": [signer.public_jwk() for signer in signers]}


class FakeKeyFetch:
    """Serves a JWKS document, counting the calls. `document` can be swapped (a key rotation); fail=True raises."""

    def __init__(self, document: Mapping[str, Any]) -> None:
        self.document = document
        self.calls = 0
        self.fail = False

    def __call__(self) -> Mapping[str, Any]:
        self.calls += 1
        if self.fail:
            raise ConnectionError("the key set could not be fetched")
        return self.document


class Clock:
    """A monotonic clock the test moves by hand."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def verifier_for(*signers: TestSigner, clock: Clock | None = None) -> tuple[TokenVerifier, FakeKeyFetch]:
    fetch = FakeKeyFetch(jwks(*signers))
    keys = KeySet(fetch, clock=clock or Clock())
    return TokenVerifier(SETTINGS, keys), fetch


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
