"""When extraction sends each source (US-46, S4-29): a public page goes when a public provider has room.

S4-2's live run sent every source at once: about 27 per search through 8 workers, the public pages last, after the
Reddit threads had filled Groq. Most public pages then found no room (Mistral takes 2 a minute, Groq's minute was
Reddit's), so stores, makers and review sites gave no claim, and no price was found.

So a search reads its sources in two lanes, sharing one set of workers:

- Reddit-class sources (Reddit threads, and YouTube videos with comments, which carry the reddit data class and go to
  Groq only) and search snippets keep their own lane and rules: the extract stage's Reddit share and budget decide.
- Public pages wait in one queue, ordered maker, store, review site, then any other page, as the price is likeliest
  there. The head of the queue is sent when the LLM client says a public provider has room for it. Its 15 seconds
  start when it is sent, so time in the queue is not its own.

New sends stop when the time left would no longer cover one source's time and every later stage's limit, so those
stages keep their time (build rule 9). What was never sent is "not reached", and the coverage note says so.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Generic, Protocol, TypeVar

from app.contracts import DataClass, Source, SourceType

R = TypeVar("R")

log = logging.getLogger(__name__)

SNIPPET = "search_snippet"
# Maker, store, review site (a lab's test is a review), then any other public page.
SEND_ORDER: dict[SourceType, int] = {
    SourceType.MANUFACTURER: 0,
    SourceType.RETAILER: 1,
    SourceType.REVIEW_SITE: 2,
    SourceType.LAB: 2,
}
OTHER_PAGES = 3
SENT_GAP_S = 0.5  # after a public page is sent, before the next is looked at: the sent one books its slot meanwhile
ROOM_STEP_S = 2.0  # the longest single wait for room, so a cancelled or failed search stops waiting soon
KEPT_FOR_PAGES = 2  # worker slots the Reddit lane leaves free for public pages, when a search has some


class RoomCheck(Protocol):
    """Whether a public provider has room for this page within `within_s`, looking without booking: the LLM
    client's `wait_for_public_room`. None: it can't tell (a client with no room check), so the page goes at once."""

    def __call__(self, source: Source, within_s: float) -> bool | None: ...


@dataclass
class SendWindow:
    """New sends are allowed while the time left covers one source's limit and every later stage's (build rule 9)."""

    seconds_left: Callable[[], float]
    later_stages_s: float
    source_s: float

    def seconds_open(self) -> float:
        return max(0.0, self.seconds_left() - self.later_stages_s - self.source_s)

    def is_open(self) -> bool:
        return self.seconds_open() > 0


def public_page(source: Source) -> bool:
    """A page read as public text: not a search snippet, which is never sent, and not of the reddit data class."""
    return source.via != SNIPPET and source.data_class is DataClass.PUBLIC


def send_order(sources: Sequence[Source]) -> tuple[list[int], list[int]]:
    """The Reddit-class and snippet lane in research's order, and the public queue: maker, store, review, the rest."""
    community = [i for i, s in enumerate(sources) if not public_page(s)]
    public = sorted(
        (i for i, s in enumerate(sources) if public_page(s)),
        key=lambda i: (SEND_ORDER.get(sources[i].type, OTHER_PAGES), i),
    )
    return community, public


@dataclass
class Sent(Generic[R]):
    """What the lanes read, in the sources' own order, and how many sources were never sent."""

    results: list[R] = field(default_factory=list)
    not_reached: int = 0


def read_in_lanes(
    sources: Sequence[Source],
    read: Callable[[Source], R | None],
    *,
    workers: int,
    window: SendWindow,
    room: RoomCheck | None,
    stop: Callable[[], bool] = lambda: False,
    pause: Callable[[float], None] = time.sleep,
) -> Sent[R]:
    """Read every source the window allows, at most `workers` at a time.

    `stop` is the search halting for another reason (a failed fallback, a cancel): what is left then is not counted as
    not reached, which says the time ran out. `read` gives None for a source it didn't start: not reached, unless the
    search had halted. A search snippet is never read, so it is never counted. With no room check, or one that can't
    tell, a public page is sent as soon as a worker is free, as before.
    """
    community, public = send_order(sources)
    results: dict[int, R] = {}
    not_reached = 0
    lock = threading.Lock()
    slots = threading.BoundedSemaphore(max(1, workers))
    # The Reddit lane leaves two slots for pages, so threads waiting on Groq never hold every worker (S4-29).
    kept = KEPT_FOR_PAGES if public and workers > KEPT_FOR_PAGES else 0
    community_slots = threading.BoundedSemaphore(max(1, workers - kept))

    def missed(indexes: Sequence[int]) -> None:
        nonlocal not_reached
        counted = sum(1 for i in indexes if sources[i].via != SNIPPET)
        with lock:
            not_reached += counted

    def run(index: int, lane: threading.BoundedSemaphore | None) -> None:
        try:
            out = read(sources[index])
            if out is None:
                if not stop():
                    missed([index])
            else:
                with lock:
                    results[index] = out
        except Exception as exc:  # the runner's read never raises; if one did, the rest go on (build rule 9)
            log.warning("extract: reading a %s source raised %s", sources[index].type.value, type(exc).__name__)
        finally:
            slots.release()
            if lane is not None:
                lane.release()

    def feed_community(pool: ThreadPoolExecutor) -> None:
        for at, index in enumerate(community):
            community_slots.acquire()
            slots.acquire()
            sent = False
            try:
                if stop():
                    return
                if not window.is_open():
                    missed(community[at:])
                    return
                pool.submit(run, index, community_slots)
                sent = True
            finally:
                if not sent:
                    slots.release()
                    community_slots.release()

    with ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="extract-source") as pool:
        feeder = threading.Thread(target=feed_community, args=(pool,), name="extract-community", daemon=True)
        feeder.start()
        try:
            for at, index in enumerate(public):
                slots.acquire()
                sent = False
                try:
                    verdict = _room_for(sources[index], window, room, stop)
                    if verdict == "closed":
                        missed(public[at:])
                        break
                    if verdict == "halted":
                        break
                    pool.submit(run, index, None)
                    sent = True
                finally:
                    if not sent:
                        slots.release()
                if verdict == "room":
                    pause(SENT_GAP_S)  # the sent page books its slot before the next one looks
        finally:
            feeder.join()
    return Sent([results[i] for i in sorted(results)], not_reached)


def _room_for(source: Source, window: SendWindow, room: RoomCheck | None, stop: Callable[[], bool]) -> str:
    """Wait, in short steps, until a public provider has room for this page.

    "room": there is room; "unchecked": no check could tell, so send now; "closed": the window closed first;
    "halted": the search stopped for another reason.
    """
    while True:
        if stop():
            return "halted"
        if not window.is_open():
            return "closed"
        if room is None:
            return "unchecked"
        verdict = room(source, min(ROOM_STEP_S, window.seconds_open()))
        if verdict is None:
            return "unchecked"
        if verdict:
            return "room" if window.is_open() else "closed"
