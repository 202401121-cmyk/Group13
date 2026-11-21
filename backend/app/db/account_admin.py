"""Deleting a Supabase Auth user (US-11, P-43, D-32): the one call that ends an account.

Supabase's Auth admin API, as its docs read on 21 Nov 2026 (docs/api-setup.md):
- `DELETE <SUPABASE_URL>/auth/v1/admin/users/<user id>` deletes the user. Without `should_soft_delete: true` the
  delete is hard: the row leaves auth.users, so the foreign keys then remove the user's requests, searches, profile and
  preferences (on delete cascade).
- It needs a secret key (`sb_secret_...`), sent on the `apikey` header. A secret key is not a JWT and never goes on
  `Authorization: Bearer`; Supabase refuses a secret key sent from a browser.

A user already gone (404) counts as deleted, so asking again after a lost answer is safe. Anything else is
AdminUnavailable with a plain reason: the status code or the error's type, never the key, a token or a user id.
"""

from __future__ import annotations

import logging
from typing import Protocol
from uuid import UUID

import httpx

from app.auth.settings import AdminSettings

log = logging.getLogger(__name__)

TIMEOUT_S = 10.0


class AdminUnavailable(Exception):
    """The user could not be deleted now. `reason` is plain words for the log line, never a key or an id."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class AuthAdmin(Protocol):
    def delete_user(self, user_id: UUID) -> None:
        """Delete the user for good. Raises AdminUnavailable."""
        ...


class SupabaseAuthAdmin:
    """Supabase's Auth admin API, through httpx. `transport` lets tests answer without a network."""

    def __init__(
        self, settings: AdminSettings, transport: httpx.BaseTransport | None = None, timeout_s: float = TIMEOUT_S
    ) -> None:
        self._settings = settings
        self._transport = transport
        self._timeout_s = timeout_s

    def __repr__(self) -> str:
        return "SupabaseAuthAdmin(<settings hidden>)"

    def delete_user(self, user_id: UUID) -> None:
        url = f"{self._settings.users_url}/{user_id}"
        headers = {"apikey": self._settings.secret_key, "Content-Type": "application/json"}
        try:
            with httpx.Client(transport=self._transport, timeout=self._timeout_s) as client:
                response = client.request("DELETE", url, headers=headers, json={"should_soft_delete": False})
        except Exception as exc:  # the network, or a key that can't go in a header: the type only, never the key
            raise AdminUnavailable(f"Supabase Auth could not be reached ({type(exc).__name__})") from None
        if response.status_code in (200, 204):
            return
        if response.status_code == 404:  # already gone: a repeated delete after a lost answer
            log.info("account delete: the user was already gone from Supabase Auth")
            return
        raise AdminUnavailable(f"Supabase Auth answered {response.status_code}")
