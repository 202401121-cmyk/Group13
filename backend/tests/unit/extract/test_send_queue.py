"""Extraction's send queue (US-46, S4-29): public pages wait for a public provider's room, makers, stores and review
sites first; Reddit-class sources keep their own lane; nothing new is sent once the later stages would lose their time,
and what was never sent is counted. Made-up sources; the room check and the clock are fakes.
"""

from __future__ import annotations

import threading
from typing import Any

from app.contracts import DataClass, ExtractInput, LLMTask, Source, SourceType, StageContext
from app.extract import GuardedExtractStage
from app.extract.parsing import RawExtraction
from app.extract.schedule import KEPT_FOR_PAGES, ROOM_STEP_S, SendWindow, read_in_lanes, send_order
from app.llm.window import window_until
from app.research.youtube import _Video, video_source
from tests.fakes.llm import FakeLLMClient
from tests.fakes.reddit import NOW
from tests.fakes.research import review_source, thread, web_source


def _page(kind: SourceType, name: str) -> Source:
    page = review_source(f"https://{name}.example/", text=f"Made-up page {name}.")
    return Source.model_validate({**page.model_dump(), "type": kind})


def _video(comments: list[str]) -> Source:
    made = _Video("vid00000001x", "Made-up earbuds review", "Made-up Channel", "We test the Nova Buds.", NOW)
    source = video_source(made, comments, NOW)
    assert source is not None
    return source


OPEN = SendWindow(seconds_left=lambda: 150.0, later_stages_s=59.0, source_s=15.0)


def _lanes(sources: list[Source], **kw: Any) -> tuple[list[str], int]:
    """Read every source the queue sends, one at a time, and give the IDs in the order they were read."""
    order: list[str] = []
    lock = threading.Lock()

    def read(source: Source) -> str:
        with lock:
            order.append(source.id)
        return source.id

    sent = read_in_lanes(sources, read, workers=kw.pop("workers", 1), window=kw.pop("window", OPEN), **kw)
    assert sent.results == [s.id for s in sources if s.id in order]  # results keep the sources' own order
    return order, sent.not_reached


def test_public_pages_go_maker_then_store_then_review_then_other() -> None:
    review, other = _page(SourceType.REVIEW_SITE, "review"), _page(SourceType.WEB_SEARCH, "other")
    store, maker, lab = (
        _page(SourceType.RETAILER, "store"),
        _page(SourceType.MANUFACTURER, "maker"),
        _page(SourceType.LAB, "lab"),
    )
    sources = [review, other, store, maker, lab]
    assert send_order(sources) == ([], [3, 2, 0, 4, 1])  # maker, store, the review and the lab in research's order
    order, missed = _lanes(sources, room=None)
    assert order == [maker.id, store.id, review.id, lab.id, other.id]
    assert missed == 0


def test_reddit_threads_and_videos_with_comments_keep_their_own_lane() -> None:
    reddit, snippet = thread("t3_aaaaaaa"), web_source("https://snippet.example/")
    talked, quiet = _video(["A made-up viewer's comment."]), _video([])
    assert talked.data_class is DataClass.REDDIT and quiet.data_class is DataClass.PUBLIC
    community, public = send_order([reddit, quiet, snippet, talked])
    assert community == [0, 2, 3]  # Reddit, the snippet and the video with comments: research's order, no queue
    assert public == [1]  # a video with no comments is a public page


def test_a_page_waits_in_the_queue_until_a_public_provider_has_room() -> None:
    asked: list[float] = []

    def room(_: Source, within_s: float) -> bool:
        asked.append(within_s)
        return len(asked) >= 3  # no room twice, then room

    order, missed = _lanes([_page(SourceType.REVIEW_SITE, "review")], room=room, pause=lambda _: None)
    assert len(order) == 1 and missed == 0
    assert len(asked) == 3
    assert all(within <= ROOM_STEP_S for within in asked)  # short steps, so a cancelled search stops waiting soon


def test_time_in_the_queue_is_not_the_sources_15_seconds() -> None:
    """The source's own limit starts in `read`, which the queue calls only once there is room."""
    started: list[float] = []
    clock = {"now": 0.0}

    def room(_: Source, within_s: float) -> bool:
        clock["now"] += within_s  # waiting in the queue
        return clock["now"] >= 10

    def read(source: Source) -> str:
        started.append(clock["now"])
        return source.id

    sent = read_in_lanes(
        [_page(SourceType.RETAILER, "store")], read, workers=1, window=OPEN, room=room, pause=lambda _: None
    )
    assert sent.results and started == [clock["now"]]  # it was read after the wait, with its whole limit ahead


def test_a_provider_freeing_room_mid_search_sends_the_next_page() -> None:
    first, second = _page(SourceType.MANUFACTURER, "maker"), _page(SourceType.RETAILER, "store")
    freed = threading.Event()
    done: list[str] = []

    def room(source: Source, within_s: float) -> bool:
        return source.id == first.id or freed.wait(0.05)

    def read(source: Source) -> str:
        done.append(source.id)
        if source.id == first.id:
            freed.set()  # its call ended: the provider has room again
        return source.id

    sent = read_in_lanes([second, first], read, workers=2, window=OPEN, room=room, pause=lambda _: None)
    assert done == [first.id, second.id]
    assert sent.not_reached == 0


