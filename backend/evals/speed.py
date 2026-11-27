"""US-34's speed on 20 briefs (S5-7, D-80 item 5): each interview turn's time, each stage's, and a repeat's.

Run from backend/:  python -m evals.speed            (adds to evals/results/speed.json; resumes where it stopped)
                    python -m evals.speed --report   (the summary from what is recorded; no call)

It calls real APIs: run it by hand, never in CI or pytest, after 05:30 IST (00:00 UTC, when the day's caps start
again). Each brief in evals/briefs/twenty.yaml (written by people, By-hand job 3; b01 to b20) goes through the app's
own API, in this process, as the frontend sends it: intake, criteria, the interview, then POST /api/search/runs, read
until it ends. A seeded shopper answers each question with a rough level (S4-3's simulator: the criterion's default
weight plus seeded noise, the nearest level), so a brief always gets the same answers. The same search is then sent
once more, and the time to its cached answer is kept (US-34: 3 s or less).

Before each brief it checks today's Serper count (usage.json, only read) against the day cap in config/research.yaml:
with less than SERPER_PER_BRIEF left it stops for the day, and the next run carries on. So the 20 briefs may take
several days (By-hand job 7 runs the rest). Every call through httpx is counted, and Serper past SERPER_PER_BRIEF a
brief is refused. The database is off for the run (DATABASE_URL empty), so no search or session is saved anywhere.

The record holds times, statuses, stage names and brief ids only: no request, answer, product or source text.
The report: the share of interview turns within 5 s, the share of searches within 120 s, the slowest stage by its
median time, every search past 150 s (there must be none), and each repeat's time with whether it was cached.
"""

from __future__ import annotations

import io
import json
import os
import random
import statistics
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any, Protocol

import yaml  # type: ignore[import-untyped]  # PyYAML ships no type stubs

from app.contracts import RoughLevel

from .interview_sim import FLIP_RATE, NOISE_SD, nearest_level, neighbour_level
from .run_baselines import BACKEND, RESULTS_DIR

TWENTY = BACKEND / "evals" / "briefs" / "twenty.yaml"
SPEED_OUT = RESULTS_DIR / "speed.json"
RUNNER = "python -m evals.speed (live)"
BRIEFS_USED = 20
SERPER_PER_BRIEF = 12  # a brief's most: research's 6 and enrich's 6 (D-78); commerce's shopping look-ups share them
TURN_TARGET_S = 5.0
SEARCH_TARGET_S = 120.0
SEARCH_LIMIT_S = 150.0
REPEAT_TARGET_S = 3.0
POLL_S = 0.5
MAX_TURNS = 40
SHOPPER_SEED = "speed"
LEVEL_WORDS = {RoughLevel.LOW: "Low", RoughLevel.MEDIUM: "Medium", RoughLevel.HIGH: "High"}
LEVEL_WORDS[RoughLevel.GOOD_ENOUGH] = "Good enough"
ENDED = ("done", "partial", "failed", "cancelled")
RAN = ("done", "partial", "failed")  # a search that ran; "within 120 s" counts only "done"


class Api(Protocol):
    """The app's API, as the speed run calls it: a status and a JSON body for each request."""

    def post(self, path: str, body: Mapping[str, Any]) -> tuple[int, Any]: ...

    def get(self, path: str) -> tuple[int, Any]: ...


@dataclass(frozen=True)
class Brief:
    id: str
    category: str
    request: str


