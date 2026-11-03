"""The Reddit archive smoke script's pieces (US-12), offline: httpx.MockTransport stands in for both archives."""

from __future__ import annotations

import httpx

from app.research.reddit import ArcticShiftSource, PullPushSource
from app.research.reddit.cache import RedditCache
from scripts.smoke_sources import check
from tests.fakes.reddit import Archive, ok


def test_check_reports_the_newest_post_and_never_a_username() -> None:
    post = {"id": "1abcaaa", "subreddit": "laptops", "title": "t", "author": "made_up_x", "created_utc": 1793491200}
    archive = Archive().on("/api/posts/search", ok({"data": [post]}))
    http = archive.client()
    row = check(ArcticShiftSource(http, cache=RedditCache(folder=None)), http)
    assert (row.archive, row.works, row.newest) == ("Arctic Shift", "yes", "2026-11-01 00:00 UTC")
    assert "made_up_x" not in repr(row)
    assert dict(archive.requests[0].url.params) == {"subreddit": "laptops", "sort": "desc", "limit": "1"}


def test_check_reports_a_down_archive() -> None:
    archive = Archive().on("/reddit/search/submission/", httpx.Response(522))
    http = archive.client()
    row = check(PullPushSource(http, cache=RedditCache(folder=None)), http)
    assert (row.works, row.note) == ("no", "PullPush: HTTP 522")
    params = dict(archive.requests[0].url.params)
    assert params == {"subreddit": "laptops", "sort": "desc", "size": "1", "sort_type": "created_utc"}
