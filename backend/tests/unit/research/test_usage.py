"""Provider use is counted across restarts and capped per search, per day and in total (US-12)."""

from __future__ import annotations

import contextlib
import json
import logging
import subprocess
import threading
from collections.abc import Callable
from datetime import date
from pathlib import Path

import pytest

from app.research.settings import ProviderCap
from app.research.usage import DEFAULT_USAGE_PATH, UsageBook

CAPS = {
    "serper": ProviderCap(per_search=6, per_day=60, total=2400),
    "reddit_archive": ProviderCap(per_search=10, per_day=500),
    "pages": ProviderCap(per_search=16, per_day=200),
}
DAY = date(2026, 11, 8)


def _book(
    path: Path | None, *, caps: dict[str, ProviderCap] = CAPS, today: Callable[[], date] = lambda: DAY
) -> UsageBook:
    return UsageBook(caps, path=path, today=today)


def test_counts_survive_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "usage.json"
    search = _book(path).search()
    assert search.take("serper")
    assert search.take("serper")
    assert search.take("pages")
    again = _book(path)
    assert again.today()["serper"] == 2
    assert again.today()["pages"] == 1
    assert again.total()["serper"] == 2


def test_a_new_utc_day_starts_at_zero_but_keeps_the_total(tmp_path: Path) -> None:
    path = tmp_path / "usage.json"
    assert _book(path).search().take("serper")
    tomorrow = _book(path, today=lambda: date(2026, 11, 9))
    assert tomorrow.today()["serper"] == 0
    assert tomorrow.total()["serper"] == 1


def test_the_daily_cap_is_enforced(tmp_path: Path) -> None:
    book = _book(tmp_path / "usage.json", caps={"serper": ProviderCap(per_search=100, per_day=2)})
    search = book.search()
    assert search.take("serper")
    assert search.take("serper")
    assert not search.take("serper")
    assert not book.search().take("serper")
    assert book.today()["serper"] == 2


def test_the_search_cap_is_enforced() -> None:
    book = _book(None, caps={"serper": ProviderCap(per_search=2, per_day=100)})
    first = book.search()
    assert first.take("serper")
    assert first.take("serper")
    assert not first.take("serper")
    assert first.left("serper") == 0
    assert book.search().take("serper")  # a new search has its own cap
    assert first.used["serper"] == 2


def test_the_serper_total_cap_is_enforced(tmp_path: Path) -> None:
    path = tmp_path / "usage.json"
    caps = {"serper": ProviderCap(per_search=100, per_day=100, total=2)}
    assert _book(path, caps=caps).search().take("serper")
    assert _book(path, caps=caps, today=lambda: date(2026, 11, 9)).search().take("serper")
    assert not _book(path, caps=caps, today=lambda: date(2026, 11, 10)).search().take("serper")


def test_left_is_the_smallest_of_the_three_caps(tmp_path: Path) -> None:
    book = _book(tmp_path / "usage.json", caps={"serper": ProviderCap(per_search=6, per_day=4, total=3)})
    assert book.search().left("serper") == 3


def test_an_unknown_provider_is_refused_not_counted() -> None:
    search = _book(None).search()
    assert not search.take("youtube")
    assert search.left("youtube") == 0


def test_a_broken_counts_file_starts_fresh(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    path = tmp_path / "usage.json"
    path.write_text("{not json", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        book = _book(path)
        assert book.search().take("serper")
    assert book.today()["serper"] == 1
    assert "usage" in caplog.text
    assert json.loads(path.read_text(encoding="utf-8"))["today"]["serper"] == 1


@pytest.mark.parametrize("content", ['{"format": 1, "day": "2026-11-08", "today": {"serper": "many"}}', "[1, 2]"])
def test_odd_counts_are_ignored(tmp_path: Path, content: str) -> None:
    path = tmp_path / "usage.json"
    path.write_text(content, encoding="utf-8")
    assert _book(path).today()["serper"] == 0


def test_a_failed_save_never_stops_a_search(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    blocker = tmp_path / "a-file"
    blocker.write_text("", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        book = _book(blocker / "usage.json")
        assert book.search().take("serper")
    assert book.today()["serper"] == 1
    assert str(tmp_path) not in caplog.text  # the type of error only


def test_two_threads_cannot_both_take_the_last_unit(tmp_path: Path) -> None:
    """Each waits inside the cap check for the other. Only a missing lock lets both be there, so this can't be luck."""
    together = threading.Barrier(2, timeout=0.2)

    class Waiting(UsageBook):
        def _left(self, provider: str, used_in_search: int) -> int:
            left = super()._left(provider, used_in_search)
            with contextlib.suppress(threading.BrokenBarrierError):
                together.wait()
            return left

    book = Waiting({"serper": ProviderCap(per_search=10, per_day=1)}, path=tmp_path / "usage.json", today=lambda: DAY)
    taken: list[bool] = []

    def worker() -> None:
        taken.append(book.search().take("serper"))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(taken) == [False, True]
    assert book.today()["serper"] == 1


def test_the_counts_file_is_git_ignored() -> None:
    repository = DEFAULT_USAGE_PATH.parents[3]
    done = subprocess.run(
        ["git", "check-ignore", "--quiet", str(DEFAULT_USAGE_PATH)], cwd=repository, capture_output=True, check=False
    )
    assert done.returncode == 0


def test_the_daily_cap_holds_when_the_file_reads_but_will_not_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "usage.json"
    book = _book(path, caps={"serper": ProviderCap(per_search=100, per_day=2)})
    assert book.search().take("serper")

    def refuse(*_: object) -> None:
        raise PermissionError("made up")

    monkeypatch.setattr("app.research.usage.os.replace", refuse)
    search = book.search()
    assert search.take("serper")
    assert not search.take("serper")  # the file still says 1, but this process knows it has sent 2


def test_a_failed_save_leaves_no_temporary_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_: object) -> None:
        raise PermissionError("made up")

    monkeypatch.setattr("app.research.usage.os.replace", refuse)
    assert _book(tmp_path / "usage.json").search().take("serper")
    assert list(tmp_path.iterdir()) == []


def test_another_processs_larger_count_is_believed(tmp_path: Path) -> None:
    path = tmp_path / "usage.json"
    book = _book(path, caps={"serper": ProviderCap(per_search=100, per_day=3)})
    assert book.search().take("serper")
    path.write_text(
        json.dumps({"format": 1, "day": DAY.isoformat(), "today": {"serper": 3}, "total": {"serper": 3}}),
        encoding="utf-8",
    )
    assert not book.search().take("serper")


def test_a_new_day_resets_a_running_process(tmp_path: Path) -> None:
    days = [DAY]
    for path in (tmp_path / "usage.json", None):
        days[0] = DAY
        book = _book(path, today=lambda: days[0])
        assert book.search().take("serper")
        days[0] = date(2026, 11, 9)
        assert book.today()["serper"] == 0
