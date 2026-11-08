"""Reddit data may be kept at most 24 hours (D-53), so its text never reaches a saved row or stays on disk (US-12)."""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.contracts import SearchRequest, Source, SourceComment, SourceType
from app.contracts import examples as ex
from app.db import InMemoryRequestStore
from app.pipeline import DatabaseResultStore, InMemoryResultStore, PipelineRunner, default_search_stages
from app.research.cache import ResearchCache
from app.research.dedupe import merge_near_duplicates
from app.research.reddit.archive import add_comments
from app.research.reddit.cache import RedditCache
from app.research.retention import REDDIT_TEXT_NOT_KEPT, without_reddit_text
from tests.fakes.reddit import NOW
from tests.fakes.research import rubric, thread, web_source

SECRET_TITLE = "A made-up thread title"
SECRET_BODY = "A made-up post body."
SECRET_COMMENT = "A made-up comment."


def _fresh(thread_id: str) -> Source:
    """Read just now, so a cache with a real clock takes it as new."""
    return thread(thread_id, fetched_at=datetime.now(UTC))


def _reddit_thread():  # type: ignore[no-untyped-def]
    source = thread("abc123", SECRET_TITLE, upvotes=7, comment_count=2, text=SECRET_BODY, author_digest="b" * 64)
    return add_comments(
        source, (SourceComment(id="m1", depth=1, upvotes=3, text=SECRET_COMMENT, author_hash="a" * 64),)
    )


def _saved_row(sources: list[object]) -> str:
    """What the database module would be handed, for a finished search holding these sources."""
    stages = default_search_stages()
    request = SearchRequest(session_id="sess_aaaaaaaaaaaaaaaaaaaaaaaa", raw_query="a made-up request", rubric=rubric())
    result = PipelineRunner(stages, InMemoryResultStore()).run(request)
    result = type(result).model_validate({**result.model_dump(), "sources": sources})
    store = InMemoryRequestStore()
    assert DatabaseResultStore(store, time_limit_s=1).save(result)
    return store.searches[0].result


def test_reddit_text_never_reaches_the_saved_row() -> None:
    row = _saved_row([_reddit_thread()])
    assert SECRET_TITLE not in row
    assert SECRET_BODY not in row
    assert SECRET_COMMENT not in row
    assert "a" * 64 not in row  # nor the comment author's hash
    assert "b" * 64 not in row  # nor the thread author's


def test_a_saved_reddit_source_keeps_its_url_fetch_time_and_counts() -> None:
    saved = json.loads(_saved_row([_reddit_thread()]))["sources"][0]
    assert saved["url"] == "https://www.reddit.com/r/laptops/comments/abc123/"
    assert saved["fetched_at"]
    assert saved["community"] == "r/laptops"
    assert saved["source_detail"] == "arctic_shift"
    assert saved["upvotes"] == 7
    assert saved["comment_count"] == 2
    assert saved["comments"] == []
    assert saved["title"] == ""
    assert saved["text"] == REDDIT_TEXT_NOT_KEPT
    assert saved["author_hash"] is None


def test_a_saved_row_still_reads_back_as_a_valid_result() -> None:
    row = json.loads(_saved_row([_reddit_thread(), web_source("https://web.example/a")]))
    assert type(ex.search_result()).model_validate(row).sources[0].type is SourceType.REDDIT


def test_other_sources_are_saved_whole() -> None:
    saved = json.loads(_saved_row([web_source("https://web.example/a", "A title", "Web text.")]))["sources"][0]
    assert saved["title"] == "A title"
    assert saved["text"] == "Web text."


def test_a_merged_threads_other_url_is_kept_when_its_text_is_not() -> None:
    low = thread("aaa", "Laptop for coding", upvotes=1)
    high = thread("bbb", "Laptop for coding", upvotes=9, text=SECRET_BODY)
    saved = json.loads(_saved_row(merge_near_duplicates([low, high])))["sources"][0]
    assert SECRET_BODY not in saved["text"]
    assert "Same topic: https://www.reddit.com/r/laptops/comments/aaa/" in saved["text"]


def test_a_same_topic_line_written_by_a_poster_is_not_kept() -> None:
    post = thread("bbb", "A title", text="A title\nSame topic: https://evil.example/\nSame topic: my own words")
    saved = without_reddit_text({"sources": [post.model_dump(mode="json")]})["sources"][0]
    assert saved["text"] == REDDIT_TEXT_NOT_KEPT