def test_the_window_closes_when_later_stages_would_lose_their_time() -> None:
    clock = {"left": 100.0}
    window = SendWindow(seconds_left=lambda: clock["left"], later_stages_s=59.0, source_s=15.0)
    assert window.is_open() and window.seconds_open() == 26.0
    clock["left"] = 74.0
    assert not window.is_open()  # one more source would leave the later stages less than their limits

    clock["left"] = 100.0

    def read(source: Source) -> str:
        clock["left"] -= 10  # each source takes 10 s of the search
        return source.id

    pages = [_page(SourceType.REVIEW_SITE, f"r{i}") for i in range(6)]
    sent = read_in_lanes(pages, read, workers=1, window=window, room=None)
    assert len(sent.results) == 3  # sent at 100, 90 and 80 s left; at 70 the window had closed
    assert sent.not_reached == 3


def test_pages_left_when_the_window_closes_are_not_reached_in_the_note() -> None:
    stage = GuardedExtractStage(FakeLLMClient())
    stage.count_not_reached("srch_test", 3)
    assert stage.counts_for("srch_test") == {
        "sent": 0,
        "over_reddit_budget": 0,
        "over_reddit_share": 0,
        "over_source_cap": 0,
        "unreadable": 0,
        "no_criteria": 0,
        "not_reached": 3,
    }
    assert (
        stage.coverage_note("srch_test") == "3 sources were not read, because the search ran out of time to send them."
    )


def test_a_reddit_thread_does_not_start_after_the_window_closes() -> None:
    closed = SendWindow(seconds_left=lambda: 60.0, later_stages_s=59.0, source_s=15.0)
    order, missed = _lanes([thread("t3_aaaaaaa"), thread("t3_bbbbbbb")], window=closed, room=None)
    assert order == [] and missed == 2


def test_a_client_without_a_room_check_sends_at_once() -> None:
    stage = GuardedExtractStage(FakeLLMClient())  # a fake client has no wait_for_public_room
    inp = ExtractInput(source=_page(SourceType.RETAILER, "store"), criterion_keys=["price"], category_label="laptop")
    assert stage.room_for(StageContext(search_id="srch_test").search_id, inp, within_s=0.0) is None  # can't tell
    paused: list[float] = []
    order, _ = _lanes(
        [_page(SourceType.RETAILER, "a"), _page(SourceType.RETAILER, "b")], room=lambda *_: None, pause=paused.append
    )
    assert len(order) == 2 and paused == []  # sent at once, with no gap
    order, _ = _lanes([_page(SourceType.RETAILER, f"s{i}") for i in range(3)], room=None, workers=3)
    assert len(order) == 3


def test_a_snippet_is_never_counted_as_not_reached() -> None:
    closed = SendWindow(seconds_left=lambda: 60.0, later_stages_s=59.0, source_s=15.0)
    snippets = [web_source(f"https://s{i}.example/") for i in range(3)]
    order, missed = _lanes([*snippets, thread("t3_aaaaaaa")], window=closed, room=None)
    assert order == [] and missed == 1  # the thread; snippets are never read, so never "not reached"


def test_the_reddit_lane_leaves_slots_for_public_pages() -> None:
    """Threads that hold their workers (waiting on Groq) never take the slots kept for pages."""
    release = threading.Event()
    page = _page(SourceType.RETAILER, "store")
    threads = [thread(f"t3_{i:07d}") for i in range(10)]
    read_page = threading.Event()

    def read(source: Source) -> str:
        if source.id == page.id:
            read_page.set()
        else:
            release.wait(5)
        return source.id

    def run() -> None:
        read_in_lanes([*threads, page], read, workers=4, window=OPEN, room=None)

    runner = threading.Thread(target=run)
    runner.start()
    assert read_page.wait(5)  # read while every thread is still held
    release.set()
    runner.join(10)
    assert KEPT_FOR_PAGES == 2


def test_a_read_that_raises_loses_no_slot_and_the_rest_are_read() -> None:
    pages = [_page(SourceType.REVIEW_SITE, f"r{i}") for i in range(4)]

    def read(source: Source) -> str:
        if source.id == pages[0].id:
            raise RuntimeError("a made-up failure")
        return source.id

    sent = read_in_lanes(pages, read, workers=1, window=OPEN, room=None)
    assert sent.results == [p.id for p in pages[1:]]  # one worker, so a lost slot would have hung here


def test_sources_left_by_a_halted_search_are_not_counted_as_out_of_time() -> None:
    order, missed = _lanes([_page(SourceType.RETAILER, "a"), thread("t3_aaaaaaa")], room=None, stop=lambda: True)
    assert order == [] and missed == 0


class _WindowSpy(FakeLLMClient):
    """Records the send window each extraction call ran in."""

    def __init__(self) -> None:
        super().__init__({LLMTask.EXTRACT_CLAIMS: RawExtraction(mentions=[], claims=[])})
        self.windows: list[float | None] = []

    def complete(self, task: LLMTask, system: str, user: str, schema: Any, **kw: Any) -> Any:
        self.windows.append(window_until())
        return super().complete(task, system, user, schema, **kw)


def test_a_public_call_runs_in_the_searchs_window_and_a_reddit_one_outside() -> None:
    spy = _WindowSpy()
    stage = GuardedExtractStage(spy)
    ctx = StageContext(search_id="srch_window")
    stage.open_window(ctx.search_id, 1234.5)
    page = ExtractInput(source=_page(SourceType.RETAILER, "store"), criterion_keys=["price"], category_label="laptop")
    talk = ExtractInput(source=thread("t3_aaaaaaa"), criterion_keys=["price"], category_label="laptop")
    stage.run(page, ctx)
    stage.run(talk, ctx)
    assert spy.windows == [1234.5, None]  # a public page may wait for its last resort; Reddit text keeps its rules
