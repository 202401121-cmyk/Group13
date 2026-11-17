"""Tuning the smart interview's three numbers (US-04, D-69 #5) with the simulator, on a tuning seed.

Run from backend/:  python -m evals.interview_tune     (writes evals/results/interview-tune.json; a few minutes)

- The numbers: the prior sd (how unsure a default is), the exact answer's noise sd, and the settled sd (how sure the top
  criteria must be before the interview stops). D-69 started from 2.5, 1.5 and 1.2.
- The grid: every prior sd in PRIOR_SDS, exact sd in EXACT_SDS and settled sd in SETTLED_SDS, with the settled sd below
  the prior sd. The simulated shoppers answer with rough levels only, so the exact sd can't change a result here.
- The rule, accuracy first (Om, 17 Nov 2026):
  1. Keep the settings whose accuracy on the tuning seed is at least the fixed order's.
  2. Take the fewest questions (mean).
  3. Break a tie with the settings nearest D-69's: the sum of each number's change relative to D-69's.
  4. Then take the smaller numbers.
  If no setting is as accurate, the most accurate is taken, then by the same tie-breaks, and the record says so.
- The seed: TUNING_SEED, never the report seed (evals.interview_sim's SEED, 47). The final run, on the report seed, is
  `python -m evals.interview_sim --planners smart`.

The chosen numbers are BeliefSettings' defaults in app/interview/beliefs.py; a test keeps the two the same.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from collections.abc import Sequence
from dataclasses import astuple, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.interview import AdaptiveInterviewStage
from app.interview.beliefs import D69_SETTINGS, BeliefSettings

from .interview_sim import CATEGORIES, SHOPPERS, simulate
from .run_baselines import RESULTS_DIR

TUNING_SEED = 4747  # task #47 twice: not the report seed 47
PRIOR_SDS = (2.0, 2.5, 3.0)
EXACT_SDS = (1.0, 1.5, 2.0)
SETTLED_SDS = tuple(round(0.5 + 0.1 * i, 1) for i in range(11))  # 0.5 to 1.5
D69 = D69_SETTINGS
TUNE_OUT = RESULTS_DIR / "interview-tune.json"
RUNNER = "python -m evals.interview_tune (offline)"
RULE = (
    "accuracy first (Om, 17 Nov 2026): keep the settings at least as accurate as the fixed order on the tuning seed; "
    "then the fewest questions; then the nearest D-69's numbers (sum of relative changes); then the smaller numbers"
)


@dataclass(frozen=True)
class Row:
    """One setting's result on the simulated shoppers."""

    settings: BeliefSettings
    questions: float
    accuracy: float
    mae: float

    def as_dict(self) -> dict[str, float]:
        return {
            "prior_sd": self.settings.prior_sd,
            "exact_sd": self.settings.exact_sd,
            "settled_sd": self.settings.settled_sd,
            "questions": self.questions,
            "accuracy": self.accuracy,
            "mae": self.mae,
        }


def grid() -> list[BeliefSettings]:
    return [
        BeliefSettings(prior_sd=prior, exact_sd=exact, settled_sd=settled)
        for prior in PRIOR_SDS
        for exact in EXACT_SDS
        for settled in SETTLED_SDS
        if settled < prior
    ]


def distance(settings: BeliefSettings) -> float:
    """How far the settings are from D-69's: the sum of each number's change relative to D-69's."""
    return sum(abs(a - b) / b for a, b in zip(astuple(settings), astuple(D69), strict=True))


def choose(rows: Sequence[Row], fixed_accuracy: float) -> Row:
    """The rule above. Raises ValueError with no rows."""
    if not rows:
        raise ValueError("no settings to choose from")
    keep = [r for r in rows if r.accuracy >= fixed_accuracy]
    if keep:
        return min(keep, key=lambda r: (r.questions, distance(r.settings), astuple(r.settings)))
    return min(rows, key=lambda r: (-r.accuracy, r.questions, distance(r.settings), astuple(r.settings)))


def measure(settings: BeliefSettings, seed: int, shoppers: int) -> Row:
    """The smart interview with these numbers on the simulated shoppers."""
    record = simulate("smart", seed, shoppers, make=lambda: AdaptiveInterviewStage(settings=settings))
    overall = record["summary"]["overall"]
    return Row(settings, questions=overall["questions_mean"], accuracy=overall["accuracy"], mae=overall["mae_mean"])


def tune(
    seed: int = TUNING_SEED, shoppers: int = SHOPPERS, candidates: Sequence[BeliefSettings] | None = None
) -> dict[str, Any]:
    """Every setting on the grid (or `candidates`), the fixed order beside them, and the choice: the record without
    its date, commit and runner. Same seed, same output."""
    settings_list = list(candidates) if candidates is not None else grid()
    fixed = simulate("fixed", seed, shoppers)["summary"]["overall"]
    rows = [measure(settings, seed, shoppers) for settings in settings_list]
    chosen = choose(rows, fixed["accuracy"])
    d69 = next((r for r in rows if r.settings == D69), None) or measure(D69, seed, shoppers)
    return {
        "settings": {
            "seed": seed,
            "shoppers_per_category": shoppers,
            "categories": list(CATEGORIES),
            "prior_sds": list(PRIOR_SDS),
            "exact_sds": list(EXACT_SDS),
            "settled_sds": list(SETTLED_SDS),
            "note": "the simulated shoppers answer with rough levels only, so the exact sd can't change a result here",
        },
        "rule": RULE,
        "fixed": fixed,
        "chosen": chosen.as_dict(),
        "keeps_fixed_accuracy": chosen.accuracy >= fixed["accuracy"],
        "d69": d69.as_dict(),
        "rows": [r.as_dict() for r in rows],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.interview_tune", description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--seed", type=int, default=TUNING_SEED)
    parser.add_argument("--shoppers", type=int, default=SHOPPERS, help="per category")
    parser.add_argument("--out-dir", type=Path, default=None, help=f"default: {RESULTS_DIR.as_posix()}")
    args = parser.parse_args(argv)
    if args.out_dir is None and (args.seed, args.shoppers) != (TUNING_SEED, SHOPPERS):
        parser.error("a run with another seed or shopper count needs --out-dir: it never replaces the committed record")
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    from scripts.eval_trust import current_commit

    record = {
        "generated": datetime.now(UTC).date().isoformat(),
        "commit": current_commit(),
        "runner": RUNNER,
        **tune(args.seed, args.shoppers),
    }
    fixed, chosen, d69 = record["fixed"], record["chosen"], record["d69"]
    print(f"{'':28}{'questions':>12}{'accuracy':>12}{'weight error':>14}")
    print(f"{'fixed order':28}{fixed['questions_mean']:>12.4g}{fixed['accuracy']:>12.4g}{fixed['mae_mean']:>14.4g}")
    for name, row in (("D-69's numbers", d69), ("chosen", chosen)):
        label = f"{name} {row['prior_sd']}/{row['exact_sd']}/{row['settled_sd']}"
        print(f"{label:28}{row['questions']:>12.4g}{row['accuracy']:>12.4g}{row['mae']:>14.4g}")
    out_dir: Path = args.out_dir or RESULTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / TUNE_OUT.name
    path.write_bytes((json.dumps(record, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    print(f"Written: {path.as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
