"""US-49's label check (S4-13): each decide brief's hand-labelled winner, recomputed by the scoring code.

A brief's `expect.shown` is written by hand, best first. Its first product is the label checked here: the products it
shows are scored again with app.decide.scoring.rank, at the brief's own weights, with the same value index and
directions decide uses, and the label must come out on top, or tied with the top on score. A disagreement means the
hand label or the scoring code is wrong, and either way the gate fails. A brief that shows nothing has no winner.

Run from backend/:  python -m evals.labels
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from app.contracts import DecideInput
from app.decide.directions import directions_for
from app.decide.rules import index_values
from app.decide.scoring import rank

from .run_baselines import BACKEND

BRIEFS = BACKEND / "tests" / "fixtures" / "decide"


def briefs(root: Path = BRIEFS) -> list[Path]:
    """The five decide briefs, and the close one kept apart for the stability check (S4-28)."""
    return sorted(root.glob("*.json")) + sorted((root / "stability").glob("*.json"))


def disagreement(path: Path) -> str | None:
    """Why this brief's hand-labelled winner is wrong, or None if the scoring code agrees (or it shows nothing)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    shown: list[str] = data["expect"]["shown"]
    if not shown:
        return None
    inp = DecideInput.model_validate(data["input"])
    by_label = {p.id: p for p in inp.products} | {p.name: p for p in inp.products}  # labels are ids or names
    missing = [label for label in shown if label not in by_label]
    if missing:
        return f"{path.stem}: the label names products the brief doesn't have: {missing}"
    products = [by_label[label] for label in shown]
    values = index_values(inp.values, {p.id for p in products})
    directions = directions_for([w.key for w in inp.rubric.weights], inp.rubric.category)
    ranked = rank(products, inp.rubric, values, directions)
    if not ranked:
        return f"{path.stem}: the scoring code ranks none of the shown products"
    top = ranked[0].score
    tied = {r.product_id for r in ranked if r.score == top}
    winner = by_label[shown[0]]
    if winner.id in tied:
        return None
    best = next(p.name for p in products if p.id == ranked[0].product_id)
    return f"{path.stem}: labelled winner {winner.name}, but the scoring code puts {best} first ({top})"


def disagreements(root: Path = BRIEFS) -> list[str]:
    return [problem for path in briefs(root) if (problem := disagreement(path)) is not None]


def main() -> int:
    problems = disagreements()
    for problem in problems:
        print(f"FAIL {problem}")
    print(f"{len(briefs())} briefs checked, {len(problems)} disagreements")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
