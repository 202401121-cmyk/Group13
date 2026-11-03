"""Reddit source settings (US-12) and the subreddit allowlist."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]

from app.intake.lexicon import load_lexicon
from app.research.reddit import SubredditConfigError, load_reddit_settings, load_subreddits, subreddits_for
from app.research.reddit.settings import archive_user_agent
from app.research.reddit.subreddits import DEFAULT_SUBREDDITS_PATH

OFFICIAL = {
    "REDDIT_CLIENT_ID": "fake-client-id",
    "REDDIT_CLIENT_SECRET": "fake-client-secret",
    "REDDIT_USER_AGENT": "script:shopsense:0.1 (by /u/someone)",
}


def test_defaults() -> None:
    s = load_reddit_settings({})
    assert (s.mode, s.archives) == ("archive", ("arctic_shift", "pullpush"))
    assert s.arctic_shift_base_url == "https://arctic-shift.photon-reddit.com"
    assert s.pullpush_base_url == "https://api.pullpush.io"
    assert s.user_salt is None and not s.official_ready


def test_values_from_the_environment() -> None:
    s = load_reddit_settings(
        {
            "REDDIT_SOURCE": "OFF",
            "REDDIT_ARCHIVES": "pullpush, arctic_shift, pullpush",
            "ARCTIC_SHIFT_BASE_URL": "https://arctic.example.test/",
            "REDDIT_USER_SALT": "pepper",
        }
    )
    assert (s.mode, s.archives) == ("off", ("pullpush", "arctic_shift"))
    assert s.arctic_shift_base_url == "https://arctic.example.test"
    assert s.user_salt == "pepper"


def test_bad_values_fall_back_to_the_defaults(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    s = load_reddit_settings(
        {"REDDIT_SOURCE": "scrape", "REDDIT_ARCHIVES": "pushshift", "PULLPUSH_BASE_URL": "http://api.pullpush.io"}
    )
    assert (s.mode, s.archives, s.pullpush_base_url) == (
        "archive",
        ("arctic_shift", "pullpush"),
        "https://api.pullpush.io",
    )
    assert "REDDIT_SOURCE" in caplog.text and "PULLPUSH_BASE_URL" in caplog.text


def test_official_needs_all_three_api_settings_or_uses_the_archives(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    assert load_reddit_settings({"REDDIT_SOURCE": "official", **OFFICIAL}).mode == "official"
    partial = {"REDDIT_SOURCE": "official", "REDDIT_CLIENT_ID": "fake-client-id"}
    assert load_reddit_settings(partial).mode == "archive"
    assert "using the archives instead" in caplog.text


def test_secrets_never_show_in_the_settings_repr(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    s = load_reddit_settings({"REDDIT_SOURCE": "official", "REDDIT_USER_SALT": "pepper-secret", **OFFICIAL})
    for secret in ("pepper-secret", "fake-client-id", "fake-client-secret"):
        assert secret not in repr(s)
        assert secret not in caplog.text


def test_a_missing_salt_is_reported_once_by_name(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    load_reddit_settings({})
    assert caplog.text.count("REDDIT_USER_SALT is not set") == 1


def test_the_archive_user_agent_says_who_we_are() -> None:
    agent = archive_user_agent()
    assert agent.startswith("ShopSense/")
    assert "(IT314 student project; +https://github.com/202401121-cmyk/Group13)" in agent


# --- the subreddit allowlist ---------------------------------------------------------------------


def test_every_category_has_subreddits() -> None:
    config = load_subreddits()
    assert set(config.categories) == set(load_lexicon().categories)
    assert all(subreddits_for(c) for c in config.categories)


def test_a_category_gets_its_own_subreddits_first_then_shared_ones() -> None:
    assert load_subreddits().for_category("laptop") == [
        "SuggestALaptop",
        "laptops",
        "GamingLaptops",
        "IndiaTech",
        "IndianGaming",
    ]
    assert subreddits_for("laptop") == ["SuggestALaptop", "laptops", "GamingLaptops", "IndiaTech"]  # 4 at most
    assert load_subreddits().for_category("mattress") == ["Mattress", "BuyItForLife"]
    assert subreddits_for("kayak_paddle") == []  # a category we have no list for


def test_the_stale_subreddits_are_not_listed() -> None:
    every = {n for names in load_subreddits().categories.values() for n in names}
    every |= {n for g in load_subreddits().shared for n in g.subreddits}
    assert not every & {"webcams", "BluetoothSpeakers", "IndianWatchCommunity", "ceilingfan", "CarSeats"}


def _write(tmp_path: Path, data: object) -> Path:
    path = tmp_path / "subs.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def _raw() -> dict[str, object]:
    data: dict[str, object] = yaml.safe_load(DEFAULT_SUBREDDITS_PATH.read_text(encoding="utf-8"))
    return data


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda d: d["categories"].pop("laptop"), "missing"),
        (lambda d: d["categories"].update({"kayak": ["Kayaking"]}), "not a category"),
        (lambda d: d["categories"].update({"laptop": ["r/laptops"]}), "not a subreddit name"),
        (lambda d: d["categories"].update({"laptop": ["laptops", "Laptops"]}), "listed twice"),
        (lambda d: d["categories"].update({"laptop": []}), "at least one"),
        (lambda d: d["shared"][0].update({"families": ["gadgets"]}), "not a product family"),
    ],
    ids=["missing-category", "unknown-category", "bad-name", "duplicate", "empty", "unknown-family"],
)
def test_the_loader_refuses_a_bad_list(tmp_path: Path, change: object, message: str) -> None:
    data = _raw()
    change(data)  # type: ignore[operator]
    with pytest.raises(SubredditConfigError, match=message):
        load_subreddits(_write(tmp_path, data))
