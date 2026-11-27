"""S5-21 (US-34, D-94): the live briefs with a timed interview and research begun at its first answer. Offline: the
research stub's example sources and fake models; the clock is the test's own."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.contracts import InterviewState
from app.contracts import examples as ex
from app.early_search import EarlySearches
from evals import live_briefs
from tests.fakes.early import WAIT_S


def state() -> InterviewState:
    return InterviewState(session_id=ex.SESSION_ID, intent=ex.intent(), criteria=ex.criteria())


def test_the_early_and_compare_options_are_read(tmp_path: Path) -> None:
    opts = live_briefs.options(["--categories", "laptop", "--early", "--compare", str(tmp_path / "base.json")])
    assert (opts.early, opts.compare, opts.cold) == (True, tmp_path / "base.json", False)
    plain = live_briefs.options([])
    assert (plain.early, plain.compare) == (False, None)


def test_the_timed_interview_waits_after_each_of_the_first_answers_and_begins_the_job_at_the_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    begun: list[int] = []
    pauses: list[float] = []
    step, answers = live_briefs.timed_interview(state(), lambda: begun.append(len(pauses)), 10.0, pauses.append)
    assert step.kind == "finished" and step.rubric is not None and answers > 0
    assert begun == [0]  # once, right after the first answer
    assert pauses == [10.0] * live_briefs.EARLY_ANSWERS  # the confirm comes 8 waits after the first answer
    # Past EARLY_ANSWERS the shopper answers at once: no wait.
    monkeypatch.setattr(live_briefs, "EARLY_ANSWERS", 2)
    fewer: list[float] = []
    _, again = live_briefs.timed_interview(state(), lambda: None, 10.0, fewer.append)
    assert fewer == [10.0, 10.0] and again == answers > 2


def test_a_short_interview_waits_out_the_rest_so_every_brief_gives_the_job_the_same_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(live_briefs, "EARLY_ANSWERS", 40)
    pauses: list[float] = []
    _, answers = live_briefs.timed_interview(state(), lambda: None, 10.0, pauses.append)
    assert answers < 40 and pauses == [10.0] * 40


def test_the_timed_interview_stops_at_the_clis_turn_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.cli.MAX_TURNS", 1)
    pauses: list[float] = []
    step, answers = live_briefs.timed_interview(state(), lambda: None, 10.0, pauses.append)
    assert step.kind != "finished" and answers == 1 and pauses == [10.0] * live_briefs.EARLY_ANSWERS


def test_an_interview_with_no_question_waits_for_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.cli.MAX_TURNS", 0)
    pauses: list[float] = []
    begun: list[bool] = []
    _, answers = live_briefs.timed_interview(state(), lambda: begun.append(True), 10.0, pauses.append)
    assert (answers, pauses, begun) == (0, [], [])


def _quoting_extract(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.extract import GuardedExtractStage
    from app.extract.settings import ExtractSettings, load_extract_settings
    from tests.unit.evals.test_live_funnel import _QuotingClient

    settings = load_extract_settings(env={})
    small = ExtractSettings.model_validate({**settings.model_dump(), "max_sources_per_search": 4})
    monkeypatch.setattr("app.pipeline.default_extract_stage", lambda: GuardedExtractStage(_QuotingClient(), small))


def test_an_early_brief_takes_the_job_over_and_records_the_time_from_confirm_to_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _quoting_extract(monkeypatch)
    jobs: list[Any] = []
    real_begin = live_briefs._begin

    def begin(registry: Any, session_id: str, *rest: Any) -> None:
        real_begin(registry, session_id, *rest)
        jobs.append(registry._jobs[session_id])

    def pause(seconds: float) -> None:
        if jobs:  # the test's clock: each wait lasts until the job has ended, so the confirm always finds it ready
            assert jobs[0].done.wait(WAIT_S)

    monkeypatch.setattr(live_briefs, "_begin", begin)
    brief = live_briefs.run_brief("laptop", live_briefs.QUERIES["laptop"], early=True, gap_s=10.0, pause=pause)
    assert brief["finished"] is True and brief["early_taken_over"] is True
    assert brief["answers"] > 0 and isinstance(brief["asked_again"], int)
    assert isinstance(brief["confirm_to_result_seconds"], float)
    assert brief["steps"]["read"] > 0 and brief["steps"]["extracted"] > 0  # the job's reading, counted
    assert brief["stages"]["research"]["status"] == "ok"
    assert live_briefs.text_in(brief) == []


def test_a_brief_without_early_research_records_its_confirm_time_too(monkeypatch: pytest.MonkeyPatch) -> None:
    _quoting_extract(monkeypatch)
    brief = live_briefs.run_brief("laptop", live_briefs.QUERIES["laptop"])
    assert brief["early_taken_over"] is False and brief["answers"] == 0 and brief["asked_again"] == 0
    assert brief["confirm_to_result_seconds"] >= brief["search_seconds"] - 0.5
    assert live_briefs.text_in(brief) == []


def test_an_early_brief_that_needs_a_clarification_starts_no_job() -> None:
    brief = live_briefs.run_brief("laptop", "something nice", early=True, pause=lambda s: None)
    assert brief == {
        "category": "laptop",
        "finished": False,
        "why": live_briefs.NOT_FINISHED[0],
        "presearch_seconds": brief["presearch_seconds"],
    }


def test_an_interview_that_never_finishes_is_recorded_and_its_job_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    _quoting_extract(monkeypatch)
    monkeypatch.setattr("app.cli.MAX_TURNS", 1)
    closed: list[bool] = []
    original = EarlySearches.close

    def close(self: EarlySearches) -> None:
        closed.append(True)
        original(self)

    monkeypatch.setattr(EarlySearches, "close", close)
    brief = live_briefs.run_brief("laptop", live_briefs.QUERIES["laptop"], early=True, pause=lambda s: None)
    assert (brief["finished"], brief["why"]) == (False, live_briefs.NOT_FINISHED[1])
    assert closed == [True]  # the job, if any, stops with its brief


class _Counted:
    """An extract stage's counts per search, as the live stage keeps them."""

    name = "extract"

    def __init__(self, counts: dict[str, dict[str, int] | None]) -> None:
        self._counts = counts
        self.notes: list[str] = []

    def counts_for(self, search_id: str) -> dict[str, int] | None:
        return self._counts.get(search_id)

    def coverage_note(self, search_id: str) -> str | None:
        self.notes.append(search_id)
        return None


