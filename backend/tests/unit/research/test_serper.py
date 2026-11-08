"""Web search results through Serper (US-12), against httpx.MockTransport. Nothing is ever really sent."""

from __future__ import annotations

import json
import logging
from datetime import timedelta

import httpx
import pytest

from app.contracts import DataClass, SourceTier, SourceType
from app.research.cache import ResearchCache
from app.research.queries import candidate_names
from app.research.reddit import IntervalLimiter
from app.research.serper import SERPER_URL, SerperSource, host_of, url_key
from app.research.settings import ProviderCap
from app.research.usage import SearchUsage, UsageBook
from tests.fakes.recorded import load_recording, recorded_transport
from tests.fakes.reddit import NOW, Clock
from tests.fakes.research import result, serper_reply

KEY = "not-a-real-serper-key"
CAPS = {"serper": ProviderCap(per_search=6, per_day=60, total=2400)}


def _usage(per_search: int = 6) -> SearchUsage:
    return UsageBook({"serper": ProviderCap(per_search=per_search, per_day=60)}, path=None).search()


def _serper(
    answer: httpx.Response | Exception, *, cache: ResearchCache | None = None
) -> tuple[SerperSource, list[httpx.Request]]:
    asked: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        asked.append(request)
        if isinstance(answer, Exception):
            raise answer
        return answer

    clock = Clock()
    source = SerperSource(
        httpx.Client(transport=httpx.MockTransport(handle)),
        KEY,
        cache=cache or ResearchCache(folder=None, now=lambda: NOW),
        limiter=IntervalLimiter(0.2, clock=clock, sleep=clock.sleep),
        now=lambda: NOW,
    )
    return source, asked


def _ok(*organic: dict[str, str]) -> httpx.Response:
    return httpx.Response(200, json=serper_reply(*organic))


def test_results_become_web_search_sources() -> None:
    source, asked = _serper(_ok(result("https://www.techdesk.example/nova?x=1", "Nova review", "It lasts ten hours.")))
    found = source.search("best laptop India", "IN", _usage())
    assert found.failure is None
    item = found.sources[0]
    assert str(item.url) == "https://www.techdesk.example/nova?x=1"
    assert item.type is SourceType.WEB_SEARCH
    assert item.tier is SourceTier.EDITORIAL
    assert item.title == "Nova review"
    assert item.text == "It lasts ten hours."
    assert item.site == "techdesk.example"
    assert item.community is None
    assert item.via == "search_snippet"
    assert item.fetched_at == NOW
    assert item.data_class is DataClass.PUBLIC
    assert item.text_hash and item.id.startswith("src_")
    body = json.loads(asked[0].content)
    assert body == {"q": "best laptop India", "gl": "in", "hl": "en", "num": 10}
    assert str(asked[0].url) == SERPER_URL
    assert asked[0].headers["X-API-KEY"] == KEY


def test_a_global_region_sends_no_country() -> None:
    source, asked = _serper(_ok())
    source.search("best laptop", "GLOBAL", _usage())
    assert "gl" not in json.loads(asked[0].content)


def test_a_site_list_narrows_the_query_and_counts_once() -> None:
    source, asked = _serper(_ok())
    usage = _usage()
    source.search("laptop review India", "IN", usage, sites=("a.example", "b.example"))
    assert json.loads(asked[0].content)["q"] == "laptop review India (site:a.example OR site:b.example)"
    assert usage.used["serper"] == 1


@pytest.mark.parametrize(
    "link",
    ["https://www.reddit.com/r/laptops/comments/zzz000/", "https://old.reddit.com/r/a/", "https://redd.it/zzz000"],
)
def test_a_result_pointing_at_reddit_is_never_a_source(link: str) -> None:
    """D-53: search results pointing at Reddit are for links and titles only, so its snippet goes nowhere."""
    source, _ = _serper(_ok(result(link, "A thread title", "Text copied from a Reddit post.")))
    found = source.search("laptop", "IN", _usage())
    assert found.sources == ()
    assert found.reddit_titles == ("A thread title",)
    assert "Text copied" not in repr(found)


def test_a_lookalike_host_is_not_taken_for_reddit() -> None:
    source, _ = _serper(_ok(result("https://notreddit.example/a", "Title", "A snippet.")))
    assert len(source.search("laptop", "IN", _usage()).sources) == 1


