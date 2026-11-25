"""Google Safe Browsing Lookup API v4 for a store page before a link to it is shown (US-24, D-99 #3).

POST /v4/threatMatches:find with the page's address alone (D-99 #8): Google answers an empty object when the address is
on none of its lists, and {"matches": [...]} when it is (developers.google.com/safe-browsing/v4/lookup-api, read 25 Nov
2026). The API is free for non-commercial use; this is a student project. The key goes in the x-goog-api-key header,
never in the address, so no log of a request can hold it.

- no SAFE_BROWSING_API_KEY: the check is skipped, said once in the log, and the other checks still decide;
- an error, a timeout or a reply we can't read: "error", and the caller keeps Google's link;
- a match: "threat". Google's terms let us treat an address as unsafe only on an answer at most 30 minutes old, so the
  caller keeps that verdict no longer.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Literal

import httpx

log = logging.getLogger(__name__)

SAFE_BROWSING_URL = "https://safebrowsing.googleapis.com/v4/threatMatches:find"
TIMEOUT_S = 3.0
THREAT_TYPES = ("MALWARE", "SOCIAL_ENGINEERING", "UNWANTED_SOFTWARE", "POTENTIALLY_HARMFUL_APPLICATION")
CLIENT = {"clientId": "shopsense", "clientVersion": "1.0"}
Answer = Literal["safe", "threat", "error", "skipped"]


_noted = threading.Event()
_note_lock = threading.Lock()


def note_no_key() -> None:
    """Said once a process, however many checks reach it at once: the check is skipped, not failed."""
    with _note_lock:
        if _noted.is_set():
            return
        _noted.set()
    log.warning("commerce: SAFE_BROWSING_API_KEY is not set; the Safe Browsing check is skipped")


def forget_the_note() -> None:
    """For tests: say it again next time."""
    _noted.clear()


class SafeBrowsing:
    def __init__(self, http: httpx.Client, api_key: str | None, *, timeout_s: float = TIMEOUT_S) -> None:
        self.http = http
        self._api_key = api_key
        self.timeout_s = timeout_s

    def __repr__(self) -> str:
        return f"SafeBrowsing(key_set={self._api_key is not None})"  # never the key

    def check(self, url: str) -> Answer:
        """ "safe", "threat", "error" (keep Google's link) or "skipped" (no key)."""
        if not self._api_key:
            note_no_key()
            return "skipped"
        body: dict[str, Any] = {
            "client": CLIENT,
            "threatInfo": {
                "threatTypes": list(THREAT_TYPES),
                "platformTypes": ["ANY_PLATFORM"],
                "threatEntryTypes": ["URL"],
                "threatEntries": [{"url": url}],
            },
        }
        try:
            response = self.http.post(
                SAFE_BROWSING_URL, json=body, headers={"x-goog-api-key": self._api_key}, timeout=self.timeout_s
            )
        except httpx.HTTPError as exc:
            log.info("commerce: the Safe Browsing check failed (%s)", type(exc).__name__)
            return "error"
        if response.status_code != 200:
            log.info("commerce: the Safe Browsing check failed (HTTP %d)", response.status_code)
            return "error"
        try:
            reply = response.json()
        except ValueError:
            return "error"
        if not isinstance(reply, dict):
            return "error"
        matches = reply.get("matches", [])
        if not isinstance(matches, list):
            return "error"  # a reply we can't read is never taken for "safe"
        return "threat" if matches else "safe"
