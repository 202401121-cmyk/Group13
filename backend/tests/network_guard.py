"""Blocks the network for tests (US-32). The fixture that installs it is in conftest.py.

Any connection, DNS lookup or UDP send for a host outside this machine raises NetworkBlocked at once, before a packet
leaves. The LLM client catches every exception and falls back, so a blocked call could end in a passing test: each
attempt is therefore also remembered, and check() fails the test at teardown.

The limits:
- it covers this Python process only. Subprocesses open their own sockets, and so does psycopg's C library when the
  host is an IP address. Database tests use TEST_DATABASE_URL, and conftest.py forces the "off" store everywhere else;
- it is installed for each test, so import time and module or session fixtures run before it;
- asyncio on Windows connects through ConnectEx, which skips socket.connect. The app has no async HTTP client.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from typing import Any, Protocol

import pytest

LOCAL_NAMES = {"localhost", "ip6-localhost", "ip6-loopback"}


class NetworkBlocked(RuntimeError):
    """Not an OSError, so that socket and httpx code can't mistake it for a dropped connection and retry."""

    def __init__(self, host: str) -> None:
        super().__init__(
            f"Tests may not use the network: tried to reach {host}. "
            "Use a recorded response from tests/fixtures/recorded/"
        )


class Marked(Protocol):
    def get_closest_marker(self, name: str) -> object | None: ...


def may_use_network(node: Marked) -> bool:
    """Only tests marked `live` may. They never run in CI or in a plain pytest run."""
    return node.get_closest_marker("live") is not None


class NetworkGuard:
    def __init__(self) -> None:
        self.attempts: list[str] = []

    @staticmethod
    def is_local(host: object) -> bool:
        if host is None or host in ("", b""):
            return True  # getaddrinfo(None) and "" mean this machine
        name = (host.decode("ascii", "replace") if isinstance(host, bytes) else str(host)).lower().rstrip(".")
        if name in LOCAL_NAMES or name.endswith(".localhost") or name == socket.gethostname().lower():
            return True
        try:
            address = ipaddress.ip_address(name)
        except ValueError:
            return False
        address = getattr(address, "ipv4_mapped", None) or address
        return address.is_loopback or address.is_unspecified

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def checked(target: object, name: str, address_of: Callable[..., Any]) -> None:
            real = getattr(target, name, None)
            if real is None:
                return  # Windows sockets have no sendmsg

            def guarded(*args: Any, **kwargs: Any) -> Any:
                self.allow(address_of(*args, **kwargs))
                return real(*args, **kwargs)

            monkeypatch.setattr(target, name, guarded)

        checked(socket.socket, "connect", lambda sock, address: address)
        checked(socket.socket, "connect_ex", lambda sock, address: address)
        checked(socket.socket, "sendto", lambda sock, *args: args[-1])
        checked(socket.socket, "sendmsg", lambda sock, *args, **kwargs: args[3] if len(args) > 3 else None)
        for lookup in ("getaddrinfo", "gethostbyname", "gethostbyname_ex", "gethostbyaddr"):
            checked(socket, lookup, lambda host=None, *args, **kwargs: (host,))
        checked(socket, "getnameinfo", lambda address, *args: address)

    def allow(self, address: Any) -> None:
        if not isinstance(address, tuple):
            return  # a Unix socket's path, or a socket that is already connected
        host = address[0]
        if self.is_local(host):
            return
        self.attempts.append(str(host))
        raise NetworkBlocked(str(host))

    def check(self) -> None:
        if self.attempts:
            hosts = ", ".join(sorted(set(self.attempts)))
            pytest.fail(f"This test tried to use the network and something swallowed the error: {hosts}", pytrace=False)
