"""The reader ladder and Firecrawl's other tools (US-38, D-100): a plain fetch, Jina, a Firecrawl scrape, then a
Firecrawl scrape with the page rendered; map for one site's page by a product's name; batch scrape for several pages.
Every request asks for the basic proxy, and every credit is counted before it goes. Recorded answers only."""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
import pytest

from app.pipeline import build_coverage_note
from app.research import readers as readers_module
from app.research.cache import ResearchCache
from app.research.readers import (
    FIRECRAWL_BATCH_URL,
    FIRECRAWL_MAP_URL,
    FIRECRAWL_SCRAPE_URL,
    JINA_URL,
    PROXY,
    RENDER_ACTIONS,
    FirecrawlClient,
    FirecrawlGate,
    MappedLink,
)
from app.research.stage import LiveResearchStage, readers_note
from tests.fakes.reddit import NOW
from tests.fakes.research import Web, html_page
from tests.unit.research.test_page_readers import PAGE, SITE, THIN, WORDS, _jina, _pages, _scrape, _usage

LOGIN = f"{SITE}/account/login"


def _scrapes(web: Web) -> list[dict[str, Any]]:
    return [json.loads(r.content) for r in web.requests if str(r.url) == FIRECRAWL_SCRAPE_URL]


class Scrapes:
    """Firecrawl's scrape: each answer in turn, one a request (the last repeats)."""

    def __init__(self, *answers: httpx.Response) -> None:
        self.answers = list(answers)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


# --- the ladder ---------------------------------------------------------------------------------------------------


def test_a_page_thin_everywhere_else_is_read_by_firecrawl_with_its_scripts_run() -> None:
    web = (
        Web()
        .on(PAGE, THIN)
        .on(*_jina(PAGE, text="Loading..."))
        .on(FIRECRAWL_SCRAPE_URL, Scrapes(_scrape(markdown="Loading..."), _scrape()))
    )
    pages = _pages(web)
    pages.ends = time.monotonic() + 60
    found = pages.fetch(PAGE, _usage())
    assert found.source is not None and found.source.via == "firecrawl"
    plain, rendered = _scrapes(web)
    assert "actions" not in plain and plain["proxy"] == PROXY
    assert rendered["actions"] == list(RENDER_ACTIONS) and rendered["waitFor"] == 2000 and rendered["proxy"] == PROXY
    assert all(action["type"] in ("scroll", "wait") for action in rendered["actions"])  # never a click
    assert [f.detail.split(":")[0] for f in found.lost_to] == ["Jina Reader", "Firecrawl"]
    assert pages.tally() == {
        "wanted": 1,
        "thin_direct": 1,
        "thin_jina": 1,
        "thin_firecrawl": 1,
        "read_firecrawl_rendered": 1,
    }


def test_each_step_failing_in_turn_names_every_reader() -> None:
    web = Web().on(PAGE, THIN).on(f"{JINA_URL}{PAGE}", httpx.Response(500))
    web.on(FIRECRAWL_SCRAPE_URL, Scrapes(_scrape("Loading..."), _scrape(status=500)))
    pages = _pages(web)
    pages.ends = time.monotonic() + 60
    found = pages.fetch(PAGE, _usage())
    assert found.source is None
    # Named as Firecrawl, so the coverage note's reader sentence matches it (app/pipeline.py's READER_NAMES).
    assert [f.detail.split(":")[0] for f in found.lost_to] == ["Jina Reader", "Firecrawl", "Firecrawl"]
    assert found.lost_to[-1].detail == "Firecrawl: rendered: HTTP 500"
    assert len(_scrapes(web)) == 2  # the rendered step is tried once: never retried (a retry costs another page)
    assert "Firecrawl had an error, so some hard-to-read pages may be missing." in (
        build_coverage_note([], list(found.lost_to), False) or ""
    )


def test_a_firecrawl_scrape_that_failed_is_never_rendered() -> None:
    web = Web().on(PAGE, THIN).on(f"{JINA_URL}{PAGE}", httpx.Response(500))
    web.on(FIRECRAWL_SCRAPE_URL, _scrape(status=500))
    pages = _pages(web)
    pages.ends = time.monotonic() + 60
    usage = _usage()
    assert pages.fetch(PAGE, usage).source is None
    sent = _scrapes(web)
    assert len(sent) == 2 and all("actions" not in body for body in sent)  # the scrape and its one retry (US-31)
    assert usage.left("firecrawl_pages") == _usage().left("firecrawl_pages") - 2


