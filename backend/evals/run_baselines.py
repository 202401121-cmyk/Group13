"""Sprint 3 baselines, offline (S3-18, task #45): every evaluation that runs on recorded data, in one record.

Run from backend/:  python -m evals.run_baselines            (writes evals/results/sprint-3-offline.json)

No network, no model calls, and the same numbers on every run. Each measure says where its number came from. A
result file that isn't there is reported as "missing", and something nothing records yet (an extraction, a page read)
as "not_recorded": neither is ever filled in with a guess. The live half is evals/live_briefs.py, and the floors are
in evals/thresholds.yaml (evals/thresholds.py).

- matching: the fuzzy baseline and the trained matcher, from ml/results; and the fuzzy match stage itself on our own
  labelled pairs (machine labels), by kind of pair (evals/pairs.py)
- trust: learned, tier order and majority vote on ml/labelling/conflicts.csv (scripts/eval_trust.py)
- decide: the five made-up briefs in tests/fixtures/decide: rule breaks among shown products, popularity beating
  fit, the hand-written expectations, and coverage per ranked product
- research: the recorded web searches in tests/fixtures/recorded, read as the app reads them
"""

from __future__ import annotations

import argparse
import io
import json
import statistics
import sys
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from app.contracts import DecideInput, DecideResult
from app.decide import decide
from app.decide.notes import left_out

from . import checks, pairs

Status = Literal["measured", "missing", "not_recorded"]
Measure = dict[str, Any]  # id, value, unit, status, source, detail: JSON as it is written

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
RESULTS_DIR = BACKEND / "evals" / "results"
OFFLINE_OUT = RESULTS_DIR / "sprint-3-offline.json"
FUZZY_FILE = "matching_baseline.json"
MODEL_FILE = "matcher_v1.json"
WDC = {"wdc_80cc20": "80cc20rnd000un", "wdc_50cc50": "50cc50rnd000un", "wdc_20cc80": "20cc80rnd000un"}
RECORDED_AT = datetime(2026, 11, 8, tzinfo=UTC)  # a fixed clock for reading recordings: the same sources every run


@dataclass(frozen=True)
class Paths:
    ml_results: Path
    labelling: Path
    briefs: Path
    recordings: Path
    conflicts: Path


def default_paths() -> Paths:
    return Paths(
        ml_results=REPO / "ml" / "results",
        labelling=REPO / "ml" / "labelling",
        briefs=BACKEND / "tests" / "fixtures" / "decide",
        recordings=BACKEND / "tests" / "fixtures" / "recorded",
        conflicts=REPO / "ml" / "labelling" / "conflicts.csv",
    )


def _rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def measure(
    measure_id: str, value: float | None, unit: str, source: str, detail: str = "", status: Status = "measured"
) -> Measure:
    if value is None and status == "measured":
        status = "missing"
    return {
        "id": measure_id,
        "value": None if value is None else round(float(value), 4),
        "unit": unit,
        "status": status,
        "source": source,
        "detail": detail,
    }


def _rate(merged: int, total: int) -> float | None:
    return merged / total if total else None


# --- matching ---------------------------------------------------------------------------------------------------------