def test_pages_read_are_the_first_searchs_and_pages_asked_again_are_counted_apart() -> None:
    counts = {"srch_early": {"sent": 5, "not_reached": 1}, "srch_final": {"sent": 2}, "srch_more": {"sent": 1}}
    inner = _Counted({**counts, "srch_none": None})
    keep = live_briefs._KeepExtract(inner)
    keep.coverage_note("srch_none")  # no counts for that search: nothing kept
    keep.coverage_note("srch_early")
    keep.coverage_note("srch_final")
    keep.coverage_note("srch_more")
    assert keep.counts == {"sent": 5, "not_reached": 1}  # a page asked again is not read again
    assert keep.counts_again == {"sent": 3}
    assert inner.notes == ["srch_none", "srch_early", "srch_final", "srch_more"]
    bare = live_briefs._KeepExtract(type("Bare", (), {"name": "extract"})())
    assert bare.coverage_note("srch_early") is None and bare.counts is None


def _brief(category: str, steps: dict[str, int], **more: Any) -> dict[str, Any]:
    return {"category": category, "finished": True, "steps": steps, **more}


STEPS = {"read": 29, "extracted": 8, "with_a_price": 28, "ranked": 0}


def test_the_comparison_reads_each_brief_both_records_finished() -> None:
    base = {
        "briefs": [
            _brief("laptop", STEPS, search_seconds=56.01),
            _brief("smartphone", STEPS, search_seconds=57.5),
            {"category": "earbuds", "finished": False},
        ]
    }
    early = {
        "briefs": [
            _brief("laptop", {**STEPS, "read": 40, "ranked": 3}, search_seconds=50.0, confirm_to_result_seconds=51.2),
            _brief("earbuds", STEPS, confirm_to_result_seconds=40.0),
        ]
    }
    assert live_briefs.compared(base, early) == [
        {
            "category": "laptop",
            "base": {
                "pages_read": 29,
                "sources_with_claims": 8,
                "priced_products": 28,
                "ranked_products": 0,
                "confirm_to_result_seconds": 56.01,
            },
            "early": {
                "pages_read": 40,
                "sources_with_claims": 8,
                "priced_products": 28,
                "ranked_products": 3,
                "confirm_to_result_seconds": 51.2,
            },
        }
    ]