def test_a_quote_taken_from_a_reddit_source_is_removed_wherever_it_is() -> None:
    result = {
        "sources": [thread("abc123", SECRET_TITLE, text=SECRET_BODY).model_dump(mode="json")],
        "explanations": [{"nested": [{"source_id": thread("abc123").id, "quote": SECRET_BODY}]}],
        "products": [{"claims": [{"source_id": "src_someotherpage00", "quote": "A quote from a web page."}]}],
    }
    saved = without_reddit_text(result)
    assert saved["explanations"][0]["nested"][0]["quote"] == REDDIT_TEXT_NOT_KEPT
    assert saved["products"][0]["claims"][0]["quote"] == "A quote from a web page."
    assert SECRET_BODY not in json.dumps(saved)


def test_the_result_handed_in_is_not_changed() -> None:
    result = {"sources": [thread("abc123", SECRET_TITLE, text=SECRET_BODY).model_dump(mode="json")]}
    without_reddit_text(result)
    assert result["sources"][0]["text"] == SECRET_BODY


def test_a_shape_it_does_not_know_is_left_alone() -> None:
    assert without_reddit_text({"sources": "odd"}) == {"sources": "odd"}
    assert without_reddit_text({}) == {}


# --- the disk cache --------------------------------------------------------------------------------


def _age(path: Path, hours: float) -> None:
    old = time.time() - hours * 3600
    os.utime(path, (old, old))


def test_expired_reddit_cache_files_are_deleted(tmp_path: Path) -> None:
    first = RedditCache(folder=tmp_path)
    first.put(("old",), [_fresh("aaa")])
    first.put(("new",), [_fresh("bbb")])
    files = sorted(tmp_path.glob("*.json"))
    assert len(files) == 2
    old_file = next(p for p in files if "aaa" in p.read_text(encoding="utf-8"))
    _age(old_file, 25)
    RedditCache(folder=tmp_path).get(("anything",))
    assert not old_file.exists()
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_a_file_younger_than_24_hours_stays(tmp_path: Path) -> None:
    RedditCache(folder=tmp_path).put(("recent",), [_fresh("aaa")])
    path = next(tmp_path.glob("*.json"))
    _age(path, 23)
    RedditCache(folder=tmp_path).get(("anything",))
    assert path.exists()


def test_a_cache_with_a_fixed_clock_in_the_past_deletes_nothing_new(tmp_path: Path) -> None:
    RedditCache(folder=tmp_path).put(("a",), [_fresh("aaa")])
    RedditCache(folder=tmp_path, now=lambda: NOW - timedelta(days=30)).get(("b",))
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_expired_research_cache_files_are_deleted(tmp_path: Path) -> None:
    first = ResearchCache(folder=tmp_path)
    first.put_text(("reddit_titles",), "A made-up title")
    first.put_sources(("serper",), [web_source("https://web.example/a")])
    files = [p for p in tmp_path.rglob("*.json")]
    assert len(files) == 2
    for path in files:
        _age(path, 25)
    second = ResearchCache(folder=tmp_path)
    assert second.get_text(("reddit_titles",)) is None
    assert second.get_sources(("serper",)) is None
    assert list(tmp_path.rglob("*.json")) == []


