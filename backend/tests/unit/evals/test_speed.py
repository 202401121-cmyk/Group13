"""US-34's speed run (S5-7): a fake API answers, no network, no model. Every brief and answer is made up."""

from __future__ import annotations

import io
import itertools
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from evals import speed
from evals.interview_sim import criteria_for

BRIEF = speed.Brief("b01", "earbuds", "made-up earbuds for the gym")


class FakeApi:
    """The app's API: intake, criteria, two questions, then a search run that ends after one look."""

    def __init__(self, *, ready: bool = True, finish: bool = True, run_status: str = "done", refuse: int = 0) -> None:
        self.ready, self.finish, self.run_status, self.refuse = ready, finish, run_status, refuse
        self.sent: list[tuple[str, Mapping[str, Any]]] = []
        self.asked = 0
        self.runs = itertools.count(1)
        self.looks: dict[str, int] = {}

    def post(self, path: str, body: Mapping[str, Any]) -> tuple[int, Any]:
        self.sent.append((path, body))
        if path == "/api/presearch/intake":
            return 200, {"session_id": "sess_" + "a" * 24, "ready_for_research": self.ready}
        if path.endswith("/criteria"):
            return 200, criteria_for("earbuds").model_dump(mode="json")
        if path.endswith("/interview/start") or path.endswith("/interview/reply"):
            keys = [*[c.key for c in criteria_for("earbuds").criteria][:2], "no_such_key"]
            if self.asked < len(keys):
                self.asked += 1
                question = {"id": f"q{self.asked}", "criterion_key": keys[self.asked - 1]}
                return 200, {"kind": "question", "question": question}
            return 200, {"kind": "finished", "question": None, "rubric": {"made": "up"} if self.finish else None}
        if path == "/api/search/runs":
            if self.refuse:
                return self.refuse, {}
            return 202, {"search_id": f"srch_{next(self.runs)}"}
        raise AssertionError(path)

    def get(self, path: str) -> tuple[int, Any]:
        seen = self.looks[path] = self.looks.get(path, 0) + 1
        if seen == 1:
            return 200, {"status": "running"}
        cached = path.endswith("_2")
        reports = [{"stage": "research", "duration_ms": 30_000}, {"stage": "extract", "duration_ms": 60_000}]
        return 200, {"status": self.run_status, "result": {"cached": cached, "stage_reports": reports}}


@pytest.fixture(autouse=True)
def _no_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(speed.time, "sleep", lambda s: None)


def test_a_brief_goes_through_the_api_with_a_seeded_shopper_and_a_cached_repeat() -> None:
    api = FakeApi()
    out = speed.run_brief(api, BRIEF)
    assert out["finished"] and len(out["turns"]) == 4  # the start, two answers and one skipped question
    replies = [body for path, body in api.sent if path.endswith("/interview/reply")]
    keys = [c.key for c in criteria_for("earbuds").criteria][:2]
    levels = [
        speed.shopper_level("b01", k, criteria_for("earbuds").criteria[i].default_weight) for i, k in enumerate(keys)
    ]
    assert [r.get("level") for r in replies] == [lv.value for lv in levels] + [None]
    assert replies[-1]["reply_text"] == "Skip" and replies[0]["question_id"] == "q1"
    assert out["search"]["stages"] == {"research": 30.0, "extract": 60.0} and not out["search"]["cached"]
    assert out["repeat"]["cached"] and "stages" not in out["repeat"]
    assert "made-up" not in json.dumps(out)  # no request text in the record


def test_the_same_brief_always_gets_the_same_answers() -> None:
    assert speed.shopper_level("b01", "fit", 6.0) == speed.shopper_level("b01", "fit", 6.0)
    assert len({speed.shopper_level(f"b{n:02}", "fit", 6.0) for n in range(1, 21)}) > 1


@pytest.mark.parametrize(
    ("api", "why"),
    [
        (FakeApi(ready=False), "intake asked a clarification"),
        (FakeApi(finish=False), "the interview did not finish"),
    ],
)
def test_a_brief_that_stops_early_says_why(api: FakeApi, why: str) -> None:
    out = speed.run_brief(api, BRIEF)
    assert not out["finished"] and out["why"] == why


