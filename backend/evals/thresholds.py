"""The floors in evals/thresholds.yaml (S3-18, task #45), which US-49's quality gate will enforce in Sprint 4.

Run from backend/:
    python -m evals.thresholds --check    validate the file, and check every measured value against its floor
    python -m evals.thresholds --write    rewrite the file from evals/results/ (a team decision: say so in the PR)

Every measured value has exactly one floor. How each floor is set:
- safety: exact. Zero rule breaks among shown products, zero unsourced shown products, and popularity never wins.
- quality: the measured value less a stated margin.
  - 0.05 for a rate (US-49 fails a drop of more than 10 points, so a floor never sits outside that).
  - 10% for a count, rounded down.
  - One conflict for the trust counts (out of 5).
  - None for the briefs' hand-written expectations, which are right or wrong.
  - A time is held to a ceiling at its stage's own time limit, never to a measured time, which varies.
- A target from the plan sits next to its floor, and is never lowered: precision of 0.95 or more for the matcher;
  trust right more often than majority vote.
"""

from __future__ import annotations

import argparse
import io
import json
import math
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal

import yaml  # type: ignore[import-untyped]  # PyYAML ships no type stubs
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.contracts import SEARCH_TIME_LIMIT_S, STAGE_TIME_LIMITS_S, StageName

from .run_baselines import BACKEND, OFFLINE_OUT, RESULTS_DIR

THRESHOLDS = BACKEND / "evals" / "thresholds.yaml"
LIVE_OUT = RESULTS_DIR / "sprint-3-live.json"
RATE_MARGIN = 0.05
COUNT_MARGIN = 0.10

Kind = Literal["safety", "quality"]
Direction = Literal["at_least", "at_most"]

SAFETY: dict[str, str] = {
    "decide.hard_rule_violations_shown": "A shown product never breaks an active hard rule, the budget or an exclusion "
    "(US-17, build rule 2): decide holds it back.",
    "decide.most_mentioned_worst_fit_wins": "Mentions never buy a rank: the most-mentioned product never wins while it "
    "fits worst (US-17, build rule 2).",
    "live.hard_rule_violations_shown": "A shown product never breaks an active hard rule, the budget or an exclusion "
    "(US-17, build rule 2), in a real search.",
    "live.most_mentioned_worst_fit_wins": "Mentions never buy a rank, in a real search (US-17, build rule 2).",
    "live.unsourced_shown": "No product is shown without a source (build rule 5): verify drops it before decide.",
}
AT_MOST = {"matching.stage.different_merged"}  # a rate where lower is better
EXACT = {"decide.briefs_as_expected"}  # hand-written expectations: right or wrong, so no margin
PLAN_TARGETS: dict[str, tuple[float, str]] = {
    "matching.model.own_pairs.precision": (
        0.95,
        "ml/MODELS.md card 1 and project_docs/07_DATA_AND_ML.md: precision of 0.95 or more",
    ),
    "trust.learned_minus_majority": (
        1,
        "ml/MODELS.md card 2: more seeded conflicts resolved correctly than majority vote",
    ),
}


