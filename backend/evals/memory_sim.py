"""Preference memory on 20 seeded returning shoppers (US-06, S5-3 DECIDED 6).

Run from backend/:  python -m evals.memory_sim     (writes evals/results/memory-sim.json)

- Shoppers: made by code from the S4-3 interview simulator (evals/interview_sim.py), never hand labels. Shopper i is
  in category CATEGORIES[i % 6], with hidden true weights and fixed answers (make_shopper, its own seed).
- First session: the live interview (US-04's AdaptiveInterviewStage), answered turn by turn; its final rubric is saved
  as the app saves it (app.memory, under a made-up user id per shopper), on day 0.
- Second session, 7 days later, the same category and the same hidden weights, twice: without memory (a fresh
  interview) and with it (recall, then the interview starts with what was remembered).
- Per shopper: the questions asked in the second session with and without memory; the repeated questions, criteria
  asked again that were answered in the first session; and whether decide's best match with the final rubric is the
  one the true weights give (accuracy, as interview_sim scores it).
- The target (US-06): 50% fewer repeated questions, with accuracy no lower than without memory.

The embeddings are the app's own model on this machine (config/memory.yaml) unless --embedder word, a model-free
stand-in for quick checks. Nothing leaves the machine; no LLM call, no network after the model's first download.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import re
import statistics
import sys
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.contracts import InterviewStep
from app.interview import AdaptiveInterviewStage
from app.interview.remembered import with_remembered
from app.memory import InMemoryPreferenceStore, MemoryService, MemorySettings, load_memory_settings
from app.memory.embedder import Embedder, load_embedder

from .interview_sim import CATEGORIES, brief_for, criteria_for, interview, make_shopper, score, state_for
from .run_baselines import RESULTS_DIR, Measure, measure

SEED = 62  # task #62
SHOPPERS = 20
GAP_DAYS = 7
FIRST_DAY = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
USERS = uuid.UUID("6b0e9c62-0000-4000-8000-000000000062")  # the namespace the made-up user ids come from
MEMORY_OUT = RESULTS_DIR / "memory-sim.json"
RUNNER = "python -m evals.memory_sim (offline)"
SOURCE = (
    "backend/evals/memory_sim.py (20 seeded returning shoppers from evals/interview_sim.py), app.memory, app.interview"
)


class WordEmbedder:
    """A model-free stand-in for quick checks: a bag of words, each hashed to one of 384 places, scaled to length 1."""

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            values = [0.0] * 384
            for word in re.findall(r"[a-z0-9]+", text.lower()):
                values[int(hashlib.sha256(word.encode()).hexdigest(), 16) % 384] += 1.0
            norm = math.sqrt(sum(v * v for v in values)) or 1.0
            out.append([v / norm for v in values])
        return out


class _Ready:
    def __init__(self, embedder: Embedder) -> None:
        self.embedder = embedder

    def start(self) -> None:
        return None

    def get(self, timeout: float) -> Embedder:
        return self.embedder


@dataclass(frozen=True)
class Returning:
    category: str
    index: int
    remembered: int
    questions_without: int
    questions_with: int
    repeated_without: int
    repeated_with: int
    same_best_without: bool
    same_best_with: bool

    def row(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _asked(step: InterviewStep) -> set[str]:
    return {e.question.criterion_key for e in step.state.history if e.status != "remembered"}


def _answered(step: InterviewStep) -> set[str]:
    return {e.question.criterion_key for e in step.state.history if e.status == "answered"}


def run_one(index: int, embedder: Embedder, settings: MemorySettings, seed: int = SEED) -> Returning:
    """One returning shopper: a first session saved, then the second without and with memory."""
    category = CATEGORIES[index % len(CATEGORIES)]
    criteria = criteria_for(category)
    shopper = make_shopper(criteria, index, seed)
    brief = brief_for(criteria)
    user = uuid.uuid5(USERS, f"{seed}:{index}")
    second_day = FIRST_DAY + timedelta(days=GAP_DAYS)
    store = InMemoryPreferenceStore(clock=lambda: FIRST_DAY)
    first = interview(AdaptiveInterviewStage(), criteria, shopper, "smart")
    if first.rubric is None:
        raise RuntimeError("the first session did not finish with a rubric")
    MemoryService(settings, _Ready(embedder), store, clock=lambda: FIRST_DAY).remember(user, first.state, first.rubric)
    without = interview(AdaptiveInterviewStage(), criteria, shopper, "smart")
    recalled = MemoryService(settings, _Ready(embedder), store, clock=lambda: second_day).recall(
        user, state_for(criteria)
    )
    start = with_remembered(state_for(criteria), recalled)
    with_memory = interview(AdaptiveInterviewStage(), criteria, shopper, "smart", start=start)
    answered_before = _answered(first)
    return Returning(
        category=category,
        index=index,
        remembered=len(start.remembered),
        questions_without=without.questions_asked,
        questions_with=with_memory.questions_asked,
        repeated_without=len(_asked(without) & answered_before),
        repeated_with=len(_asked(with_memory) & answered_before),
        same_best_without=score(criteria, shopper, without, brief).same_best,
        same_best_with=score(criteria, shopper, with_memory, brief).same_best,
    )


def simulate(
    embedder: Embedder, settings: MemorySettings, seed: int = SEED, shoppers: int = SHOPPERS
) -> dict[str, Any]:
    """Every returning shopper: the record without its date, commit, runner and model. Same seed, same output."""
    results = [run_one(i, embedder, settings, seed) for i in range(shoppers)]
    repeated_without = sum(r.repeated_without for r in results)
    repeated_with = sum(r.repeated_with for r in results)
    reduction = 1 - repeated_with / repeated_without if repeated_without else 0.0
    accuracy_with = sum(r.same_best_with for r in results) / len(results)
    accuracy_without = sum(r.same_best_without for r in results) / len(results)
    measures: list[Measure] = [
        measure(
            "memory.repeated_reduction",
            reduction,
            "rate",
            SOURCE,
            f"share fewer repeated questions in the second session with memory ({repeated_with} against "
            f"{repeated_without} without), over {len(results)} returning shoppers",
        ),
        measure(
            "memory.accuracy",
            accuracy_with,
            "rate",
            SOURCE,
            "share of returning shoppers whose best match with the second session's final rubric, with memory, is the "
            "one their true weights give",
        ),
        measure(
            "memory.accuracy_change",
            accuracy_with - accuracy_without,
            "difference",
            SOURCE,
            f"accuracy with memory less accuracy without ({accuracy_without:.4g} without)",
        ),
    ]
    return {
        "settings": {
            "seed": seed,
            "shoppers": shoppers,
            "gap_days": GAP_DAYS,
            "categories": list(CATEGORIES),
            "top_k": settings.top_k,
            "min_score": settings.min_score,
            "half_life_days": settings.half_life_days,
        },
        "measures": measures,
        "summary": {
            "repeated_without": repeated_without,
            "repeated_with": repeated_with,
            "repeated_reduction": round(reduction, 4),
            "questions_without_mean": round(statistics.fmean(r.questions_without for r in results), 4),
            "questions_with_mean": round(statistics.fmean(r.questions_with for r in results), 4),
            "remembered_mean": round(statistics.fmean(r.remembered for r in results), 4),
            "accuracy_without": round(accuracy_without, 4),
            "accuracy_with": round(accuracy_with, 4),
            "target_met": reduction >= 0.5 and accuracy_with >= accuracy_without,
        },
        "shoppers": [r.row() for r in results],
    }


def _embedder(kind: str, settings: MemorySettings) -> tuple[Embedder, dict[str, str]]:
    if kind == "word":
        return WordEmbedder(), {"embedder": "word (model-free stand-in)"}
    return load_embedder(settings), {
        "embedder": "model",
        "repo": settings.repo,
        "revision": settings.revision,
        "model_file": settings.model_file,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.memory_sim", description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--embedder", choices=("model", "word"), default="model")
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--shoppers", type=int, default=SHOPPERS)
    parser.add_argument("--out-dir", type=Path, default=None, help=f"default: {RESULTS_DIR.as_posix()}")
    args = parser.parse_args(argv)
    if args.out_dir is None and (args.seed, args.shoppers, args.embedder) != (SEED, SHOPPERS, "model"):
        parser.error(
            "a run with another seed, count or embedder needs --out-dir: it never replaces the committed record"
        )
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    from scripts.eval_trust import current_commit

    settings = load_memory_settings(env={})
    embedder, about = _embedder(args.embedder, settings)
    record = {
        "generated": datetime.now(UTC).date().isoformat(),
        "commit": current_commit(),
        "runner": RUNNER,
        "model": about,
        **simulate(embedder, settings, args.seed, args.shoppers),
    }
    for key, value in record["summary"].items():
        print(f"{key:24} {value}")
    out = (args.out_dir or RESULTS_DIR) / MEMORY_OUT.name
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes((json.dumps(record, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    print(f"Written: {out.as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