def test_thin_text_moves_to_the_next_step_and_the_first_with_enough_wins() -> None:
    web = Web().on(PAGE, THIN).on(*_jina(PAGE, text="Loading...")).on(FIRECRAWL_SCRAPE_URL, _scrape())
    pages = _pages(web)
    found = pages.fetch(PAGE, _usage())
    assert found.source is not None and found.source.via == "firecrawl"
    assert len(_scrapes(web)) == 1  # the plain scrape was enough: nothing rendered
    assert pages.tally()["read_firecrawl"] == 1


@pytest.mark.parametrize(
    ("answer", "why"),
    [
        (httpx.Response(404, json={"success": False}), "a page that isn't there"),
        (httpx.Response(402, json={"success": False}), "out of credits: switched off"),
        (httpx.Response(401, json={"success": False}), "a bad key: switched off"),
        (httpx.Response(429, json={"success": False}), "too many: cooling"),
    ],
)
def test_a_firecrawl_scrape_rendering_cant_help_goes_no_further(answer: httpx.Response, why: str) -> None:
    web = Web().on(PAGE, THIN).on(f"{JINA_URL}{PAGE}", httpx.Response(500)).on(FIRECRAWL_SCRAPE_URL, answer)
    gate = FirecrawlGate()
    pages = _pages(web, gate=gate)
    pages.ends = time.monotonic() + 60
    assert pages.fetch(PAGE, _usage()).source is None
    assert len(_scrapes(web)) == 1, why
    if answer.status_code in (401, 402):  # off for the run: the next page goes no further than Jina
        again = _pages(web, gate=gate)
        again.fetch(f"{SITE}/another-page", _usage())
        assert len(_scrapes(web)) == 1


def test_the_cap_reached_mid_ladder_stops_before_the_rendered_step() -> None:
    from app.research.settings import ProviderCap

    web = Web().on(PAGE, THIN).on(*_jina(PAGE, text="Loading...")).on(FIRECRAWL_SCRAPE_URL, _scrape("Loading..."))
    pages = _pages(web)
    pages.ends = time.monotonic() + 60
    found = pages.fetch(PAGE, _usage(firecrawl_pages=ProviderCap(per_search=1, per_day=30)))
    assert found.source is None and len(_scrapes(web)) == 1
    assert found.lost_to[-1].reason == "quota_exhausted" and "rendered" in found.lost_to[-1].detail


def test_the_rendered_step_never_goes_with_too_little_time_before_the_deadline() -> None:
    web = Web().on(PAGE, THIN).on(*_jina(PAGE, text="Loading...")).on(FIRECRAWL_SCRAPE_URL, _scrape("Loading..."))
    pages = _pages(web)
    pages.ends = time.monotonic() + 2  # research's deadline is 2 s away: a rendered scrape takes longer
    found = pages.fetch(PAGE, _usage())
    assert found.source is None and len(_scrapes(web)) == 1
    # Skipped, not failed: the thin scrape is the last reader named, and the skip is only counted.
    assert found.lost_to[-1].detail == "Firecrawl: too little text"
    assert pages.tally()["skipped_firecrawl_rendered"] == 1


def test_the_rendered_step_has_its_own_time_limit_and_asks_for_a_fresh_page() -> None:
    web = Web().on(FIRECRAWL_SCRAPE_URL, _scrape())
    client = _client(web)
    assert not isinstance(client.scrape(PAGE, _usage(), rendered=True, until=time.monotonic() + 600), tuple)
    assert not isinstance(client.scrape(PAGE, _usage(), rendered=True), tuple)
    for body in _scrapes(web):
        assert body["maxAge"] == 0  # the plain scrape just read Firecrawl's copy
        assert body["timeout"] <= readers_module.RENDER_MAX_S * 1000  # 10 minutes away or none: at most RENDER_MAX_S


def test_the_rendered_step_checks_its_time_again_after_waiting_for_its_slot() -> None:
    web = Web().on(FIRECRAWL_SCRAPE_URL, _scrape())
    client = _client(web)

    class SlowGate(FirecrawlGate):
        def try_start(self, wait_s: float | None) -> Any:
            time.sleep(0.2)  # the slot came late: under RENDER_MIN_S is now left
            return super().try_start(wait_s)

    client.gate = SlowGate()
    until = time.monotonic() + readers_module.RENDER_MIN_S + 0.1
    assert client.scrape(PAGE, _usage(), rendered=True, until=until) == ("timeout", "no time left to ask")
    assert _scrapes(web) == []


