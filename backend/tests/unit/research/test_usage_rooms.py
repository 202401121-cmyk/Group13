"""D-78 (S4-37): enrich has its own Serper room in a search, and only a call really sent counts.

Research, enrich and commerce each keep their own search usage on one book. Research's per-search 6 can't use up
enrich's 6; the day and the total stay shared. A call refused before it leaves (by a run budget) gives its unit back to
the search, the day and the total. Made-up counts only; nothing is sent anywhere.
"""

from __future__ import annotations

import threading
from datetime import date
from pathlib import Path

import pytest

from app.contracts.enrich import MAX_ENRICH_SEARCHES
from app.research.settings import ProviderCap, load_research_settings
from app.research.usage import UsageBook, not_sent, sent

DAY = date(2026, 11, 19)


def _book(per_search: int = 6, per_day: int = 80, total: int | None = 2400, path: Path | None = None) -> UsageBook:
    caps = {"serper": ProviderCap(per_search=per_search, per_day=per_day, total=total)}
    return UsageBook(caps, path=path, today=lambda: DAY)


@pytest.fixture(autouse=True)
def _nothing_waiting() -> None:
    """Each test starts with no unit waiting on this thread to be said sent or refused."""
    for provider in ("serper", "jina", "firecrawl", "firecrawl_pages", "reddit_archive"):
        sent(provider)


def _take_all(usage: object, provider: str = "serper") -> int:
    taken = 0
    while usage.take(provider):  # type: ignore[attr-defined]
        taken += 1
        sent(provider)
    return taken


def test_research_using_its_6_leaves_enrich_its_6() -> None:
    book = _book()
    research = book.search()
    enrich = book.search(per_search={"serper": MAX_ENRICH_SEARCHES})
    assert _take_all(research) == 6
    assert _take_all(enrich) == 6
    assert book.today()["serper"] == 12  # the day is shared


def test_enrichs_room_is_its_own_whatever_researchs_per_search() -> None:
    book = _book(per_search=2)
    assert _take_all(book.search()) == 2
    enrich = book.search(per_search={"serper": MAX_ENRICH_SEARCHES})
    assert enrich.per_search("serper") == 6 and enrich.left("serper") == 6
    assert _take_all(enrich) == 6


def test_research_and_enrich_using_theirs_still_leave_commerce_its_own() -> None:
    """D-78 item 4: commerce keeps its own search usage, so it is never starved by the other two in a search."""
    book = _book()
    _take_all(book.search())
    _take_all(book.search(per_search={"serper": MAX_ENRICH_SEARCHES}))
    commerce = book.search()
    assert commerce.left("serper") == 6


def test_a_call_refused_before_sending_counts_nothing(tmp_path: Path) -> None:
    path = tmp_path / "usage.json"
    book = _book(path=path)
    usage = book.search()
    assert usage.take("serper")
    sent("serper")
    assert usage.take("serper")
    assert not_sent("serper")  # the run budget refused it: it never left
    assert usage.used["serper"] == 1
    assert book.today()["serper"] == 1 and book.total()["serper"] == 1
    again = _book(path=path)  # through the file, as another process reads it
    assert again.today()["serper"] == 1 and again.total()["serper"] == 1


def test_a_refused_unit_can_be_taken_again() -> None:
    usage = _book(per_search=1).search()
    assert usage.take("serper")
    assert not_sent("serper")
    assert usage.take("serper")  # the search's room came back too


def test_nothing_taken_means_nothing_given_back() -> None:
    book = _book()
    usage = book.search()
    assert not not_sent("serper")
    assert usage.take("serper")
    sent("serper")
    assert not not_sent("serper")  # it was sent: it stays counted
    assert book.today()["serper"] == 1


def test_a_cap_refusal_counts_nothing() -> None:
    book = _book(per_day=1)
    first, second = book.search(), book.search()
    assert first.take("serper")
    sent("serper")
    assert not second.take("serper")
    assert not not_sent("serper")
    assert book.today()["serper"] == 1 and second.used["serper"] == 0


def test_the_daily_cap_still_stops_everything_once_reached() -> None:
    book = _book(per_day=8)
    research = book.search()
    enrich = book.search(per_search={"serper": MAX_ENRICH_SEARCHES})
    commerce = book.search()
    assert _take_all(research) == 6
    assert _take_all(enrich) == 2  # the day has 2 left, though enrich's own room is 6
    assert _take_all(commerce) == 0
    assert book.today()["serper"] == 8


def test_two_searches_at_once_count_correctly() -> None:
    book = _book(per_day=80)
    start = threading.Barrier(4, timeout=5)
    counts: dict[str, int] = {}
    lock = threading.Lock()

    def run(name: str, usage: object, refuse_every: int) -> None:
        start.wait()
        taken = refused = 0
        while usage.take("serper"):  # type: ignore[attr-defined]
            if (taken + refused) % refuse_every == refuse_every - 1:
                assert not_sent("serper")  # this thread's own unit comes back, never another's
                refused += 1
                if refused > 20:  # a guard: the test never needs this many
                    break
                continue
            sent("serper")
            taken += 1
        with lock:
            counts[name] = taken

    searches = [
        ("research a", book.search(), 3),
        ("enrich a", book.search(per_search={"serper": MAX_ENRICH_SEARCHES}), 2),
        ("research b", book.search(), 100),
        ("enrich b", book.search(per_search={"serper": MAX_ENRICH_SEARCHES}), 100),
    ]
    threads = [threading.Thread(target=run, args=search) for search in searches]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert counts == {"research a": 6, "enrich a": 6, "research b": 6, "enrich b": 6}
    assert book.today()["serper"] == 24 and book.total()["serper"] == 24


def test_serper_has_80_a_day() -> None:
    caps = load_research_settings(env={}).caps
    assert caps["serper"].per_day == 80
    assert caps["serper"].per_search == 6 and caps["serper"].total == 2400


def test_a_unit_given_back_is_never_written_over_by_another_books_count(tmp_path: Path) -> None:
    """Research, commerce and enrich each keep a book on one file. One book reading the higher count before another
    gives a unit back must not write that count back later (the reviewer's case)."""
    path = tmp_path / "usage.json"
    enrich_book, research_book = _book(path=path), _book(path=path)
    enrich = enrich_book.search(per_search={"serper": MAX_ENRICH_SEARCHES})
    research = research_book.search()
    assert enrich.take("serper")  # the file says 1
    assert research.left("serper") == 6  # research's book reads the file now
    assert not_sent("serper")  # enrich's request is refused before it leaves
    assert research.take("serper")
    sent("serper")
    assert research_book.today()["serper"] == 1 and enrich_book.today()["serper"] == 1
    assert _book(path=path).total()["serper"] == 1


def test_every_unit_of_one_refused_call_goes_back() -> None:
    """A Firecrawl search takes a page and two credits; refused, all three go back."""
    caps = {
        "firecrawl": ProviderCap(per_search=30, per_day=30),
        "firecrawl_pages": ProviderCap(per_search=4, per_day=30),
    }
    book = UsageBook(caps, path=None, today=lambda: DAY)
    usage = book.search()
    assert usage.take("firecrawl_pages") and usage.take("firecrawl") and usage.take("firecrawl")
    assert not_sent("firecrawl") == 2 and not_sent("firecrawl_pages") == 1
    assert book.today() == {"firecrawl": 0, "firecrawl_pages": 0}
