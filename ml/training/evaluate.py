"""Evaluate the trained matcher against the fuzzy baseline (US-15, S3-4, DECIDED 5 and 6).

    python ml/training/evaluate.py [--model ml/models/matcher_v1/epoch-003] [--out ml/results/matcher_v1.json]

1. The threshold is chosen on the dev part (public dev pairs and our own dev families): precision of 0.95 or more, with
   the highest recall at that precision. If none reaches 0.95 the result says so, and the target is not lowered.
2. Both matchers are then scored once on test data: our own test families (machine labels, ml/DATA.md 4.1) and the WDC
   Products test sets, which were never trained on. The fuzzy baseline uses its own threshold from
   ml/results/matching_baseline.json (the one chosen on its own dev data). Precision is the headline.
3. Precision, recall and F1 come with 95% bootstrap intervals (own_eval.bootstrap, the S3-5 seed and resamples).
Writes ml/results/matcher_v1.json with the date, data counts, commit and threshold.
"""

from __future__ import annotations

import sys
from pathlib import Path as _Path

sys.path.insert(0, str(_Path(__file__).resolve().parents[1] / "scripts"))  # ml/scripts holds the shared code

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path

from checkpoints import latest_checkpoint
from common import MODEL_DIR, load, score
from match_baseline import SCORER_BY_NAME
from matcher_data import LABELLING_DIR, build_all
from matcher_threshold import TARGET_PRECISION, choose_threshold
from own_eval import LABELS_NOTE, RESAMPLES, SEED, _metrics, bootstrap, current_commit, read_gold
from pairsets import DEFAULT_RAW_DIR, Pair, load_wdc, split_by_family, wdc_subsets

BASELINE = Path(__file__).resolve().parents[1] / "results" / "matching_baseline.json"
OUT = Path(__file__).resolve().parents[1] / "results" / "matcher_v1.json"
WDC_SUBSETS = ("80cc20rnd000un", "50cc50rnd000un", "20cc80rnd000un")  # the seen-products test sets, one per level


def fuzzy_choice(dataset: str, subset_starts: str, text: str = "title") -> tuple[str, float]:
    """(scorer name, threshold) the baseline chose on its own dev data, from matching_baseline.json."""
    results = json.loads(BASELINE.read_text(encoding="utf-8"))["results"]
    for entry in results:
        if entry["dataset"] == dataset and entry["subset"].startswith(subset_starts) and entry["text"] == text:
            return str(entry["scorer"]), float(entry["threshold"])
    raise SystemExit(f"{BASELINE.name} has no {dataset} / {subset_starts} entry: run match_baseline.py first")


def _row(
    name: str, method: str, threshold: float, gold: list[bool], called: list[bool], note: str
) -> dict[str, object]:
    outcomes = list(zip(gold, called, strict=True))
    point = _metrics(outcomes)
    intervals = bootstrap(outcomes, RESAMPLES, SEED)
    tp = sum(g and c for g, c in outcomes)
    fp = sum(c and not g for g, c in outcomes)
    fn = sum(g and not c for g, c in outcomes)
    return {
        "dataset": name,
        "method": method,
        "threshold": round(threshold, 4),
        "pairs": len(outcomes),
        "true_matches": sum(gold),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": len(outcomes) - tp - fp - fn,
        **{m: None if point[m] is None else round(float(point[m] or 0.0), 4) for m in point},
        "intervals_95": {
            m: [None if i.low is None else round(i.low, 4), None if i.high is None else round(i.high, 4), i.undefined]
            for m, i in intervals.items()
        },
        "note": note,
    }


def _fuzzy_called(pairs: list[Pair], scorer: str, threshold: float) -> list[bool]:
    return [s >= threshold for s in SCORER_BY_NAME[scorer].score_pairs(pairs)]


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate the matcher against the fuzzy baseline.")
    parser.add_argument("--model", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    started = time.monotonic()
    folder = args.model
    if folder is None:
        found = latest_checkpoint(MODEL_DIR)
        if found is None:
            raise SystemExit(f"no finished checkpoint in {MODEL_DIR}: run train.py first")
        folder = found[1]
    model, tokenizer = load(folder)

    data = build_all(DEFAULT_RAW_DIR, LABELLING_DIR, SEED)
    dev_scores = score(model, tokenizer, [(p.left, p.right) for p in data.dev])
    choice = choose_threshold(dev_scores, [p.label for p in data.dev])
    print(
        f"dev: {len(data.dev)} pairs; threshold {choice.threshold:.4f}, precision {choice.precision:.3f}, "
        f"recall {choice.recall:.3f}, target {TARGET_PRECISION} met: {choice.met}"
    )

    rows: list[dict[str, object]] = []
    gold = read_gold(LABELLING_DIR)
    own_test = split_by_family(gold.pairs, lambda p: p.families).test
    own_pairs = [p.as_pair("test") for p in own_test]
    probs = score(model, tokenizer, [(p.left_text, p.right_text) for p in own_pairs])
    labels = [p.label for p in own_pairs]
    rows.append(
        _row("own_pairs", "matcher", choice.threshold, labels, [s >= choice.threshold for s in probs], LABELS_NOTE)
    )
    scorer, threshold = fuzzy_choice("own_pairs", "family split, threshold chosen on dev")
    rows.append(
        _row(
            "own_pairs", f"fuzzy {scorer}", threshold, labels, _fuzzy_called(own_pairs, scorer, threshold), LABELS_NOTE
        )
    )

    for subset in wdc_subsets(DEFAULT_RAW_DIR):
        if subset.name not in WDC_SUBSETS:
            continue
        pairs = load_wdc(subset.test_file, "test")
        probs = score(model, tokenizer, [(p.left_text, p.right_text) for p in pairs])
        labels = [p.label for p in pairs]
        note = "WDC Products test set, never trained on (D-54)"
        rows.append(
            _row(
                f"wdc_products {subset.name}",
                "matcher",
                choice.threshold,
                labels,
                [s >= choice.threshold for s in probs],
                note,
            )
        )
        scorer, threshold = fuzzy_choice("wdc_products", subset.name)
        rows.append(
            _row(
                f"wdc_products {subset.name}",
                f"fuzzy {scorer}",
                threshold,
                labels,
                _fuzzy_called(pairs, scorer, threshold),
                note,
            )
        )

    result = {
        "date": datetime.now(UTC).date().isoformat(),
        "commit": current_commit(),
        "model": folder.relative_to(MODEL_DIR.parent.parent.parent).as_posix()
        if folder.is_relative_to(MODEL_DIR.parent.parent.parent)
        else folder.name,
        "threshold": {
            "value": round(choice.threshold, 4),
            "dev_precision": round(choice.precision, 4),
            "dev_recall": round(choice.recall, 4),
            "target_precision": TARGET_PRECISION,
            "target_met_on_dev": choice.met,
            "rule": "precision >= 0.95 on the dev part, with the highest recall at that precision",
        },
        "data": data.counts,
        "dev_pairs": len(data.dev),
        "bootstrap": {"resamples": RESAMPLES, "seed": SEED, "interval": "95% percentile"},
        "own_pairs_labels": LABELS_NOTE,
        "results": rows,
        "seconds": round(time.monotonic() - started, 1),
    }
    args.out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    for row in rows:
        print(
            f"{row['dataset']:<34} {row['method']:<26} thr {row['threshold']:<6} "
            f"P {row['precision']} R {row['recall']} "
            f"F1 {row['f1']}  (n={row['pairs']}, matches={row['true_matches']})"
        )
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