def test_robots_saying_no_stops_before_any_reader_and_is_counted() -> None:
    web = Web().on(f"{SITE}/robots.txt", httpx.Response(200, text="User-agent: *\nDisallow: /"))
    web.on(*_jina(PAGE)).on(FIRECRAWL_SCRAPE_URL, _scrape())
    pages = _pages(web)
    assert pages.fetch(PAGE, _usage()).reason == "blocked"
    assert web.asked(f"{JINA_URL}{PAGE}") == 0 and _scrapes(web) == []
    assert pages.tally() == {"wanted": 1, "blocked": 1}


def test_a_login_page_is_refused_before_any_request() -> None:
    web = Web().on(LOGIN, html_page()).on(*_jina(LOGIN)).on(FIRECRAWL_SCRAPE_URL, _scrape())
    assert _pages(web).fetch(LOGIN, _usage()).source is None
    assert web.requests == []


def test_a_cached_page_spends_nothing() -> None:
    web = Web().on(PAGE, html_page())
    cache = ResearchCache(folder=None, now=lambda: NOW)
    first = _pages(web, cache=cache)
    assert first.fetch(PAGE, _usage()).source is not None
    sent = len(web.requests)
    again = _pages(web, cache=cache)
    usage = _usage()
    found = again.fetch(PAGE, usage)
    assert found.from_cache and len(web.requests) == sent
    assert usage.left("pages") == _usage().left("pages")  # nothing counted
    assert again.tally() == {"wanted": 1, "read_cache": 1}


# --- the note -------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tally", "note"),
    [
        ({}, None),
        ({"read_direct": 4}, None),
        ({"read_jina": 1}, "1 hard-to-read page was read, 1 by Jina Reader."),
        (
            {"read_jina": 2, "read_firecrawl": 1, "read_firecrawl_rendered": 1},
            "4 hard-to-read pages were read, 2 by Jina Reader and 2 by Firecrawl (1 after the page's scripts ran).",
        ),
        ({"read_firecrawl": 3}, "3 hard-to-read pages were read, 3 by Firecrawl."),
    ],
)
def test_the_note_names_the_readers_that_worked(tally: dict[str, int], note: str | None) -> None:
    assert readers_note(tally) == note


def test_research_keeps_each_searchs_tally_for_its_note_and_the_live_briefs() -> None:
    from app.contracts import ResearchInput, StageContext
    from tests.fakes.research import FakeSerper, rubric

    web = Web().on(PAGE, THIN).on(*_jina(PAGE))
    pages = _pages(web)
    stage = LiveResearchStage(
        serper=FakeSerper({}),
        pages=pages,
        reddit=None,
        youtube=None,
        usage=None,  # type: ignore[arg-type]
    )
    stage._keep_tally("srch_one", {"read_jina": 1, "wanted": 1})
    assert stage.reader_tally("srch_one") == {"read_jina": 1, "wanted": 1}
    assert stage.coverage_note("srch_one") == "1 hard-to-read page was read, 1 by Jina Reader."
    assert stage.reader_tally("srch_one") == {"read_jina": 1, "wanted": 1}  # the note leaves it for live_briefs
    assert stage.coverage_note("srch_none") is None
    for n in range(70):
        stage._keep_tally(f"srch_{n}", {"wanted": n})
    assert stage.reader_tally("srch_0") == {} and stage.reader_tally("srch_69") == {"wanted": 69}
    pages._count("read_jina")  # a page this reader read: the run must keep its tally under the search's id
    stage.run(ResearchInput(rubric=rubric()), StageContext(search_id="srch_run"))
    assert pages.ends is not None  # research gave the ladder its deadline
    assert stage.reader_tally("srch_run")["read_jina"] == 1
    assert stage.coverage_note("srch_run") == "1 hard-to-read page was read, 1 by Jina Reader."


def test_the_research_note_reaches_the_coverage_note() -> None:
    note = build_coverage_note([], [], False, research_note="1 hard-to-read page was read, 1 by Jina Reader.")
    assert note == "1 hard-to-read page was read, 1 by Jina Reader."


# --- map ------------------------------------------------------------------------------------------------------------


def _client(web: Web, cache: ResearchCache | None = None, key: str | None = "fc-made-up") -> FirecrawlClient:
    return FirecrawlClient(
        web.client(),
        key,
        cache=cache or ResearchCache(folder=None, now=lambda: NOW),
        gate=FirecrawlGate(),
        now=lambda: NOW,
    )


