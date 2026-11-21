"""US-22's measures (S5-4 DECIDED 3, 6 and 7): the threshold, the check on the held-out pairs, its time and its memory.

Run from backend/:  python -m evals.citation.run  [--write-threshold]

1. The threshold is tuned on the 40 tuning pairs only (metrics.tune): the most wrong sentences caught with at most 15%
   of the right ones rejected. --write-threshold writes it to config/nli.yaml.
2. On the 60 test pairs only: the share of deliberately wrong sentences caught (target 80%) and of right ones wrongly
   rejected (target at most 15%), each with a 95% bootstrap interval. The model's probability alone is given too, so
   the number rule's share can be seen.
3. Time: the check itself (check.py's checked_by_model, one batch per product, through the check's one interface) on
   the top 3 products of every recorded run with public quotes, replayed offline as the quality gate replays them
   (evals/gate.py). Each batch is one sentence per criterion its evidence call is given, read against every quote of
   that criterion (evidence_batches). Median and slowest, against the 5 s limit.
4. P-10: the process's peak memory with the trained matcher and this model loaded, and after the timing.

The model must be pinned in config/nli.yaml (python -m evals.citation.model_check --write-config) and, like the
matcher, already be in backend/.cache/. Writes evals/results/citation.json; evals/thresholds.py turns its measures into
floors. Nothing leaves this machine: the pairs and the recorded runs' quotes are read here only.
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import time
from datetime import UTC, datetime
from typing import Any

from app.explain.check import checked_by_model
from app.explain.evidence import evidence_request
from app.explain.facts import ProductFacts, facts_for
from app.explain.nli import OnnxNli, load_nli
from app.explain.nli_settings import NliSettings, load_nli_settings
from app.explain.parsing import Draft

from ..memory_probe import MB, memory_bytes
from ..run_baselines import RESULTS_DIR
from . import metrics
from .config_file import write_config
from .model_check import best_entailment
from .pairs import LabelledPair, load_pairs, load_split

CITATION_OUT = RESULTS_DIR / "citation.json"
WRITTEN = 3  # evidence sentences for the top 3 (D-91); the rest get the template, which is never checked
SOURCE = "backend/evals/citation/ (pairs.csv, by-hand job 2), app.explain.check, app.explain.nli"


def scored(scorer: OnnxNli, pairs: list[LabelledPair]) -> list[metrics.Scored]:
    found = best_entailment(scorer, [(p.quote, p.sentence) for p in pairs])
    return [
        metrics.Scored(p.id, p.supported, prob, metrics.numbers_in_quote(p.sentence, p.quote))
        for p, prob in zip(pairs, found, strict=True)
    ]


def evidence_batches() -> list[tuple[str, ProductFacts, list[Draft]]]:
    """Each top-3 product of every recorded run with public quotes, as the evidence check gets it (D-91): one sentence
    per criterion its call was given, citing every quote of that criterion. With no LLM here, each sentence is its
    criterion's first quote itself, the longest a sentence may be read against, so each batch is as big as it can be."""
    from ..gate import recordings, replay

    out: list[tuple[str, ProductFacts, list[Draft]]] = []
    for folder in recordings():
        done = replay(folder)
        claims = [c for result in done.results for c in result.claims]
        ranked = done.result.decision.ranked if done.result.decision is not None else []
        for product in ranked[:WRITTEN]:
            facts = facts_for(product, claims)
            request = evidence_request(facts, claims) if facts.score is not None and facts.top else None
            if request is None:
                continue
            drafts = []
            for key in request.labels:
                quoted = [q for q in request.quotes if q.key == key]
                drafts.append(
                    Draft(key, quoted[0].quote, tuple(q.claim_id for q in quoted), tuple(q.quote for q in quoted))
                )
            out.append((folder.name, facts, drafts))
    return out


def batch_pairs(scorer: OnnxNli, drafts: list[Draft]) -> int:
    return sum(len(scorer.premises(q)) for d in drafts for q in d.quotes)


def timing(scorer: OnnxNli, settings: NliSettings, threshold: float) -> dict[str, Any]:
    products = evidence_batches()
    if not products:
        raise SystemExit("the recorded runs gave no product with a public quote")
    _, facts, drafts = products[0]
    checked_by_model(drafts, facts, scorer, threshold, 60.0)  # warm up
    seconds: list[float] = []
    pairs: list[int] = []
    for _, facts, drafts in products:
        pairs.append(batch_pairs(scorer, drafts))
        started = time.perf_counter()
        checked_by_model(drafts, facts, scorer, threshold, 60.0)
        seconds.append(time.perf_counter() - started)
    return {
        "products": len(products),
        "runs": sorted({run for run, _, _ in products}),
        "pairs_per_product": {"median": statistics.median(pairs), "most": max(pairs)},
        "seconds_median": round(statistics.median(seconds), 3),
        "seconds_slowest": round(max(seconds), 3),
        "limit_s": settings.time_limit_s,
    }


def _mb(steps: dict[str, float], name: str) -> None:
    peak, now = memory_bytes()
    steps[f"{name}_peak_mb"] = round(peak / MB, 1)
    steps[f"{name}_now_mb"] = round(now / MB, 1)


