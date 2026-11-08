"""LLM calls connect over IPv4 by default: on some networks IPv6 hangs until it times out, using up a stage's limit."""

from __future__ import annotations

import logging
import re
import socket
import ssl
import sys
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.request import getproxies_environment

import httpx
import pytest
from pydantic import BaseModel

import app.llm
from app.contracts import DataClass, Difficulty, IntakeInput, LLMTask, LLMUnavailable, Provider, StageContext
from app.intake import IntakeStage
from app.llm import build_llm_client, load_llm_config
from app.llm.config import DEFAULT_CONFIG_PATH, with_env_overrides
from app.llm.providers import build_http_client, note_ipv4_skipped, note_no_client

SETTING = re.compile(r"^force_ipv4: \w+\n", re.MULTILINE)
ON = "force_ipv4: true\n"
OFF = "force_ipv4: false\n"
KEYS = {settings.api_key_env: "fake-key" for settings in load_llm_config().providers}
CARRIES_HTTPS = ("HTTPS_PROXY", "ALL_PROXY")
PROVIDERS_LOG = "app.llm.providers"


@dataclass
class LocalServer:
    port: int
    paths: list[str]


@pytest.fixture(autouse=True)
def no_proxy_on_this_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests set their own proxies, so a proxy on a developer's machine must not change what they see."""
    for name in (*CARRIES_HTTPS, "HTTP_PROXY", "NO_PROXY"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)
    monkeypatch.setattr("httpx._utils.getproxies", getproxies_environment)  # the environment, not the registry
    monkeypatch.setitem(sys.modules, "socksio", None)  # as if not installed, even for someone who has httpx[socks]
    note_ipv4_skipped.cache_clear()
    note_no_client.cache_clear()


