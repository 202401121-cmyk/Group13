"""YouTube in the live research stage (US-38): its videos join the sources, and when it fails or is capped the search
goes on and the coverage note says so. With no key it is simply left out, as Firecrawl is. Made-up data only."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx
import pytest

from app.contracts import DataClass, ResearchInput, ResearchResult, SourceFailure, SourceType, StageContext
from app.pipeline import build_coverage_note
from app.research.client import ClientUnavailable
from app.research.queries import youtube_query
from app.research.serper import SerperResult
from app.research.settings import ProviderCap, ResearchSettings, load_research_settings
from app.research.stage import LiveResearchStage, _taking_turns
from app.research.usage import SearchUsage, UsageBook
from app.research.youtube import YouTubeResult, YouTubeSource, video_source
from tests.fakes.research import FakePages, FakeReddit, FakeSerper, review_source, rubric, thread, web_source

REVIEW = "https://www.reviews.example/nova"
NOW = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)


def video(n: int, comments: list[str] | None = None) -> object:
    from app.research.youtube import _Video

    made = _Video(f"vid{n:08d}x", f"Made-up review {n}", "Made-up Channel", "Made-up description.", NOW)
    return video_source(made, comments or [], NOW)


@dataclass
class FakeYouTube:
    result: YouTubeResult = field(default_factory=YouTubeResult)
    delay_s: float = 0.0
    error: Exception | None = None
    queries: list[str] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def search(self, query: str, usage: SearchUsage) -> YouTubeResult:
        with self._lock:
            self.queries.append(query)
        if self.delay_s:
            time.sleep(self.delay_s)
        if self.error is not None:
            raise self.error
        return self.result


def _book() -> UsageBook:
    return UsageBook(
        {
            "serper": ProviderCap(per_search=6, per_day=60),
            "reddit_archive": ProviderCap(per_search=10, per_day=500),
            "pages": ProviderCap(per_search=16, per_day=200),
            "youtube_search": ProviderCap(per_search=2, per_day=80),
            "youtube": ProviderCap(per_search=8, per_day=9000),
        },
        path=None,
    )


def _stage(youtube: object, deadline_s: float = 5.0) -> LiveResearchStage:
    return LiveResearchStage(
        settings=ResearchSettings(review_domains=("reviews.example",), deadline_s=deadline_s),
        serper=FakeSerper(default=SerperResult(sources=(web_source("https://web.example/a"), web_source(REVIEW)))),  # type: ignore[arg-type]
        pages=FakePages(pages={REVIEW: review_source(REVIEW)}),  # type: ignore[arg-type]
        reddit=FakeReddit(threads=[thread("aaa")]),  # type: ignore[arg-type]
        youtube=youtube,  # type: ignore[arg-type]
        usage=_book(),
        pool=ThreadPoolExecutor(max_workers=6),
    )


def _run(stage: LiveResearchStage) -> ResearchResult:
    return stage.run(ResearchInput(rubric=rubric()), StageContext(search_id="srch_test"))


def _note(result: ResearchResult) -> str:
    return build_coverage_note([], result.failures, False) or ""


def test_youtubes_videos_join_the_sources_with_the_others() -> None:
    videos = tuple(v for v in (video(1, ["Good bass."]), video(2), video(3)) if v is not None)
    tube = FakeYouTube(YouTubeResult(sources=videos))  # type: ignore[arg-type]
    result = _run(_stage(tube))
    found = [s for s in result.sources if s.type is SourceType.YOUTUBE]
    assert [s.title for s in found] == ["Made-up review 1", "Made-up review 2", "Made-up review 3"]
    assert [s.data_class for s in found] == [DataClass.REDDIT, DataClass.PUBLIC, DataClass.PUBLIC]
    assert {SourceType.REDDIT, SourceType.WEB_SEARCH, SourceType.REVIEW_SITE} <= {s.type for s in result.sources}
    assert not [f for f in result.failures if f.source_type is SourceType.YOUTUBE]


def test_videos_and_threads_take_turns_so_extraction_reads_both() -> None:
    """Both are reddit class and share extraction's Reddit share and budget: listed after the threads, no video would
    ever be read."""
    videos = tuple(v for v in (video(1, ["Good bass."]), video(2, ["Loose fit."])) if v is not None)
    result = _run(_stage(FakeYouTube(YouTubeResult(sources=videos))))  # type: ignore[arg-type]
    community = [s.type for s in result.sources if s.data_class is DataClass.REDDIT]
    threads = community.count(SourceType.REDDIT)
    assert threads >= 1 and community.count(SourceType.YOUTUBE) == 2
    assert community[:2] == [SourceType.REDDIT, SourceType.YOUTUBE]  # a video right after the first thread
    assert community == [s.type for s in _taking_turns([thread("t")] * threads, list(videos))]  # type: ignore[arg-type]


def test_taking_turns_alternates_then_keeps_the_rest_in_order() -> None:
    first = [thread(f"t{n}") for n in range(3)]
    second = [v for v in (video(1), video(2)) if v is not None]
    turns = _taking_turns(first, second)  # type: ignore[arg-type]
    assert [s.id for s in turns] == [first[0].id, second[0].id, first[1].id, second[1].id, first[2].id]  # type: ignore[union-attr]
    assert _taking_turns([], second) == second  # type: ignore[arg-type]


def test_the_youtube_query_is_built_from_known_words_only() -> None:
    tube = FakeYouTube()
    _run(_stage(tube))
    assert tube.queries == [youtube_query(rubric(), load_research_settings(env={}).known_uses)]
    assert tube.queries == ["best laptop for coding under 70000 rupees review"]
    private = rubric(use="coding for my friend Asha's startup")
    assert youtube_query(private) == "best laptop for coding under 70000 rupees review"  # her words never go further


@pytest.mark.parametrize(
    ("failure", "sentence"),
    [
        ("quota_exhausted", "YouTube ran out of quota."),
        ("blocked", "YouTube could not be read."),
        ("rate_limited", "YouTube hit a rate limit."),
        ("error", "YouTube had an error."),
    ],
)
def test_youtube_failing_leaves_the_search_going_and_the_note_says_so(failure: str, sentence: str) -> None:
    tube = FakeYouTube(YouTubeResult(failure=SourceFailure(source_type=SourceType.YOUTUBE, reason=failure)))  # type: ignore[arg-type]
    result = _run(_stage(tube))
    assert {SourceType.REDDIT, SourceType.WEB_SEARCH, SourceType.REVIEW_SITE} <= {s.type for s in result.sources}
    assert sentence in _note(result)


def test_youtube_raising_or_hanging_never_stops_the_search() -> None:
    raising = _run(_stage(FakeYouTube(error=RuntimeError("made up"))))
    assert "YouTube had an error." in _note(raising)
    assert raising.sources
    hanging = _run(_stage(FakeYouTube(delay_s=3.0), deadline_s=1.0))
    assert "YouTube timed out." in _note(hanging)


def test_with_no_key_youtube_is_simply_left_out(monkeypatch: pytest.MonkeyPatch) -> None:
    built: list[httpx.Client] = []

    def build() -> httpx.Client:
        client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
        built.append(client)
        return client

    monkeypatch.setattr("app.research.stage.build_research_client", build)
    monkeypatch.setenv("YOUTUBE_API_KEY", "")
    stage = LiveResearchStage(settings=ResearchSettings(), serper=None, pages=None, reddit=None, usage=_book())
    wiring = stage._wire(ResearchSettings())
    assert wiring.youtube is None and wiring.youtube_problem is None


def test_with_a_key_the_real_source_is_built_and_never_shows_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.research.stage.build_research_client",
        lambda: httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))),
    )
    monkeypatch.setenv("YOUTUBE_API_KEY", "not-a-real-youtube-key")
    stage = LiveResearchStage(settings=ResearchSettings(), serper=None, pages=None, reddit=None, usage=_book())
    wiring = stage._wire(ResearchSettings())
    assert isinstance(wiring.youtube, YouTubeSource)
    assert wiring.youtube.max_videos == 5 and wiring.youtube.comments_per_video == 20
    assert "not-a-real-youtube-key" not in repr(wiring.youtube)
    off = stage._wire(ResearchSettings(max_youtube_videos=0))
    assert off.youtube is None  # 0 videos turns YouTube off


def test_with_a_key_but_no_client_youtube_is_named_as_failing(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_client() -> httpx.Client:
        raise ClientUnavailable("ImportError")

    monkeypatch.setattr("app.research.stage.build_research_client", no_client)
    monkeypatch.setenv("YOUTUBE_API_KEY", "not-a-real-youtube-key")
    stage = LiveResearchStage(settings=ResearchSettings(), serper=None, pages=None, reddit=None, usage=_book())
    assert stage._wire(ResearchSettings()).youtube_problem == "error"


def test_the_config_caps_youtube_searches_at_80_a_day() -> None:
    settings = load_research_settings(env={})
    assert settings.caps["youtube_search"] == ProviderCap(per_search=2, per_day=80)  # one call and its retry
    assert settings.caps["youtube"].per_search == 8 and settings.caps["youtube"].per_day <= 10_000
    assert settings.caps == ResearchSettings().caps  # the defaults in code say the same
    assert (settings.max_youtube_videos, settings.youtube_comments_per_video) == (5, 20)


def test_the_source_caps_still_fit_the_contract_with_youtube() -> None:
    with pytest.raises(ValueError):
        ResearchSettings(max_reddit_threads=20, max_web_results=20, max_review_pages=12, max_youtube_videos=9)
    assert ResearchSettings(max_youtube_videos=8).max_youtube_videos == 8