def run(write_threshold: bool = False) -> dict[str, Any]:
    from app.entities.model import load_model_scorer
    from app.entities.settings import load_matcher_settings
    from scripts.eval_trust import current_commit

    memory: dict[str, float] = {}
    _mb(memory, "start")
    load_model_scorer(load_matcher_settings(env={}))
    _mb(memory, "with_matcher")
    settings = load_nli_settings(env={})
    loadable = NliSettings.model_validate({**settings.model_dump(), "threshold": settings.threshold or 0.5})
    started = time.perf_counter()
    scorer = load_nli(loadable)  # its sha256 and the per-machine reference check included
    load_s = round(time.perf_counter() - started, 3)
    if not isinstance(scorer, OnnxNli):
        raise SystemExit("the scorer is not the ONNX model")
    _mb(memory, "with_both")

    tune_ids, test_ids = load_split()
    pairs = load_pairs()
    tuning = scored(scorer, [p for p in pairs if p.id in set(tune_ids)])
    test = scored(scorer, [p for p in pairs if p.id in set(test_ids)])
    threshold = metrics.tune(tuning)
    if write_threshold:
        write_config({"threshold": threshold})
    caught, rejected = metrics.rates(test, threshold)
    intervals = metrics.bootstrap(test, threshold)
    alone = [metrics.Scored(p.id, p.supported, p.entailment, True) for p in test]
    caught_alone, rejected_alone = metrics.rates(alone, threshold)
    tune_caught, tune_rejected = metrics.rates(tuning, threshold)
    took = timing(scorer, settings, threshold)
    _mb(memory, "after_timing")
    import onnxruntime

    wrong = sum(not p.supported for p in test)
    right = len(test) - wrong
    detail = f"of the 60 test pairs ({wrong} wrong, {right} right), at the threshold {threshold} tuned on the other 40"
    measures = [
        {
            "id": "citation.test.caught_wrong",
            "value": round(caught, 4),
            "unit": "rate",
            "status": "measured",
            "source": SOURCE,
            "detail": f"share of the deliberately wrong sentences rejected, {detail}; 95% bootstrap interval "
            f"{intervals['caught'][0]:.2f} to {intervals['caught'][1]:.2f}",
        },
        {
            "id": "citation.test.wrongly_rejected",
            "value": round(rejected, 4),
            "unit": "rate",
            "status": "measured",
            "source": SOURCE,
            "detail": f"share of the right sentences rejected, {detail}; 95% bootstrap interval "
            f"{intervals['wrongly_rejected'][0]:.2f} to {intervals['wrongly_rejected'][1]:.2f}",
        },
        {
            "id": "citation.check_seconds_median",
            "value": took["seconds_median"],
            "unit": "seconds",
            "status": "measured",
            "source": "backend/evals/citation/run.py (the recorded runs, replayed offline), app.explain.check",
            "detail": f"the check of one product's evidence sentences, over the top 3 of {len(took['runs'])} "
            f"recorded runs ({took['products']} products)",
        },
        {
            "id": "citation.check_seconds_slowest",
            "value": took["seconds_slowest"],
            "unit": "seconds",
            "status": "measured",
            "source": "backend/evals/citation/run.py (the recorded runs, replayed offline), app.explain.check",
            "detail": f"the slowest product's check, of {took['products']}",
        },
    ]
    return {
        "generated": datetime.now(UTC).date().isoformat(),
        "commit": current_commit(),
        "runner": "python -m evals.citation.run (offline; the model and the matcher from backend/.cache/)",
        "model": {
            "repo": settings.repo,
            "revision": settings.revision,
            "file": settings.model_file,
            "load_s": load_s,
            "onnxruntime": onnxruntime.__version__,
            "processor": platform.processor() or platform.machine(),
        },
        "threshold": threshold,
        "tuning": {"pairs": len(tuning), "caught": round(tune_caught, 4), "wrongly_rejected": round(tune_rejected, 4)},
        "test": {
            "pairs": len(test),
            "wrong": wrong,
            "right": right,
            "caught": round(caught, 4),
            "wrongly_rejected": round(rejected, 4),
            "intervals_95": intervals,
            "model_alone": {"caught": round(caught_alone, 4), "wrongly_rejected": round(rejected_alone, 4)},
            "misjudged": metrics.misjudged(test, threshold),
            "targets": {"caught": metrics.CAUGHT_TARGET, "wrongly_rejected": metrics.MAX_WRONGLY_REJECTED},
        },
        "writers": dict(
            sorted({w: sum(p.written_by == w for p in pairs) for w in {p.written_by for p in pairs}}.items())
        ),
        "time": took,
        "peak_memory": memory,
        "measures": measures,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.citation.run", description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--write-threshold", action="store_true", help="write the tuned threshold to config/nli.yaml")
    args = parser.parse_args(argv)
    record = run(args.write_threshold)
    CITATION_OUT.parent.mkdir(parents=True, exist_ok=True)
    CITATION_OUT.write_bytes((json.dumps(record, indent=2) + "\n").encode("utf-8"))
    print(json.dumps({k: record[k] for k in ("threshold", "tuning", "test", "time", "peak_memory")}, indent=2))
    print(f"Written: {CITATION_OUT.as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
