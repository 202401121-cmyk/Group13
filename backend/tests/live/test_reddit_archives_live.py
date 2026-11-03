"""Live checks of the two Reddit archives (US-12). They call real APIs, so they never run in CI or a plain test run.

Run them on purpose, from backend/:
    RUN_LIVE_TESTS=1 pytest -m live tests/live -q

Each sends at most 4 requests through the app's own source (rate limits included). A failure here means the
archive is down or has changed, not that the code is wrong: the offline tests cover the code.
"""

from __future__ import annotations

import os

import httpx
import pytest

from app.research.reddit import ArcticShiftSource, PullPushSource, RequestBudget
from app.research.reddit.archive import ArchiveSource
from app.research.reddit.cache import RedditCache

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("RUN_LIVE_TESTS") != "1", reason="live: set RUN_LIVE_TESTS=1 to run"),
]


def _check(source: ArchiveSource) -> None:
    budget = RequestBudget(4)
    result = source.search_threads("laptop for coding", ["SuggestALaptop", "laptops"], limit=10, budget=budget)
    assert result.failure is None, result.failure
    assert result.sources, "the archive answered but found no laptop threads"
    assert all(str(s.url).startswith("https://www.reddit.com/r/") for s in result.sources)
    assert all(s.source_detail == source.name for s in result.sources)
    assert budget.used <= 4


def test_arctic_shift_answers_a_laptop_search() -> None:
    with httpx.Client() as http:
        _check(ArcticShiftSource(http, cache=RedditCache(folder=None)))


def test_pullpush_answers_a_laptop_search() -> None:
    with httpx.Client() as http:
        _check(PullPushSource(http, cache=RedditCache(folder=None)))