class Target(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    value: float
    source: str = Field(min_length=3)
    note: str = ""


class Floor(BaseModel):
    """One measure's floor (or, for direction at_most, its ceiling)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Kind
    direction: Direction
    floor: float
    measured: float
    unit: str = Field(min_length=1)
    margin: str = Field(min_length=3)
    measured_on: Literal["offline", "live"]
    source: str = Field(min_length=3)
    date: date
    commit: str = Field(min_length=7)
    rule: str = Field(min_length=10)
    target: Target | None = None

    @model_validator(mode="after")
    def _sides(self) -> Floor:
        if self.kind == "safety":
            if self.direction != "at_most" or self.floor != 0:
                raise ValueError("a safety floor is exact: at most 0")
        elif self.direction == "at_least" and self.floor > self.measured:
            raise ValueError(f"a quality floor ({self.floor}) can't be above what was measured ({self.measured})")
        elif self.direction == "at_most" and self.floor < self.measured:
            raise ValueError(f"a quality ceiling ({self.floor}) can't be below what was measured ({self.measured})")
        return self


class Thresholds(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal[1]
    measures: dict[str, Floor] = Field(min_length=1)

    @model_validator(mode="after")
    def _targets(self) -> Thresholds:
        for measure_id, floor in self.measures.items():
            plan = PLAN_TARGETS.get(measure_id)
            if plan is None:
                if floor.target is not None:
                    raise ValueError(f"{measure_id}: only the plan sets a target")
                continue
            if floor.target is None or floor.target.value != plan[0] or floor.target.source != plan[1]:
                raise ValueError(f"{measure_id}: the plan's target is {plan[0]} ({plan[1]}), never lowered or dropped")
        for measure_id, floor in self.measures.items():
            if (measure_id in SAFETY) != (floor.kind == "safety"):
                raise ValueError(f"{measure_id}: only {sorted(SAFETY)} are safety measures, and each of them is one")
        return self


def load(path: Path) -> Thresholds:
    return Thresholds.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


# --- from measures to floors ------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Rule:
    kind: Kind
    direction: Direction
    margin: str
    rule: str
    limit: float | None = None


def _stage_limit(measure_id: str) -> tuple[float, str] | None:
    if measure_id == "live.search_seconds_max":
        return SEARCH_TIME_LIMIT_S, f"the search's own limit, {SEARCH_TIME_LIMIT_S:g} s"
    prefix = "live.stage_seconds_max."
    if not measure_id.startswith(prefix):
        return None
    stage = StageName(measure_id.removeprefix(prefix))
    if stage is StageName.EXTRACT:  # sources are read in parallel, each under 15 s: the phase has the search's limit
        return SEARCH_TIME_LIMIT_S, f"the search's limit, {SEARCH_TIME_LIMIT_S:g} s (each source has 15 s)"
    return STAGE_TIME_LIMITS_S[stage], f"the stage's own limit, {STAGE_TIME_LIMITS_S[stage]:g} s"


# Why a quality measure has a floor at all: the reason behind it, by family (the longest matching prefix wins).
WHY: dict[str, str] = {
    "matching.fuzzy.": "The fuzzy baseline (ml/MODELS.md card 8) is the matcher's fallback: it must not get worse.",
    "matching.model.": "The trained matcher (ml/MODELS.md card 1) must not get worse than Sprint 3's.",
    "matching.stage.same_merged": "The match stage (rules and fuzzy) must keep merging the same products it merges "
    "now (US-15).",
    "matching.stage.variant_merged": "The match stage must keep folding variants into one product (DR-02, US-15).",
    "matching.stage.different_merged": "The match stage must not merge more different products than now: uncertain "
    "pairs stay separate (US-15).",
    "trust.": "Learned trust (US-16) must keep resolving the seeded conflicts it resolves now, and beat majority vote.",
    "decide.briefs_as_expected": "Every made-up brief gives its hand-written groups (US-17, US-18).",
    "decide.coverage_": "Ranked products keep the evidence coverage they have now (US-18).",
    "research.recorded.": "Reading the recorded web searches finds the sources it finds now (US-12).",
    "live.briefs_finished": "Every brief finishes, whatever fails on the way (build rules 9 and 10).",
    "live.ranked_products": "Real searches keep showing products.",
    "live.briefs_with_a_score": "Shown products get scores, not 'not ranked' everywhere (S3-10b's demo risk).",
    "live.coverage_mean": "Ranked products keep the evidence coverage they have now (US-18).",
    "live.sources_per_brief": "Research keeps finding sources (US-12, US-38).",
    "live.pages_": "The page readers keep reading pages (US-38).",
    "live.claims_per_source": "Extraction keeps finding claims in what it reads (US-14).",
    "live.quote_found_share": "A claim's quote is its source's own words (build rule 4, US-14).",
}


def _why(measure_id: str, detail: str) -> str:
    keys = [key for key in WHY if measure_id.startswith(key)]
    return WHY[max(keys, key=len)] if keys else detail


def rule_for(measure: Mapping[str, Any]) -> Rule:
    measure_id, unit = measure["id"], measure["unit"]
    if measure_id in SAFETY:
        return Rule("safety", "at_most", "exact: none at all", SAFETY[measure_id])
    limit = _stage_limit(measure_id)
    if limit is not None:
        return Rule("quality", "at_most", f"a ceiling at {limit[1]}", "No stage may hang (build rule 9).", limit[0])
    why = _why(measure_id, measure["detail"])
    if measure_id in EXACT:
        return Rule("quality", "at_least", "none: hand-written expectations are right or wrong", why)
    direction: Direction = "at_most" if measure_id in AT_MOST else "at_least"
    if unit == "rate":
        side = "above" if direction == "at_most" else "below"
        return Rule("quality", direction, f"{RATE_MARGIN} {side} the measured rate", why)
    if unit == "conflicts" and measure_id.endswith("_minus_majority"):
        return Rule("quality", direction, "one conflict below the measured difference (5 conflicts)", why)
    if unit == "conflicts":
        return Rule("quality", direction, "one conflict below the measured count (5 conflicts)", why)
    return Rule("quality", direction, "10% below the measured value, rounded down", why)


def floor_for(rule: Rule, measured: float) -> float:
    if rule.kind == "safety":
        return 0.0
    if rule.limit is not None:
        return float(rule.limit)
    if rule.margin.startswith("none"):
        return measured
    if rule.margin.startswith(str(RATE_MARGIN)):
        bound = measured + RATE_MARGIN if rule.direction == "at_most" else measured - RATE_MARGIN
        return round(min(1.0, max(0.0, bound)), 4)
    if rule.margin.startswith("one conflict below the measured difference"):
        return measured - 1  # a difference can be below zero: never clamped
    if rule.margin.startswith("one conflict"):
        return max(0.0, measured - 1)
    lowered = measured * (1 - COUNT_MARGIN)
    if float(measured).is_integer():
        return float(math.floor(round(lowered, 9)))
    return math.floor(round(lowered * 100, 6)) / 100  # rounded first, so float error never costs a hundredth


def build(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """thresholds.yaml's content from the results records, each with its date and commit."""
    measures: dict[str, Any] = {}
    for record in records:
        on = "live" if "live" in record["runner"] else "offline"
        for m in record["measures"]:
            if m["status"] != "measured":
                continue
            rule = rule_for(m)
            entry: dict[str, Any] = {
                "kind": rule.kind,
                "direction": rule.direction,
                "floor": floor_for(rule, m["value"]),
                "measured": m["value"],
                "unit": m["unit"],
                "margin": rule.margin,
                "measured_on": on,
                "source": f"{record['file']}; {m['source']}",
                "date": record["generated"],
                "commit": record["commit"],
                "rule": rule.rule,
            }
            if m["id"] in PLAN_TARGETS:
                value, source = PLAN_TARGETS[m["id"]]
                met = m["value"] >= value
                note = "met" if met else "not met"
                if m["id"] == "matching.model.own_pairs.precision":
                    note = f"{note} as a number, but not shown: {m['detail']}"
                entry["target"] = {"value": value, "source": source, "note": note}
            measures[m["id"]] = entry
    return {"version": 1, "measures": dict(sorted(measures.items()))}


def all_measures() -> list[dict[str, Any]]:
    """Every measure in the committed results records, offline and live."""
    out: list[dict[str, Any]] = []
    for path in (OFFLINE_OUT, LIVE_OUT):
        if path.exists():
            out += json.loads(path.read_text(encoding="utf-8"))["measures"]
    return out


def check(thresholds: Thresholds, measures: Iterable[Mapping[str, Any]]) -> list[str]:
    """What the gate would refuse: a measure missing from the results, or past its floor. Empty when all pass."""
    found = {m["id"]: m for m in measures}
    problems = [
        f"{measure_id} is measured but has no floor in thresholds.yaml"
        for measure_id, m in sorted(found.items())
        if m["status"] == "measured" and measure_id not in thresholds.measures
    ]
    for measure_id, floor in thresholds.measures.items():
        m = found.get(measure_id)
        if m is None or m["status"] != "measured" or m["value"] is None:
            problems.append(f"{measure_id} is missing from the results")
        elif floor.direction == "at_least" and m["value"] < floor.floor:
            problems.append(f"{measure_id} is below its floor: {m['value']} < {floor.floor}")
        elif floor.direction == "at_most" and m["value"] > floor.floor:
            problems.append(f"{measure_id} is above its ceiling: {m['value']} > {floor.floor}")
    return problems


HEADER = """\
# Sprint 3 baselines: one floor per measure (S3-18, task #45). US-49's quality gate (Sprint 4) enforces them.
# Written by `python -m evals.thresholds --write` from evals/results/; check with `python -m evals.thresholds --check`.
# The rules (evals/thresholds.py): a safety floor is exact; a quality floor is the measured value less the stated
# margin; a time has a ceiling at its stage's own limit; a target from the plan is written next to its floor and is
# never lowered. Changing a floor is a team decision: say so in the pull request. The report is
# docs/evaluation/sprint-3-baselines.md.
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.thresholds", description="The floors in thresholds.yaml.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="validate the file and check the results against it")
    group.add_argument("--write", action="store_true", help="rewrite the file from evals/results/")
    args = parser.parse_args(argv)
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if args.write:
        records = []
        for path in (OFFLINE_OUT, LIVE_OUT):
            if path.exists():
                record = json.loads(path.read_text(encoding="utf-8"))
                records.append({**record, "file": path.relative_to(BACKEND.parent).as_posix()})
        content = build(records)
        Thresholds.model_validate(content)  # never write a file the schema refuses
        body = yaml.safe_dump(content, sort_keys=False, allow_unicode=True, width=120)
        THRESHOLDS.write_bytes((HEADER + body).encode("utf-8"))
        print(f"Written: {len(content['measures'])} floors in {THRESHOLDS.relative_to(BACKEND.parent).as_posix()}")
        return 0
    try:
        loaded = load(THRESHOLDS)
    except (OSError, ValidationError) as exc:
        print(f"thresholds.yaml is not valid: {exc}")
        return 1
    problems = check(loaded, all_measures())
    for problem in problems:
        print(problem)
    print(f"{len(loaded.measures)} floors, {len(problems)} problems")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
