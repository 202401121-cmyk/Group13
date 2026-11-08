"""The search-run settings (US-12): worker count, how long finished runs are kept, and the keep-alive interval."""

from __future__ import annotations

import logging

import pytest

from app.api.search_runs import RunSettings, load_run_settings


def test_the_defaults_are_4_workers_30_minutes_15_seconds_and_500_kept_runs() -> None:
    settings = load_run_settings({})
    assert settings == RunSettings()
    assert (settings.workers, settings.keep_s, settings.ping_s, settings.max_kept) == (4, 30 * 60.0, 15.0, 500)


def test_values_are_read_from_the_environment() -> None:
    env = {
        "SEARCH_RUN_WORKERS": "2",
        "SEARCH_RUN_KEEP_MINUTES": "5",
        "SEARCH_RUN_PING_SECONDS": "0.5",
        "SEARCH_RUN_MAX_KEPT": "50",
    }
    assert load_run_settings(env) == RunSettings(workers=2, keep_s=300.0, ping_s=0.5, max_kept=50)


@pytest.mark.parametrize(
    ("name", "raw"),
    [
        ("SEARCH_RUN_WORKERS", "four"),
        ("SEARCH_RUN_WORKERS", "2.5"),
        ("SEARCH_RUN_WORKERS", "0"),
        ("SEARCH_RUN_KEEP_MINUTES", "-1"),
        ("SEARCH_RUN_KEEP_MINUTES", "nan"),
        ("SEARCH_RUN_PING_SECONDS", "inf"),
        ("SEARCH_RUN_PING_SECONDS", "soon"),
        ("SEARCH_RUN_MAX_KEPT", "lots"),
        ("SEARCH_RUN_MAX_KEPT", "0"),
        ("SEARCH_RUN_MAX_KEPT", "12.5"),
        ("SEARCH_RUN_MAX_KEPT", "9" * 400),  # too big for a float: once crashed start-up
        ("SEARCH_RUN_WORKERS", "9" * 400),
    ],
)
def test_a_bad_value_falls_back_to_the_default_with_a_warning(
    name: str, raw: str, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        assert load_run_settings({name: raw}) == RunSettings()
    assert name in caplog.text


def test_a_blank_value_means_the_default_without_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    env = {
        "SEARCH_RUN_WORKERS": " ",
        "SEARCH_RUN_KEEP_MINUTES": "",
        "SEARCH_RUN_PING_SECONDS": "",
        "SEARCH_RUN_MAX_KEPT": "",
    }
    with caplog.at_level(logging.WARNING):
        assert load_run_settings(env) == RunSettings()
    assert caplog.text == ""


def test_values_are_kept_within_safe_limits_with_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        high = load_run_settings(
            {"SEARCH_RUN_WORKERS": "40", "SEARCH_RUN_KEEP_MINUTES": "99999", "SEARCH_RUN_PING_SECONDS": "600"}
        )
    assert high == RunSettings(workers=16, keep_s=1440 * 60.0, ping_s=15.0)
    assert all(
        name in caplog.text for name in ("SEARCH_RUN_WORKERS", "SEARCH_RUN_KEEP_MINUTES", "SEARCH_RUN_PING_SECONDS")
    )
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        low = load_run_settings({"SEARCH_RUN_KEEP_MINUTES": "0.001", "SEARCH_RUN_PING_SECONDS": "0.0001"})
    assert (low.keep_s, low.ping_s) == (60.0, 0.01)
    assert "between" in caplog.text


def test_the_keep_alive_is_never_longer_than_fastapis_own() -> None:
    """FastAPI sends its own keep-alive after 15 s of silence, so a longer setting would not be honoured."""
    assert load_run_settings({"SEARCH_RUN_PING_SECONDS": "15"}).ping_s == 15.0
    assert load_run_settings({"SEARCH_RUN_PING_SECONDS": "16"}).ping_s == 15.0


def test_a_value_inside_the_limits_gives_no_warning(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        load_run_settings({"SEARCH_RUN_WORKERS": "16", "SEARCH_RUN_KEEP_MINUTES": "1", "SEARCH_RUN_PING_SECONDS": "15"})
    assert caplog.text == ""


def test_the_kept_runs_limit_is_1_to_10000(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        assert load_run_settings({"SEARCH_RUN_MAX_KEPT": "1"}).max_kept == 1
        assert load_run_settings({"SEARCH_RUN_MAX_KEPT": "10000"}).max_kept == 10_000
    assert caplog.text == ""
    with caplog.at_level(logging.WARNING):
        assert load_run_settings({"SEARCH_RUN_MAX_KEPT": "20000"}).max_kept == 10_000
    assert "SEARCH_RUN_MAX_KEPT must be between 1 and 10000" in caplog.text
