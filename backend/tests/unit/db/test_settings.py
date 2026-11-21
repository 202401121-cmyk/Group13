"""Database settings (US-01): optional URL, a bounded timeout, and a URL that never shows up in text."""

from __future__ import annotations

import logging

import pytest

from app.db import describe_host, load_settings
from app.db.settings import (
    DEFAULT_SAVE_TIMEOUT_S,
    DEFAULT_TIMEOUT_S,
    MAX_SAVE_TIMEOUT_S,
    MAX_TIMEOUT_S,
    MIN_SAVE_TIMEOUT_S,
    MIN_TIMEOUT_S,
)

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


# --- the search save's own time limit (US-52, P-38) ----------------------------------------------------------------


def test_a_search_save_has_its_own_limit_of_15_seconds_by_default() -> None:
    settings = load_settings({})
    assert settings.save_timeout_s == DEFAULT_SAVE_TIMEOUT_S == 15.0
    assert settings.timeout_s == DEFAULT_TIMEOUT_S == 2.0  # every other call keeps DB_TIMEOUT_SECONDS


def test_the_save_limit_is_read_alone_and_never_changes_the_other_calls() -> None:
    settings = load_settings({"DB_SAVE_TIMEOUT_SECONDS": "12", "DB_TIMEOUT_SECONDS": "3"})
    assert (settings.save_timeout_s, settings.timeout_s) == (12.0, 3.0)
    assert load_settings({"DB_TIMEOUT_SECONDS": "30"}).save_timeout_s == DEFAULT_SAVE_TIMEOUT_S


@pytest.mark.parametrize(("raw", "expected"), [("2", 2.0), ("9.5", 9.5), ("15", 15.0)])
def test_the_save_limit_is_allowed_from_2_to_15_seconds(
    raw: str, expected: float, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        assert load_settings({"DB_SAVE_TIMEOUT_SECONDS": raw}).save_timeout_s == expected
    assert caplog.text == ""


@pytest.mark.parametrize("raw", ["15.5", "16", "20", "30", "90"])
def test_a_save_limit_above_15_seconds_is_refused_and_15_is_used(raw: str, caplog: pytest.LogCaptureFixture) -> None:
    """The frontend waits 170 s for research: 150 + 15 + the 1 s grace is the most that fits (P-38)."""
    assert MAX_SAVE_TIMEOUT_S == 15.0
    with caplog.at_level(logging.WARNING):
        assert load_settings({"DB_SAVE_TIMEOUT_SECONDS": raw}).save_timeout_s == MAX_SAVE_TIMEOUT_S
    assert "DB_SAVE_TIMEOUT_SECONDS must be between 2 and 15; using 15" in caplog.text


def test_a_save_limit_below_2_seconds_is_refused_and_2_is_used(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        assert load_settings({"DB_SAVE_TIMEOUT_SECONDS": "1"}).save_timeout_s == MIN_SAVE_TIMEOUT_S == 2.0
    assert "DB_SAVE_TIMEOUT_SECONDS must be between 2 and 15; using 2" in caplog.text


def test_the_longest_wait_for_a_save_fits_inside_the_frontends_170_seconds() -> None:
    from app.contracts import SEARCH_TIME_LIMIT_S
    from app.db import SAVE_GRACE_S

    frontend_research_wait_s = 170  # frontend/src/lib/api: RESEARCH_TIMEOUT_MS (D-43)
    assert frontend_research_wait_s > SEARCH_TIME_LIMIT_S + MAX_SAVE_TIMEOUT_S + SAVE_GRACE_S


@pytest.mark.parametrize("raw", ["soon", "-2", "0", "nan", "inf"])
def test_a_bad_save_limit_falls_back_to_15_seconds_with_a_warning(raw: str, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        assert load_settings({"DB_SAVE_TIMEOUT_SECONDS": raw}).save_timeout_s == DEFAULT_SAVE_TIMEOUT_S
    assert "DB_SAVE_TIMEOUT_SECONDS must be a positive number" in caplog.text


def test_the_save_limit_shows_in_repr_and_the_url_still_never_does() -> None:
    text = repr(load_settings({"DATABASE_URL": SECRET_URL, "DB_SAVE_TIMEOUT_SECONDS": "12"}))
    assert "save_timeout_s=12.0" in text
    assert FAKE_PASSWORD not in text
