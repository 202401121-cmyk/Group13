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

Beside each number (S5-15, P-45) sits a second from the same changes, seed and method: Spearman with each group the
app marks "too close to call" (products linked through tied_with in the unchanged order) counted as one position. The
group's members share one place, the average of their places, in both orders, and rho is Spearman's for tied places
(Pearson's r on the places). A change that only reorders a group's members then moves nothing, as the app never claimed
an order inside the group; any product that crosses into or out of a group moves the shared place, so it counts. A
brief with no ties gives the same number both ways; one whose shown products all fall in one group has no ordered
products, reported in words, never as 1.0. The close brief's second median has its own floor (the measured value less
0.05, never below 0.9 while it is met); the first keeps its floor and its "not met".

Run from backend/:  python -m evals.stability   (offline; rewrites evals/results/us19-stability.json)
"""

from __future__ import annotations

import json
import math
import statistics
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from app.contracts import DecideInput, RankedProduct, Rubric
from app.decide import decide

from .run_baselines import BACKEND, RESULTS_DIR, _rel

BRIEFS = BACKEND / "tests" / "fixtures" / "decide"
CLOSE_BRIEF = BRIEFS / "stability" / "close_earbuds.json"  # D-74: close products, so the check can fail
CLOSE_MEASURE = "decide.stability.close_median"
UNTIED_MEASURE = "decide.stability.close_untied_median"  # P-45: each tie group the app marks counts as one position
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
NO_ORDER = (
    "no ordered products: once each group the app marks too close to call shares one place, fewer than 2 different "
    "places are left to order"
)
WHAT_COUNTS = {  # what each number counts, one sentence each (P-45), as ml/MODELS.md card 4 says it
    "median": "Today's number compares the places of every shown product, each in its own position.",
    "untied": 'The second number gives each group of products the app marks "too close to call" one shared place, '
    "the average of its members' places, so a reorder inside a group moves nothing and any other move counts.",
}


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


def tie_heads(order: Sequence[str], tied_with: Mapping[str, Sequence[str]]) -> dict[str, str]:
    """Each shown product's group, named by its best-placed member in this order: itself when it is tied with none.
    The app marks ties in pairs; pairs that share a product join one group, so a group can chain."""
    heads: dict[str, str] = {}
    for pid in order:  # best first, so a group's first member reached is its best-placed one
        if pid in heads:
            continue
        reach = [pid]
        while reach:
            member = reach.pop()
            if member not in heads:
                heads[member] = pid
                reach += tied_with.get(member, ())
    return heads


def places(order: Sequence[str], heads: Mapping[str, str]) -> dict[str, float]:
    """Each product's place in this order (1 is first), with every member of a tie group at the group's average
    place: the group is one position, and a reorder inside it changes no place."""
    members: dict[str, list[int]] = {}
    for place, pid in enumerate(order, start=1):
        members.setdefault(heads.get(pid, pid), []).append(place)
    return {pid: statistics.fmean(members[heads.get(pid, pid)]) for pid in order}


def tied_spearman(before: Sequence[str], after: Sequence[str], heads: Mapping[str, str]) -> float:
    """Spearman's rho between two orders of the same products with each tie group at one shared place: Pearson's r
    on the places, the usual rho for tied ranks. Needs 2 or more different places before (see untied); when the
    change leaves every position at the same average place, nothing of the order is kept, so it is 0."""
    if sorted(before) != sorted(after):
        raise ValueError("the two orders hold different products")
    x, y = places(before, heads), places(after, heads)
    mean_x, mean_y = statistics.fmean(x.values()), statistics.fmean(y.values())
    dx = [x[pid] - mean_x for pid in before]
    dy = [y[pid] - mean_y for pid in before]
    spread_y = sum(d * d for d in dy)
    if spread_y == 0:
        return 0.0
    r = sum(a * b for a, b in zip(dx, dy, strict=True)) / math.sqrt(sum(d * d for d in dx) * spread_y)
    return min(1.0, max(-1.0, r))


def _spread(rhos: Sequence[float]) -> tuple[float | None, float | None]:
    """The median and lowest rho, rounded; None for both when there is nothing to count."""
    if not rhos:
        return None, None
    return round(statistics.median(rhos), 4), round(min(rhos), 4)


def _meets(median: float | None) -> bool | None:
    """US-19's target met or not; None when there is no number to hold to it (no ordered products)."""
    return None if median is None else median >= TARGET


def untied(
    before: Sequence[str], tied_with: Mapping[str, Sequence[str]], afters: Sequence[Sequence[str]]
) -> tuple[dict[str, Any], list[float]]:
    """P-45's number for one brief: the rhos with each tie group as one position, and its summary (the positions,
    the groups, the median and lowest). Fewer than 2 different places is "no ordered products", never a 1.0: one
    group holding every product, or groups whose members stand on both sides of the rest, level on average."""
    heads = tie_heads(before, tied_with)
    first = list(dict.fromkeys(heads[pid] for pid in before))  # one position per group, best first
    groups = [[pid for pid in before if heads[pid] == head] for head in first]
    ordered = len(set(places(before, heads).values())) >= 2
    rhos = [tied_spearman(before, after, heads) for after in afters] if ordered else []
    median, lowest = _spread(rhos)
    summary = {
        "positions": len(first),
        "tie_groups": [group for group in groups if len(group) > 1],
        "median": median,
        "lowest": lowest,
        "note": None if rhos else NO_ORDER,
    }
    return summary, rhos


def said(value: float | bool | None) -> str:
    """A median, lowest or "target met" as the command prints it: "no ordered products" in words when there is none."""
    return "no ordered products" if value is None else str(value)