def test_map_finds_a_sites_addresses_by_a_name_for_one_credit_and_keeps_them_a_day() -> None:
    links = [
        {"url": "https://www.maker.example/nova-book-14/specs", "title": "Nova Book 14 specs"},
        "https://www.maker.example/nova-book-14",
        {"url": "http://insecure.example/x"},
        7,
    ]
    web = Web().on(FIRECRAWL_MAP_URL, httpx.Response(200, json={"success": True, "links": links}))
    cache = ResearchCache(folder=None, now=lambda: NOW)
    usage = _usage()
    found = _client(web, cache).map("https://www.maker.example", "Nova Book 14", usage)
    assert found == [
        MappedLink("https://www.maker.example/nova-book-14/specs", "Nova Book 14 specs"),
        MappedLink("https://www.maker.example/nova-book-14"),
    ]
    (sent,) = [json.loads(r.content) for r in web.requests]
    assert sent == {
        "url": "https://www.maker.example",
        "search": "Nova Book 14",
        "limit": 20,
        "includeSubdomains": False,
        "location": {"country": "IN"},
    }
    assert _usage().left("firecrawl") - usage.left("firecrawl") == 1
    again = _client(web, cache).map("https://www.maker.example", "Nova Book 14", _usage())
    assert again == found and len(web.requests) == 1  # kept: no second request


@pytest.mark.parametrize(
    ("answer", "reason"),
    [
        (httpx.Response(200, json={"success": True, "links": "none"}), "error"),
        (httpx.Response(402, json={"success": False}), "quota_exhausted"),
    ],
)
def test_map_finding_nothing_says_why(answer: httpx.Response, reason: str) -> None:
    web = Web().on(FIRECRAWL_MAP_URL, answer)
    found = _client(web).map("https://www.maker.example", "Nova Book 14", _usage())
    assert isinstance(found, tuple) and found[0] == reason


def test_map_with_no_key_or_no_credits_sends_nothing() -> None:
    from app.research.settings import ProviderCap

    web = Web()
    assert _client(web, key=None).map("https://www.maker.example", "Nova", _usage())[0] == "blocked"  # type: ignore[index]
    capped = _usage(firecrawl=ProviderCap(per_search=0, per_day=30))
    assert _client(web).map("https://www.maker.example", "Nova", capped)[0] == "quota_exhausted"  # type: ignore[index]
    assert web.requests == []


def test_a_kept_map_that_cant_be_read_says_so() -> None:
    cache = ResearchCache(folder=None, now=lambda: NOW)
    cache.put_text(("firecrawl_map", "https://www.maker.example", "Nova"), "not json")
    found = _client(Web(), cache).map("https://www.maker.example", "Nova", _usage())
    assert found == ("error", "a kept map could not be read")


# --- batch scrape -----------------------------------------------------------------------------------------------------

A, B, C = (f"{SITE}/page-{n}" for n in "abc")


class Batch:
    """Firecrawl's batch scrape: the job, then its progress, as told."""

    def __init__(self, polls: list[dict[str, Any]], start: httpx.Response | None = None) -> None:
        self.polls = polls
        self.start = start or httpx.Response(200, json={"success": True, "id": "job-1"})
        self.cancelled = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return self.start
        if request.method == "DELETE":
            self.cancelled = True
            return httpx.Response(200, json={"success": True})
        return httpx.Response(200, json=self.polls.pop(0) if len(self.polls) > 1 else self.polls[0])


def _page(url: str, markdown: str = WORDS, status: int = 200, error: str | None = None) -> dict[str, Any]:
    meta: dict[str, Any] = {"sourceURL": url, "statusCode": status, "title": "A made-up page"}
    if error:
        meta["error"] = error
    return {"markdown": markdown, "metadata": meta}


def _batch_web(batch: Batch) -> Web:
    web = Web().on(FIRECRAWL_BATCH_URL, batch).on(f"{FIRECRAWL_BATCH_URL}/job-1", batch)
    return web