def test_a_failed_intake_or_criteria_call_is_recorded() -> None:
    class Down(FakeApi):
        def __init__(self, fail: str) -> None:
            super().__init__()
            self.fail = fail

        def post(self, path: str, body: Mapping[str, Any]) -> tuple[int, Any]:
            if path.endswith(self.fail):
                return 503, {}
            return super().post(path, body)

    assert speed.run_brief(Down("/intake"), BRIEF)["why"] == "intake failed"
    assert speed.run_brief(Down("/criteria"), BRIEF)["why"] == "criteria failed"


def test_a_refused_or_lost_run_is_recorded_without_stages() -> None:
    refused = speed.run_search(FakeApi(refuse=503), {"made": "up"}, lambda: 0.0)
    assert refused["status"] == "refused (503)" and not refused["cached"]

    class Lost(FakeApi):
        def get(self, path: str) -> tuple[int, Any]:
            return 404, {}

    lost = speed.run_search(Lost(), {"made": "up"}, lambda: 0.0)
    assert lost["status"] == "lost (404)" and lost["stages"] == {}


def test_the_run_stops_for_the_day_before_the_serper_cap_and_resumes(tmp_path: Path) -> None:
    briefs = [speed.Brief(f"b{n:02}", "earbuds", "made up") for n in range(1, 4)]
    record = speed.load_record(tmp_path / "speed.json")
    rooms = iter([30, 12, 11])
    saved: list[int] = []
    why = speed.run(FakeApi(), record, briefs, lambda: next(rooms), lambda r: saved.append(len(r["briefs"])))
    assert why == "stopped for the day before b03: 11 Serper queries left, 12 needed" and saved == [1, 2]

    class Budget:
        def __init__(self) -> None:
            self.shares: list[dict[str, int]] = []

        def share(self, **parts: int) -> Any:
            from contextlib import nullcontext

            self.shares.append(parts)
            return nullcontext()

    budget = Budget()
    assert speed.run(FakeApi(), record, briefs, lambda: None, lambda r: None, budget) == "every brief has run"
    assert [b["brief"] for b in record["briefs"]] == ["b01", "b02", "b03"] and budget.shares == [{"serper": 12}]
    assert speed.serper_room({"serper": 70}, 80) == 10 and speed.serper_room({}, None) is None


def test_the_report_names_the_shares_the_slowest_stage_and_any_search_past_150s() -> None:
    first = {"seconds": 90.0, "status": "done", "cached": False, "stages": {"research": 30.0, "extract": 50.0}}
    second = {"seconds": 151.0, "status": "partial", "cached": False, "stages": {"research": 40.0, "extract": 70.0}}
    record = {
        "briefs": [
            {
                "brief": "b01",
                "finished": True,
                "turns": [1.0, 6.0],
                "search": first,
                "repeat": {"seconds": 0.4, "cached": True},
            },
            {
                "brief": "b02",
                "finished": True,
                "turns": [2.0],
                "search": second,
                "repeat": {"seconds": 4.0, "cached": True},
            },
            {"brief": "b03", "finished": False, "why": "intake asked a clarification", "turns": []},
        ]
    }
    out = speed.summary(record)
    assert out["turns_within_5s"] == round(2 / 3, 4) and out["searches_within_120s"] == 0.5
    assert out["slowest_stage"] == "extract" and out["stage_median_seconds"] == {"extract": 60.0, "research": 35.0}
    assert out["past_150s"] == ["b02"] and out["repeats_within_3s"] == 1 and out["repeats_cached"] == 2
    assert out["not_finished"] == {"b03": "intake asked a clarification"}
    empty = speed.summary({})
    assert empty["turns_within_5s"] is None and empty["slowest_stage"] is None


def test_the_twenty_briefs_are_read_in_file_order(tmp_path: Path) -> None:
    """A made-up file in twenty.yaml's format (the team's own reaches main in its own PR): the first 20, in order."""
    lines = ["written_by: [A Writer]", "briefs:"]
    for n in range(1, 23):
        lines += [
            f"  - id: b{n:02}",
            "    category: laptop",
            "    unseen: false",
            f'    request: "made-up request {n}"',
        ]
    full = tmp_path / "twenty.yaml"
    full.write_text("\n".join(lines) + "\n", encoding="utf-8")
    briefs = speed.read_briefs(full)
    assert [b.id for b in briefs] == [f"b{n:02}" for n in range(1, 21)]
    assert briefs[0] == speed.Brief("b01", "laptop", "made-up request 1")
    assert speed.TWENTY.name == "twenty.yaml" and speed.TWENTY.parent.name == "briefs"
    short = tmp_path / "t.yaml"
    short.write_text("written_by: [A]\nbriefs: []\n", encoding="utf-8")
    with pytest.raises(ValueError, match="needs 20 briefs"):
        speed.read_briefs(short)


