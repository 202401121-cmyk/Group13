"""No test may reach the internet (US-32): any outbound connection fails at once, with a message saying what to do."""

from __future__ import annotations

import contextlib
import socket
import textwrap
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from tests.network_guard import NetworkBlocked, NetworkGuard, may_use_network

pytest_plugins = ["pytester"]
BACKEND = Path(__file__).resolve().parents[2]


def test_an_outbound_connection_fails_at_once_with_a_clear_message(network_guard: NetworkGuard) -> None:
    with (
        socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock,
        pytest.raises(NetworkBlocked, match=r"may not use the network.*93\.184\.216\.34.*tests/fixtures/recorded"),
    ):
        sock.connect(("93.184.216.34", 443))
    network_guard.attempts.clear()


def test_connect_ex_is_blocked_too(network_guard: NetworkGuard) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock, pytest.raises(NetworkBlocked):
        sock.connect_ex(("93.184.216.34", 443))
    network_guard.attempts.clear()


def test_a_dns_lookup_for_an_outside_host_is_blocked(network_guard: NetworkGuard) -> None:
    with pytest.raises(NetworkBlocked, match=r"api\.groq\.com"):
        socket.getaddrinfo("api.groq.com", 443)
    assert network_guard.attempts == ["api.groq.com"]
    network_guard.attempts.clear()


@pytest.mark.parametrize("lookup", [socket.gethostbyname, socket.gethostbyname_ex, socket.gethostbyaddr])
def test_the_older_dns_calls_are_blocked_too(network_guard: NetworkGuard, lookup: object) -> None:
    name = lookup.__name__  # type: ignore[attr-defined]
    with pytest.raises(NetworkBlocked, match=r"api\.groq\.com"):
        getattr(socket, name)("api.groq.com")
    network_guard.attempts.clear()


def test_a_reverse_lookup_is_blocked(network_guard: NetworkGuard) -> None:
    with pytest.raises(NetworkBlocked, match=r"93\.184\.216\.34"):
        socket.getnameinfo(("93.184.216.34", 443), 0)
    network_guard.attempts.clear()


def test_udp_to_an_outside_host_is_blocked(network_guard: NetworkGuard) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock, pytest.raises(NetworkBlocked):
        sock.sendto(b"x", ("93.184.216.34", 53))
    network_guard.attempts.clear()


def test_udp_to_this_machine_still_works() -> None:
    with (
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server,
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock,
    ):
        server.bind(("127.0.0.1", 0))
        assert sock.sendto(b"x", server.getsockname()) == 1


def test_this_machines_own_name_is_local() -> None:
    assert NetworkGuard.is_local(socket.gethostname())


def test_httpx_to_an_outside_host_is_blocked(network_guard: NetworkGuard) -> None:
    with httpx.Client() as http, pytest.raises(NetworkBlocked, match=r"api\.groq\.com"):
        http.get("https://api.groq.com/openai/v1/models")
    network_guard.attempts.clear()


def test_localhost_still_connects() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        assert socket.getaddrinfo("localhost", port)
        with socket.create_connection(("127.0.0.1", port), timeout=2) as client:
            assert client.getpeername()[1] == port


@pytest.mark.parametrize(
    "host", ["localhost", "127.0.0.1", "127.0.0.53", "::1", "::ffff:127.0.0.1", "app.localhost", None, ""]
)
def test_these_hosts_are_local(host: str | None) -> None:
    assert NetworkGuard.is_local(host)


@pytest.mark.parametrize("host", ["api.groq.com", "93.184.216.34", "10.0.0.5", "localhost.example.com"])
def test_these_hosts_are_not_local(host: str) -> None:
    assert not NetworkGuard.is_local(host)


def test_a_swallowed_attempt_still_fails_the_test(network_guard: NetworkGuard) -> None:
    """The LLM client catches every exception, so a blocked call could fall back and pass unnoticed."""
    with contextlib.suppress(Exception):
        socket.getaddrinfo("api.groq.com", 443)
    with pytest.raises(pytest.fail.Exception, match=r"api\.groq\.com"):
        network_guard.check()
    network_guard.attempts.clear()


def test_the_fixture_itself_fails_a_test_that_swallowed_the_error(pytester: pytest.Pytester) -> None:
    pytester.syspathinsert(BACKEND)
    pytester.makeconftest("from tests.conftest import network_guard")
    pytester.makepyfile(
        """
        import contextlib
        import socket

        def test_swallows_it():
            with contextlib.suppress(Exception):
                socket.getaddrinfo("api.groq.com", 443)
        """
    )
    result = pytester.runpytest_inprocess("-p", "no:cacheprovider")
    result.assert_outcomes(passed=1, errors=1)
    result.stdout.fnmatch_lines(["*something swallowed the error: api.groq.com*"])