def read_briefs(path: Path = TWENTY) -> list[Brief]:
    """b01 to b20, in file order: the first BRIEFS_USED. The rest are for Sprint 6's final study."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    entries = data.get("briefs") if isinstance(data, dict) else None
    if not isinstance(entries, list) or len(entries) < BRIEFS_USED:
        raise ValueError(f"{path.name}: needs {BRIEFS_USED} briefs")
    return [Brief(str(e["id"]), str(e["category"]), str(e["request"])) for e in entries[:BRIEFS_USED]]


def shopper_level(brief_id: str, key: str, default_weight: float) -> RoughLevel:
    """The brief's seeded shopper's answer to one criterion: the same every time for the same brief and key."""
    rng = random.Random(f"{SHOPPER_SEED}:{brief_id}:{key}")
    weight = min(10.0, max(0.0, default_weight + rng.gauss(0.0, NOISE_SD)))
    level = nearest_level(weight)
    return neighbour_level(level, weight) if rng.random() < FLIP_RATE else level


def _timed(call: Callable[[], tuple[int, Any]], clock: Callable[[], float]) -> tuple[int, Any, float]:
    started = clock()
    status, body = call()
    return status, body, round(clock() - started, 3)


def run_brief(api: Api, brief: Brief, clock: Callable[[], float] = time.monotonic) -> dict[str, Any]:
    """One brief through the API. Numbers, statuses and stage names only."""
    record: dict[str, Any] = {"brief": brief.id, "category": brief.category, "turns": []}
    status, intake, seconds = _timed(lambda: api.post("/api/presearch/intake", {"text": brief.request}), clock)
    record["intake_seconds"] = seconds
    if status != 200 or not intake.get("ready_for_research"):
        return {
            **record,
            "finished": False,
            "why": "intake asked a clarification" if status == 200 else "intake failed",
        }
    session = intake["session_id"]
    status, criteria, seconds = _timed(lambda: api.post(f"/api/presearch/{session}/criteria", {}), clock)
    record["criteria_seconds"] = seconds
    if status != 200:
        return {**record, "finished": False, "why": "criteria failed"}
    defaults = {c["key"]: float(c["default_weight"]) for c in criteria["criteria"]}
    status, step, seconds = _timed(lambda: api.post(f"/api/presearch/{session}/interview/start", {}), clock)
    record["turns"].append(seconds)
    for _ in range(MAX_TURNS):
        if status != 200 or step.get("kind") == "finished" or not step.get("question"):
            break
        question = step["question"]
        key = question.get("criterion_key")
        level = shopper_level(brief.id, key, defaults[key]) if key in defaults else None
        body: dict[str, Any] = {"reply_text": "Skip", "question_id": question["id"]}
        if level is not None:
            body |= {"reply_text": LEVEL_WORDS[level], "level": level.value}
        status, step, seconds = _timed(partial(api.post, f"/api/presearch/{session}/interview/reply", body), clock)
        record["turns"].append(seconds)
    if status != 200 or step.get("rubric") is None:
        return {**record, "finished": False, "why": "the interview did not finish"}
    request = {"session_id": session, "raw_query": brief.request, "rubric": step["rubric"]}
    record["search"] = run_search(api, request, clock)
    record["repeat"] = run_search(api, request, clock, stages=False)
    return {**record, "finished": record["search"]["status"] in ("done", "partial")}


def run_search(api: Api, request: Mapping[str, Any], clock: Callable[[], float], stages: bool = True) -> dict[str, Any]:
    """POST /api/search/runs, then the run read until it ends: the time from the post to the end, the status, whether
    it came from the cache and, for a search, each stage's own time."""
    started = clock()
    status, started_run = api.post("/api/search/runs", request)
    if status != 202:
        return {"seconds": round(clock() - started, 3), "status": f"refused ({status})", "cached": False}
    path = f"/api/search/runs/{started_run['search_id']}"
    while True:
        status, state = api.get(path)
        if status != 200 or state.get("status") in ENDED:
            break
        time.sleep(POLL_S)
    seconds = round(clock() - started, 3)
    result = (state or {}).get("result") or {}
    out: dict[str, Any] = {
        "seconds": seconds,
        "status": state.get("status") if status == 200 else f"lost ({status})",
        "cached": bool(result.get("cached")),
    }
    if stages:
        out["stages"] = {r["stage"]: round(r["duration_ms"] / 1000, 3) for r in result.get("stage_reports", [])}
    return out


# --- the record, the day's room, and the report -----------------------------------------------------------------------


def load_record(path: Path = SPEED_OUT) -> dict[str, Any]:
    if path.exists():
        record: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return record
    return {"story": "US-34 (S5-7): speed on the 20 briefs", "runner": RUNNER, "runs": [], "briefs": []}


