"""The page readers' snapshots for commerce's store-page checks (US-24, D-99), and Firecrawl's limits read from config.
Made-up replies through a mock transport; no live call is made. Every page here is made up.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.research.cache import ResearchCache
from app.research.readers import (
    SNAPSHOT_MAX_CHARS,
    FirecrawlClient,
    FirecrawlGate,
    JinaReader,
    PageSnapshot,
    firecrawl_limits,
    shared_gate,
)
from app.research.settings import FirecrawlLimits, load_research_settings
from tests.fakes.store_web import book

PAGE = "https://gameloot.in/asus-vivobook-15-x1504va-laptop"


def _client(reply: httpx.Response, seen: list[httpx.Request] | None = None) -> httpx.Client:
    def handle(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return reply

    return httpx.Client(transport=httpx.MockTransport(handle))


def _firecrawl(reply: httpx.Response, key: str | None = "fc-made-up", seen: list[httpx.Request] | None = None) -> Any:
    gate = FirecrawlGate(limits=FirecrawlLimits(per_minute=100, at_once=4))
    return FirecrawlClient(_client(reply, seen), key, cache=ResearchCache(folder=None), gate=gate)


def _scrape(data: object) -> httpx.Response:
    return httpx.Response(200, json={"success": True, "data": data})


# --- Firecrawl ----------------------------------------------------------------------------------------------------


def test_a_firecrawl_snapshot_has_the_pages_status_its_end_and_its_raw_html() -> None:
    seen: list[httpx.Request] = []
    html = "<html><script type='application/ld+json'>{}</script></html>"
    meta = {"statusCode": 200, "url": "https://www.gameloot.in/p/1", "sourceURL": PAGE, "title": ["Vivobook", 7]}
    usage = book().search()
    page = _firecrawl(_scrape({"rawHtml": html, "metadata": meta}), seen=seen).snapshot(PAGE, usage)
    assert page == PageSnapshot(200, "https://www.gameloot.in/p/1", "Vivobook", html)
    (request,) = seen
    assert json.loads(request.content)["formats"] == ["rawHtml"]  # scripts kept, so schema.org data
    assert (usage.used["firecrawl"], usage.used["firecrawl_pages"]) == (1, 1)


@pytest.mark.parametrize(
    ("meta", "expected"),
    [
        ({"statusCode": True, "url": 5, "title": None}, PageSnapshot(None, "", "", "x")),
        ({"statusCode": "200", "title": "T"}, PageSnapshot(None, "", "T", "x")),
    ],
)
def test_a_firecrawl_snapshot_never_guesses_what_it_was_not_told(meta: dict[str, object], expected: object) -> None:
    assert _firecrawl(_scrape({"rawHtml": "x", "metadata": meta})).snapshot(PAGE, book().search()) == expected


@pytest.mark.parametrize("data", [{"metadata": {"statusCode": 200}}, {"rawHtml": "x"}, ["not", "an", "object"]])
def test_a_firecrawl_reply_with_no_page_is_an_error(data: object) -> None:
    assert _firecrawl(_scrape(data)).snapshot(PAGE, book().search()) == ("error", "the reply had no page")


def test_a_firecrawl_snapshot_needs_its_key_and_room() -> None:
    seen: list[httpx.Request] = []
    assert _firecrawl(_scrape({}), key=None, seen=seen).snapshot(PAGE, book().search())[0] == "blocked"
    full = book().search({"firecrawl_pages": 0})
    assert _firecrawl(_scrape({}), seen=seen).snapshot(PAGE, full) == ("quota_exhausted", "a cap is reached")
    assert seen == []


def test_a_firecrawl_snapshot_keeps_html_up_to_its_limit() -> None:
    html = "a" * (SNAPSHOT_MAX_CHARS + 10)
    page = _firecrawl(_scrape({"rawHtml": html, "metadata": {"statusCode": 200, "url": PAGE}})).snapshot(
        PAGE, book().search()
    )
    assert isinstance(page, PageSnapshot) and len(page.html) == SNAPSHOT_MAX_CHARS


def test_a_failed_firecrawl_snapshot_says_why() -> None:
    assert _firecrawl(httpx.Response(402)).snapshot(PAGE, book().search())[0] == "quota_exhausted"


# --- Jina ---------------------------------------------------------------------------------------------------------


def _jina(reply: httpx.Response, key: str | None = None, seen: list[httpx.Request] | None = None) -> JinaReader:
    return JinaReader(_client(reply, seen), key)


def test_a_jina_snapshot_asks_for_json_and_html_and_sends_its_key() -> None:
    seen: list[httpx.Request] = []
    data = {"url": "https://www.gameloot.in/p/1", "title": "Vivobook", "content": "<html>page</html>"}
    page = _jina(httpx.Response(200, json={"code": 200, "data": data}), key="jina-made-up", seen=seen).snapshot(
        PAGE, book().search()
    )
    assert page == PageSnapshot(200, "https://www.gameloot.in/p/1", "Vivobook", "<html>page</html>")
    (request,) = seen
    assert str(request.url) == f"https://r.jina.ai/{PAGE}"
    assert request.headers["accept"] == "application/json" and request.headers["x-respond-with"] == "html"
    assert request.headers["authorization"] == "Bearer jina-made-up"


@pytest.mark.parametrize(
    ("warning", "status"),
    [
        ("Target URL returned error 404: Not Found", 404),
        ("Target URL returned error 403: Forbidden", 403),
        ("This page may need JavaScript", None),  # a warning with no status: not taken for a 200
        (None, 200),
    ],
)
def test_a_jina_snapshot_reads_the_pages_status_from_its_warning(warning: str | None, status: int | None) -> None:
    data: dict[str, object] = {"url": PAGE, "title": 3, "content": "<html></html>"}
    if warning is not None:
        data["warning"] = warning
    page = _jina(httpx.Response(200, json={"data": data})).snapshot(PAGE, book().search())
    assert page == PageSnapshot(status, PAGE, "", "<html></html>")


@pytest.mark.parametrize(
    ("reply", "why"),
    [
        (httpx.Response(200, text="not JSON"), ("error", "the reply was not JSON")),
        (httpx.Response(200, json={"data": {"url": PAGE}}), ("error", "the reply had no page")),
        (httpx.Response(200, json=["no", "data"]), ("error", "the reply had no page")),
        (httpx.Response(451), ("error", "HTTP 451")),
    ],
)
def test_a_jina_reply_with_no_page_is_an_error(reply: httpx.Response, why: tuple[str, str]) -> None:
    assert _jina(reply).snapshot(PAGE, book().search()) == why


def test_a_jina_snapshot_reads_the_html_where_jina_puts_it_in_html_mode() -> None:
    data = {"url": PAGE, "title": "Vivobook", "html": "<html>from html</html>", "content": "markdown"}
    page = _jina(httpx.Response(200, json={"data": data})).snapshot(PAGE, book().search())
    assert isinstance(page, PageSnapshot) and page.html == "<html>from html</html>"


def test_a_jina_snapshot_with_no_url_says_nothing_about_where_the_page_ended() -> None:
    page = _jina(httpx.Response(200, json={"data": {"content": "<p/>"}})).snapshot(PAGE, book().search())
    assert page == PageSnapshot(200, "", "", "<p/>")


def test_a_jina_snapshot_needs_room() -> None:
    seen: list[httpx.Request] = []
    full = book().search({"jina": 0})
    assert _jina(httpx.Response(200), seen=seen).snapshot(PAGE, full) == ("quota_exhausted", "a cap is reached")
    assert seen == []


# --- Firecrawl's limits come from config (D-99 #7) ----------------------------------------------------------------


def test_firecrawls_limits_are_read_from_research_yaml() -> None:
    assert load_research_settings(env={}).firecrawl_limits == FirecrawlLimits(per_minute=10, at_once=2, cool_s=60)
    assert firecrawl_limits() == load_research_settings(env={}).firecrawl_limits
    assert shared_gate() is shared_gate()  # one gate a process


def test_the_gate_keeps_to_the_limits_it_is_given() -> None:
    now = [0.0]
    gate = FirecrawlGate(lambda: now[0], limits=FirecrawlLimits(per_minute=3, at_once=1, cool_s=5), wait_s=0.01)
    assert gate.try_start() is None
    assert gate.try_start() == "rate_limited"  # one at a time
    gate.finish(429)
    assert gate.try_start() == "rate_limited"  # cooling
    now[0] = 6.0
    assert gate.try_start() is None
    gate.finish(200)
    assert gate.try_start() is None
    gate.finish(200)
    assert gate.try_start() == "rate_limited"  # three this minute


def test_a_broken_limit_in_research_yaml_falls_back_to_the_free_plans(tmp_path: Any) -> None:
    broken = tmp_path / "research.yaml"
    broken.write_text("firecrawl_limits: {per_minute: 0}\n", encoding="utf-8")
    assert load_research_settings(broken, env={}).firecrawl_limits == FirecrawlLimits()
