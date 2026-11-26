"""D-53's sum (US-12 fix): a Reddit answer served at the last moment, held by reading begun during the interview for up
to EARLY_KEEP_S (US-34), read by the longest search, whose result is then saved (after the 150 s, P-38), whose run is
then kept for SEARCH_RUN_KEEP_MINUTES and dropped by the next housekeeping tick, is gone before the data is 24 hours
old, and so is its file on disk. The margin stays 60 s at any allowed save limit. Fake clocks only."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.contracts import SEARCH_TIME_LIMIT_S
from app.db import SAVE_GRACE_S
from app.research.reddit.cache import DEFAULT_TTL, SERVE_MARGIN, RedditCache, serve_limit
from app.search_run_limits import EARLY_KEEP_S, SWEEP_EVERY_S
from tests.fakes.research import thread

READ_AT = datetime(2026, 11, 14, 9, 0, tzinfo=UTC)
LONGEST_SEARCH = timedelta(seconds=SEARCH_TIME_LIMIT_S)
ONE_TICK = timedelta(seconds=SWEEP_EVERY_S)
LONGEST_SAVE = timedelta(seconds=15 + SAVE_GRACE_S)  # DB_SAVE_TIMEOUT_SECONDS's default and most, and the grace
EARLY = timedelta(seconds=EARLY_KEEP_S)  # US-34: an early job, before its search takes it over


def _save(limit: str) -> timedelta:
    return timedelta(seconds=float(limit) + SAVE_GRACE_S)


def test_serve_limit_is_24_hours_less_early_reading_the_longest_search_its_save_keep_time_tick_and_margin() -> None:
    assert timedelta(seconds=60) == SERVE_MARGIN
    assert timedelta(minutes=15) == EARLY
    assert timedelta(seconds=150) == LONGEST_SEARCH
    assert timedelta(seconds=16) == LONGEST_SAVE
    assert timedelta(seconds=600) == ONE_TICK
    expected = (
        timedelta(hours=24) - EARLY - LONGEST_SEARCH - LONGEST_SAVE - timedelta(minutes=30) - ONE_TICK - SERVE_MARGIN
    )
    assert serve_limit({}) == expected == timedelta(hours=23, minutes=1, seconds=14)


@pytest.mark.parametrize(
    ("save", "limit"),
    [
        ("2", timedelta(hours=23, minutes=1, seconds=27)),
        ("10", timedelta(hours=23, minutes=1, seconds=19)),
        ("15", timedelta(hours=23, minutes=1, seconds=14)),
        ("90", timedelta(hours=23, minutes=1, seconds=14)),  # refused: 15 is used
    ],
)
def test_serve_limit_follows_the_save_limit(save: str, limit: timedelta) -> None:
    assert serve_limit({"DB_SAVE_TIMEOUT_SECONDS": save}) == limit


@pytest.mark.parametrize(
    ("keep", "limit"),
    [
        ("1", timedelta(hours=23, minutes=30, seconds=14)),
        ("30", timedelta(hours=23, minutes=1, seconds=14)),
        ("120", timedelta(hours=21, minutes=31, seconds=14)),
    ],
)
def test_serve_limit_follows_a_custom_keep_time(keep: str, limit: timedelta) -> None:
    kept = timedelta(minutes=float(keep))
    assert (
        serve_limit({"SEARCH_RUN_KEEP_MINUTES": keep})
        == DEFAULT_TTL - EARLY - LONGEST_SEARCH - LONGEST_SAVE - kept - ONE_TICK - SERVE_MARGIN
        == limit
    )


def test_a_reddit_answer_is_served_just_before_the_limit_and_not_at_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEARCH_RUN_KEEP_MINUTES", "30")
    monkeypatch.setenv("DB_SAVE_TIMEOUT_SECONDS", "15")
    moments = [READ_AT]
    cache = RedditCache(folder=None, now=lambda: moments[0])  # its default TTL: serve_limit()
    assert cache.ttl == timedelta(hours=23, minutes=1, seconds=14)
    cache.put(("q",), [thread("aaa", fetched_at=READ_AT)])

    moments[0] = READ_AT + cache.ttl - timedelta(seconds=1)
    assert cache.get(("q",)) is not None
    moments[0] = READ_AT + cache.ttl
    assert cache.get(("q",)) is None


@pytest.mark.parametrize("save", ["2", "10", "15"])
@pytest.mark.parametrize("keep", ["1", "30", "120"])
def test_the_worst_case_ends_before_24_hours(keep: str, save: str) -> None:
    """On an idle server, where only the timer clears things: both the run and the file go before 24 hours.

    The run: served at the last moment, then held by an early job for its whole keep time (US-34), then the longest
    search, then its save (its limit and the grace), then the keep-time, then a full tick before the timer drops it.
    The file: past its limit, then a full tick before the timer's sweep deletes it. At any allowed save limit, the run
    leaves exactly the 60 s margin.
    """
    kept = timedelta(minutes=float(keep))
    limit = serve_limit({"SEARCH_RUN_KEEP_MINUTES": keep, "DB_SAVE_TIMEOUT_SECONDS": save})
    served_at_the_edge = limit - timedelta(microseconds=1)
    run_gone = served_at_the_edge + EARLY + LONGEST_SEARCH + _save(save) + kept + ONE_TICK
    file_gone = limit + ONE_TICK
    for gone in (run_gone, file_gone):
        assert gone < timedelta(hours=24)
        assert timedelta(hours=24) - gone >= SERVE_MARGIN  # the margin is left whole
    assert timedelta(hours=24) - (limit + EARLY + LONGEST_SEARCH + _save(save) + kept + ONE_TICK) == SERVE_MARGIN
