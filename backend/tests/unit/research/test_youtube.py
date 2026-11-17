"""YouTube as a source (US-38, S4-15): the Data API finds 3 to 5 review videos, and each becomes one source.

Titles, descriptions and top comments only, never a transcript (docs/api-setup.md, W2). A video with comments carries
the reddit data class (P-23), so only Groq reads it and it is never saved; no commenter's name is ever kept. Every
video, channel and comment here is made up for the tests.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.contracts import DataClass, SourceType
from app.contracts.research import MAX_SOURCE_CHARS
from app.llm.fitting import estimate_tokens
from app.llm.retry import RetryClock
from app.research.cache import ResearchCache
from app.research.capping import cap_source
from app.research.settings import ProviderCap, ResearchSettings
from app.research.usage import SearchUsage, UsageBook
from app.research.youtube import COMMENT_SEPARATOR, MAX_SOURCE_TOKENS, YouTubeSource, without_viewers

NOW = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)
KEY = "not-a-real-youtube-key"
QUERY = "best earbuds under 5000 rupees review"
CAPS = {name: cap for name, cap in ResearchSettings().caps.items() if name.startswith("youtube")}  # the real caps


def video_ids(count: int) -> list[str]:
    return [f"vid{n:08d}x" for n in range(1, count + 1)]  # 11 characters, as YouTube's ids are


def search_reply(ids: list[str]) -> dict[str, Any]:
    return {"items": [{"id": {"kind": "youtube#video", "videoId": i}, "snippet": {"title": f"Title {i}"}} for i in ids]}


def videos_reply(ids: list[str]) -> dict[str, Any]:
    return {
        "items": [
            {
                "id": i,
                "snippet": {
                    "title": f"Made-up earbuds review {n}",
                    "channelTitle": f"Made-up Tech Channel {n}",
                    "channelId": f"UCcreator{n:015d}",
                    "description": f"We test the Nova Buds {n} for a month: battery, fit and calls.",
                    "publishedAt": "2026-10-01T10:00:00Z",
                },
            }
            for n, i in enumerate(ids, start=1)
        ]
    }


def comments_reply(video_id: str, texts: list[str]) -> dict[str, Any]:
    return {
        "items": [
            {
                "snippet": {
                    "videoId": video_id,
                    "topLevelComment": {
                        "snippet": {
                            "textOriginal": text,
                            "textDisplay": text,
                            "authorDisplayName": "Priya Madeup",
                            "authorChannelId": {"value": "UCviewer000000000000001"},
                            "authorChannelUrl": "http://www.youtube.com/@priyamadeup",
                            "likeCount": 3,
                        }
                    },
                }
            }
            for text in texts
        ]
    }


def google_error(reason: str, status: int = 403) -> httpx.Response:
    body = {"error": {"code": status, "message": "made up", "errors": [{"reason": reason, "domain": "youtube"}]}}
    return httpx.Response(status, json=body)


class Tube:
    """A made-up YouTube Data API: answers by path, records every request, refuses anything else."""

    def __init__(
        self,
        ids: list[str],
        comments: dict[str, httpx.Response | list[str]] | None = None,
        search: httpx.Response | None = None,
        videos: httpx.Response | None = None,
    ) -> None:
        self.ids = ids
        self.comments = comments or {}
        self.search = search
        self.videos = videos
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        assert request.url.host == "www.googleapis.com"
        assert "caption" not in path and "timedtext" not in path, "never a transcript"
        if path == "/youtube/v3/search":
            return self.search or httpx.Response(200, json=search_reply(self.ids))
        if path == "/youtube/v3/videos":
            return self.videos or httpx.Response(200, json=videos_reply(self.ids))
        if path == "/youtube/v3/commentThreads":
            vid = request.url.params["videoId"]
            answer = self.comments.get(vid, [f"Good bass on these, video {vid}.", "Calls sound clear to me."])
            return (
                answer if isinstance(answer, httpx.Response) else httpx.Response(200, json=comments_reply(vid, answer))
            )
        raise AssertionError(f"unexpected request to {path}")

    def paths(self) -> list[str]:
        return [r.url.path.rsplit("/", 1)[-1] for r in self.requests]


def source_of(
    tube: Tube,
    tmp_path: Path,
    *,
    key: str | None = KEY,
    cache_on: bool = True,
    retry: RetryClock | None = None,
) -> YouTubeSource:
    client = httpx.Client(transport=httpx.MockTransport(tube.handle))
    cache = ResearchCache(tmp_path / "cache", enabled=cache_on, now=lambda: NOW)
    return YouTubeSource(client, key, cache=cache, now=lambda: NOW, retry=retry or RetryClock(sleep=lambda _: None))


def usage(caps: dict[str, ProviderCap] | None = None, path: Path | None = None) -> SearchUsage:
    return UsageBook(caps or CAPS, path=path).search()


# --- the calls ------------------------------------------------------------------------------------------------------


def test_five_found_make_five_sources_with_one_search_one_videos_call_and_comments_for_each(tmp_path: Path) -> None:
    tube = Tube(video_ids(5))
    used = usage()
    result = source_of(tube, tmp_path).search(QUERY, used)
    assert result.failure is None
    assert len(result.sources) == 5
    assert {s.type for s in result.sources} == {SourceType.YOUTUBE}
    assert tube.paths() == ["search", "videos", *["commentThreads"] * 5]
    assert used.used == {"youtube_search": 1, "youtube": 6}


def test_fewer_found_are_used_as_found(tmp_path: Path) -> None:
    tube = Tube(video_ids(2))
    result = source_of(tube, tmp_path).search(QUERY, usage())
    assert len(result.sources) == 2
    assert result.failure is None


def test_more_than_five_found_keeps_the_first_five(tmp_path: Path) -> None:
    tube = Tube(video_ids(7))
    result = source_of(tube, tmp_path).search(QUERY, usage({**CAPS, "youtube": ProviderCap(per_search=8, per_day=99)}))
    assert len(result.sources) == 5
    assert tube.requests[1].url.params["id"] == ",".join(video_ids(5))


def test_search_list_asks_for_relevant_videos_in_india_with_the_key_in_a_header_only(tmp_path: Path) -> None:
    tube = Tube(video_ids(3))
    source_of(tube, tmp_path).search(QUERY, usage())
    search = tube.requests[0]
    params = dict(search.url.params)
    assert params == {
        "part": "snippet",
        "type": "video",
        "order": "relevance",
        "regionCode": "IN",
        "maxResults": "5",
        "q": QUERY,
    }
    assert all(r.headers["X-Goog-Api-Key"] == KEY for r in tube.requests)
    assert all(KEY not in str(r.url) for r in tube.requests)  # never in a URL, so never in a log


def test_videos_and_comments_are_asked_for_their_text_only(tmp_path: Path) -> None:
    tube = Tube(video_ids(3))
    source_of(tube, tmp_path).search(QUERY, usage())
    videos = dict(tube.requests[1].url.params)
    assert videos == {"part": "snippet", "id": ",".join(video_ids(3))}
    comments = dict(tube.requests[2].url.params)
    assert comments == {
        "part": "snippet",
        "videoId": video_ids(3)[0],
        "order": "relevance",
        "maxResults": "20",
        "textFormat": "plainText",
    }


def test_no_key_sends_nothing(tmp_path: Path) -> None:
    tube = Tube(video_ids(3))
    result = source_of(tube, tmp_path, key=None).search(QUERY, usage())
    assert tube.requests == []
    assert result.failure is not None and result.failure.reason == "blocked"
    assert result.sources == ()


# --- the source -----------------------------------------------------------------------------------------------------


def test_a_video_with_comments_is_one_source_of_the_reddit_data_class(tmp_path: Path) -> None:
    vid = video_ids(1)[0]
    tube = Tube([vid], comments={vid: ["Battery lasts two days for me.", "The fit is loose while running."]})
    [source] = source_of(tube, tmp_path).search(QUERY, usage()).sources
    assert str(source.url) == f"https://www.youtube.com/watch?v={vid}"
    assert (source.type, source.site, source.via) == (SourceType.YOUTUBE, "youtube.com", "api")
    assert source.data_class is DataClass.REDDIT  # P-23: viewers' words, so Groq only and never saved
    assert source.title == "Made-up earbuds review 1"
    assert source.published_at == datetime(2026, 10, 1, 10, 0, tzinfo=UTC)
    assert source.fetched_at == NOW
    text = source.text
    assert text.startswith("Made-up earbuds review 1\n")  # the title is in the text, so a quote may come from it
    assert "Video from the channel Made-up Tech Channel 1." in text
    assert "We test the Nova Buds 1 for a month" in text
    assert "Viewer comments:" in text
    assert "Battery lasts two days for me." in text and "The fit is loose while running." in text
    assert COMMENT_SEPARATOR in text  # each comment set apart
    assert source.comments == () and source.author_hash is None


def test_a_video_with_comments_off_is_a_public_source(tmp_path: Path) -> None:
    vid = video_ids(1)[0]
    tube = Tube([vid], comments={vid: google_error("commentsDisabled")})
    result = source_of(tube, tmp_path).search(QUERY, usage())
    [source] = result.sources
    assert result.failure is None
    assert source.data_class is DataClass.PUBLIC
    assert "Viewer comments:" not in source.text


def test_a_commenters_name_handle_or_channel_is_never_kept(tmp_path: Path) -> None:
    vid = video_ids(1)[0]
    texts = [
        "@rahul.madeup agreed, the case feels cheap.",
        "See my test at https://www.youtube.com/@priyamadeup and youtube.com/channel/UCviewer000000000000001 too.",
        "Thanks @Made_Up-Viewer42 for the tip.",
    ]
    tube = Tube([vid], comments={vid: texts})
    [source] = source_of(tube, tmp_path).search(QUERY, usage()).sources
    dumped = source.model_dump_json()
    for name in ("Priya Madeup", "priyamadeup", "rahul.madeup", "Made_Up-Viewer42", "UCviewer000000000000001"):
        assert name not in dumped
    assert "a viewer agreed, the case feels cheap." in source.text
    assert "Thanks a viewer for the tip." in source.text


@pytest.mark.parametrize(
    ("comment", "cleaned"),
    [
        ("priya.k@gmail.com says the case is loose", "a viewer says the case is loose"),
        ("thanks @ priya for the tip", "thanks a viewer for the tip"),
        ("agreed.@priya", "agreed.a viewer"),
        ("see instagram.com/@priya.k and x.com/@priya", "see a link and a link"),
        ("my review is at youtube.com/PriyaTech", "my review is at a link"),
        ("@@priya ok", "a viewer ok"),
        ("@ab\u00b7cd yes", "a viewer yes"),
        ("@\u092a\u094d\u0930\u093f\u092f\u093e sahi hai", "a viewer sahi hai"),
        ("https://www.youtube.com/@%E0%A4%AA watch this", "a link watch this"),
        ("got it @12999 in the sale", "got it @12999 in the sale"),  # a price, not a person
        ("bought on amazon.in for 1999", "bought on amazon.in for 1999"),  # a shop, with no path to a profile
    ],
)
def test_no_viewer_is_named_in_any_form(comment: str, cleaned: str) -> None:
    assert without_viewers(comment) == cleaned


def test_with_too_little_time_left_comments_wait_and_the_videos_come_back_public(tmp_path: Path) -> None:
    tube = Tube(video_ids(3))
    retry = RetryClock(sleep=lambda _: None, time_left=lambda: 5.0)  # less than a whole call
    result = source_of(tube, tmp_path, retry=retry).search(QUERY, usage())
    assert len(result.sources) == 3 and result.failure is None
    assert {s.data_class for s in result.sources} == {DataClass.PUBLIC}
    assert "commentThreads" not in tube.paths()


def test_one_retried_comment_read_still_fits_the_per_search_cap(tmp_path: Path) -> None:
    tube = Tube(video_ids(5))
    flaky = {"left": 1}

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/commentThreads") and flaky["left"]:
            flaky["left"] -= 1
            tube.requests.append(request)
            return httpx.Response(503, text="made-up hiccup")
        return tube.handle(request)

    client = httpx.Client(transport=httpx.MockTransport(handle))
    cache = ResearchCache(tmp_path / "cache", now=lambda: NOW)
    retry = RetryClock(sleep=lambda _: None, time_left=lambda: 30.0)
    used = usage()
    result = YouTubeSource(client, KEY, cache=cache, now=lambda: NOW, retry=retry).search(QUERY, used)
    assert result.failure is None  # never "ran out of quota" for one retry
    assert [s.data_class for s in result.sources] == [DataClass.REDDIT] * 5
    assert used.used["youtube"] == 7


def test_the_text_fits_the_source_cap_so_the_stages_cap_never_drops_the_comments(tmp_path: Path) -> None:
    vid = video_ids(1)[0]
    comments = [f"Made-up comment {n}: the bass is punchy and the fit is fine. " * 6 for n in range(20)]
    tube = Tube([vid], comments={vid: comments})
    [source] = source_of(tube, tmp_path).search(QUERY, usage()).sources
    assert estimate_tokens(source.text) <= MAX_SOURCE_TOKENS
    assert len(source.text) <= MAX_SOURCE_CHARS
    assert cap_source(source, MAX_SOURCE_TOKENS) == source  # the stage's own cap leaves it whole
    assert "Made-up comment 0:" in source.text and source.data_class is DataClass.REDDIT


def test_a_long_description_leaves_room_for_the_comments(tmp_path: Path) -> None:
    vid = video_ids(1)[0]
    reply = videos_reply([vid])
    reply["items"][0]["snippet"]["description"] = "We test every made-up feature of these earbuds in detail. " * 400
    tube = Tube([vid], comments={vid: ["Battery lasts two days for me."]}, videos=httpx.Response(200, json=reply))
    [source] = source_of(tube, tmp_path).search(QUERY, usage()).sources
    assert "Battery lasts two days for me." in source.text  # the description took at most a third
    assert cap_source(source, MAX_SOURCE_TOKENS) == source


def test_each_comment_is_its_own_paragraph_set_apart(tmp_path: Path) -> None:
    vid = video_ids(1)[0]
    tube = Tube([vid], comments={vid: ["First made-up comment.", "Second made-up comment."]})
    [source] = source_of(tube, tmp_path).search(QUERY, usage()).sources
    assert f"First made-up comment.\n\n{COMMENT_SEPARATOR}\n\nSecond made-up comment." in source.text


# --- caps, the cache and failures -----------------------------------------------------------------------------------


def test_the_daily_search_cap_sends_nothing(tmp_path: Path) -> None:
    book = UsageBook(CAPS, path=None)
    for _ in range(80):
        assert book.search().take("youtube_search")
    tube = Tube(video_ids(3))
    result = source_of(tube, tmp_path).search(QUERY, book.search())
    assert tube.requests == []
    assert result.failure is not None and result.failure.reason == "quota_exhausted"


def test_out_of_units_keeps_the_videos_read_as_public_sources(tmp_path: Path) -> None:
    tube = Tube(video_ids(4))
    caps = {**CAPS, "youtube": ProviderCap(per_search=3, per_day=9000)}  # videos.list, then comments for two
    result = source_of(tube, tmp_path).search(QUERY, usage(caps))
    assert len(result.sources) == 4
    assert [s.data_class for s in result.sources] == [
        DataClass.REDDIT,
        DataClass.REDDIT,
        DataClass.PUBLIC,
        DataClass.PUBLIC,
    ]
    assert result.failure is not None and result.failure.reason == "quota_exhausted"


def test_a_quota_error_from_youtube_says_so(tmp_path: Path) -> None:
    tube = Tube(video_ids(3), search=google_error("quotaExceeded"))
    result = source_of(tube, tmp_path).search(QUERY, usage())
    assert result.failure is not None and result.failure.reason == "quota_exhausted"
    assert result.sources == ()


def test_a_quota_error_while_reading_comments_keeps_the_videos(tmp_path: Path) -> None:
    ids = video_ids(3)
    tube = Tube(ids, comments={ids[1]: google_error("quotaExceeded")})
    result = source_of(tube, tmp_path).search(QUERY, usage())
    assert len(result.sources) == 3
    assert result.failure is not None and result.failure.reason == "quota_exhausted"
    assert tube.paths().count("commentThreads") == 2  # it stops asking once the quota is gone


@pytest.mark.parametrize(
    ("answer", "reason"),
    [
        (google_error("forbidden"), "blocked"),
        (google_error("keyInvalid", status=400), "error"),
        (google_error("rateLimitExceeded"), "rate_limited"),
        (httpx.Response(500, text="made-up outage"), "error"),
        (httpx.Response(200, text="not json"), "error"),
    ],
)
def test_a_failed_search_names_its_reason(answer: httpx.Response, reason: str, tmp_path: Path) -> None:
    tube = Tube(video_ids(3), search=answer)
    result = source_of(tube, tmp_path).search(QUERY, usage())
    assert result.failure is not None and result.failure.reason == reason
    assert result.failure.source_type is SourceType.YOUTUBE
    assert KEY not in (result.failure.detail or "")


def test_a_timeout_is_a_timeout(tmp_path: Path) -> None:
    def hang(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("made up", request=request)

    client = httpx.Client(transport=httpx.MockTransport(hang))
    cache = ResearchCache(tmp_path / "cache", now=lambda: NOW)
    retry = RetryClock(sleep=lambda _: None, time_left=lambda: 0.0)  # no time for a retry
    result = YouTubeSource(client, KEY, cache=cache, now=lambda: NOW, retry=retry).search(QUERY, usage())
    assert result.failure is not None and result.failure.reason == "timeout"


def test_a_server_error_is_tried_once_more(tmp_path: Path) -> None:
    tube = Tube(video_ids(3))
    answers: list[httpx.Response] = [httpx.Response(503, text="made-up hiccup")]

    def flaky(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/search") and answers:
            tube.requests.append(request)
            return answers.pop()
        return tube.handle(request)

    client = httpx.Client(transport=httpx.MockTransport(flaky))
    cache = ResearchCache(tmp_path / "cache", now=lambda: NOW)
    retry = RetryClock(sleep=lambda _: None, time_left=lambda: 30.0)
    used = usage()  # the real caps leave room for the one retry
    result = YouTubeSource(client, KEY, cache=cache, now=lambda: NOW, retry=retry).search(QUERY, used)
    assert result.failure is None and len(result.sources) == 3
    assert tube.paths().count("search") == 2
    assert used.used["youtube_search"] == 2  # the retry is a call like any other


def test_comments_that_fail_keep_the_video_as_a_public_source(tmp_path: Path) -> None:
    ids = video_ids(2)
    tube = Tube(ids, comments={ids[0]: httpx.Response(404, json={"error": {"errors": [{"reason": "videoNotFound"}]}})})
    result = source_of(tube, tmp_path).search(QUERY, usage())
    assert result.failure is None
    assert [s.data_class for s in result.sources] == [DataClass.PUBLIC, DataClass.REDDIT]


def test_a_repeat_comes_from_the_cache_kept_only_for_the_reddit_serve_limit(tmp_path: Path) -> None:
    tube = Tube(video_ids(3))
    source = source_of(tube, tmp_path)
    first = source.search(QUERY, usage())
    asked = len(tube.requests)
    used = usage()
    again = source.search(QUERY, used)
    assert again.sources == first.sources and again.from_cache
    assert len(tube.requests) == asked
    assert not any(used.used.values())  # a cache hit takes no quota
    files = list((tmp_path / "cache").rglob("*.json"))
    assert files and all(f.parent.name == "searches" for f in files)  # searches/: the serve limit, under 24 hours


def test_a_failure_is_never_cached(tmp_path: Path) -> None:
    tube = Tube(video_ids(3), search=google_error("quotaExceeded"))
    source = source_of(tube, tmp_path)
    source.search(QUERY, usage())
    assert not list((tmp_path / "cache").rglob("*.json"))


def test_the_source_never_shows_its_key() -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    source = YouTubeSource(client, KEY, cache=ResearchCache(enabled=False))
    assert KEY not in repr(source)


def test_no_request_ever_asks_for_a_transcript_or_captions(tmp_path: Path) -> None:
    tube = Tube(video_ids(5))
    source_of(tube, tmp_path).search(QUERY, usage())
    assert all("caption" not in r.url.path and "timedtext" not in r.url.path for r in tube.requests)
    assert {r.url.path for r in tube.requests} <= {
        "/youtube/v3/search",
        "/youtube/v3/videos",
        "/youtube/v3/commentThreads",
    }
