"""US-41 (S5-6, DECIDED 7): data coverage of the top 5 before and after enrich's fact look-ups.

Coverage here is the share of the shopper's whole weight that a product's values have evidence for (Known or
Disputed), averaged over the first ranking's top 5. "Before" is what verify found first; "after" is what decide ranks
on once enrich's claims went through verify again. Decide's own coverage leaves out a criterion that no shown product
has: that is exactly the gap a look-up can fill, so it is counted here.

    python -m evals.enrich_coverage --out FILE            the five recorded gate runs, enrich live (Serper, pages, one
                                                           LLM call each); research and extraction replay offline
    python -m evals.enrich_coverage --offline --out FILE   the same with the stub enrich: no call at all
    python -m evals.enrich_coverage --rebuild --merge B.json --out FILE
                                                           no call: the record's replays, and each live brief's
                                                           top5_evidence from evals/live_briefs.py's record B.json

S5-23 (D-102): when few products rank, the look-ups also reach "can't tell" products, so each run also records the
products looked up and their coverage before and after (`looked_up`, `looked_up_before`, `looked_up_after`), and the
products ranked with a score before and after (`ranked_before`, `ranked_after`).

The live briefs record the same numbers (evals/live_briefs.py, `top5_evidence`). The record holds numbers and fixed
labels only. Every call is held to its service's budget (scripts/find_conflicts.py's guard); Reddit's is 0 here.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.contracts import (
    DecideInput,
    DecideResult,
    EnrichInput,
    EnrichResult,
    ResolvedValue,
    Rubric,
    StageContext,
    ValueState,
)
from app.decide.rules import PRICE_KEY

from .run_baselines import RESULTS_DIR

TOP = 5
EVIDENCE = (ValueState.KNOWN, ValueState.DISPUTED)
OUT = RESULTS_DIR / "us41-enrich-coverage.json"
# Firecrawl in credits: a fact search that reads its results costs 5 (D-101), so a replay needs about 15 of them.
BUDGET = {"llm": 100, "serper": 75, "reddit": 0, "jina": 30, "firecrawl": 75}


class Recorder:
    """A stage, and every input and output it gave in this run, in order. Everything else is the stage's own."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.calls: list[tuple[Any, Any]] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    def run(self, inp: Any, ctx: StageContext) -> Any:
        out = self.inner.run(inp, ctx)
        self.calls.append((inp, out))
        return out


def evidence_share(rubric: Rubric, ids: Sequence[str], values: Sequence[ResolvedValue]) -> float | None:
    """The mean share of the shopper's whole weight these products' values have evidence for. None with no product."""
    if not ids:
        return None
    weight = {w.key: w.normalized for w in rubric.weights}
    states = {(v.product_id, v.criterion_key): v.state for v in values}
    return statistics.fmean(sum(w for key, w in weight.items() if states.get((pid, key)) in EVIDENCE) for pid in ids)


def top5_evidence(decide: Recorder, enrich: Recorder | None = None) -> dict[str, Any]:
    """Before and after, over the first ranking's top 5, and the look-ups enrich made. Numbers only."""
    rankings = [
        (inp, out) for inp, out in decide.calls if isinstance(inp, DecideInput) and isinstance(out, DecideResult)
    ]
    if not rankings:
        return {"top": 0}
    first_in, first_out = rankings[0]
    last_in, last_out = rankings[-1]
    ids = [r.product_id for r in first_out.ranked[:TOP]]
    before = evidence_share(first_in.rubric, ids, first_in.values)
    after = evidence_share(last_in.rubric, ids, last_in.values)
    final_ids = [r.product_id for r in last_out.ranked[:TOP]]
    record: dict[str, Any] = {
        "top": len(ids),
        "before": None if before is None else round(before, 4),
        "after": None if after is None else round(after, 4),
        "after_final_top": _rounded(evidence_share(last_in.rubric, final_ids, last_in.values)),
        "rankings": len(rankings),
        "ranked_before": _scored(first_out),
        "ranked_after": _scored(last_out),
    }
    calls = [(i, o) for i, o in (enrich.calls if enrich else []) if isinstance(i, EnrichInput)]
    if calls:
        inp, out = calls[-1]
        result = out if isinstance(out, EnrichResult) else EnrichResult()
        record["look_ups"] = {
            "price_targets": sum(1 for t in inp.targets if t.criterion_key == PRICE_KEY),
            "fact_targets": sum(1 for t in inp.targets if t.criterion_key != PRICE_KEY),
            "searches": result.searches_used,
            "llm_calls": result.llm_calls_used,
            "fact_claims": sum(1 for c in result.new_claims if c.criterion_key != PRICE_KEY),
            "price_claims": sum(1 for c in result.new_claims if c.criterion_key == PRICE_KEY),
        }
        looked = list(dict.fromkeys(t.product_id for t in inp.targets))  # ranked or "can't tell" (D-102)
        record["looked_up"] = len(looked)
        record["looked_up_before"] = _rounded(evidence_share(first_in.rubric, looked, first_in.values))
        record["looked_up_after"] = _rounded(evidence_share(last_in.rubric, looked, last_in.values))
    return record


def _scored(decision: DecideResult) -> int:
    """The products a decision ranks with a score."""
    return sum(1 for item in decision.ranked if item.score is not None)


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 4)


