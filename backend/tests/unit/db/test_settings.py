"""Database settings (US-01): optional URL, a bounded timeout, and a URL that never shows up in text."""

from __future__ import annotations

import logging

import pytest

from app.db import describe_host, load_settings
from app.db.settings import DEFAULT_TIMEOUT_S, MAX_TIMEOUT_S, MIN_TIMEOUT_S

FAKE_PASSWORD = "SuperSecret123"  # made up; the URL is built in parts so no real-looking secret sits in the code
SECRET_URL = "postgresql://" + "postgres.abcdef:" + FAKE_PASSWORD + "@aws-0-ap-south-1.pooler.example.com:5432/postgres"


@pytest.mark.parametrize("env", [{}, {"DATABASE_URL": ""}, {"DATABASE_URL": "   "}])
def test_no_url_means_the_database_is_off(env: dict[str, str]) -> None:
    settings = load_settings(env)
    assert settings.url is None
    assert not settings.enabled
    assert settings.timeout_s == DEFAULT_TIMEOUT_S


def test_a_url_turns_the_database_on() -> None:
    settings = load_settings({"DATABASE_URL": f"  {SECRET_URL}  ", "DB_TIMEOUT_SECONDS": "3.5"})
    assert settings.enabled
    assert settings.url == SECRET_URL
    assert settings.timeout_s == 3.5


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("", DEFAULT_TIMEOUT_S), ("1", 1.0), ("0.1", MIN_TIMEOUT_S), ("600", MAX_TIMEOUT_S)],
)
def test_the_timeout_is_kept_between_its_limits(raw: str, expected: float) -> None:
    assert load_settings({"DB_TIMEOUT_SECONDS": raw}).timeout_s == expected


@pytest.mark.parametrize("raw", ["soon", "-2", "0", "nan", "inf"])
def test_a_bad_timeout_falls_back_to_the_default_with_a_warning(raw: str, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        assert load_settings({"DB_TIMEOUT_SECONDS": raw}).timeout_s == DEFAULT_TIMEOUT_S
    assert "DB_TIMEOUT_SECONDS must be a positive number" in caplog.text


def test_the_url_never_appears_in_repr_or_str() -> None:
    settings = load_settings({"DATABASE_URL": SECRET_URL})
    for text in (repr(settings), str(settings), f"{settings}"):
        assert FAKE_PASSWORD not in text
        assert "postgresql://" not in text
        assert "aws-0-ap-south-1.pooler.example.com" in text  # the host is the one part that may show


@pytest.mark.parametrize(
    ("url", "host"),
    [
        (SECRET_URL, "aws-0-ap-south-1.pooler.example.com"),
        ("postgresql://u@127.0.0.1:1/db", "127.0.0.1"),
        ("host=db.example password=x", "unknown"),  # a key=value string: no host to show
        (None, "none"),
    ],
)
def test_describe_host_gives_the_host_and_nothing_else(url: str | None, host: str) -> None:
    assert describe_host(url) == host
