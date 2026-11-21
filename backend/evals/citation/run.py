"""US-22's measures (S5-4 DECIDED 3, 6 and 7): the threshold, the check on the held-out pairs, its time and its memory.

Run from backend/:  python -m evals.citation.run  [--write-threshold]

1. The threshold is tuned on the 40 tuning pairs only (metrics.tune): the most wrong sentences caught with at most 15%
   of the right ones rejected. --write-threshold writes it to config/nli.yaml.
2. On the 60 test pairs only: the share of deliberately wrong sentences caught (target 80%) and of right ones wrongly
   rejected (target at most 15%), each with a 95% bootstrap interval. The model's probability alone is given too, so
   the number rule's share can be seen.
3. Time: the check itself (check.py's check_explanation, one batch per product, through the same time-limited call as
   the stage) on the top 3 products of every recorded run, replayed offline as the quality gate replays them
   (evals/gate.py). Each paragraph is what the LLM would be asked to say, in code's words: the rank, the top 3 and a
   written trade-off, each citing its claims. Median and slowest, against the 5 s limit.
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

from app.contracts import Claim, Explanation, ExplanationSentence
from app.explain.check import TRADE_OFF, check_explanation, items, quotes_of
from app.explain.facts import ProductFacts, facts_for
from app.explain.nli import OnnxNli, load_nli
from app.explain.nli_settings import NliSettings, load_nli_settings
from app.explain.templates import strength_sentence

from ..memory_cost import MB, memory_bytes
from ..run_baselines import RESULTS_DIR
from . import metrics
from .config_file import write_config
from .model_check import best_entailment
from .pairs import LabelledPair, load_pairs, load_split

CITATION_OUT = RESULTS_DIR / "citation.json"
WRITTEN = 3  # the LLM writes for the top 3 (US-20); the rest get the template, which is never checked
SOURCE = "backend/evals/citation/ (pairs.csv, by-hand job 2), app.explain.check, app.explain.nli"


def scored(scorer: OnnxNli, pairs: list[LabelledPair]) -> list[metrics.Scored]:
    found = best_entailment(scorer, [(p.quote, p.sentence) for p in pairs])
    return [
        metrics.Scored(p.id, p.supported, prob, metrics.numbers_in_quote(p.sentence, p.quote))
        for p, prob in zip(pairs, found, strict=True)
    ]


def paragraph(facts: ProductFacts) -> Explanation:
    """What the LLM is asked to say, in code's words, each sentence citing the claims of what it is about."""
    first = facts.top[0]
    sentences = [
        ExplanationSentence(
            text=f"It ranks #{facts.rank} with a score of {facts.score} out of 100.", claim_ids=list(first.claim_ids)
        )
    ]
    sentences += [
        ExplanationSentence(text=strength_sentence(f), claim_ids=list(f.claim_ids)) for f in facts.top if f.claim_ids
    ]
    trade = facts.trade_off
    return Explanation(
        product_id=facts.product_id,
        sentences=sentences[:4],
        trade_off=f"Its weakest point is its {trade.fact.label}." if trade is not None else None,
        unverified=list(facts.unverified),
        method="llm_unchecked",
    )


def written_products() -> list[tuple[str, ProductFacts, dict[str, Claim]]]:
    """The top 3 products of every recorded run, as explain would be given them, with a claim to cite."""
    from ..gate import recordings, replay

    out: list[tuple[str, ProductFacts, dict[str, Claim]]] = []
    for folder in recordings():
        done = replay(folder)
        claims = [c for result in done.results for c in result.claims]
        ranked = done.result.decision.ranked if done.result.decision is not None else []
        by_id = {c.id: c for c in claims}
        for product in ranked[:WRITTEN]:
            facts = facts_for(product, claims)
            if facts.score is not None and facts.top and facts.top[0].claim_ids:
                out.append((folder.name, facts, by_id))
    return out


def timing(scorer: OnnxNli, settings: NliSettings, threshold: float) -> dict[str, Any]:
    products = written_products()
    if not products:
        raise SystemExit("the recorded runs gave no product with a claim to cite")
    _, facts, claims = products[0]
    check_explanation(paragraph(facts), facts, claims, scorer, threshold, 60.0)  # warm up
    seconds: list[float] = []
    pairs: list[int] = []
    checked = passed = 0
    for _, facts, claims in products:
        para = paragraph(facts)
        todo = items(para, facts)  # the sentences and the trade-off, as the check reads them
        pairs.append(sum(len(scorer.premises(q)) for item in todo for q in quotes_of(item, claims)))
        started = time.perf_counter()
        out = check_explanation(para, facts, claims, scorer, threshold, 60.0)
        seconds.append(time.perf_counter() - started)
        checked += len(todo)
        passed += sum(s.supported is True for s in out.sentences)
        passed += any(i.index == TRADE_OFF for i in todo) and out.trade_off == para.trade_off
    return {
        "products": len(products),
        "runs": sorted({run for run, _, _ in products}),
        "pairs_per_product": {"median": statistics.median(pairs), "most": max(pairs)},
        # code's own words restating scores, as the LLM is asked to: how many its quotes back at the threshold
        "sentences": {"checked": checked, "passed": passed},
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
            "detail": f"the check of one product's paragraph, over the top 3 of {len(took['runs'])} recorded runs "
            f"({took['products']} products)",
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
