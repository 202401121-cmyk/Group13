"""US-19's stability check (S4-7, DECIDED 4): does the rank order hold under random 10% weight changes?

Each of the five made-up decide briefs (tests/fixtures/decide) is decided as it is, then CHANGES times with every
weight's share moved by up to SHIFT either way (uniform, from CHANGE_SEED) and renormalised: the same products, the same
filter, and the brief's own seed for the simulations. Spearman's rho compares each changed order of the shown products
with the original one. US-19's target is a median of 0.9 or better, never lowered; the lowest is reported beside it. A
brief showing fewer than 2 products has no order to change, so it is listed but not counted.

The five briefs keep their shown products far apart, so they can't fail. Beside them (S4-28, D-74), a made-up brief
with close products (tests/fixtures/decide/stability/close_earbuds.json, in its own folder so the five-brief baselines
never read it) is checked the same way, from the same seed. Its median is a measure with a floor in
evals/thresholds.yaml (the measured value less 0.05); the 0.9 target stays beside it, met or not met, never tuned for.

Run from backend/:  python -m evals.stability   (offline; rewrites evals/results/us19-stability.json)
"""

from __future__ import annotations

import json
import statistics
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from app.contracts import DecideInput, Rubric
from app.decide import decide

from .run_baselines import BACKEND, RESULTS_DIR

BRIEFS = BACKEND / "tests" / "fixtures" / "decide"
CLOSE_BRIEF = BRIEFS / "stability" / "close_earbuds.json"  # D-74: close products, so the check can fail
CLOSE_MEASURE = "decide.stability.close_median"
STABILITY_OUT = RESULTS_DIR / "us19-stability.json"
RUNNER = "python -m evals.stability (offline)"
CHANGES = 100
SHIFT = 0.10
CHANGE_SEED = 19
TARGET = 0.9  # US-19's target for the median: never lowered
NO_CHANGE_NOTE = (
    "No change reordered any of the five briefs' shown products: in them they are far apart, so this shows the order "
    "holds there, not how close products behave. The close brief beside them (`close`, D-74) is the check that can "
    "fail: there, changes do reorder the shown products."
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


def _load(path: Path) -> DecideInput:
    return DecideInput.model_validate(json.loads(path.read_text(encoding="utf-8"))["input"])


def _changes(inp: DecideInput, rng: np.random.Generator) -> tuple[list[str], list[list[str]]]:
    """The order as it is, and the order after each of CHANGES weight changes."""
    before = _order(inp)
    afters = [
        _order(DecideInput.model_validate({**inp.model_dump(), "rubric": changed(inp.rubric, rng)}))
        for _ in range(CHANGES)
    ]
    return before, afters


def close_brief(path: Path = CLOSE_BRIEF) -> dict[str, Any]:
    """The close brief's rhos over CHANGES weight changes from its own generator, seeded as the five briefs' are."""
    before, afters = _changes(_load(path), np.random.default_rng(CHANGE_SEED))
    rhos = [spearman(before, after) for after in afters]
    median, lowest = round(statistics.median(rhos), 4), round(min(rhos), 4)
    meets = median >= TARGET
    return {
        "brief": path.relative_to(BACKEND.parent).as_posix(),
        "shown": len(before),
        "median": median,
        "lowest": lowest,
        "target_median": TARGET,
        "meets_target": meets,
        "top3_reordered": sum(1 for after in afters if after[:3] != before[:3]),
        "note": None
        if meets
        else f"The median Spearman, {median}, is below US-19's target of 0.9: on close products a 10% change of the "
        "weights reorders the shown products often enough to miss it. Reported as it is, never tuned to pass.",
    }


def measure(briefs: Path = BRIEFS) -> dict[str, Any]:
    """The stability record: each brief's rhos over CHANGES weight changes, their median and lowest, and in all, and
    beside them the close brief's (D-74) with its measure for evals/thresholds.yaml."""
    rng = np.random.default_rng(CHANGE_SEED)
    per_brief: dict[str, Any] = {}
    every: list[float] = []
    for path in sorted(briefs.glob("*.json")):
        inp = _load(path)
        before = _order(inp)
        if len(before) < 2:  # nothing to reorder: its trivial 1.0s would only flatter the median
            per_brief[path.stem] = {"shown": len(before), "counted": False}
            continue
        _, afters = _changes(inp, rng)
        rhos = [spearman(before, after) for after in afters]
        every += rhos
        per_brief[path.stem] = {
            "shown": len(before),
            "counted": True,
            "median": round(statistics.median(rhos), 4),
            "lowest": round(min(rhos), 4),
        }
    median = round(statistics.median(every), 4) if every else None
    lowest = round(min(every), 4) if every else None
    close = close_brief()
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
        "close": close,
        "measures": [
            {
                "id": CLOSE_MEASURE,
                "value": close["median"],
                "unit": "rho",
                "status": "measured",
                "source": f"{close['brief']} (made up), {CHANGES} weight changes of up to {SHIFT:.0%}, seed "
                f"{CHANGE_SEED}, app.decide",
                "detail": "The close brief's median Spearman between its shown order and each changed one.",
            }
        ],
    }


def main(argv: Sequence[str] = ()) -> int:
    if argv:
        print("usage: python -m evals.stability", file=sys.stderr)
        return 2
    from scripts.eval_trust import current_commit

    record = {
        "generated": datetime.now(UTC).date().isoformat(),
        "commit": current_commit(),
        **measure(),
    }  # for its floor
    STABILITY_OUT.write_bytes((json.dumps(record, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))  # LF only
    close = record["close"]
    print(f"median {record['median']}, lowest {record['lowest']} (target {TARGET}): {STABILITY_OUT.name}")
    print(f"close brief: median {close['median']}, lowest {close['lowest']}, target met: {close['meets_target']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