def changed(rubric: Rubric, rng: np.random.Generator) -> Rubric:
    """The rubric with each weight's share moved by up to SHIFT either way, then renormalised to add up to 1."""
    moved = {w.key: w.normalized * float(rng.uniform(1 - SHIFT, 1 + SHIFT)) for w in rubric.weights}
    total = sum(moved.values()) or 1.0
    weights = [{**w.model_dump(), "normalized": moved[w.key] / total} for w in rubric.weights]
    return Rubric.model_validate({**rubric.model_dump(), "weights": weights})


def _order(inp: DecideInput) -> list[str]:
    return [r.product_id for r in decide(inp).ranked]


def _ties(ranked: Sequence[RankedProduct]) -> dict[str, list[str]]:
    """What the app marks "too close to call" for each shown product, as the brief is."""
    return {r.product_id: list(r.tied_with) for r in ranked}


def _load(path: Path) -> DecideInput:
    return DecideInput.model_validate(json.loads(path.read_text(encoding="utf-8"))["input"])


def _afters(inp: DecideInput, rng: np.random.Generator) -> list[list[str]]:
    """The order after each of CHANGES weight changes."""
    return [
        _order(DecideInput.model_validate({**inp.model_dump(), "rubric": changed(inp.rubric, rng)}))
        for _ in range(CHANGES)
    ]


def close_brief(path: Path = CLOSE_BRIEF) -> dict[str, Any]:
    """The close brief's rhos over CHANGES weight changes from its own generator, seeded as the five briefs' are, and
    beside them P-45's, with each tie group as one position, from the same changes."""
    inp = _load(path)
    ranked = decide(inp).ranked
    before = [r.product_id for r in ranked]
    afters = _afters(inp, np.random.default_rng(CHANGE_SEED))
    rhos = [spearman(before, after) for after in afters]
    median, lowest = round(statistics.median(rhos), 4), round(min(rhos), 4)
    meets = median >= TARGET
    ties, _ = untied(before, _ties(ranked), afters)
    return {
        "brief": _rel(path),
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
        "untied": {**ties, "target_median": TARGET, "meets_target": _meets(ties["median"])},
    }


def measure(briefs: Path = BRIEFS, close: Path = CLOSE_BRIEF) -> dict[str, Any]:
    """The stability record: each brief's rhos over CHANGES weight changes, their median and lowest, and in all, and
    beside them the close brief's (D-74) with its measures for evals/thresholds.yaml. Each number has P-45's beside
    it, with each tie group as one position, from the same changes."""
    rng = np.random.default_rng(CHANGE_SEED)
    per_brief: dict[str, Any] = {}
    every: list[float] = []
    every_untied: list[float] = []
    for path in sorted(briefs.glob("*.json")):
        inp = _load(path)
        ranked = decide(inp).ranked
        before = [r.product_id for r in ranked]
        if len(before) < 2:  # nothing to reorder: its trivial 1.0s would only flatter the median
            per_brief[path.stem] = {"shown": len(before), "counted": False}
            continue
        afters = _afters(inp, rng)
        rhos = [spearman(before, after) for after in afters]
        every += rhos
        ties, untied_rhos = untied(before, _ties(ranked), afters)
        every_untied += untied_rhos
        per_brief[path.stem] = {
            "shown": len(before),
            "counted": True,
            "median": round(statistics.median(rhos), 4),
            "lowest": round(min(rhos), 4),
            "untied": ties,
        }
    median = round(statistics.median(every), 4) if every else None
    lowest = round(min(every), 4) if every else None
    untied_median, untied_lowest = _spread(every_untied)
    near = close_brief(close)
    source = f"{near['brief']} (made up), {CHANGES} weight changes of up to {SHIFT:.0%}, seed {CHANGE_SEED}, app.decide"
    near_untied = near["untied"]["median"]
    return {
        "story": "US-19 (S4-7): the rank order under random 10% weight changes",
        "runner": RUNNER,
        "briefs": f"backend/tests/fixtures/decide ({len(per_brief)} made-up briefs)",
        "counted": "briefs with 2 or more shown products; one with fewer has no order to change",
        "changes_per_brief": CHANGES,
        "shift": SHIFT,
        "change_seed": CHANGE_SEED,
        "decide_seed": "each brief's own DecideInput.seed",
        "what_counts": WHAT_COUNTS,
        "target_median": TARGET,
        "median": median,
        "lowest": lowest,
        "meets_target": median is not None and median >= TARGET,
        "note": NO_CHANGE_NOTE if lowest == 1.0 else None,
        "untied": {
            "median": untied_median,
            "lowest": untied_lowest,
            "target_median": TARGET,
            "meets_target": _meets(untied_median),
            "note": None if every_untied else NO_ORDER,
        },
        "per_brief": per_brief,
        "close": near,
        "measures": [
            {
                "id": CLOSE_MEASURE,
                "value": near["median"],
                "unit": "rho",
                "status": "measured",
                "source": source,
                "detail": "The close brief's median Spearman between its shown order and each changed one.",
            },
            {
                "id": UNTIED_MEASURE,
                "value": near_untied,
                "unit": "rho",
                "status": "missing" if near_untied is None else "measured",
                "source": source,
                "detail": "The close brief's median Spearman with each tie group the app marks as one position (P-45).",
            },
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
    untied, close_untied = record["untied"], close["untied"]
    print(
        f"tie groups as one position (P-45): median {said(untied['median'])}, lowest {said(untied['lowest'])} "
        f"(target {TARGET}); close brief: median {said(close_untied['median'])}, lowest "
        f"{said(close_untied['lowest'])}, target met: {said(close_untied['meets_target'])}"
    )
    print(WHAT_COUNTS["median"])
    print(WHAT_COUNTS["untied"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