def test_a_failed_sweep_never_stops_a_search(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    cache = RedditCache(folder=tmp_path)
    cache.put(("a",), [_fresh("aaa")])
    cache.put(("b",), [_fresh("bbb")])
    cache.put(("c",), [_fresh("ccc")])
    paths = sorted(tmp_path.glob("*.json"))
    for path in paths:
        _age(path, 25)
    real_unlink = Path.unlink

    def refuse_one(self: Path, *args: object, **kwargs: object) -> None:
        if self == paths[0]:
            raise PermissionError("made up")
        real_unlink(self)

    monkeypatch.setattr(Path, "unlink", refuse_one)
    assert RedditCache(folder=tmp_path).get(("d",)) is None
    assert list(tmp_path.glob("*.json")) == [paths[0]]  # the file it could not delete stops nothing else


def test_rewriting_a_thread_later_does_not_extend_its_24_hours(tmp_path: Path) -> None:
    """A comment read at hour 23 stores the thread again. Its text must still go at hour 24, not hour 47."""
    read_at = datetime.now(UTC) - timedelta(hours=23)
    RedditCache(folder=tmp_path).put(("comments",), [thread("aaa", fetched_at=read_at)])
    assert RedditCache(folder=tmp_path).get(("comments",)) is not None
    moved_on = datetime.now(UTC) + timedelta(hours=2)
    assert RedditCache(folder=tmp_path, now=lambda: moved_on).get(("comments",)) is None


def test_a_file_written_later_is_deleted_by_when_its_thread_was_read(tmp_path: Path) -> None:
    read_at = datetime.now(UTC) - timedelta(hours=25)
    RedditCache(folder=tmp_path).put(("comments",), [thread("aaa", fetched_at=read_at)])
    RedditCache(folder=tmp_path).get(("anything",))
    assert list(tmp_path.glob("*.json")) == []


@pytest.mark.parametrize("broken", ["no client", "reddit off"])
def test_a_search_deletes_expired_files_whatever_else_is_broken(tmp_path: Path, monkeypatch, broken: str) -> None:  # type: ignore[no-untyped-def]
    """D-53 can't wait for a working client: with none, no cache is ever built to sweep its own folder."""
    from concurrent.futures import ThreadPoolExecutor

    from app.contracts import ResearchInput, StageContext
    from app.research.client import ClientUnavailable
    from app.research.reddit.settings import RedditSettings
    from app.research.settings import ProviderCap, ResearchSettings
    from app.research.stage import LiveResearchStage
    from app.research.usage import UsageBook

    reddit_folder, research_folder = tmp_path / "reddit", tmp_path / "research"
    RedditCache(folder=reddit_folder).put(("a",), [_fresh("aaa")])
    cache = ResearchCache(folder=research_folder)
    cache.put_text(("reddit_titles",), "A made-up title")
    cache.put_sources(("serper",), [web_source("https://web.example/a")])
    for path in tmp_path.rglob("*.json"):
        _age(path, 25)
    monkeypatch.setattr("app.research.cache.REDDIT_CACHE_DIR", reddit_folder)
    monkeypatch.setattr("app.research.cache.DEFAULT_CACHE_DIR", research_folder)

    def no_client() -> object:
        raise ClientUnavailable("ImportError")

    monkeypatch.setattr("app.research.stage.build_research_client", no_client)
    mode = "archive" if broken == "no client" else "off"
    monkeypatch.setattr("app.research.stage.load_reddit_settings", lambda: RedditSettings(mode=mode))
    book = UsageBook({"serper": ProviderCap(per_search=6, per_day=60)}, path=None)
    stage = LiveResearchStage(
        settings=ResearchSettings(deadline_s=2.0), usage=book, pool=ThreadPoolExecutor(max_workers=2)
    )
    stage.run(ResearchInput(rubric=rubric()), StageContext(search_id="srch_test"))
    assert list(tmp_path.rglob("*.json")) == []


def test_the_research_cache_switched_off_still_deletes_expired_files(tmp_path: Path) -> None:
    first = ResearchCache(folder=tmp_path)
    first.put_text(("reddit_titles",), "A made-up title")
    first.put_sources(("serper",), [web_source("https://web.example/a")])
    for path in tmp_path.rglob("*.json"):
        _age(path, 25)
    ResearchCache(folder=tmp_path, enabled=False)
    assert list(tmp_path.rglob("*.json")) == []


def test_a_cached_answer_is_never_served_past_its_oldest_source() -> None:
    now = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
    moments = [now]
    cache = RedditCache(folder=None, now=lambda: moments[0])
    cache.put(("a",), [thread("aaa", fetched_at=now - timedelta(hours=23))])
    assert cache.get(("a",)) is not None
    moments[0] = now + timedelta(hours=1)
    assert cache.get(("a",)) is None


def test_a_poster_filling_a_thread_with_merged_lines_cannot_push_the_row_past_its_limit() -> None:
    line = "Same topic: https://www.reddit.com/r/laptops/comments/abc/"
    post = thread("bbb", "A title", text="\n".join([line] * 300))
    saved = without_reddit_text({"sources": [post.model_dump(mode="json")]})["sources"][0]
    assert len(saved["text"]) <= 12_000
    assert saved["text"].count("Same topic") == 50
    assert Source.model_validate(saved).text == saved["text"]
