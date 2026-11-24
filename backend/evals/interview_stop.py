"""US-04's product-aware stop (D-75, S5-14), tried on the interview simulator only.

Run from backend/:  python -m evals.interview_stop     (writes evals/results/interview-stop-tune.json and
                    evals/results/interview-sim-stop.json; about 10 minutes, offline)

- The rule: once the shopper has given at least MIN_ANSWERS answers, decide's seeded simulations
  (app/decide/simulation.py) score the brief's products, with each criterion's weight drawn from the interview's
  current belief about it: normal, with the belief's mean and sd, never below 0, and renormalised in each simulation as
  decide does. The interview stops when one product is first in at least `share` of the simulations. Products level
  at the top share that simulation, as in decide. The share is always above one half: at a half or less, two products
  tied at the top would stop the interview.
- Until then, the smart interview (US-04) runs as it is: the same question order, the same settled stop and the same
  10-question limit. The rule only adds a way to stop.
- The share is tuned on the tuning seed (4747), accuracy first, as US-04's numbers were (Om, 17 Nov 2026):
  1. keep the shares at least as accurate as the fixed order there;
  2. then the fewest questions;
  3. then the share nearest START_SHARE (0.9, D-75's starting point);
  4. then the higher share.
  If no share is as accurate, the most accurate is taken, then by the same tie-breaks, and the record says so.
- The report is on the simulator's own seed (47), against the fixed order on the same shoppers.
- The bar (D-75 #2): on the report seed, accuracy at least the fixed order's and at least 30% fewer questions. Only
  then may the rule ship in app/interview/. The record says "met" or "not met".
- The live app can't run it as it is (LIVE_LIMIT): no products exist until research runs, after the interview.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from app.contracts import InterviewState, InterviewStep, Rubric, WeightBelief
from app.contracts.rubric import normalize
from app.decide.simulation import SIMULATIONS, draw
from app.interview import AdaptiveInterviewStage
from app.interview.beliefs import BeliefSettings, beliefs_for
from app.interview.plan import plan_questions
from app.interview.rubric_builder import build_rubric
from app.interview.stage import _finished, _pending

from .interview_sim import CATEGORIES, SEED, SHOPPERS, Brief, out_path, simulate
from .interview_tune import TUNING_SEED
from .run_baselines import RESULTS_DIR

MIN_ANSWERS = 3  # D-75 #1: never before 3 answers
START_SHARE = 0.9  # D-75 #1: where the tuning starts
SHARES = tuple(round(0.51 + 0.01 * i, 2) for i in range(49))  # 0.51 to 0.99: always above one half
FEWER = 0.30  # US-04 criterion 4: 30% fewer questions than the fixed order
LIVE_LIMIT = (
    "Simulator only: the live interview has no products, because research runs after it. A product-aware stop could "
    "only run live if research started during the interview."
)
PLANNER = "stop"
TUNE_OUT = RESULTS_DIR / "interview-stop-tune.json"
REPORT_OUT = out_path(PLANNER)
RUNNER = "python -m evals.interview_stop (offline)"
RULE = (
    "accuracy first (Om, 17 Nov 2026): keep the shares at least as accurate as the fixed order on the tuning seed; "
    "then the fewest questions; then the share nearest 0.9; then the higher share"
)
_WEIGHT_STREAM = 1  # the weights' own random stream, apart from decide's for the scores
_TINY = 1e-9


# --- the rule -------------------------------------------------------------------------------------------------------


def answers_given(state: InterviewState) -> int:
    """The answers that move a belief (beliefs.py): answered or remembered, with a weight. A skip is not an answer."""
    return sum(
        1
        for entry in state.history
        if entry.status in ("answered", "remembered")
        and entry.classification is not None
        and entry.classification.weight is not None
    )


def first_shares(
    brief: Brief, beliefs: Mapping[str, WeightBelief], category: str, seed: int, sims: int = SIMULATIONS
) -> dict[str, float]:
    """Each product's share of the simulations it tops: decide's draws of the brief's scores, with weights drawn from
    the beliefs. Products level at the top share that simulation, so the shares add up to 1. Same seed, same shares."""
    keys = list(beliefs)
    flat = normalize(dict.fromkeys(keys, 1.0))
    rubric = Rubric.model_validate(
        {
            "category": category,
            "weights": [
                {"key": key, "weight": 5.0, "normalized": flat[key], "origin": "default", "uncertainty": 0.0}
                for key in keys
            ],
        }
    )
    values = {(v.product_id, v.criterion_key): v for v in brief.values}
    drawn = draw(brief.products, rubric, values, {}, seed, sims)  # the scores; its weights are the flat ones, unused
    rng = np.random.default_rng([seed % (1 << 32), _WEIGHT_STREAM])
    mean = np.array([beliefs[key].mean for key in drawn.keys], dtype=np.float64)
    sd = np.array([beliefs[key].sd for key in drawn.keys], dtype=np.float64)
    weights = np.clip(mean + sd * rng.standard_normal((sims, len(drawn.keys))), 0.0, None)
    totals = weights.sum(axis=1, keepdims=True)
    weights = np.where(totals > _TINY, weights / np.where(totals > _TINY, totals, 1.0), 1.0 / len(drawn.keys))
    scored = np.einsum("sk,spk->sp", weights, drawn.scores)
    first = scored >= scored.max(axis=1, keepdims=True) - _TINY
    shares = (first / first.sum(axis=1, keepdims=True)).mean(axis=0)
    return {pid: float(share) for pid, share in zip(drawn.product_ids, shares, strict=True)}


class ProductAwareStage(AdaptiveInterviewStage):
    """The smart interview with D-75's product-aware stop, for the simulator: it needs the brief's products, which the
    live interview never has (research runs after it)."""

    def __init__(
        self,
        brief: Brief,
        share: float = START_SHARE,
        *,
        seed: int = SEED,
        sims: int = SIMULATIONS,
        min_answers: int = MIN_ANSWERS,
        settings: BeliefSettings | None = None,
    ) -> None:
        if not 0.5 < share <= 1:
            raise ValueError("the share must be above one half and at most 1, or a tie at the top would stop it")
        super().__init__(settings=settings)
        self.brief = brief
        self.share = share
        self.seed = seed
        self.sims = sims
        self.min_answers = min_answers

    def _smart_next(self, state: InterviewState) -> InterviewStep:
        """A question on screen is always asked again; past MIN_ANSWERS answers, a clear leader ends the interview."""
        if _pending(state.history) is None and answers_given(state) >= self.min_answers and self.clear_leader(state):
            plan = plan_questions(state.criteria, state.intent, state.history, self.rules)
            return _finished(state, build_rubric(state, plan), self.ordering)
        return super()._smart_next(state)

    def clear_leader(self, state: InterviewState) -> bool:
        """Whether one product is first in at least `share` of the simulations, with the beliefs the answers give."""
        beliefs = beliefs_for(state, self.settings)
        shares = first_shares(self.brief, beliefs, state.criteria.category, self.seed, self.sims)
        return max(shares.values()) >= self.share - _TINY


def simulate_stop(
    share: float, seed: int = SEED, shoppers: int = SHOPPERS, categories: Sequence[str] = CATEGORIES
) -> dict[str, Any]:
    """The product-aware stop at one share on the simulated shoppers: the simulator's record. Same seed, same output."""
    return simulate(
        PLANNER, seed, shoppers, categories, make_for=lambda brief: ProductAwareStage(brief, share, seed=seed)
    )