def _load(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def _from_rows(
    prefix: str, path: Path, rows: Mapping[str, Mapping[str, Any] | None], read: Callable[[Mapping[str, Any], str], Any]
) -> list[Measure]:
    """Precision, recall and F1 per test set, or each reported missing with the reason."""
    out: list[Measure] = []
    for name, row in rows.items():
        for metric in ("precision", "recall", "f1"):
            measure_id = f"{prefix}.{name}.{metric}"
            if not path.exists():
                out.append(measure(measure_id, None, "rate", _rel(path), f"file not found: {_rel(path)}", "missing"))
            elif row is None:
                out.append(measure(measure_id, None, "rate", _rel(path), f"no {name} row in {path.name}", "missing"))
            else:
                value, detail = read(row, metric)
                out.append(measure(measure_id, value, "rate", f"{_rel(path)}: {name}", detail))
    return out


def matching_from_results(paths: Paths) -> list[Measure]:
    fuzzy_path, model_path = paths.ml_results / FUZZY_FILE, paths.ml_results / MODEL_FILE
    fuzzy = _load(fuzzy_path) or {"results": []}
    model = _load(model_path) or {"results": []}

    def fuzzy_row(name: str) -> Mapping[str, Any] | None:
        for row in fuzzy["results"]:
            if name == "own_pairs" and row.get("dataset") == "own_pairs" and "chosen on dev" in row.get("subset", ""):
                return dict(row)
            if (
                row.get("dataset") == "wdc_products"
                and row.get("subset") == WDC.get(name)
                and row.get("text") == "title"
            ):
                return dict(row)
        return None

    def model_row(name: str) -> Mapping[str, Any] | None:
        dataset = "own_pairs" if name == "own_pairs" else f"wdc_products {WDC[name]}"
        found = [r for r in model["results"] if r.get("dataset") == dataset and r.get("method") == "matcher"]
        return dict(found[0]) if found else None

    def read_fuzzy(row: Mapping[str, Any], metric: str) -> tuple[Any, str]:
        span = (row.get("intervals") or {}).get(metric) or {}
        note = f"; 95% interval [{span.get('low')}, {span.get('high')}]" if span else ""
        labels = "; machine labels (ml/DATA.md 4.1)" if row.get("dataset") == "own_pairs" else ""
        return row["test"][metric], f"token_set_ratio at {row['threshold']}, test part{note}{labels}"

    def read_model(row: Mapping[str, Any], metric: str) -> tuple[Any, str]:
        low, high, _ = row["intervals_95"][metric]
        labels = "; machine labels (ml/DATA.md 4.1)" if row["dataset"] == "own_pairs" else ""
        return row[metric], f"matcher at {row['threshold']}, tp {row['tp']} fp {row['fp']}; [{low}, {high}]{labels}"

    names = ["own_pairs", *WDC]
    return [
        *_from_rows("matching.fuzzy", fuzzy_path, {n: fuzzy_row(n) for n in names}, read_fuzzy),
        *_from_rows("matching.model", model_path, {n: model_row(n) for n in names}, read_model),
    ]


def matching_stage(paths: Paths) -> tuple[list[Measure], dict[str, Any]]:
    """The fuzzy match stage (the app's default matcher) on every agreed labelled pair."""
    loaded, dropped = pairs.load_pairs(paths.labelling)
    found = [pair for pair in loaded if pairs.fits_a_mention(pair)]
    too_long = len(loaded) - len(found)
    source = f"{_rel(paths.labelling)} (agreed machine labels), FuzzyMatchStage"
    if not found:
        empty = f"no labelled pairs in {_rel(paths.labelling)}"
        ids = ("same_merged", "different_merged", "variant_merged")
        return [measure(f"matching.stage.{name}", None, "rate", source, empty, "missing") for name in ids], {}
    rates = pairs.stage_rates(found)
    out = [
        measure("matching.stage.same_merged", _rate(*rates["same"]), "rate", source, "same pairs the stage merges"),
        measure(
            "matching.stage.different_merged",
            _rate(*rates["different"]),
            "rate",
            source,
            "different pairs the stage wrongly merges",
        ),
        measure(
            "matching.stage.variant_merged",
            _rate(*rates["variant"]),
            "rate",
            source,
            "variant pairs merged as one product with variants (DR-02)",
        ),
    ]
    context = {
        "pairs": {kind: {"merged": merged, "pairs": total} for kind, (merged, total) in rates.items()},
        "dropped": {**dropped, "a name longer than a mention (150 characters)": too_long},
        "labels": "machine labels (ml/DATA.md 4.1); every agreed pair, not only S3-5's test part",
    }
    return out, context


# --- trust ------------------------------------------------------------------------------------------------------------


def trust(paths: Paths) -> tuple[list[Measure], dict[str, Any]]:
    from app.verify.trust import load_trust_table
    from scripts.eval_trust import METHODS, evaluate, load_conflicts

    source = f"{_rel(paths.conflicts)}, scripts/eval_trust.py"
    if not paths.conflicts.exists():
        why = f"file not found: {_rel(paths.conflicts)}"
        ids = ("trust.learned.correct", "trust.tier_order.correct", "trust.learned_minus_majority")
        return [measure(measure_id, None, "conflicts", source, why, "missing") for measure_id in ids], {}
    report = evaluate(load_conflicts(paths.conflicts), load_trust_table())
    n = len(report.per_conflict)
    correct = {method: report.totals[method].correct for method in METHODS}
    out = [
        measure("trust.learned.correct", correct["learned"], "conflicts", source, f"of {n} conflicts"),
        measure("trust.tier_order.correct", correct["tier_order"], "conflicts", source, f"of {n} conflicts"),
        measure(
            "trust.learned_minus_majority",
            correct["learned"] - correct["majority_vote"],
            "conflicts",
            source,
            f"learned {correct['learned']} against majority vote {correct['majority_vote']}, of {n}",
        ),
    ]
    context = {
        "conflicts": n,
        "correct": correct,
        "no_answer": {method: report.totals[method].no_answer for method in METHODS},
        "fair_subset": {"conflicts": report.fair_count, "correct": {m: report.fair[m].correct for m in METHODS}},
    }
    return out, context


# --- decide -----------------------------------------------------------------------------------------------------------


def _groups(result: DecideResult) -> dict[str, Any]:
    return {
        "shown": [r.product_id for r in result.ranked],
        "filtered_out": {f.product_id: list(f.reasons) for f in result.filtered if f.group == "filtered_out"},
        "cant_tell": {f.product_id: list(f.reasons) for f in result.filtered if f.group == "cant_tell"},
        "left_out": left_out(result),
        "relaxation_hint": result.relaxation_hint,
    }


def decide_briefs(paths: Paths) -> tuple[list[Measure], dict[str, Any]]:
    files = sorted(paths.briefs.glob("*.json"))
    source = f"{_rel(paths.briefs)} ({len(files)} made-up briefs), app.decide"
    if not files:
        why = f"no briefs in {_rel(paths.briefs)}"
        ids = ("hard_rule_violations_shown", "most_mentioned_worst_fit_wins", "briefs_as_expected")
        return [measure(f"decide.{name}", None, "count", source, why, "missing") for name in ids], {}
    broken = unknown = popular = expected = 0
    coverages: list[float] = []
    per_brief: dict[str, Any] = {}
    for path in files:
        brief = json.loads(path.read_text(encoding="utf-8"))
        inp = DecideInput.model_validate(brief["input"])
        result = decide(inp)
        shown_ids = {r.product_id for r in result.ranked}
        b, u = checks.shown_violations(inp.rubric, [p for p in inp.products if p.id in shown_ids], inp.values)
        counts = {p.id: p.recommender_count for p in inp.products}
        indexed = checks.by_product(inp.values)
        fits = {r.product_id: checks.fit(inp.rubric, indexed.get(r.product_id, {})) for r in result.ranked}
        wins = checks.most_mentioned_wins_fitting_worst([(r.product_id, r.rank) for r in result.ranked], fits, counts)
        as_expected = _groups(result) == brief["expect"]
        broken, unknown, popular, expected = broken + b, unknown + u, popular + int(wins), expected + int(as_expected)
        coverages += [r.coverage for r in result.ranked]
        per_brief[path.stem] = {
            "shown": len(result.ranked),
            "held_back": len(result.filtered),
            "coverage": [round(r.coverage, 4) for r in result.ranked],
            "as_expected": as_expected,
        }
    out = [
        measure(
            "decide.hard_rule_violations_shown",
            broken,
            "products",
            source,
            "shown products breaking a rule, by evals/checks.py's own reading of the rubric (not decide's filter)",
        ),
        measure(
            "decide.most_mentioned_worst_fit_wins",
            popular,
            "briefs",
            source,
            "briefs where the most-mentioned product ranks first while it fits worst (fit: weights times each "
            "value's own score, not decide's score)",
        ),
        measure("decide.briefs_as_expected", expected, "briefs", source, f"of {len(files)}, against the hand labels"),
        measure(
            "decide.coverage_mean",
            statistics.fmean(coverages) if coverages else None,
            "rate",
            source,
            f"mean coverage over {len(coverages)} ranked products",
        ),
        measure("decide.coverage_min", min(coverages) if coverages else None, "rate", source, "lowest coverage"),
    ]
    return out, {"briefs": per_brief, "shown_with_unknown_rule": unknown}


# --- research (the recorded searches) ---------------------------------------------------------------------------------


def research_recorded(paths: Paths) -> tuple[list[Measure], dict[str, Any]]:
    from app.research.serper import read_results

    files = sorted(paths.recordings.glob("*/serper.json"))
    source = f"{_rel(paths.recordings)}: {', '.join(f.parent.name for f in files) or 'none'}"
    if not files:
        why = f"no recorded web search in {_rel(paths.recordings)}"
        return [
            measure("research.recorded.sources", None, "sources", source, why, "missing"),
            measure("research.recorded.source_types", None, "types", source, why, "missing"),
        ], {}
    types: Counter[str] = Counter()
    searches = reddit_links = 0
    for path in files:
        for call in json.loads(path.read_text(encoding="utf-8")):
            body = call.get("response_body")
            if not isinstance(body, dict) or str(call.get("status")) != "200":
                continue
            result = read_results(list(body.get("organic") or []), lambda: RECORDED_AT)
            searches += 1
            reddit_links += len(result.reddit_titles)
            types.update(s.type.value for s in result.sources)
    out = [
        measure("research.recorded.sources", sum(types.values()), "sources", source, f"from {searches} searches"),
        measure("research.recorded.source_types", len(types), "types", source, "kinds of source found"),
    ]
    return out, {"searches": searches, "sources_by_type": dict(sorted(types.items())), "reddit_links": reddit_links}


def not_recorded() -> list[Measure]:
    """What the recordings can't measure: they hold no extraction and no page reads. evals/live_briefs.py does."""
    why = "the recorded runs hold no extract calls or page reads; measured live (evals/results/sprint-3-live.json)"
    source = "backend/tests/fixtures/recorded"
    return [
        measure("extraction.claims_per_source", None, "claims", source, why, "not_recorded"),
        measure("extraction.quote_found_share", None, "rate", source, why, "not_recorded"),
        measure("research.pages_read", None, "pages", source, why, "not_recorded"),
    ]


# --- the run ----------------------------------------------------------------------------------------------------------


def run_offline(paths: Paths) -> dict[str, Any]:
    stage, stage_context = matching_stage(paths)
    trusted, trust_context = trust(paths)
    decided, decide_context = decide_briefs(paths)
    searched, research_context = research_recorded(paths)
    measures = [*matching_from_results(paths), *stage, *trusted, *decided, *searched, *not_recorded()]
    return {
        "measures": sorted(measures, key=lambda m: m["id"]),
        "context": {
            "matching_stage": stage_context,
            "trust": trust_context,
            "decide": decide_context,
            "research_recorded": research_context,
        },
    }


def table(record: Mapping[str, Any]) -> str:
    lines = [f"{'measure':46} {'value':>9}  {'unit':10} status"]
    for m in record["measures"]:
        value = "-" if m["value"] is None else f"{m['value']:g}"
        lines.append(f"{m['id']:46} {value:>9}  {m['unit']:10} {m['status']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.run_baselines", description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--out", type=Path, default=OFFLINE_OUT, help="where to write the record")
    args = parser.parse_args(argv)
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    from scripts.eval_trust import current_commit

    record = {
        "generated": datetime.now(UTC).date().isoformat(),
        "commit": current_commit(),
        "runner": "python -m evals.run_baselines (offline)",
        **run_offline(default_paths()),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes((json.dumps(record, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    print(table(record))
    print(f"\nWritten: {_rel(args.out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
