"""The interview simulator (S4-3, ML-1): seeded shoppers with hidden weights answer the real interview.

Run from backend/:  python -m evals.interview_sim     (writes evals/results/interview-sim-<planner>.json)

Prep for task #47 (US-04): the smart interview must match the fixed order's accuracy with 30% fewer questions on
simulated shoppers. This measures the fixed order (US-05) now, so US-04's planner is held to it on the same shoppers.

- Shoppers: for each of the six listings categories, 50 shoppers. A shopper's hidden true weights are the category's
  default weights plus seeded noise, clipped to 0 to 10. Each answer is the rough level (US-08) nearest the true weight,
  and with a seeded 15% chance the neighbouring level instead. Answers are fixed per criterion when the shopper is
  made, so every planner meets the same shoppers.
- Turns: the shopper answers through the interviewer's own `run`, one turn at a time, until it says it is finished.
  The simulator never stops an interview itself. No LLM call and no network.
- Briefs: one made-up brief per category, on its fallback criteria. Its 6 products each have the same scores in a
  different order, so none is better at everything and the best match depends on the weights.
- Per shopper: the questions asked; the mean absolute error of the final weights against the true ones; whether the
  top 3 criteria match; and whether decide's best match with the final rubric is the one the true weights give.
  Accuracy is the share of shoppers where it is. It is the one measure with a floor (evals/thresholds.yaml).

Planners: PLANNERS maps a name to a class with `run(InterviewTurn, StageContext) -> InterviewStep`.
- "fixed": the InterviewStage, the fixed order.
- "smart": US-04's AdaptiveInterviewStage, the live interview, with its tuned numbers (evals/interview_tune.py tunes
  them on another seed).
- "d69": the same class with D-69's starting numbers, kept as a reference: fewer questions, less accuracy.
`--planners fixed,smart,d69` prints them side by side.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import random
import statistics
import string
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from app.contracts import (
    MAX_QUESTIONS,
    MAX_UNCLEAR_PER_QUESTION,
    ROUGH_LEVEL_WEIGHTS,
    CriteriaInput,
    CriteriaSet,
    DecideInput,
    Intent,
    InterviewState,
    InterviewStep,
    InterviewTurn,
    Product,
    ResolvedValue,
    RoughLevel,
    Rubric,
    StageContext,
    ValueState,
)
from app.contracts.rubric import normalize
from app.criteria import fallback_set
from app.decide import decide
from app.interview import AdaptiveInterviewStage, InterviewStage
from app.interview.beliefs import D69_SETTINGS

from .run_baselines import RESULTS_DIR, Measure, measure

CATEGORIES = ("smartphone", "air_fryer", "laptop", "smartwatch", "earbuds", "headphones")  # ml/labelling/listings.csv
SEED = 47  # task #47
SHOPPERS = 50  # per category
NOISE_SD = 2.0  # spread of a shopper's true weights around the category defaults
FLIP_RATE = 0.15  # chance an answer is the neighbouring rough level
PRODUCTS = 6  # per brief
BRIEF_SEED = 47  # the briefs stay the same whatever seed the shoppers use
TOP = 3
SESSION_ID = "sess_interviewsim0047"
SKIP_REPLY = "Skip"
MAX_TURNS = MAX_QUESTIONS * (MAX_UNCLEAR_PER_QUESTION + 1)  # past this, an interviewer is not going to finish
RUNNER = "python -m evals.interview_sim (offline)"
SOURCE = "backend/evals/interview_sim.py (made-up shoppers and briefs), app.interview, app.decide"

# The rough levels from the lowest weight to the highest: low 2, good enough 3, medium 5, high 8.
LEVELS: tuple[RoughLevel, ...] = tuple(sorted(RoughLevel, key=lambda level: ROUGH_LEVEL_WEIGHTS[level]))


class Interviewer(Protocol):
    def run(self, inp: InterviewTurn, ctx: StageContext) -> InterviewStep: ...


def _d69() -> Interviewer:
    return AdaptiveInterviewStage(settings=D69_SETTINGS)


PLANNERS: dict[str, Callable[[], Interviewer]] = {"fixed": InterviewStage, "smart": AdaptiveInterviewStage, "d69": _d69}


def out_path(planner: str, out_dir: Path = RESULTS_DIR) -> Path:
    return out_dir / f"interview-sim-{planner}.json"


FIXED_OUT = out_path("fixed")
SMART_OUT = out_path("smart")
D69_OUT = out_path("d69")


# --- shoppers ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Shopper:
    """Hidden true weights, and the answer given to each criterion (None: a skip)."""

    category: str
    index: int
    true_weights: dict[str, float]
    answers: dict[str, RoughLevel | None]
    flipped: frozenset[str] = frozenset()


def nearest_level(weight: float) -> RoughLevel:
    """The rough level nearest a weight. Halfway between two, the lower one."""
    return min(LEVELS, key=lambda level: (abs(ROUGH_LEVEL_WEIGHTS[level] - weight), ROUGH_LEVEL_WEIGHTS[level]))


def neighbour_level(level: RoughLevel, weight: float) -> RoughLevel:
    """The adjacent level a shopper picks by mistake: the one on the true weight's side of `level`; with the weight
    exactly on it, the nearer one (the lower on a tie); at either end, the only one."""
    i = LEVELS.index(level)
    sides = [LEVELS[j] for j in (i - 1, i + 1) if 0 <= j < len(LEVELS)]
    at = ROUGH_LEVEL_WEIGHTS[level]
    if weight > at and i + 1 < len(LEVELS):
        return LEVELS[i + 1]
    if weight < at and i > 0:
        return LEVELS[i - 1]
    return min(sides, key=lambda side: (abs(ROUGH_LEVEL_WEIGHTS[side] - weight), ROUGH_LEVEL_WEIGHTS[side]))


def make_shopper(
    criteria: CriteriaSet, index: int, seed: int = SEED, noise_sd: float = NOISE_SD, flip_rate: float = FLIP_RATE
) -> Shopper:
    """A seeded shopper: the same seed, category and index always give the same shopper."""
    rng = random.Random(f"{seed}:{criteria.category}:{index}")
    weights = {
        c.key: round(min(10.0, max(0.0, c.default_weight + rng.gauss(0.0, noise_sd))), 2) for c in criteria.criteria
    }
    answers: dict[str, RoughLevel | None] = {}
    flipped: set[str] = set()
    for c in criteria.criteria:
        level = nearest_level(weights[c.key])
        if rng.random() < flip_rate:
            level = neighbour_level(level, weights[c.key])
            flipped.add(c.key)
        answers[c.key] = level
    return Shopper(criteria.category, index, weights, answers, frozenset(flipped))


def shopper_with(criteria: CriteriaSet, weights: Mapping[str, float], *, skip_all: bool = False) -> Shopper:
    """A shopper with given true weights who answers each with its nearest level (never wrong), or skips them all."""
    true_weights = {c.key: float(weights[c.key]) for c in criteria.criteria}
    answers = {key: None if skip_all else nearest_level(w) for key, w in true_weights.items()}
    return Shopper(criteria.category, -1, true_weights, answers)


# --- one interview ----------------------------------------------------------------------------------------------------


def label_of(category: str) -> str:
    return category.replace("_", " ").capitalize()


def criteria_for(category: str) -> CriteriaSet:
    return fallback_set(CriteriaInput(category=category, category_label=label_of(category)))


def state_for(criteria: CriteriaSet) -> InterviewState:
    """A made-up request with no budget, rule or exclusion, so the final rubric goes straight into decide."""
    label = label_of(criteria.category)
    intent = Intent(
        raw_query=f"{label} for a simulated shopper",
        category=criteria.category,
        category_label=label,
        detected_by="rules",
    )
    return InterviewState(session_id=SESSION_ID, intent=intent, criteria=criteria)


def interview(
    interviewer: Interviewer, criteria: CriteriaSet, shopper: Shopper, planner: str = "fixed"
) -> InterviewStep:
    """Answer the interviewer turn by turn until it says it is finished. The stop is always the interviewer's.

    The questions are counted here too, as the distinct questions the shopper was shown (a rephrase is the same
    question), and a planner whose own `questions_asked` differs is an error: US-04 is judged on that count."""
    ctx = StageContext(search_id="srch_interviewsim")
    step = interviewer.run(InterviewTurn(state=state_for(criteria)), ctx)
    shown: set[str] = set()
    for _ in range(MAX_TURNS):
        if step.kind == "finished":
            break
        if step.question is None:
            raise RuntimeError(f"planner {planner!r} asked nothing in a {step.kind!r} step")
        shown.add(step.question.id)
        answer = shopper.answers.get(step.question.criterion_key)
        turn = (
            InterviewTurn(state=step.state, reply_text=SKIP_REPLY)
            if answer is None
            else InterviewTurn(state=step.state, level=answer)
        )
        step = interviewer.run(turn, ctx)
    if step.kind != "finished":
        raise RuntimeError(f"planner {planner!r} did not finish within {MAX_TURNS} turns")
    if step.questions_asked != len(shown):
        raise RuntimeError(f"planner {planner!r} reports {step.questions_asked} questions but asked {len(shown)}")
    return step


# --- briefs and scoring -----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Brief:
    products: list[Product]
    values: list[ResolvedValue]
    scores: dict[str, dict[str, float]]  # product id -> criterion key -> score


def _claim_id(product_id: str, key: str) -> str:
    return "clm_" + hashlib.sha256(f"{product_id}:{key}".encode()).hexdigest()[:12]


def brief_for(criteria: CriteriaSet) -> Brief:
    """The category's made-up test brief: each product has the same evenly spaced scores, 9 down to 2, in its own
    seeded order. No product dominates another, so the best match depends on the weights."""
    rng = random.Random(f"brief:{BRIEF_SEED}:{criteria.category}")
    keys = [c.key for c in criteria.criteria]
    spaced = [round(9 - 7 * i / (len(keys) - 1), 2) for i in range(len(keys))]
    orders: list[list[float]] = []
    while len(orders) < PRODUCTS:
        order = rng.sample(spaced, len(spaced))
        if order not in orders:
            orders.append(order)
    label = label_of(criteria.category)
    products: list[Product] = []
    values: list[ResolvedValue] = []
    scores: dict[str, dict[str, float]] = {}
    for letter, order in zip(string.ascii_uppercase, orders, strict=False):
        product = Product(id=f"prd_sim_{criteria.category}_{letter.lower()}", name=f"Sim {label} {letter}", brand="Sim")
        products.append(product)
        scores[product.id] = dict(zip(keys, order, strict=True))
        values += [
            ResolvedValue(
                product_id=product.id,
                criterion_key=key,
                state=ValueState.KNOWN,
                score=score,
                confidence=0.8,
                supporting_claim_ids=[_claim_id(product.id, key)],
            )
            for key, score in scores[product.id].items()
        ]
    return Brief(products, values, scores)


def true_rubric(final: Rubric, true_weights: Mapping[str, float]) -> Rubric:
    """The final rubric's frame with the shopper's true weights in place of the interview's."""
    shares = normalize(dict(true_weights))
    weights = [
        {"key": key, "weight": weight, "normalized": shares[key], "origin": "manual", "uncertainty": 0.0}
        for key, weight in true_weights.items()
    ]
    return Rubric.model_validate({**final.model_dump(), "weights": weights})


