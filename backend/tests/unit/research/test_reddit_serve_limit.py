"""D-53's sum (US-12 fix): a Reddit answer served at the last moment, read by the longest search, whose run is then kept
for SEARCH_RUN_KEEP_MINUTES and dropped by the next housekeeping tick, is gone before the data is 24 hours old, and so
is its file on disk. Fake clocks only."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.contracts import SEARCH_TIME_LIMIT_S
from app.research.reddit.cache import DEFAULT_TTL, SERVE_MARGIN, RedditCache, serve_limit
from app.search_run_limits import SWEEP_EVERY_S
from tests.fakes.research import thread

READ_AT = datetime(2026, 11, 14, 9, 0, tzinfo=UTC)
LONGEST_SEARCH = timedelta(seconds=SEARCH_TIME_LIMIT_S)
ONE_TICK = timedelta(seconds=SWEEP_EVERY_S)


def test_serve_limit_is_24_hours_less_the_keep_time_the_longest_search_one_tick_and_the_margin() -> None:
    assert timedelta(seconds=60) == SERVE_MARGIN
    assert timedelta(seconds=150) == LONGEST_SEARCH
    assert timedelta(seconds=600) == ONE_TICK
    assert serve_limit({}) == timedelta(hours=24) - timedelta(minutes=30) - LONGEST_SEARCH - ONE_TICK - SERVE_MARGIN
    assert serve_limit({}) == timedelta(hours=23, minutes=16, seconds=30)


@pytest.mark.parametrize(
    ("keep", "limit"),
    [
        ("1", timedelta(hours=23, minutes=45, seconds=30)),
        ("30", timedelta(hours=23, minutes=16, seconds=30)),
        ("120", timedelta(hours=21, minutes=46, seconds=30)),
    ],
)
def test_serve_limit_follows_a_custom_keep_time(keep: str, limit: timedelta) -> None:
    kept = timedelta(minutes=float(keep))
    assert (
        serve_limit({"SEARCH_RUN_KEEP_MINUTES": keep})
        == DEFAULT_TTL - kept - LONGEST_SEARCH - ONE_TICK - SERVE_MARGIN
        == limit
    )


def test_a_reddit_answer_is_served_just_before_the_limit_and_not_at_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEARCH_RUN_KEEP_MINUTES", "30")
    moments = [READ_AT]
    cache = RedditCache(folder=None, now=lambda: moments[0])  # its default TTL: serve_limit()
    assert cache.ttl == timedelta(hours=23, minutes=16, seconds=30)
    cache.put(("q",), [thread("aaa", fetched_at=READ_AT)])

    moments[0] = READ_AT + cache.ttl - timedelta(seconds=1)
    assert cache.get(("q",)) is not None
    moments[0] = READ_AT + cache.ttl
    assert cache.get(("q",)) is None


@pytest.mark.parametrize("keep", ["1", "30", "120"])
def test_the_worst_case_ends_before_24_hours(keep: str) -> None:
    """On an idle server, where only the timer clears things: both the run and the file go before 24 hours.

    The run: served at the last moment, then the longest search, then the keep-time, then a full tick before the timer
    drops it. The file: past its limit, then a full tick before the timer's sweep deletes it.
    """
    kept = timedelta(minutes=float(keep))
    limit = serve_limit({"SEARCH_RUN_KEEP_MINUTES": keep})
    served_at_the_edge = limit - timedelta(microseconds=1)
    run_gone = served_at_the_edge + LONGEST_SEARCH + kept + ONE_TICK
    file_gone = limit + ONE_TICK
    for gone in (run_gone, file_gone):
        assert gone < timedelta(hours=24)
        assert timedelta(hours=24) - gone >= SERVE_MARGIN  # the margin is left whole