def test_the_report_alone_makes_no_call_and_bad_arguments_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys

    path = tmp_path / "speed.json"
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(io.BytesIO(), encoding="cp1252"))
    assert speed.main(["--report"], path) == 0 and sys.stdout.encoding == "utf-8"
    assert json.loads(path.read_text(encoding="utf-8"))["summary"]["briefs_recorded"] == 0
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    assert speed.main(["--report"], path) == 0
    assert speed.main(["--live"]) == 2


def test_main_runs_the_live_app_with_the_database_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The live path, with the app, the guard and the briefs made up: nothing reaches the network or a database."""
    import os

    from fastapi import FastAPI

    import app.main
    from evals import live_briefs

    monkeypatch.setenv("DATABASE_URL", "postgresql://made-up")
    monkeypatch.setattr(app.main, "create_app", lambda: FastAPI())
    monkeypatch.setattr(live_briefs.LiveBudget, "install", lambda self: None)
    monkeypatch.setattr(speed, "read_briefs", lambda: [])
    path = tmp_path / "speed.json"
    assert speed.main([], path) == 0
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["runs"][-1]["stopped"] == "every brief has run" and os.environ["DATABASE_URL"] == ""


def test_the_client_api_returns_status_and_json() -> None:
    class Response:
        def __init__(self, code: int, body: Any) -> None:
            self.status_code, self.body = code, body

        def json(self) -> Any:
            if self.body is None:
                raise ValueError("not json")
            return self.body

    class Client:
        def post(self, path: str, json: Any) -> Response:
            return Response(202, {"posted": json})

        def get(self, path: str) -> Response:
            return Response(500, None)

    api = speed.ClientApi(Client())
    assert api.post("/x", {"a": 1}) == (202, {"posted": {"a": 1}}) and api.get("/x") == (500, {})


def test_an_interview_that_never_finishes_stops_at_its_turn_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    class Endless(FakeApi):
        def post(self, path: str, body: Mapping[str, Any]) -> tuple[int, Any]:
            if "/interview/" in path:
                return 200, {"kind": "question", "question": {"id": "q", "criterion_key": "fit"}}
            return super().post(path, body)

    monkeypatch.setattr(speed, "MAX_TURNS", 3)
    out = speed.run_brief(Endless(), BRIEF)
    assert out["why"] == "the interview did not finish" and len(out["turns"]) == 4


def test_the_speed_run_runs_as_a_script(monkeypatch: pytest.MonkeyPatch) -> None:
    import runpy
    import sys

    monkeypatch.setattr(sys, "argv", ["python -m evals.speed", "--unknown"])
    with pytest.raises(SystemExit) as ended:
        runpy.run_module("evals.speed", run_name="__main__")
    assert ended.value.code == 2


def test_refused_lost_and_cached_first_searches_never_count_toward_the_criteria() -> None:
    record = {
        "briefs": [
            {"brief": "b01", "finished": True, "turns": [], "search": {"seconds": 0.02, "status": "refused (503)"}},
            {
                "brief": "b02",
                "finished": True,
                "turns": [],
                "search": {"seconds": 0.5, "status": "done", "cached": True},
            },
            {
                "brief": "b03",
                "finished": True,
                "turns": [],
                "search": {"seconds": 10.0, "status": "failed", "cached": False},
            },
            {
                "brief": "b04",
                "finished": True,
                "turns": [],
                "search": {"seconds": 80.0, "status": "done", "cached": False},
            },
        ]
    }
    out = speed.summary(record)
    assert out["searches"] == 2 and out["searches_within_120s"] == 0.5  # a failed search never "finishes in time"
    assert out["not_measured"] == {"b01": "not run: refused (503)", "b02": "answered from the cache"}