def best_match(rubric: Rubric, brief: Brief) -> str | None:
    """The best match the rubric's weights give, by the plain weighted ranking. This measures the weights themselves:
    the simulations (US-19) would also move it with each weight's uncertainty, which the true rubric (manual, 0) and
    the interview's (0.2 to 0.5) don't share, so the two best matches wouldn't be compared on the same footing."""
    result = decide(DecideInput(rubric=rubric, products=brief.products, values=brief.values), weighted_only=True)
    return result.ranked[0].product_id if result.ranked else None


def top3(weights: Mapping[str, float], order: Sequence[str]) -> list[str]:
    """The 3 heaviest criteria; ties keep the criteria's own order."""
    return sorted(order, key=lambda key: (-weights[key], order.index(key)))[:TOP]


@dataclass(frozen=True)
class ShopperResult:
    category: str
    index: int
    questions: int
    mae: float
    top3: bool
    same_best: bool
    true_best: str | None
    final_best: str | None
    flips: int

    def row(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "index": self.index,
            "questions": self.questions,
            "mae": round(self.mae, 4),
            "top3": self.top3,
            "same_best": self.same_best,
            "true_best": self.true_best,
            "final_best": self.final_best,
            "flips": self.flips,
        }


def score(criteria: CriteriaSet, shopper: Shopper, step: InterviewStep, brief: Brief) -> ShopperResult:
    if step.rubric is None:
        raise ValueError("score a finished interview only")
    given = {w.key: w.weight for w in step.rubric.weights}  # the 0 to 10 weights, not the shares
    final = {c.key: given[c.key] for c in criteria.criteria}
    keys = [c.key for c in criteria.criteria]
    true_best = best_match(true_rubric(step.rubric, shopper.true_weights), brief)
    final_best = best_match(step.rubric, brief)
    return ShopperResult(
        category=criteria.category,
        index=shopper.index,
        questions=step.questions_asked,
        mae=statistics.fmean(abs(final[key] - shopper.true_weights[key]) for key in keys),
        top3=set(top3(final, keys)) == set(top3(shopper.true_weights, keys)),  # the same 3, in any order
        same_best=true_best == final_best,
        true_best=true_best,
        final_best=final_best,
        flips=len(shopper.flipped),
    )


