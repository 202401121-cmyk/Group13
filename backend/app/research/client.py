"""The HTTP client for every research source (US-12): built the way the LLM clients are.

build_http_client (app/llm/providers.py) is the one place that builds a client, so research connects over IPv4 while
force_ipv4 is on, for the same reason the LLM calls do. A client that can't be built, for example ALL_PROXY=socks5://...
with no SOCKS support installed, raises ClientUnavailable: the stage skips that source with a coverage note. The log
says the error's type once, and never the proxy's URL, which can hold a password.
"""

from __future__ import annotations

import logging
from functools import cache

import httpx

from app.llm.config import load_llm_config
from app.llm.providers import build_http_client

log = logging.getLogger(__name__)


class ClientUnavailable(Exception):
    """No HTTP client could be built. Its text is the error's type only."""


@cache
def note_no_client(error: str) -> None:
    log.warning("no research client could be built (%s): sources that need one are skipped", error)


def build_research_client() -> httpx.Client:
    try:
        force_ipv4 = load_llm_config().force_ipv4
    except Exception as exc:  # a broken models.yaml has its own errors; research still connects the safe way
        log.warning("models.yaml could not be read for force_ipv4 (%s); using IPv4", type(exc).__name__)
        force_ipv4 = True
    try:
        return build_http_client(force_ipv4)
    except Exception as exc:  # httpx raises several kinds for a proxy it can't use; none may crash a search (rule 9)
        name = type(exc).__name__
        note_no_client(name)
        raise ClientUnavailable(name) from None