def test_a_video_result_keeps_only_its_title() -> None:
    """docs/api-setup.md: search results that link to videos are titles and links only."""
    source, _ = _serper(_ok(result("https://www.youtube.com/watch?v=abc", "A video title", "Copied from the video.")))
    item = source.search("laptop", "IN", _usage()).sources[0]
    assert item.text == "A video title"


def test_a_missing_snippet_falls_back_to_the_title() -> None:
    source, _ = _serper(
        httpx.Response(200, json={"organic": [{"title": "Only a title", "link": "https://a.example/"}]})
    )
    assert source.search("laptop", "IN", _usage()).sources[0].text == "Only a title"


@pytest.mark.parametrize(
    "organic",
    [
        {"title": "No link"},
        {"title": "Odd link", "link": "not a link"},
        {"title": "Ftp", "link": "ftp://a.example/x", "snippet": "s"},
        {"link": "https://a.example/nothing-to-read"},
        "not an object",
    ],
)
def test_an_unusable_result_is_skipped(organic: object) -> None:
    good = result("https://b.example/ok")
    source, _ = _serper(httpx.Response(200, json={"organic": [organic, good]}))
    assert [str(s.url) for s in source.search("laptop", "IN", _usage()).sources] == ["https://b.example/ok"]


def test_the_same_link_twice_is_kept_once() -> None:
    link = "https://a.example/x"
    source, _ = _serper(_ok(result(link), result(link)))
    assert len(source.search("laptop", "IN", _usage()).sources) == 1


def test_the_cache_serves_a_repeat_query_with_no_call() -> None:
    source, asked = _serper(_ok(result("https://a.example/x"), result("https://www.reddit.com/r/a/", "A title")))
    usage = _usage()
    first = source.search("laptop", "IN", usage)
    second = source.search("laptop", "IN", usage)
    assert len(asked) == 1
    assert usage.used["serper"] == 1
    assert second.sources == first.sources
    assert second.reddit_titles == ("A title",)
    assert second.from_cache
    assert not first.from_cache


def test_another_query_or_region_is_not_served_from_the_cache() -> None:
    source, asked = _serper(_ok())
    usage = _usage()
    source.search("laptop", "IN", usage)
    source.search("phone", "IN", usage)
    source.search("laptop", "US", usage)
    assert len(asked) == 3


def test_a_cached_result_expires_after_24_hours() -> None:
    moments = [NOW]
    cache = ResearchCache(folder=None, now=lambda: moments[0])
    source, asked = _serper(_ok(result("https://a.example/x")), cache=cache)
    usage = _usage()
    source.search("laptop", "IN", usage)
    moments[0] = NOW + timedelta(hours=23)
    source.search("laptop", "IN", usage)
    assert len(asked) == 1
    moments[0] = NOW + timedelta(hours=24)
    source.search("laptop", "IN", usage)
    assert len(asked) == 2


def test_a_failure_is_never_cached() -> None:
    answers: list[httpx.Response] = [httpx.Response(500), _ok(result("https://a.example/x"))]
    source = SerperSource(
        httpx.Client(transport=httpx.MockTransport(lambda _: answers.pop(0))),
        KEY,
        cache=ResearchCache(folder=None, now=lambda: NOW),
        limiter=IntervalLimiter(0),
        now=lambda: NOW,
    )
    usage = _usage()
    assert source.search("laptop", "IN", usage).failure is not None
    assert len(source.search("laptop", "IN", usage).sources) == 1


def test_the_cache_works_across_restarts_and_holds_no_reddit_snippet(tmp_path: object) -> None:
    from pathlib import Path

    folder = Path(str(tmp_path))
    answer = _ok(result("https://a.example/x"), result("https://www.reddit.com/r/a/", "A title", "Reddit post text."))
    first, _ = _serper(answer, cache=ResearchCache(folder=folder, now=lambda: NOW))
    first.search("laptop", "IN", _usage())
    second, asked = _serper(_ok(), cache=ResearchCache(folder=folder, now=lambda: NOW))
    assert len(second.search("laptop", "IN", _usage()).sources) == 1
    assert asked == []
    on_disk = "".join(p.read_text(encoding="utf-8") for p in folder.rglob("*") if p.is_file())
    assert "Reddit post text" not in on_disk