# --- the run ----------------------------------------------------------------------------------------------------------


def _summary(results: Sequence[ShopperResult]) -> dict[str, Any]:
    return {
        "shoppers": len(results),
        "questions_mean": round(statistics.fmean(r.questions for r in results), 4),
        "mae_mean": round(statistics.fmean(r.mae for r in results), 4),
        "top3_share": round(sum(r.top3 for r in results) / len(results), 4),
        "accuracy": round(sum(r.same_best for r in results) / len(results), 4),
    }


def simulate(
    planner: str = "fixed",
    seed: int = SEED,
    shoppers: int = SHOPPERS,
    categories: Sequence[str] = CATEGORIES,
    make: Callable[[], Interviewer] | None = None,
) -> dict[str, Any]:
    """One planner on every shopper: the record without its date, commit and runner. Same seed, same output.

    `make` builds the interviewer in place of PLANNERS[planner], so the tuning can try other numbers under one name."""
    build = make or PLANNERS[planner]
    results: list[ShopperResult] = []
    orderings: set[str] = set()
    briefs: dict[str, Any] = {}
    for category in categories:
        criteria = criteria_for(category)
        brief = brief_for(criteria)
        briefs[category] = {p.name: brief.scores[p.id] for p in brief.products}
        for index in range(shoppers):
            shopper = make_shopper(criteria, index, seed)
            step = interview(build(), criteria, shopper, planner)
            orderings.add(step.ordering)
            results.append(score(criteria, shopper, step, brief))
    overall = _summary(results)
    detail = (
        f"share of {overall['shoppers']} simulated shoppers whose best match with the final rubric is the one their "
        "true weights give"
    )
    accuracy: Measure = measure(f"interview.{planner}.accuracy", overall["accuracy"], "rate", SOURCE, detail)
    return {
        "planner": planner,
        "ordering": sorted(orderings),
        "settings": {
            "seed": seed,
            "shoppers_per_category": shoppers,
            "noise_sd": NOISE_SD,
            "flip_rate": FLIP_RATE,
            "products_per_brief": PRODUCTS,
            "brief_seed": BRIEF_SEED,
            "categories": list(categories),
        },
        "measures": [accuracy],
        "summary": {
            "overall": overall,
            "by_category": {c: _summary([r for r in results if r.category == c]) for c in categories},
        },
        "briefs": briefs,
        "shoppers": [r.row() for r in results],
    }


