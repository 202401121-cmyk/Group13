"""Every research client is built the way the LLM clients are, and one that can't be built never crashes a search."""

from __future__ import annotations

import logging

import httpx
import pytest

from app.research import client as research_client
from app.research.client import ClientUnavailable, build_research_client
from app.research.reddit.factory import build_reddit_source
from app.research.reddit.settings import RedditSettings
from scripts import smoke_sources

PROXY_WITH_A_PASSWORD = (
    "socks5://" + ":".join(("someone", "not-real")) + "@proxy.example:1080"
)  # built, so no scanner trips


@pytest.fixture(autouse=True)
def _fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    research_client.note_no_client.cache_clear()
    for name in ("ALL_PROXY", "HTTPS_PROXY", "HTTP_PROXY", "all_proxy", "https_proxy", "http_proxy"):
        monkeypatch.delenv(name, raising=False)


def test_research_clients_are_built_like_llm_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[bool] = []
    real = httpx.Client()

    def build(force_ipv4: bool, **options: object) -> httpx.Client:
        asked.append(force_ipv4)
        return real

    monkeypatch.setattr(research_client, "build_http_client", build)
    assert build_research_client() is real
    assert asked == [True]  # models.yaml's force_ipv4


def test_force_ipv4_off_in_config_is_followed(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[bool] = []
    real = httpx.Client()

    class Config:
        force_ipv4 = False

    monkeypatch.setattr(research_client, "load_llm_config", lambda: Config())
    monkeypatch.setattr(research_client, "build_http_client", lambda force_ipv4, **_: asked.append(force_ipv4) or real)
    build_research_client()
    assert asked == [False]


def test_a_broken_llm_config_still_connects_over_ipv4(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[bool] = []
    real = httpx.Client()

    def broken() -> object:
        raise ValueError("made up")

    monkeypatch.setattr(research_client, "load_llm_config", broken)
    monkeypatch.setattr(research_client, "build_http_client", lambda force_ipv4, **_: asked.append(force_ipv4) or real)
    assert build_research_client() is real
    assert asked == [True]


def test_a_client_that_cannot_be_built_raises_a_plain_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALL_PROXY", PROXY_WITH_A_PASSWORD)
    with pytest.raises(ClientUnavailable) as caught:
        build_research_client()
    assert "not-real" not in str(caught.value)
    assert "proxy.example" not in str(caught.value)


def test_the_log_shows_the_error_type_never_the_proxy_url(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("ALL_PROXY", PROXY_WITH_A_PASSWORD)
    with caplog.at_level(logging.DEBUG), pytest.raises(ClientUnavailable):
        build_research_client()
    assert "ImportError" in caplog.text
    assert "not-real" not in caplog.text
    assert "proxy.example" not in caplog.text


def test_the_error_is_logged_once(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setenv("ALL_PROXY", PROXY_WITH_A_PASSWORD)
    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            with pytest.raises(ClientUnavailable):
                build_research_client()
    assert len([r for r in caplog.records if "research" in r.getMessage()]) == 1


def test_the_reddit_factory_uses_the_same_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    built: list[httpx.Client] = []

    def build() -> httpx.Client:
        client = httpx.Client()
        built.append(client)
        return client

    monkeypatch.setattr("app.research.reddit.factory.build_research_client", build)
    assert build_reddit_source(RedditSettings(mode="archive")) is not None
    assert len(built) == 1


def test_the_reddit_factory_builds_no_client_when_reddit_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    def build() -> httpx.Client:
        raise AssertionError("no client is needed")

    monkeypatch.setattr("app.research.reddit.factory.build_research_client", build)
    assert build_reddit_source(RedditSettings(mode="off")) is None


def test_the_official_reddit_path_needs_no_client(monkeypatch: pytest.MonkeyPatch) -> None:
    def build() -> httpx.Client:
        raise AssertionError("PRAW brings its own")

    monkeypatch.setattr("app.research.reddit.factory.build_research_client", build)
    settings = RedditSettings(mode="official", client_id="a", client_secret="b", user_agent="c")
    assert build_reddit_source(settings) is not None


def test_smoke_sources_uses_the_same_builder(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    built: list[httpx.Client] = []

    def build() -> httpx.Client:
        client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(522)))
        built.append(client)
        return client

    monkeypatch.setattr(smoke_sources, "build_research_client", build)
    assert smoke_sources.main([]) == 0
    assert len(built) == 1
    assert "no" in capsys.readouterr().out


def test_smoke_sources_says_so_when_no_client_can_be_built(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ALL_PROXY", PROXY_WITH_A_PASSWORD)
    assert smoke_sources.main([]) == 0
    out = capsys.readouterr().out
    assert "could not" in out
    assert "not-real" not in out


def test_the_error_text_never_holds_the_proxy_url(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def build(force_ipv4: bool, **options: object) -> httpx.Client:
        raise ValueError(f"bad proxy {PROXY_WITH_A_PASSWORD}")

    monkeypatch.setattr(research_client, "build_http_client", build)
    with caplog.at_level(logging.DEBUG), pytest.raises(ClientUnavailable) as caught:
        build_research_client()
    assert str(caught.value) == "ValueError"
    assert caught.value.__cause__ is None
    assert "not-real" not in caplog.text