def test_a_batch_reads_several_pages_counting_every_page_and_credit_first(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(readers_module, "BATCH_POLL_S", 0.0)
    batch = Batch([{"status": "scraping", "data": [_page(A)]}, {"status": "completed", "data": [_page(A), _page(B)]}])
    web = _batch_web(batch)
    usage = _usage()
    found = _client(web).batch_scrape([A, B, A], usage, until=time.monotonic() + 30)
    assert set(found) == {A, B} and all(not isinstance(v, tuple) for v in found.values())
    (sent,) = [json.loads(r.content) for r in web.requests if r.method == "POST"]
    assert sent["urls"] == [A, B] and sent["proxy"] == PROXY and sent["maxConcurrency"] >= 1
    assert _usage().left("firecrawl") - usage.left("firecrawl") == 2
    assert _usage().left("firecrawl_pages") - usage.left("firecrawl_pages") == 2


def test_a_batch_partly_failing_gives_each_page_its_own_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(readers_module, "BATCH_POLL_S", 0.0)
    batch = Batch([{"status": "completed", "data": [_page(A), _page(B, status=404), _page(C, error="timed out")]}])
    found = _client(_batch_web(batch)).batch_scrape([A, B, C], _usage(), until=time.monotonic() + 30)
    assert not isinstance(found[A], tuple)
    assert found[B] == ("not_found", "the page answered HTTP 404")
    assert found[C] == ("error", "Firecrawl couldn't read the page")


def test_a_batch_past_its_time_is_cancelled_and_its_unread_pages_said_so(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(readers_module, "BATCH_POLL_S", 0.0)
    batch = Batch([{"status": "scraping", "data": [_page(A), "not a page"]}])
    found = _client(_batch_web(batch)).batch_scrape([A, B], _usage(), until=time.monotonic() + 1.2)
    assert not isinstance(found[A], tuple) and found[B] == ("timeout", "not read in time")
    assert batch.cancelled


def test_a_batch_whose_progress_cant_be_read_ends_with_what_it_has(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(readers_module, "BATCH_POLL_S", 0.0)
    batch = Batch([{"status": "scraping"}])
    web = Web().on(FIRECRAWL_BATCH_URL, batch).on(f"{FIRECRAWL_BATCH_URL}/job-1", httpx.Response(500))
    found = _client(web).batch_scrape([A], _usage(), until=time.monotonic() + 30)
    assert found[A] == ("timeout", "not read in time")


@pytest.mark.parametrize(
    ("start", "why"),
    [
        (httpx.Response(200, json={"success": True}), ("error", "the reply had no job")),
        (httpx.Response(402, json={"success": False}), ("quota_exhausted", "HTTP 402")),
    ],
)
def test_a_batch_that_doesnt_start_fails_every_page(start: httpx.Response, why: tuple[str, str]) -> None:
    found = _client(_batch_web(Batch([], start))).batch_scrape([A, B], _usage(), until=time.monotonic() + 30)
    assert found == {A: why, B: why}


def test_a_batch_with_no_key_or_no_room_sends_nothing_and_counts_nothing() -> None:
    from app.research.settings import ProviderCap

    web = Web()
    assert _client(web, key=None).batch_scrape([A], _usage(), until=time.monotonic() + 30)[A][0] == "blocked"
    usage = _usage(firecrawl_pages=ProviderCap(per_search=1, per_day=30))
    found = _client(web).batch_scrape([A, B], usage, until=time.monotonic() + 30)
    assert found[A][0] == "quota_exhausted" and web.requests == []  # type: ignore[index]
    assert usage.left("firecrawl_pages") == 1  # the page taken before the cap said no went back


def test_every_read_asks_for_the_basic_proxy() -> None:
    web = Web().on(FIRECRAWL_SCRAPE_URL, _scrape())
    client = _client(web)
    client.scrape(PAGE, _usage())
    client.snapshot(PAGE, _usage())
    assert all(body["proxy"] == "basic" for body in _scrapes(web))


def test_a_batch_whose_progress_isnt_json_ends_with_what_it_has(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(readers_module, "BATCH_POLL_S", 0.0)
    batch = Batch([{"status": "scraping"}])
    web = Web().on(FIRECRAWL_BATCH_URL, batch).on(f"{FIRECRAWL_BATCH_URL}/job-1", httpx.Response(200, text="not json"))
    assert _client(web).batch_scrape([A], _usage(), until=time.monotonic() + 30)[A] == ("timeout", "not read in time")


def test_a_scrape_with_no_page_text_fails_and_a_list_title_gives_its_first_words() -> None:
    web = Web().on(FIRECRAWL_SCRAPE_URL, httpx.Response(200, json={"success": True, "data": {"metadata": {}}}))
    assert _client(web).scrape(PAGE, _usage()) == ("error", "the reply had no page text")
    listed = {"success": True, "data": {"markdown": WORDS, "metadata": {"title": [3, "Nova Book 14 review"]}}}
    read = _client(Web().on(FIRECRAWL_SCRAPE_URL, httpx.Response(200, json=listed))).scrape(PAGE, _usage())
    assert not isinstance(read, tuple) and read.title == "Nova Book 14 review"
