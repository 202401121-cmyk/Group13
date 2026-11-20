"""The project's public signing keys (US-30, P-24): Supabase's JSON Web Key Set, cached.

- **Fetched again after KEYS_KEEP_S (10 minutes),** as long as Supabase's own edge keeps the key set, so a rotated or
  revoked key is seen within that time (docs/api-setup.md, "Supabase Auth: checking a sign-in").
- **An unknown key id fetches the set again once,** for a key published after the cache was filled. A token naming a
  key id nobody published can't make the backend fetch more than once every UNKNOWN_KEY_GAP_S.
- **A fetch that fails** is not tried again for UNKNOWN_KEY_GAP_S, so a down key server is not hammered. Keys fetched
  less than KEYS_STALE_S ago (20 minutes, the longest Supabase's own caches keep them) stay in use meanwhile; with
  none, sign-in is unavailable for the moment (AuthUnavailable), never a crash.
- **A fetch never holds anyone up for long:** it has a hard deadline (FETCH_DEADLINE_S, which also covers a slow name
  lookup), runs outside the lock that guards the keys, and one fetch runs at a time. A request that still has usable
  keys never waits for it.
- **Only ES256 and RS256 public keys are used.** A shared secret ("oct") never is, and a malformed key is skipped
  without losing the others.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any

import httpx
from jwt import PyJWK

log = logging.getLogger(__name__)

ALGORITHMS = ("ES256", "RS256")  # Supabase's asymmetric JWT signing keys: P-256 (recommended) and RSA 2048
KEYS_KEEP_S = 600.0
KEYS_STALE_S = 1200.0
UNKNOWN_KEY_GAP_S = 30.0
FETCH_TIMEOUT_S = 3.0  # httpx's, per phase
FETCH_DEADLINE_S = 5.0  # the whole fetch, the name lookup included

KeyFetch = Callable[[], Mapping[str, Any]]


class AuthUnavailable(Exception):
    """The signing keys can't be had right now, so no token can be checked. Never a reason to refuse a guest."""


class UnknownKey(Exception):
    """The token names a key id the project hasn't published."""


def http_key_fetch(url: str, timeout_s: float = FETCH_TIMEOUT_S) -> KeyFetch:
    """Fetch the key set over https. Redirects are not followed."""

    def fetch() -> Mapping[str, Any]:
        response = httpx.get(url, timeout=timeout_s, follow_redirects=False, headers={"Accept": "application/json"})
        response.raise_for_status()
        document = response.json()
        if not isinstance(document, Mapping):
            raise ValueError("the key set is not a JSON object")
        return document

    return fetch


def usable_keys(document: Mapping[str, Any]) -> dict[str, PyJWK]:
    """The ES256 and RS256 public keys in a key set, by key id. Anything else, or anything malformed, is left out."""
    found: dict[str, PyJWK] = {}
    entries = document.get("keys")
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, Mapping):
            continue
        kid, alg, use = entry.get("kid"), entry.get("alg"), entry.get("use", "sig")
        if not isinstance(kid, str) or alg not in ALGORITHMS or use != "sig" or entry.get("kty") not in ("EC", "RSA"):
            continue
        if any(field in entry for field in ("d", "p", "q")):  # a private key never belongs in a public key set
            continue
        try:
            found[kid] = PyJWK(dict(entry), algorithm=alg)
        except Exception as exc:  # PyJWT raises InvalidKeyError, PyJWKError or ValueError for bad key data
            log.warning("a signing key was skipped: %s", type(exc).__name__)
    return found


class KeySet:
    """The cached key set. Safe to share between threads."""

    def __init__(
        self,
        fetch: KeyFetch,
        clock: Callable[[], float] = time.monotonic,
        keep_s: float = KEYS_KEEP_S,
        stale_s: float = KEYS_STALE_S,
        gap_s: float = UNKNOWN_KEY_GAP_S,
        deadline_s: float = FETCH_DEADLINE_S,
    ) -> None:
        self._fetch = fetch
        self._clock = clock
        self._keep_s = keep_s
        self._stale_s = stale_s
        self._gap_s = gap_s
        self._deadline_s = deadline_s
        self._keys: dict[str, PyJWK] = {}
        self._fetched_at: float | None = None  # the last fetch that worked
        self._tried_at: float | None = None  # the last fetch tried, worked or not
        self._state = threading.Lock()  # guards the fields above; never held during a fetch
        self._fetching = threading.Lock()  # one fetch at a time

    def key_for(self, kid: str) -> PyJWK:
        """The key with this id. Raises UnknownKey, or AuthUnavailable when no key set can be had."""
        keys, wanted = self._view(kid)
        if wanted:
            keys = self._refresh(kid, wait=not keys)
        if kid in keys:
            return keys[kid]
        if not keys:
            raise AuthUnavailable("no signing key could be had")
        raise UnknownKey("the token names a key id the project hasn't published")

    def _view(self, kid: str) -> tuple[dict[str, PyJWK], bool]:
        """The keys still usable, and whether a fetch is wanted and allowed now."""
        with self._state:
            now = self._clock()
            age = None if self._fetched_at is None else now - self._fetched_at
            usable = self._keys if age is not None and age < self._stale_s else {}
            fresh = age is not None and age < self._keep_s
            allowed = self._tried_at is None or now - self._tried_at >= self._gap_s
            return usable, allowed and (not fresh or kid not in usable)

    def _refresh(self, kid: str, wait: bool) -> dict[str, PyJWK]:
        """Fetch the key set, unless another request is already doing so. Answers the keys usable afterwards."""
        if not self._fetching.acquire(blocking=wait, timeout=self._deadline_s + 1 if wait else -1):
            return self._view(kid)[0]  # another request is fetching: use what there is
        try:
            if not self._view(kid)[1]:
                return self._view(kid)[0]  # fetched (or tried) by another request while this one waited
            with self._state:
                self._tried_at = self._clock()
            keys = self._fetch_in_time()
            if keys:
                with self._state:
                    self._keys, self._fetched_at = keys, self._clock()
            return self._view(kid)[0]
        finally:
            self._fetching.release()

    def _fetch_in_time(self) -> dict[str, PyJWK]:
        """The usable keys from one fetch, or {} if it failed or ran past the deadline. Never raises."""
        box: dict[str, Any] = {}
        done = threading.Event()

        def work() -> None:
            try:
                box["keys"] = usable_keys(self._fetch())
            except Exception as exc:  # a timeout, a refused connection, an error status, bad JSON, ...
                box["error"] = exc
            finally:
                done.set()

        threading.Thread(target=work, name="signing-keys", daemon=True).start()
        if not done.wait(self._deadline_s):  # the thread is left to finish on its own
            log.warning("the signing keys could not be fetched: no answer within %g s", self._deadline_s)
            return {}
        if "error" in box:
            log.warning("the signing keys could not be fetched: %s", type(box["error"]).__name__)
            return {}
        keys: dict[str, PyJWK] = box["keys"]
        if not keys:
            log.warning("the signing key set holds no ES256 or RS256 key")
        return keys
