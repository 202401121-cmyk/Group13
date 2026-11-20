"""Sign-in settings (US-30): the Supabase project's address, from SUPABASE_URL.

From it come the token issuer (`<SUPABASE_URL>/auth/v1`) and the address of the project's public signing keys
(`<issuer>/.well-known/jwks.json`). The backend needs nothing else to check a token: no secret, and no key the browser
has (D-32, P-24). SUPABASE_URL not set, or not an https address, turns sign-in off: every caller is a guest, and the
user routes answer 503.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

from dotenv import load_dotenv

log = logging.getLogger(__name__)

AUDIENCE = "authenticated"  # Supabase Auth's "aud" for a signed-in user's access token


@dataclass(frozen=True)
class AuthSettings:
    issuer: str  # https://<project-ref>.supabase.co/auth/v1
    audience: str = AUDIENCE

    @property
    def jwks_url(self) -> str:
        return f"{self.issuer}/.well-known/jwks.json"


def auth_settings_from(url: str | None) -> AuthSettings | None:
    """The settings for a project address, or None (sign-in off) when it is missing or not a plain https address."""
    text = (url or "").strip().rstrip("/")
    if not text:
        return None
    parts = urlsplit(text)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.query or parts.path:
        log.warning("sign-in is off: SUPABASE_URL is not a plain https address such as https://<ref>.supabase.co")
        return None
    return AuthSettings(issuer=f"{text}/auth/v1")


def load_auth_settings(env: Mapping[str, str] | None = None) -> AuthSettings | None:
    """Read SUPABASE_URL. With no `env`, python-dotenv loads the root .env first; real environment variables win."""
    if env is None:
        load_dotenv()
        env = os.environ
    return auth_settings_from(env.get("SUPABASE_URL"))