def config_file(folder: Path, setting: str) -> Path:
    """The real models.yaml with this setting, so these tests hold whatever a member has set in the tracked file."""
    text, found = SETTING.subn(setting, DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    assert found == 1
    path = folder / "models.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def with_a_login(url: str) -> str:
    """A made-up login, added at run time: CI's secret scan fails on a URL with a password written out in the source."""
    return str(httpx.URL(url).copy_with(username="campus-user", password="not-a-real-secret"))


def pool_of(http: httpx.Client) -> Any:
    transport = http._transport
    assert isinstance(transport, httpx.HTTPTransport)
    return transport._pool


def local_addresses(adapters: dict[Provider, Any]) -> set[str | None]:
    return {pool_of(adapter._http)._local_address for adapter in adapters.values()}


@pytest.fixture
def local_server() -> Iterator[LocalServer]:
    paths: list[str] = []

    class Answer(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            paths.append(self.path)
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Answer)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    yield LocalServer(server.server_address[1], paths)
    server.shutdown()
    server.server_close()
    thread.join(5)


def test_every_provider_gets_the_ipv4_transport_when_the_setting_is_on(tmp_path: Path) -> None:
    client = build_llm_client(config_file(tmp_path, ON), env=KEYS)
    assert set(client.adapters) == set(Provider)
    assert local_addresses(client.adapters) == {"0.0.0.0"}


def test_every_provider_gets_the_default_transport_when_the_setting_is_off(tmp_path: Path) -> None:
    client = build_llm_client(config_file(tmp_path, OFF), env=KEYS)
    assert set(client.adapters) == set(Provider)
    assert local_addresses(client.adapters) == {None}


def test_the_model_config_file_turns_the_setting_on() -> None:
    assert DEFAULT_CONFIG_PATH.read_text(encoding="utf-8").count(ON) == 1
    assert load_llm_config().force_ipv4 is True


def test_a_config_file_with_no_setting_means_on(tmp_path: Path) -> None:
    assert load_llm_config(config_file(tmp_path, "")).force_ipv4 is True


def test_env_overrides_keep_the_setting(tmp_path: Path) -> None:
    """build_llm_client always applies them, so losing the setting here would make the off switch do nothing."""
    config = load_llm_config(config_file(tmp_path, OFF))
    assert config.force_ipv4 is False
    assert with_env_overrides(config, {}).force_ipv4 is False
    assert with_env_overrides(config, {"MISTRAL_MODEL": "ministral-3b-latest"}).force_ipv4 is False


def test_only_the_local_address_differs_between_the_transports() -> None:
    on, off = pool_of(build_http_client(True)), pool_of(build_http_client(False))
    assert (on._local_address, off._local_address) == ("0.0.0.0", None)
    same = (
        "_max_connections",
        "_max_keepalive_connections",
        "_keepalive_expiry",
        "_retries",
        "_http1",
        "_http2",
        "_uds",
        "_socket_options",
    )
    for name in same:
        assert getattr(on, name) == getattr(off, name), name
    assert on._ssl_context.verify_mode == off._ssl_context.verify_mode == ssl.CERT_REQUIRED
    assert build_http_client(True).timeout == build_http_client(False).timeout


@pytest.mark.parametrize("name", [*CARRIES_HTTPS, *(name.lower() for name in CARRIES_HTTPS)])
def test_with_a_proxy_set_the_client_is_built_as_before(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    """Upper and lower case: httpx honours both, and lower case is the usual spelling on Linux."""
    monkeypatch.setenv(name, "http://127.0.0.1:9")
    built = build_http_client(True)
    assert pool_of(built)._local_address is None
    assert set(built._mounts) == set(httpx.Client()._mounts) != set()


def test_a_system_proxy_for_https_counts_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """A campus proxy is often set in Windows Settings, with no environment variable at all."""
    monkeypatch.setattr("httpx._utils.getproxies", lambda: {"https": "http://127.0.0.1:9"})
    assert pool_of(build_http_client(True))._local_address is None


@pytest.mark.parametrize("name", ["HTTP_PROXY", "http_proxy"])
def test_http_proxy_alone_leaves_ipv4_only_on(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, name: str
) -> None:
    """Every provider URL is https, so it would carry none of their requests, and the stall would still be ours."""
    monkeypatch.setenv(name, "http://127.0.0.1:9")
    with caplog.at_level(logging.INFO, logger=PROVIDERS_LOG):
        built = build_http_client(True)
    assert pool_of(built)._local_address == "0.0.0.0"
    assert caplog.records == []


def test_a_system_proxy_for_http_only_leaves_ipv4_only_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("httpx._utils.getproxies", lambda: {"http": "http://127.0.0.1:9"})
    assert pool_of(build_http_client(True))._local_address == "0.0.0.0"


def test_with_no_proxy_the_client_is_ipv4_only_and_nothing_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger=PROVIDERS_LOG):
        built = build_http_client(True)
    assert pool_of(built)._local_address == "0.0.0.0"
    assert not built._mounts
    assert caplog.records == []


@pytest.mark.parametrize("no_proxy", ["*", "localhost, *"])
def test_no_proxy_star_means_no_proxy_is_in_use(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, no_proxy: str
) -> None:
    """httpx then connects directly, so the stall is ours again and IPv4 only stays on."""
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("NO_PROXY", no_proxy)
    assert not httpx.Client()._mounts  # httpx agrees that no proxy is in use
    with caplog.at_level(logging.INFO, logger=PROVIDERS_LOG):
        built = build_http_client(True)
    assert pool_of(built)._local_address == "0.0.0.0"
    assert caplog.records == []


def test_a_proxy_of_another_kind_does_not_count(monkeypatch: pytest.MonkeyPatch) -> None:
    """httpx reads only http, https and all, so an FTP proxy leaves the connection ours."""
    monkeypatch.setenv("FTP_PROXY", "http://127.0.0.1:9")
    assert pool_of(build_http_client(True))._local_address == "0.0.0.0"


def test_skipping_ipv4_is_logged_once_and_never_shows_the_proxy(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("HTTPS_PROXY", with_a_login("http://127.0.0.1:9"))
    with caplog.at_level(logging.INFO, logger=PROVIDERS_LOG):
        build_http_client(True)
        build_http_client(True)
    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == logging.WARNING
    assert "force_ipv4" in caplog.text and "proxy" in caplog.text
    assert (
        "campus-user" not in caplog.text and "not-a-real-secret" not in caplog.text and "127.0.0.1" not in caplog.text
    )


def test_with_the_setting_off_a_proxy_logs_nothing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Nothing was skipped, so there is nothing to say."""
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    with caplog.at_level(logging.INFO, logger=PROVIDERS_LOG):
        assert build_http_client(False)._mounts
    assert caplog.records == []


def test_a_request_really_goes_through_the_proxy(local_server: LocalServer, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALL_PROXY", f"http://127.0.0.1:{local_server.port}")
    with build_http_client(True) as http:
        assert http.get("http://provider.test/v1/models").status_code == 200
    assert local_server.paths == ["http://provider.test/v1/models"]


def test_options_reach_the_client_on_every_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """The smoke command sets its own timeout, so it has to survive whichever client is built."""
    expected = httpx.Timeout(20.0)
    assert build_http_client(True, timeout=20.0).timeout == expected
    assert build_http_client(False, timeout=20.0).timeout == expected
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    assert build_http_client(True, timeout=20.0).timeout == expected


def detect_intent(client: Any) -> Any:
    return IntakeStage(client).run(
        IntakeInput(raw_query="best laptop for coding under 70k"), StageContext("srch_noclient01")
    )


@pytest.mark.parametrize("proxy", ["socks5://127.0.0.1:1", "socks4://127.0.0.1:1"])
def test_a_client_that_cannot_be_built_leaves_intake_on_its_keyword_rules(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, proxy: str
) -> None:
    """socks5 needs a package we don't install, and httpx refuses socks4 outright. Neither may crash a request."""
    monkeypatch.setenv("ALL_PROXY", proxy)
    with caplog.at_level(logging.INFO):
        client = build_llm_client(env=KEYS)
        intent = detect_intent(client)
    assert client.adapters == {}
    assert (intent.detected_by, intent.category) == ("rules", "laptop")
    assert "127.0.0.1" not in caplog.text  # a ValueError from httpx names the proxy's host, so only its type is logged
    assert "is not set" not in caplog.text  # the keys are fine, so nothing may say they are missing


def test_a_bulk_task_with_no_client_is_simply_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bulk tasks rotate through the pool, so this proves an empty list of models gets through that step too."""
    monkeypatch.setenv("ALL_PROXY", "socks5://127.0.0.1:1")

    class Claims(BaseModel):
        claims: list[str]

    client = build_llm_client(env=KEYS)
    with pytest.raises(LLMUnavailable):
        client.complete(
            LLMTask.EXTRACT_CLAIMS, "s", "u", Claims, data_class=DataClass.REDDIT, difficulty=Difficulty.LONG
        )


def test_with_the_setting_off_a_bad_proxy_does_not_crash_either(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALL_PROXY", "socks5://127.0.0.1:1")
    assert detect_intent(build_llm_client(config_file(tmp_path, OFF), env=KEYS)).detected_by == "rules"


def test_a_client_that_cannot_be_built_is_logged_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("ALL_PROXY", with_a_login("socks5://127.0.0.1:1"))
    with caplog.at_level(logging.WARNING, logger="app.llm"):
        build_llm_client(env=KEYS)
        build_llm_client(env=KEYS)
    about_the_client = [record for record in caplog.records if "unavailable" in record.getMessage()]
    assert len(about_the_client) == 1
    assert "ImportError" in caplog.text
    assert "not-a-real-secret" not in caplog.text and "campus-user" not in caplog.text


def test_a_client_with_the_setting_on_reaches_a_local_server_on_127_0_0_1(local_server: LocalServer) -> None:
    with build_http_client(True) as http:
        response = http.get(f"http://127.0.0.1:{local_server.port}/")
    assert (response.status_code, response.text) == (200, "ok")


def test_an_ipv6_address_listed_first_is_skipped_without_a_connection_attempt(
    local_server: LocalServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real failure: DNS lists IPv6 first and connecting to it hangs. No IPv6 connection may even be tried."""

    def ipv6_first(host: str, port: int, *args: Any, **kwargs: Any) -> list[Any]:
        return [
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", port, 0, 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port)),
        ]

    tried: list[int] = []
    real_connect = socket.socket.connect

    def spy(sock: socket.socket, address: Any) -> None:
        tried.append(sock.family)
        real_connect(sock, address)

    monkeypatch.setattr(socket, "getaddrinfo", ipv6_first)
    monkeypatch.setattr(socket.socket, "connect", spy)

    with build_http_client(True) as http:
        response = http.get(f"http://both-families.test:{local_server.port}/")

    assert response.status_code == 200
    assert tried == [socket.AF_INET]


def test_nothing_else_in_app_llm_builds_a_client() -> None:
    folder = Path(app.llm.__file__).parent
    builders = {path.name for path in folder.glob("*.py") if re.search(r"httpx\.Client\(", path.read_text("utf-8"))}
    assert builders == {"providers.py"}
