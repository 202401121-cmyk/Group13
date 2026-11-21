"""Sign-in settings (US-30): the Supabase project's address, from SUPABASE_URL.

From it come the token issuer (`<SUPABASE_URL>/auth/v1`) and the address of the project's public signing keys
(`<issuer>/.well-known/jwks.json`). The backend needs nothing else to check a token: no secret, and no key the browser
has (D-32, P-24). SUPABASE_URL not set, or not an https address, turns sign-in off: every caller is a guest, and the
user routes answer 503.

Deleting an account (US-11) is the one thing that needs a secret: SUPABASE_SECRET_KEY, for Supabase's Auth admin API.
It is read here, kept in the backend only (D-32), and never logged or shown. Without it, only that route answers 503.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
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


@dataclass(frozen=True)
class AdminSettings:
    """Supabase's Auth admin API, for deleting an account (US-11): the project's address and the secret key.

    The key is the backend's alone (D-32): never logged, never sent to the browser, and hidden from repr and str, so a
    traceback or a log line can't show it."""

    project_url: str  # https://<project-ref>.supabase.co
    secret_key: str = field(repr=False)

    @property
    def users_url(self) -> str:
        return f"{self.project_url}/auth/v1/admin/users"

    def __str__(self) -> str:
        return f"AdminSettings(project_url={self.project_url!r}, secret_key=<hidden>)"


def load_admin_settings(env: Mapping[str, str] | None = None) -> AdminSettings | None:
    """SUPABASE_URL and SUPABASE_SECRET_KEY, or None when either is missing: deleting an account then answers 503 and
    everything else works. A publishable key in its place is refused (it would fail at Supabase anyway)."""
    if env is None:
        load_dotenv()
        env = os.environ
    auth = auth_settings_from(env.get("SUPABASE_URL"))
    key = (env.get("SUPABASE_SECRET_KEY") or "").strip()
    if auth is None or not key:
        return None
    if key.startswith("sb_publishable_"):
        log.warning("deleting accounts is off: SUPABASE_SECRET_KEY holds a publishable key, not a secret one")
        return None
    return AdminSettings(project_url=auth.issuer.removesuffix("/auth/v1"), secret_key=key)