def test_a_live_test_may_use_the_network() -> None:
    live = SimpleNamespace(get_closest_marker=lambda name: object() if name == "live" else None)
    other = SimpleNamespace(get_closest_marker=lambda name: None)
    assert may_use_network(live)
    assert not may_use_network(other)


# The fixtures that guard model downloads, by name: a star import leaves out names that start with "_".
GUARDS = (
    "from tests.conftest import (network_guard, _no_model_downloads_this_session, _no_real_matcher_model,"
    " _no_real_nli_model, _no_real_memory_model)"
)

LATE_LOAD = """
    import threading
    from app.entities import model
    from app.explain import nli
    from app.memory import embedder

    later = threading.Event()
    done = threading.Event()
    refused = []

    def load_late():
        later.wait(30)
        for module in (model, nli, embedder):
            try:
                module._hf_download(repo_id="example/model", filename="model.onnx", revision="main")
            except Exception as exc:
                refused.append(type(exc).__name__)
        done.set()

    def test_starts_a_load_that_outlives_it():
        threading.Thread(target=load_late, name="memory-load", daemon=True).start()

    def test_runs_while_that_load_downloads():
        later.set()
        assert done.wait(30)
        assert refused == ["RuntimeError"] * 3
"""


def test_a_model_load_that_outlives_its_test_never_reaches_the_network(pytester: pytest.Pytester) -> None:
    """S5-12's CI flake: each model loads in a thread of its own (matcher-load, nli-load, memory-load), which can reach
    its download after the test that started it has ended and that test's patches are undone. No such late download
    may reach the network, or be blamed on the test running then."""
    pytester.syspathinsert(BACKEND)
    pytester.makeconftest(GUARDS)
    pytester.makepyfile(LATE_LOAD)
    result = pytester.runpytest_inprocess("-p", "no:cacheprovider")
    result.assert_outcomes(passed=2)


BETWEEN_TESTS = """
    import threading
    import types

    import pytest

    from app.entities import model
    from app.explain import nli
    from app.memory import embedder

    later = threading.Event()
    done = threading.Event()
    refusals = []

    def load_late():
        later.wait(30)
        for module in (model, nli, embedder):
            try:
                module._hf_download(repo_id="example/model", filename="model.onnx", revision="main")
            except Exception as exc:
                refusals.append(str(exc))
        done.set()

    def pytest_runtest_logfinish(nodeid, location):
        # After the first test's teardown has undone its patches, before the next test's guards are set up.
        if nodeid.endswith("test_starts_a_load_that_outlives_it"):
            later.set()
            done.wait(30)

    @pytest.fixture
    def late():
        return types.SimpleNamespace(load=load_late, done=done, refusals=refusals)
"""


def test_a_load_that_downloads_between_two_tests_meets_the_session_blocker(pytester: pytest.Pytester) -> None:
    """The case the session's blocker is for: a load reaches its download after its own test's patches are undone and
    before the next test's guards are in place. Only the session's blocker stands then, and it refuses."""
    pytester.syspathinsert(BACKEND)
    pytester.makeconftest(GUARDS + "\n" + textwrap.dedent(BETWEEN_TESTS))
    pytester.makepyfile(
        """
        import threading

        def test_starts_a_load_that_outlives_it(late):
            threading.Thread(target=late.load, name="memory-load", daemon=True).start()

        def test_after_it(late):
            assert late.done.is_set()
            assert len(late.refusals) == 3
            assert all("outlived the test that started it" in r for r in late.refusals)
        """
    )
    result = pytester.runpytest_inprocess("-p", "no:cacheprovider")
    result.assert_outcomes(passed=2)


def test_a_test_that_starts_a_model_download_itself_still_fails(pytester: pytest.Pytester) -> None:
    pytester.syspathinsert(BACKEND)
    pytester.makeconftest(GUARDS)
    pytester.makepyfile(
        """
        import threading
        from app.memory import embedder

        def test_loads_the_memory_model():
            def load():
                try:
                    embedder._hf_download(repo_id="example/model", filename="model.onnx", revision="main")
                except Exception:
                    pass
            threading.Thread(target=load, name="memory-load", daemon=True).start()
        """
    )
    result = pytester.runpytest_inprocess("-p", "no:cacheprovider")
    result.assert_outcomes(passed=1, errors=1)
    result.stdout.fnmatch_lines(["*a test tried to download the memory model*"])