def test_compare_prints_the_table_and_calls_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    base, early = tmp_path / "base.json", tmp_path / "early.json"
    base.write_text(json.dumps({"briefs": [_brief("laptop", STEPS, search_seconds=56.0)]}), encoding="utf-8")
    early.write_text(json.dumps({"briefs": [_brief("laptop", STEPS, confirm_to_result_seconds=50.0)]}), "utf-8")
    assert live_briefs.main(["--compare", str(base), "--out", str(early)]) == 0
    [row] = json.loads(capsys.readouterr().out)
    assert row["category"] == "laptop" and row["early"]["confirm_to_result_seconds"] == 50.0


def test_a_live_early_run_passes_early_to_each_brief_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[bool] = []

    def brief(category: str, query: str, early: bool = False) -> dict[str, Any]:
        seen.append(early)
        return {"category": category, "finished": False, "why": live_briefs.NOT_FINISHED[1]}

    monkeypatch.setattr(live_briefs, "run_brief", brief)
    monkeypatch.setattr(live_briefs.LiveBudget, "install", lambda self: None)
    monkeypatch.setattr("scripts.eval_trust.current_commit", lambda: "abc1234")
    monkeypatch.setenv("EXTRACT_MAX_SOURCES", "8")  # main sets both; monkeypatch puts them back
    monkeypatch.setenv("MATCHER", "fuzzy")
    out = tmp_path / "early.json"
    assert live_briefs.main(["--categories", "laptop", "--early", "--out", str(out)]) == 0
    assert seen == [True]
    record = json.loads(out.read_text(encoding="utf-8"))
    assert record["briefs"][0]["early"] is True and record["briefs"][0]["cache"] == "as_found"


class _Locked:
    """A folder Windows holds for `times` tries."""

    def __init__(self, times: int) -> None:
        self.times = times
        self.tries = 0

    def rename(self, target: Path) -> None:
        self.tries += 1
        if self.tries <= self.times:
            raise PermissionError("[WinError 5] Access is denied")


def test_a_folder_held_for_a_moment_is_moved_on_a_later_try(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(live_briefs, "RENAME_PAUSE_S", 0.0)
    held = _Locked(times=live_briefs.RENAME_TRIES - 1)
    live_briefs._rename(held, Path("anywhere"))  # type: ignore[arg-type]
    assert held.tries == live_briefs.RENAME_TRIES
    stuck = _Locked(times=live_briefs.RENAME_TRIES)
    with pytest.raises(PermissionError):
        live_briefs._rename(stuck, Path("anywhere"))  # type: ignore[arg-type]
    assert stuck.tries == live_briefs.RENAME_TRIES


def test_a_brief_whose_cache_cannot_be_set_aside_fails_alone(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    opts = live_briefs.options(["--categories", "laptop", "earbuds", "--cold"])
    tries: list[int] = []

    def set_aside() -> int:
        tries.append(1)
        if len(tries) == 1:
            raise PermissionError("[WinError 5] Access is denied")
        return 5

    restores: list[int] = []

    def restore() -> int:
        restores.append(1)
        if len(restores) == 2:
            raise PermissionError("[WinError 5] Access is denied")
        return 0

    monkeypatch.setattr(live_briefs, "set_aside_research_cache", set_aside)
    monkeypatch.setattr(live_briefs, "restore_research_cache", restore)
    monkeypatch.setattr("scripts.eval_trust.current_commit", lambda: "abc1234")
    ran: list[str] = []

    def brief(category: str, query: str) -> dict[str, Any]:
        ran.append(category)
        return {"category": category, "finished": False, "why": live_briefs.NOT_FINISHED[1]}

    record = live_briefs.run_all(opts, live_briefs.LiveBudget(opts.budget), brief)
    first, second = record["briefs"]
    assert (first["finished"], first["why"]) == (False, live_briefs.COLD_REFUSED)
    assert second["why"] == live_briefs.NOT_FINISHED[1] and ran == ["earbuds"]  # the next brief still ran
    assert len(restores) == 3  # the run's own first, then after each brief, one of which failed and was said
    assert "the next restore tries again" in capsys.readouterr().out
    assert live_briefs.text_in(record["briefs"]) == []