def replay_coverage(folder: Path, enrich: Callable[[], Any]) -> dict[str, Any]:
    """One recorded gate run replayed, with this enrich stage and a recorder on decide."""
    from .gate import replay

    decide: dict[str, Recorder] = {}
    kept: dict[str, Recorder] = {}

    def adjust(stages: Any) -> Any:
        decide["stage"] = stages.decide = Recorder(stages.decide)
        kept["stage"] = stages.enrich = Recorder(enrich())
        return stages

    replay(folder, adjust=adjust)
    return top5_evidence(decide["stage"], kept["stage"])


def measures(runs: dict[str, dict[str, Any]], source: str) -> list[dict[str, Any]]:
    """The before and after means, and the gain, over the runs with a first ranking; then, over every run that
    recorded them (S5-23), the products ranked before and after, and the looked-up products' coverage."""
    pairs = [(r["before"], r["after"]) for r in runs.values() if r.get("before") is not None]
    return [*_top5_measures(pairs, source), *_d102_measures(runs, source)]


def _d102_measures(runs: dict[str, dict[str, Any]], source: str) -> list[dict[str, Any]]:
    ranked = [(r["ranked_before"], r["ranked_after"]) for r in runs.values() if "ranked_before" in r]
    looked = [
        (r["looked_up_before"], r["looked_up_after"]) for r in runs.values() if r.get("looked_up_before") is not None
    ]
    out: list[dict[str, Any]] = []
    if ranked:
        for at, when in enumerate(("before", "after")):
            out.append(
                {
                    "id": f"enrich.ranked_{when}",
                    "value": sum(pair[at] for pair in ranked),
                    "unit": "count",
                    "status": "measured",
                    "source": source,
                    "detail": f"products ranked with a score over {len(ranked)} runs, {when} enrich's look-ups",
                }
            )
    if looked:
        for at, when in enumerate(("before", "after")):
            out.append(
                {
                    "id": f"enrich.looked_up_evidence_{when}",
                    "value": round(statistics.fmean(pair[at] for pair in looked), 4),
                    "unit": "rate",
                    "status": "measured",
                    "source": source,
                    "detail": (
                        f"mean over {len(looked)} runs' looked-up products, ranked or can't tell: the share of the "
                        f"shopper's weight with evidence, {when} enrich's look-ups"
                    ),
                }
            )
    return out


def _top5_measures(pairs: list[tuple[float, float]], source: str) -> list[dict[str, Any]]:
    if not pairs:
        return []
    before = statistics.fmean(b for b, _ in pairs)
    after = statistics.fmean(a for _, a in pairs)
    detail = f"mean over {len(pairs)} runs' first top {TOP}: the share of the shopper's weight with evidence"
    return [
        {
            "id": "enrich.top5_evidence_before",
            "value": round(before, 4),
            "unit": "rate",
            "status": "measured",
            "source": source,
            "detail": detail,
        },
        {
            "id": "enrich.top5_evidence_after",
            "value": round(after, 4),
            "unit": "rate",
            "status": "measured",
            "source": source,
            "detail": detail + ", after enrich's look-ups",
        },
        {
            "id": "enrich.top5_evidence_gain",
            "value": round(after - before, 4),
            "unit": "difference",
            "status": "measured",
            "source": source,
            "detail": "the after rate less the before rate: what enrich's look-ups added (US-41)",
        },
    ]


def main(argv: list[str] | None = None) -> int:
    from scripts.eval_trust import current_commit

    from .gate import recordings
    from .live_briefs import LiveBudget, text_in

    parser = argparse.ArgumentParser(prog="python -m evals.enrich_coverage")
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--offline", action="store_true", help="the stub enrich: nothing is looked up, no call is made")
    parser.add_argument("--rebuild", action="store_true", help="no call: the --out record's runs, measured again")
    parser.add_argument("--merge", type=Path, nargs="*", default=[], help="live_briefs records whose briefs count too")
    for service, limit in BUDGET.items():
        parser.add_argument(f"--budget-{service}", type=int, default=limit)
    args = parser.parse_args(argv)
    budget = {service: getattr(args, f"budget_{service}") for service in BUDGET}
    from app.enrich import EnrichStage, default_enrich_stage

    if not args.offline and not args.rebuild:
        from dotenv import load_dotenv

        load_dotenv()  # the keys stay in the environment; nothing here prints them
        LiveBudget(budget).install()
    make = EnrichStage if args.offline else default_enrich_stage
    if args.rebuild:
        runs = {
            k: v for k, v in json.loads(args.out.read_text(encoding="utf-8"))["runs"].items() if k.startswith("gate_")
        }
    else:
        runs = {folder.name: replay_coverage(folder, make) for folder in recordings()}
    for merged in args.merge:
        for number, brief in enumerate(json.loads(merged.read_text(encoding="utf-8"))["briefs"], start=1):
            runs[f"brief_{brief.get('category', 'unknown')}_{number}"] = brief.get("top5_evidence", {"top": 0})
    source = "backend/evals/enrich_coverage.py: the recorded gate runs replayed with live enrich" + (
        ", and the hand-written decide briefs run live (evals/live_briefs.py --briefs)" if args.merge else ""
    )
    record = {
        "generated": datetime.now(UTC).date().isoformat(),
        "commit": current_commit(),
        "runner": "python -m evals.enrich_coverage" + (" (offline)" if args.offline else " (live)"),
        "briefs": len(runs),
        "budget_note": f"limits {budget}",
        "measures": measures(runs, source),
        "runs": runs,
    }
    if text_in(record):
        print("refused to write: the record holds text that is not a fixed label")
        return 1
    args.out.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    for name, run in runs.items():
        print(f"{name}: {run}")
    print(f"Written: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
