"""Parity cases for the browser's re-rank (US-23, S5-5 DECIDED 2): decide's own weighted score, under other weights.

Run from backend/:  python -m scripts.export_parity_cases
    writes ../frontend/src/test/fixtures/parity.json. CI runs it and fails if the file then differs from git.

Ten inputs: the five decide briefs (tests/fixtures/decide/) and the decide input of each of the five recorded runs
(tests/fixtures/recorded/gate_*, replayed offline as the quality gate does). For each, decide runs once on the
shopper's own rubric: its shown products and their contributions are what the browser re-ranks from, as a result
carries them. Then three times with seeded random weights (0 to 10 in the slider's half steps): the real decide code's
plain weighted ranking (no simulations, as the browser has none) gives the order, each score and each coverage the
browser must match (the same order; scores within 1e-6). Offline and deterministic: the same code gives the same file.
Run it with CI's Python, 3.11: from 3.12 sum() adds floats more exactly, so a last digit could differ.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

from app.contracts import Budget, DecideInput, DecideResult, Rubric, StageContext, WeightedCriterion
from app.contracts.rubric import normalize
from app.decide import decide

BACKEND = Path(__file__).resolve().parents[1]
BRIEFS = BACKEND / "tests" / "fixtures" / "decide"
OUT = BACKEND.parent / "frontend" / "src" / "test" / "fixtures" / "parity.json"
DRAWS = 3  # weight draws per input: 10 inputs, 30 cases
SEED = 23  # US-23
STEP = 0.5  # the slider's step (frontend SLIDER_STEP)


def brief_inputs() -> Iterator[tuple[str, DecideInput]]:
    for path in sorted(BRIEFS.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        yield f"brief {path.stem}", DecideInput.model_validate(data["input"])


def recorded_inputs() -> Iterator[tuple[str, DecideInput]]:
    """Each recorded run's decide input, caught as the gate's offline replay hands it to decide."""
    from app.decide import WeightedDecideStage
    from evals.gate import recordings, replay

    caught: list[DecideInput] = []
    real = WeightedDecideStage.run

    def spy(self: WeightedDecideStage, inp: DecideInput, ctx: StageContext) -> DecideResult:
        caught.append(inp)
        return real(self, inp, ctx)

    WeightedDecideStage.run = spy  # type: ignore[method-assign]
    try:
        for folder in recordings():
            caught.clear()
            replay(folder)
            if caught:
                yield f"recorded {folder.name}", caught[-1]
    finally:
        WeightedDecideStage.run = real  # type: ignore[method-assign]


def reweighted(rubric: Rubric, rng: random.Random) -> Rubric:
    """The rubric with seeded slider weights: 0 to 10 in half steps, normalised as the backend normalises them."""
    raw = {w.key: rng.randrange(0, 21) * STEP for w in rubric.weights}
    shares = normalize(raw)
    weights = [
        WeightedCriterion(key=w.key, weight=raw[w.key], normalized=shares[w.key], origin="manual")
        for w in rubric.weights
    ]
    return Rubric.model_validate({**rubric.model_dump(), "weights": [w.model_dump() for w in weights]})


def _contributions(result: DecideResult) -> list[dict[str, Any]]:
    return [
        {
            "product_id": r.product_id,
            "contributions": [
                {"criterion_key": c.criterion_key, "score": c.score, "state": c.state.value, "basis": c.basis}
                for c in r.contributions
            ],
        }
        for r in result.ranked
    ]


def cases(inputs: Sequence[tuple[str, DecideInput]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for n, (name, inp) in enumerate(inputs):
        original = decide(inp, weighted_only=True)
        rng = random.Random(SEED * 1000 + n)
        for draw in range(DRAWS):
            rubric = reweighted(inp.rubric, rng)
            expected = decide(
                DecideInput.model_validate({**inp.model_dump(), "rubric": rubric.model_dump()}), weighted_only=True
            )
            out.append(
                {
                    "id": f"{name} #{draw + 1}",
                    "weights": [{"key": w.key, "weight": w.weight} for w in rubric.weights],
                    "products": _contributions(original),
                    "expected": [
                        {"product_id": r.product_id, "score": r.score, "coverage": r.coverage} for r in expected.ranked
                    ],
                }
            )
    return out


BUDGET_SCALES = (0.8, 1.25)  # a budget lowered and raised, to move products both ways


def _held(result: DecideResult) -> list[dict[str, Any]]:
    return [{"product_id": f.product_id, "group": f.group, "reasons": list(f.reasons)} for f in result.filtered]


def _rescaled(budget: Budget | None, scale: float) -> Budget | None:
    if budget is None:
        return None
    return Budget.model_validate(
        {
            side: None if money is None else {"amount": round(money.amount * scale, 2), "currency": money.currency}
            for side, money in (("min", budget.min), ("max", budget.max))
        }
    )


def filter_cases(inputs: Sequence[tuple[str, DecideInput]]) -> list[dict[str, Any]]:
    """The filter's parity (US-23, DECIDED 6): what decide's real filter does with the budget lowered and raised, and
    with each rule switched over, against which the browser re-filters from decide's first checked_values alone."""
    out: list[dict[str, Any]] = []
    for name, inp in inputs:
        original = decide(inp, weighted_only=True)
        rubric = inp.rubric
        # The products decide decided: what the browser re-filters. Its ranking stops at 50 (MAX_RANKED), so what
        # passes is told from what it holds back, not from what it ranks.
        everyone = [r.product_id for r in original.ranked] + [f.product_id for f in original.filtered]
        variations: list[tuple[str, Budget | None, dict[str, bool]]] = [("as decided", rubric.budget, {})]
        if rubric.budget is not None:
            variations += [(f"budget x{scale:g}", _rescaled(rubric.budget, scale), {}) for scale in BUDGET_SCALES]
            variations.append(("budget off", None, {}))
        variations += [
            (f"rule {r.id} {'off' if r.active else 'on'}", rubric.budget, {r.id: not r.active})
            for r in rubric.hard_rules
        ]
        changes = []
        for label, budget, flips in variations:
            rules = [{**r.model_dump(), "active": flips.get(r.id, r.active)} for r in rubric.hard_rules]
            changed = Rubric.model_validate(
                {**rubric.model_dump(), "budget": budget.model_dump() if budget else None, "hard_rules": rules}
            )
            result = decide(
                DecideInput.model_validate({**inp.model_dump(), "rubric": changed.model_dump()}), weighted_only=True
            )
            held = [h for h in _held(result) if h["product_id"] in everyone]
            out_ids = {h["product_id"] for h in held}
            changes.append(
                {
                    "change": label,
                    "budget": budget.model_dump(mode="json") if budget else None,
                    "rules_on": {r.id: flips.get(r.id, r.active) for r in rubric.hard_rules},
                    "shown": sorted(pid for pid in everyone if pid not in out_ids),
                    "held": sorted(held, key=lambda h: h["product_id"]),
                    "hint": result.relaxation_hint,
                }
            )
        out.append(
            {
                "id": name,
                "budget": rubric.budget.model_dump(mode="json") if rubric.budget else None,
                "rules": [r.model_dump(mode="json") for r in rubric.hard_rules],
                "exclusions": [{"kind": e.kind, "value": e.value} for e in rubric.exclusions],
                "shown": [r.product_id for r in original.ranked],
                "held": _held(original),
                "checked_values": [v.model_dump(mode="json") for v in original.checked_values],
                "changes": changes,
            }
        )
    return out


def document() -> dict[str, Any]:
    inputs = [*brief_inputs(), *recorded_inputs()]
    return {
        "about": (
            "Written by backend/scripts/export_parity_cases.py; never edit by hand. Each case: decide's shown products "
            "and their contributions on the shopper's rubric, new slider weights, and decide's own weighted ranking "
            "with them (US-23, P-42). Each filter case: what decide's filter read, and what its real filter does with "
            "the budget lowered and raised and each rule switched over. src/test/parity.test.ts checks the browser "
            "against every one."
        ),
        "cases": cases(inputs),
        "filter_cases": filter_cases(inputs),
    }


def render() -> str:
    """The file's text: the same code always gives the same text (CI compares it with the committed file)."""
    return json.dumps(document(), indent=1, ensure_ascii=False) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.export_parity_cases",
        description="Write the browser re-rank's parity cases (US-23) to " + OUT.relative_to(BACKEND.parent).as_posix(),
    )
    parser.parse_args(argv)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(render().encode("utf-8"))
    print(f"Written: {OUT.relative_to(BACKEND.parent).as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