def test_a_capped_serper_skips_with_no_call() -> None:
    source, asked = _serper(_ok())
    usage = _usage(per_search=0)
    found = source.search("laptop", "IN", usage)
    assert asked == []
    assert found.failure is not None
    assert found.failure.source_type is SourceType.WEB_SEARCH
    assert found.failure.reason == "quota_exhausted"


def test_the_cache_still_answers_when_serper_is_capped() -> None:
    source, asked = _serper(_ok(result("https://a.example/x")))
    source.search("laptop", "IN", _usage())
    found = source.search("laptop", "IN", _usage(per_search=0))
    assert len(asked) == 1
    assert found.failure is None
    assert len(found.sources) == 1


def test_no_key_skips_with_a_failure() -> None:
    asked: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        asked.append(request)
        return httpx.Response(200, json={})

    source = SerperSource(
        httpx.Client(transport=httpx.MockTransport(handle)),
        None,
        cache=ResearchCache(folder=None),
        limiter=IntervalLimiter(0),
    )
    found = source.search("laptop", "IN", _usage())
    assert asked == []
    assert found.failure is not None
    assert found.failure.reason == "blocked"


@pytest.mark.parametrize(
    ("answer", "reason"),
    [
        (httpx.Response(401), "blocked"),
        (httpx.Response(403), "blocked"),
        (httpx.Response(402), "quota_exhausted"),
        (httpx.Response(429), "rate_limited"),
        (httpx.Response(500), "error"),
        (httpx.Response(404), "error"),
        (httpx.Response(200, text="not json"), "error"),
        (httpx.Response(200, json=["a list"]), "error"),
        (httpx.ReadTimeout("slow"), "timeout"),
        (httpx.ConnectError("no route"), "error"),
    ],
)
def test_each_http_failure_gives_its_reason(answer: httpx.Response | Exception, reason: str) -> None:
    source, _ = _serper(answer)
    found = source.search("laptop", "IN", _usage())
    assert found.sources == ()
    assert found.failure is not None
    assert found.failure.source_type is SourceType.WEB_SEARCH
    assert found.failure.reason == reason


def test_a_failed_request_still_counts_as_used() -> None:
    source, _ = _serper(httpx.Response(500))
    usage = _usage()
    source.search("laptop", "IN", usage)
    assert usage.used["serper"] == 1


def _recorded() -> list[dict[str, object]]:
    return load_recording("research_laptop_coding_70k").calls("serper")


def test_a_recorded_reply_parses() -> None:
    recording = load_recording("research_laptop_coding_70k")
    source = SerperSource(
        httpx.Client(transport=recorded_transport(recording, "serper")),
        KEY,
        cache=ResearchCache(folder=None, now=lambda: NOW),
        limiter=IntervalLimiter(0),
        now=lambda: NOW,
    )
    usage = UsageBook({"serper": ProviderCap(per_search=6, per_day=60)}, path=None).search()
    replies = [source.search(f"query {number}", "IN", usage) for number in range(6)]
    assert all(reply.failure is None for reply in replies)
    sources = [s for reply in replies for s in reply.sources]
    assert len(sources) > 30
    assert all(s.type is SourceType.WEB_SEARCH and s.site and s.text for s in sources)
    assert not any("reddit" in str(s.url).lower() for s in sources)
    assert any(reply.reddit_titles for reply in replies)  # the recorder stored markers, never Reddit's words


def test_the_recording_holds_no_reddit_text_and_no_key() -> None:
    text = json.dumps(_recorded())
    assert "not stored" in text  # Reddit-pointing results were found, and redacted
    assert "X-API-KEY" not in text
    for call in _recorded():
        for item in call["response_body"]["organic"]:  # type: ignore[index]
            if "reddit" in item["link"].lower():
                assert item["link"] == "https://www.reddit.com/[reddit link not stored]"
                assert item["title"] == "[reddit text not stored]"


def test_real_search_titles_give_no_false_candidates() -> None:
    """60 real titles, and none names a product: they are lists, such as "8 Best Laptops for Coding Under 70000"."""
    titles = [
        (item["link"], item["title"])
        for call in _recorded()
        for item in call["response_body"]["organic"]  # type: ignore[index]
    ]
    assert len(titles) == 60
    assert candidate_names(titles, "laptop") == []