# --- the tuning -----------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Row:
    """One share's result on the simulated shoppers."""

    share: float
    questions: float
    accuracy: float
    mae: float

    def as_dict(self) -> dict[str, float]:
        return {"share": self.share, "questions": self.questions, "accuracy": self.accuracy, "mae": self.mae}


def _nearness(row: Row) -> tuple[float, float]:
    return round(abs(row.share - START_SHARE), 4), -row.share


def choose(rows: Sequence[Row], fixed_accuracy: float) -> Row:
    """The rule above. Raises ValueError with no rows."""
    if not rows:
        raise ValueError("no shares to choose from")
    keep = [r for r in rows if r.accuracy >= fixed_accuracy]
    if keep:
        return min(keep, key=lambda r: (r.questions, *_nearness(r)))
    return min(rows, key=lambda r: (-r.accuracy, r.questions, *_nearness(r)))


def measure(share: float, seed: int, shoppers: int = SHOPPERS) -> Row:
    overall = simulate_stop(share, seed, shoppers)["summary"]["overall"]
    return Row(share, questions=overall["questions_mean"], accuracy=overall["accuracy"], mae=overall["mae_mean"])


def tune(seed: int = TUNING_SEED, shoppers: int = SHOPPERS, shares: Sequence[float] = SHARES) -> dict[str, Any]:
    """Every share, the fixed order beside them, and the choice: the record without its date, commit and runner."""
    fixed = simulate("fixed", seed, shoppers)["summary"]["overall"]
    rows = [measure(share, seed, shoppers) for share in shares]
    chosen = choose(rows, fixed["accuracy"])
    start = next((r for r in rows if r.share == START_SHARE), None) or measure(START_SHARE, seed, shoppers)
    return {
        "settings": {
            "seed": seed,
            "shoppers_per_category": shoppers,
            "categories": list(CATEGORIES),
            "shares": list(shares),
            "min_answers": MIN_ANSWERS,
            "simulations": SIMULATIONS,
        },
        "rule": RULE,
        "fixed": fixed,
        "start": start.as_dict(),
        "chosen": chosen.as_dict(),
        "keeps_fixed_accuracy": chosen.accuracy >= fixed["accuracy"],
        "rows": [r.as_dict() for r in rows],
    }