ROWS = (
    ("questions_mean", "questions (mean)"),
    ("mae_mean", "weight error (MAE)"),
    ("top3_share", "top 3 match"),
    ("accuracy", "accuracy"),
)


def table(records: Mapping[str, Mapping[str, Any]]) -> str:
    """The planners side by side: overall, then by category."""
    names = list(records)
    lines = [f"{'':34}" + "".join(f"{name:>16}" for name in names)]
    first = records[names[0]]
    for group in ["overall", *first["summary"]["by_category"]]:
        for field, label in ROWS:
            cells = []
            for name in names:
                summary = records[name]["summary"]
                part = summary["overall"] if group == "overall" else summary["by_category"][group]
                cells.append(f"{part[field]:>16.4g}")
            lines.append(f"{group + ': ' + label:34}" + "".join(cells))
    return "\n".join(lines)


def _planners(text: str) -> list[str]:
    names = [name.strip() for name in text.split(",") if name.strip()]
    unknown = [name for name in names if name not in PLANNERS]
    if not names or unknown:
        raise argparse.ArgumentTypeError(f"unknown planner(s) {unknown}; known: {', '.join(PLANNERS)}")
    return names


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.interview_sim", description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--planners", type=_planners, default=None, help="comma-separated (default: all)")
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--shoppers", type=int, default=SHOPPERS, help="per category")
    parser.add_argument("--out-dir", type=Path, default=None, help=f"default: {RESULTS_DIR.as_posix()}")
    args = parser.parse_args(argv)
    if args.out_dir is None and (args.seed, args.shoppers) != (SEED, SHOPPERS):
        parser.error("a run with another seed or shopper count needs --out-dir: it never replaces the committed record")
    out_dir: Path = args.out_dir or RESULTS_DIR
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    from scripts.eval_trust import current_commit

    records: dict[str, dict[str, Any]] = {}
    for planner in args.planners or list(PLANNERS):
        records[planner] = {
            "generated": datetime.now(UTC).date().isoformat(),
            "commit": current_commit(),
            "runner": RUNNER,
            **simulate(planner, args.seed, args.shoppers),
        }
    print(table(records))
    out_dir.mkdir(parents=True, exist_ok=True)
    for planner, record in records.items():
        path = out_path(planner, out_dir)
        path.write_bytes((json.dumps(record, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
        print(f"Written: {path.as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