def write_record(record: Mapping[str, Any], path: Path = SPEED_OUT) -> None:
    copy = path.with_name(f"{path.name}.writing")
    copy.write_bytes((json.dumps(record, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))  # LF only
    os.replace(copy, path)


def serper_room(today: Mapping[str, int], per_day: int | None) -> int | None:
    """Serper queries left today, or None with no day cap."""
    return None if per_day is None else per_day - today.get("serper", 0)


def measured(search: Mapping[str, Any]) -> str | None:
    """Why a search's time can't count toward the criteria (None when it can): it never ran (refused, lost) or it came
    from the cache (an earlier run of the same brief within the limit), so its time and stages are not a search's."""
    if search["status"] not in RAN:
        return f"not run: {search['status']}"
    if search.get("cached"):
        return "answered from the cache"
    return None


def summary(record: Mapping[str, Any]) -> dict[str, Any]:
    """The report's numbers from what is recorded. Only searches that ran count; "within 120 s" counts finished ones."""
    briefs = [b for b in record.get("briefs", []) if b.get("search")]
    ran = [b for b in briefs if measured(b["search"]) is None]
    turns = [t for b in record.get("briefs", []) for t in b.get("turns", [])]
    by_stage: dict[str, list[float]] = {}
    for b in ran:
        for stage, seconds in b["search"].get("stages", {}).items():
            by_stage.setdefault(stage, []).append(seconds)
    medians = {stage: round(statistics.median(times), 3) for stage, times in sorted(by_stage.items())}
    slowest = max(medians, key=lambda stage: medians[stage]) if medians else None
    within = sum(b["search"]["status"] == "done" and b["search"]["seconds"] <= SEARCH_TARGET_S for b in ran)
    repeats = [b["repeat"] for b in briefs if b.get("repeat")]
    return {
        "briefs_recorded": len(record.get("briefs", [])),
        "searches": len(ran),
        "turns": len(turns),
        "turns_within_5s": _share(sum(t <= TURN_TARGET_S for t in turns), len(turns)),
        "searches_within_120s": _share(within, len(ran)),
        "stage_median_seconds": medians,
        "slowest_stage": slowest,
        "past_150s": [b["brief"] for b in ran if b["search"]["seconds"] > SEARCH_LIMIT_S],
        "not_measured": {b["brief"]: measured(b["search"]) for b in briefs if measured(b["search"]) is not None},
        "repeats_cached": sum(r["cached"] for r in repeats),
        "repeats_within_3s": sum(r["cached"] and r["seconds"] <= REPEAT_TARGET_S for r in repeats),
        "repeats": len(repeats),
        "not_finished": {b["brief"]: b.get("why") for b in record.get("briefs", []) if not b.get("finished")},
    }


def _share(count: int, total: int) -> float | None:
    return round(count / total, 4) if total else None


def run(
    api: Api,
    record: dict[str, Any],
    briefs: Sequence[Brief],
    room: Callable[[], int | None],
    save: Callable[[Mapping[str, Any]], None],
    budget: Any = None,
) -> str:
    """Each brief not yet recorded, in order, until today's Serper room is short. Saved after each. Why it stopped."""
    done = {b["brief"] for b in record["briefs"]}
    for brief in briefs:
        if brief.id in done:
            continue
        left = room()
        if left is not None and left < SERPER_PER_BRIEF:
            return f"stopped for the day before {brief.id}: {left} Serper queries left, {SERPER_PER_BRIEF} needed"
        print(f"== {brief.id}")
        if budget is not None:
            with budget.share(serper=SERPER_PER_BRIEF):
                record["briefs"].append(run_brief(api, brief))
        else:
            record["briefs"].append(run_brief(api, brief))
        save(record)
    return "every brief has run"


class ClientApi:
    """The app in this process, through FastAPI's test client: the API exactly as the frontend calls it."""

    def __init__(self, client: Any) -> None:
        self.client = client

    def post(self, path: str, body: Mapping[str, Any]) -> tuple[int, Any]:
        response = self.client.post(path, json=dict(body))
        return response.status_code, _json(response)

    def get(self, path: str) -> tuple[int, Any]:
        response = self.client.get(path)
        return response.status_code, _json(response)


def _json(response: Any) -> Any:
    try:
        return response.json()
    except ValueError:
        return {}


def main(argv: Sequence[str] | None = None, path: Path = SPEED_OUT) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args not in ([], ["--report"]):
        print("usage: python -m evals.speed [--report]", file=sys.stderr)
        return 2
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    record = load_record(path)
    if args == ["--report"]:
        record["summary"] = summary(record)
        write_record(record, path)
        print(json.dumps(record["summary"], indent=2))
        return 0
    why = live(record, path)
    record["summary"] = summary(record)
    write_record(record, path)
    print(f"{why}\n{json.dumps(record['summary'], indent=2)}")
    return 0


def live(record: dict[str, Any], path: Path) -> str:  # the real app and its real calls: by hand only
    from dotenv import load_dotenv
    from fastapi.testclient import TestClient

    os.environ["DATABASE_URL"] = ""  # no search, request or session is saved by a speed run
    load_dotenv()  # the keys stay in the environment; nothing here prints them
    from app.main import create_app
    from app.research.settings import load_research_settings
    from app.research.usage import UsageBook
    from scripts.eval_trust import current_commit

    from .live_briefs import LiveBudget

    caps = load_research_settings().caps
    book = UsageBook(caps)
    budget = LiveBudget({"serper": 10**6, "llm": 10**6, "reddit": 10**6, "jina": 10**6, "firecrawl": 10**6})
    budget.install()
    commit = current_commit()
    with TestClient(create_app()) as client:
        why = run(
            ClientApi(client),
            record,
            read_briefs(),
            lambda: serper_room(book.today(), caps["serper"].per_day),
            lambda r: write_record(r, path),
            budget,
        )
    record["runs"].append({"date": datetime.now(UTC).date().isoformat(), "commit": commit, "stopped": why})
    return why


if __name__ == "__main__":
    raise SystemExit(main())