# --- the report and the bar -----------------------------------------------------------------------------------------


def verdict(stop: Mapping[str, Any], fixed: Mapping[str, Any]) -> dict[str, Any]:
    """D-75 #2's bar: accuracy at least the fixed order's, and at least 30% fewer questions. Compared unrounded;
    the share of fewer questions is rounded for the record only."""
    fewer = 1 - stop["questions_mean"] / fixed["questions_mean"]
    met = stop["accuracy"] >= fixed["accuracy"] and fewer >= FEWER - _TINY
    return {
        "accuracy": stop["accuracy"],
        "fixed_accuracy": fixed["accuracy"],
        "questions": stop["questions_mean"],
        "fixed_questions": fixed["questions_mean"],
        "fewer_questions": round(fewer, 4),
        "target_fewer_questions": FEWER,
        "met": met,
        "says": "met" if met else "not met",
    }


def report(share: float, seed: int = SEED, shoppers: int = SHOPPERS, tuned_on: int = TUNING_SEED) -> dict[str, Any]:
    """The chosen share on the report seed, with the fixed order on the same shoppers, the bar and the live limit."""
    record = simulate_stop(share, seed, shoppers)
    fixed = simulate("fixed", seed, shoppers)["summary"]["overall"]
    bar = verdict(record["summary"]["overall"], fixed)
    return {
        **record,
        "stop": {"share": share, "min_answers": MIN_ANSWERS, "simulations": SIMULATIONS, "tuned_on_seed": tuned_on},
        "fixed": fixed,
        "bar": bar,
        "may_ship": bar["met"],  # D-75 #2: only a met bar lets the rule into app/interview/
        "live_limit": LIVE_LIMIT,
    }


def _shares(text: str) -> list[float]:
    try:
        shares = [round(float(part), 2) for part in text.split(",") if part.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"shares must be numbers: {text!r}") from exc
    if not shares or not all(0.5 < share <= 1 for share in shares):
        raise argparse.ArgumentTypeError("each share must be above one half and at most 1")
    return shares


def _line(name: str, questions: float, accuracy: float) -> str:
    return f"{name:34}{questions:>12.4g}{accuracy:>12.4g}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.interview_stop", description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--seed", type=int, default=SEED, help="the report seed")
    parser.add_argument("--tune-seed", type=int, default=TUNING_SEED)
    parser.add_argument("--shoppers", type=int, default=SHOPPERS, help="per category")
    parser.add_argument("--shares", type=_shares, default=None, help="comma-separated (default: 0.51 to 0.99)")
    parser.add_argument("--out-dir", type=Path, default=None, help=f"default: {RESULTS_DIR.as_posix()}")
    args = parser.parse_args(argv)
    shares: list[float] = args.shares or list(SHARES)
    default_run = (args.seed, args.tune_seed, args.shoppers, shares) == (SEED, TUNING_SEED, SHOPPERS, list(SHARES))
    if args.out_dir is None and not default_run:
        parser.error(
            "a run with other seeds, shoppers or shares needs --out-dir: it never replaces the committed records"
        )
    if args.seed == args.tune_seed:
        parser.error("the report seed must differ from the tuning seed (D-75: tune on one seed, report on another)")
    from scripts.eval_trust import current_commit

    stamp = {"generated": datetime.now(UTC).date().isoformat(), "commit": current_commit(), "runner": RUNNER}
    tuned = {**stamp, **tune(args.tune_seed, args.shoppers, shares)}
    reported = {**stamp, **report(tuned["chosen"]["share"], args.seed, args.shoppers, args.tune_seed)}
    print(f"Tuning seed {args.tune_seed}:{'questions':>21}{'accuracy':>12}")
    print(_line("fixed order", tuned["fixed"]["questions_mean"], tuned["fixed"]["accuracy"]))
    for name in ("start", "chosen"):
        row = tuned[name]
        print(_line(f"{name}, share {row['share']}", row["questions"], row["accuracy"]))
    bar = reported["bar"]
    print(f"Report seed {args.seed}:")
    print(_line("fixed order", bar["fixed_questions"], bar["fixed_accuracy"]))
    print(_line(f"product-aware stop, share {reported['stop']['share']}", bar["questions"], bar["accuracy"]))
    print(
        f"US-04 criterion 4 (the fixed order's accuracy with 30% fewer questions): {bar['says']} "
        f"({bar['fewer_questions']:.1%} fewer questions, accuracy {bar['accuracy']} against {bar['fixed_accuracy']})"
    )
    print(LIVE_LIMIT)
    out_dir: Path = args.out_dir or RESULTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    for path, record in ((out_dir / TUNE_OUT.name, tuned), (out_dir / REPORT_OUT.name, reported)):
        path.write_bytes((json.dumps(record, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
        print(f"Written: {path.as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
