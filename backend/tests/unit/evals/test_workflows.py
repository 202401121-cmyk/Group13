"""US-49's CI (S4-13): the fast gate on every pull request, and the full evaluation every night."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

WORKFLOWS = Path(__file__).resolve().parents[4] / ".github" / "workflows"


def _load(name: str) -> dict[Any, Any]:
    data: dict[Any, Any] = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    return data


def _runs(job: dict[str, Any]) -> str:
    return "\n".join(step.get("run", "") for step in job["steps"])


def test_ci_runs_the_quality_gate_against_main_and_every_floor() -> None:
    ci = _load("ci.yml")
    job = ci["jobs"]["quality-gate"]
    runs = _runs(job)
    assert "git show origin/main:backend/evals/results/us49-gate.json" in runs
    assert 'python -m evals.gate --baseline "$RUNNER_TEMP/main-gate.json"' in runs
    assert "python -m evals.thresholds --check" in runs
    gate_step = next(step for step in job["steps"] if "python -m evals.gate" in step.get("run", ""))
    assert gate_step["timeout-minutes"] == 3  # US-49: the fast gate finishes in under 3 minutes
    assert "took" in gate_step["run"]  # it reports its own time


def test_ci_still_runs_on_every_pull_request_with_read_only_rights() -> None:
    ci = _load("ci.yml")
    assert "pull_request" in ci[True]  # YAML reads the key `on` as True
    assert ci["permissions"] == {"contents": "read"}


def test_the_nightly_runs_the_full_evaluation_offline_and_keeps_the_records() -> None:
    nightly = _load("nightly.yml")
    assert nightly[True]["schedule"] and "workflow_dispatch" in nightly[True]
    assert nightly["permissions"] == {"contents": "read"}
    job = nightly["jobs"]["evaluate"]
    runs = _runs(job)
    for command in (
        "python -m evals.run_baselines",
        "python -m evals.interview_sim",
        "python -m evals.stability",
        "python -m evals.gate",
        "python -m evals.thresholds --check",
    ):
        assert command in runs
    upload = next(step for step in job["steps"] if str(step.get("uses", "")).startswith("actions/upload-artifact"))
    assert upload["with"]["path"] == "backend/evals/results/"
    assert upload["if"] == "always()"
    assert "live" not in runs and "smoke" not in runs  # no live API, ever