def test_the_key_never_reaches_a_failure_or_log(caplog: pytest.LogCaptureFixture) -> None:
    source, _ = _serper(httpx.ConnectError(f"proxy said {KEY}"))
    with caplog.at_level(logging.DEBUG):
        found = source.search("a shopper's made-up words", "IN", _usage())
    assert found.failure is not None
    assert KEY not in found.failure.detail
    assert KEY not in caplog.text
    assert "shopper" not in caplog.text
    assert KEY not in repr(source)


def test_each_request_waits_for_the_limiter_and_a_cache_hit_does_not() -> None:
    clock = Clock()
    limiter = IntervalLimiter(0.2, clock=clock, sleep=clock.sleep)
    source = SerperSource(
        httpx.Client(transport=httpx.MockTransport(lambda _: _ok())),
        KEY,
        cache=ResearchCache(folder=None, now=lambda: NOW),
        limiter=limiter,
        now=lambda: NOW,
    )
    usage = _usage()
    source.search("one", "IN", usage)
    source.search("two", "IN", usage)
    source.search("two", "IN", usage)
    source.search("three", "IN", usage)
    assert clock.slept == pytest.approx([0.2, 0.2])


def test_invisible_characters_and_entities_are_cleaned_from_snippets() -> None:
    zero_width = chr(0x200B)
    source, _ = _serper(
        _ok(result("https://a.example/x", f"A{zero_width} title &amp; more", f"Bat{zero_width}tery &amp; screen"))
    )
    item = source.search("laptop", "IN", _usage()).sources[0]
    assert item.title == "A title & more"
    assert item.text == "Battery & screen"


@pytest.mark.parametrize("query", ["a=1&region=in", "x=1&section=2", "y=1&currency=INR", "z=1&copy=2", "w=1&amount=3"])
def test_a_link_is_an_address_and_is_never_decoded(query: str) -> None:
    link = f"https://www.reviews.example/x?{query}"
    source, _ = _serper(_ok(result(link)))
    assert str(source.search("laptop", "IN", _usage()).sources[0].url) == link


@pytest.mark.parametrize(
    "written",
    [
        "A thread title : r/laptops",
        "A thread title - Reddit",
        "A thread title : r/laptops - Reddit",
        "A thread title | Reddit",
    ],
)
def test_a_reddit_results_title_loses_the_sites_own_ending(written: str) -> None:
    source, _ = _serper(_ok(result("https://www.reddit.com/r/laptops/comments/abc/", written)))
    assert source.search("laptop", "IN", _usage()).reddit_titles == ("A thread title",)


@pytest.mark.parametrize("link", ["https://xn--i-7iq.ws/x", "https://xn--a.example/"])
def test_an_address_that_cannot_be_read_loses_only_itself(link: str) -> None:
    good = "https://web.example/a"
    source, _ = _serper(_ok(result(link), result(good)))
    assert [str(s.url) for s in source.search("laptop", "IN", _usage()).sources] == [good]


def test_a_host_that_cannot_be_read_has_no_host_and_keeps_its_own_key() -> None:
    assert host_of("https://xn--a.example/") is None
    assert url_key("https://xn--a.example/") == "https://xn--a.example/"


@pytest.mark.parametrize(
    ("one", "other"),
    [
        ("https://a.example/a/", "https://a.example/a"),
        ("https://a.example/a#top", "https://a.example/a"),
        ("http://a.example/a", "https://a.example/a"),
        ("https://A.Example/a", "https://a.example/a"),
    ],
)
def test_one_page_linked_two_ways_has_one_key(one: str, other: str) -> None:
    assert url_key(one) == url_key(other)


@pytest.mark.parametrize(
    ("one", "other"),
    [
        ("https://a.example/a%2Fb", "https://a.example/a/b"),
        ("https://a.example/a%3Fb=1", "https://a.example/a?b=1"),
        ("https://a.example/a?b=1", "https://a.example/a?b=2"),
        ("https://a.example/a", "https://a.example:8443/a"),
    ],
)
def test_two_pages_have_two_keys(one: str, other: str) -> None:
    assert url_key(one) != url_key(other)
