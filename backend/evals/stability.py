"""US-19's stability check (S4-7, DECIDED 4): does the rank order hold under random 10% weight changes?

Each of the five made-up decide briefs (tests/fixtures/decide) is decided as it is, then CHANGES times with every
weight's share moved by up to SHIFT either way (uniform, from CHANGE_SEED) and renormalised: the same products, the same
filter, and the brief's own seed for the simulations. Spearman's rho compares each changed order of the shown products
with the original one. US-19's target is a median of 0.9 or better, never lowered; the lowest is reported beside it. A
brief showing fewer than 2 products has no order to change, so it is listed but not counted.

Run from backend/:  python -m evals.stability   (offline; rewrites evals/results/us19-stability.json)
"""

from __future__ import annotations

import json
import statistics
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

from app.contracts import DecideInput, Rubric
from app.decide import decide

from .run_baselines import BACKEND, RESULTS_DIR

BRIEFS = BACKEND / "tests" / "fixtures" / "decide"
STABILITY_OUT = RESULTS_DIR / "us19-stability.json"
RUNNER = "python -m evals.stability (offline)"
CHANGES = 100
SHIFT = 0.10
CHANGE_SEED = 19
TARGET = 0.9  # US-19's target for the median: never lowered
NO_CHANGE_NOTE = (
    "No change reordered any brief's shown products: in these briefs they are far apart, so this shows the order "
    "holds there, not how close products behave. tests/unit/evals/test_stability.py runs the same check on made-up "
    "close products, where it does find changed orders."
)


def spearman(before: Sequence[str], after: Sequence[str]) -> float:
    """Spearman's rho between two orders of the same products: 1 the same order, -1 reversed."""
    if sorted(before) != sorted(after):
        raise ValueError("the two orders hold different products")
    n = len(before)
    if n < 2:
        return 1.0
    position = {pid: i for i, pid in enumerate(after)}
    moved = sum((i - position[pid]) ** 2 for i, pid in enumerate(before))
    return 1 - 6 * moved / (n * (n * n - 1))


def changed(rubric: Rubric, rng: np.random.Generator) -> Rubric:
    """The rubric with each weight's share moved by up to SHIFT either way, then renormalised to add up to 1."""
    moved = {w.key: w.normalized * float(rng.uniform(1 - SHIFT, 1 + SHIFT)) for w in rubric.weights}
    total = sum(moved.values()) or 1.0
    weights = [{**w.model_dump(), "normalized": moved[w.key] / total} for w in rubric.weights]
    return Rubric.model_validate({**rubric.model_dump(), "weights": weights})


def _order(inp: DecideInput) -> list[str]:
    return [r.product_id for r in decide(inp).ranked]


def measure(briefs: Path = BRIEFS) -> dict[str, Any]:
    """The stability record: each brief's rhos over CHANGES weight changes, their median and lowest, and in all."""
    rng = np.random.default_rng(CHANGE_SEED)
    per_brief: dict[str, Any] = {}
    every: list[float] = []
    for path in sorted(briefs.glob("*.json")):
        inp = DecideInput.model_validate(json.loads(path.read_text(encoding="utf-8"))["input"])
        before = _order(inp)
        if len(before) < 2:  # nothing to reorder: its trivial 1.0s would only flatter the median
            per_brief[path.stem] = {"shown": len(before), "counted": False}
            continue
        rhos: list[float] = []
        for _ in range(CHANGES):
            after = _order(DecideInput.model_validate({**inp.model_dump(), "rubric": changed(inp.rubric, rng)}))
            rhos.append(spearman(before, after))
        every += rhos
        per_brief[path.stem] = {
            "shown": len(before),
            "counted": True,
            "median": round(statistics.median(rhos), 4),
            "lowest": round(min(rhos), 4),
        }
    median = round(statistics.median(every), 4) if every else None
    lowest = round(min(every), 4) if every else None
    return {
        "story": "US-19 (S4-7): the rank order under random 10% weight changes",
        "runner": RUNNER,
        "briefs": f"backend/tests/fixtures/decide ({len(per_brief)} made-up briefs)",
        "counted": "briefs with 2 or more shown products; one with fewer has no order to change",
        "changes_per_brief": CHANGES,
        "shift": SHIFT,
        "change_seed": CHANGE_SEED,
        "decide_seed": "each brief's own DecideInput.seed",
        "target_median": TARGET,
        "median": median,
        "lowest": lowest,
        "meets_target": median is not None and median >= TARGET,
        "note": NO_CHANGE_NOTE if lowest == 1.0 else None,
        "per_brief": per_brief,
    }


def main(argv: Sequence[str] = ()) -> int:
    if argv:
        print("usage: python -m evals.stability", file=sys.stderr)
        return 2
    record = measure()
    STABILITY_OUT.write_bytes((json.dumps(record, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))  # LF only
    print(f"median {record['median']}, lowest {record['lowest']} (target {TARGET}): {STABILITY_OUT.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
