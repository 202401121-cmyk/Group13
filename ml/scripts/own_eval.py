"""L2: the fuzzy title baseline on our own labelled pairs (ML-1; US-15 groundwork; D-55, D-56, D-61).

Usage (from the repository root, after pip install -r ml/requirements.txt):
  python ml/scripts/own_eval.py                                            print the numbers
  python ml/scripts/own_eval.py --out ml/results/matching_baseline.json    and record them there

What it does, in order:
1. Gate: agree-1's four-class kappa must be 0.80 or more, and at least 150 pairs must be labelled. Both numbers, and
   the usable count, are printed either way.
2. Gold labels: every label file in ml/labelling is combined by pair_id. A pair counts only when its labels agree on
   same against not same; a pair with an unsure label, or whose texts differ between files, is dropped. A variant is
   never a match. A file whose labeller is proposed-draft, or any file under ml/data/interim/, is refused.
3. Split by product family (pairsets.split_by_family, which S3-4 uses too): no family is on both sides. The dev side
   picks the scorer and threshold; the test side is scored once, and again at the WDC 80% corner-case threshold.
4. Precision, recall and F1 on the test side, each with a 95% bootstrap interval (1,000 resamples, a fixed seed), and
   how often variants and different products are called a match, by stratum and by kind of pair.
5. --out merges two "own_pairs" entries into the results file and keeps every other entry as it is.

Our pairs carry machine labels (ml/DATA.md section 4.1); every result says so. Nothing is labelled, re-labelled or
invented here, and no service is called. Exit codes: 0 done, 1 refused or a file is wrong, 2 a file is missing.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from check_labels import LABELS, CheckResult, check_file, use_utf8_stdout
from kappa import cohen_kappa
from listings import LISTING_HEADER, read_table
from make_pairs import META_HEADER
from match_baseline import OWN_PAIRS_DATASET, OWN_THRESHOLD_SUBSET, SCORER_BY_NAME, SCORERS, Result, _result_json
from match_metrics import Choice, Counts, Evaluation, Sweep, bucket, choose_on_dev, evaluate_at, show, sweep_pairs
from pairsets import ML_DIR, FamilySplit, MissingData, Pair, PairSetError, Split, ensure_present, split_by_family

LABELLING_DIR = ML_DIR / "labelling"
INTERIM_DIR = ML_DIR / "data" / "interim"
RESULTS_FILE = ML_DIR / "results" / "matching_baseline.json"
AGREEMENT_BATCH = "agree-1"
KAPPA_GATE = 0.80
MIN_LABELLED = 150
RESAMPLES = 1000
SEED = 20261111  # fixed, so the intervals are the same on every run
DRAFT_LABELLER = "proposed-draft"
LABELS_NOTE = "machine labels (ml/DATA.md section 4.1)"
SMALL_NOTE = "the test side is small, so the intervals are wide: read them, not only the point values"
SPLIT_RULE = (
    "by product family: a family's side is the first byte of sha256(family), even = dev, odd = test "
    "(pairsets.family_side); a pair whose two families fall on different sides is left out; the dev side picks the "
    "scorer and threshold, and the test side is scored once"
)
METRICS = ("precision", "recall", "f1")
KINDS = ("same", "variant", "different", "mixed")

Kind = Literal["same", "variant", "different", "mixed"]
Rates = dict[str, dict[str, dict[str, float | int | None]]]


@dataclass(frozen=True)
class GoldPair:
    """One pair with a label both labellers (or the one labeller) agree on, and where it came from."""

    pair_id: str
    left: str
    right: str
    label: bool  # a match: both say "same"
    kind: Kind  # mixed: one said variant and another different; either way, not a match
    families: frozenset[str]
    stratum: str

    def as_pair(self, split: Split) -> Pair:
        return Pair(self.pair_id, self.left, self.right, self.label, OWN_PAIRS_DATASET, split, self.kind == "variant")


@dataclass(frozen=True)
class Gold:
    pairs: list[GoldPair]
    labelled: int  # distinct pairs with at least one label
    files: list[str]
    dropped: dict[str, int]


@dataclass(frozen=True)
class Interval:
    low: float | None
    high: float | None
    undefined: int  # resamples in which the metric was 0/0, left out of the interval


@dataclass(frozen=True)
class AtThreshold:
    evaluation: Evaluation
    intervals: dict[str, Interval]
    rates: Rates


@dataclass(frozen=True)
class OwnEvaluation:
    split: FamilySplit[GoldPair]
    at_dev_choice: AtThreshold
    at_wdc80: AtThreshold
    resamples: int
    seed: int


# --- reading the labels -------------------------------------------------------------------------------------------


def label_files(directory: Path) -> list[Path]:
    """Every <batch>.<labeller>.csv for a batch in directory/batches, in batch order."""
    batches = sorted(path.name.removesuffix(".pairs.csv") for path in (directory / "batches").glob("*.pairs.csv"))
    return [path for batch in batches for path in sorted(directory.glob(f"{batch}.*.csv"))]


def _refuse_interim(paths: Sequence[Path]) -> None:
    for path in paths:
        if path.resolve().is_relative_to(INTERIM_DIR.resolve()):
            raise PairSetError(f"{path}: a file under ml/data/interim/ is a draft, never a label: refused")


def _refuse_drafts(paths: Sequence[Path]) -> None:
    """Refused before a line is read: a file named for the proposed-draft labeller, or kept under ml/data/interim/."""
    _refuse_interim(paths)
    for path in paths:
        if path.name.casefold().endswith(f".{DRAFT_LABELLER}.csv"):
            raise PairSetError(f"{path}: its labeller is {DRAFT_LABELLER}, a proposed label, never a label: refused")


def _checked(paths: Sequence[Path]) -> list[CheckResult]:
    _refuse_drafts(paths)
    results = [check_file(path) for path in paths]
    errors = [error for result in results for error in result.errors]
    if errors:
        raise PairSetError("\n".join(errors))
    for path, result in zip(paths, results, strict=True):
        if any(row.labeller.strip().casefold() == DRAFT_LABELLER for row in result.pairs):
            raise PairSetError(f"{path}: its labeller is {DRAFT_LABELLER}, a proposed label, never a label: refused")
    return results


def _pair_facts(directory: Path) -> dict[str, tuple[frozenset[str], str]]:
    """pair_id -> (the families of its two listings, its stratum), from listings.csv and batches/pairs.meta.csv."""
    listings = read_table(directory / "listings.csv", LISTING_HEADER)
    meta = read_table(directory / "batches" / "pairs.meta.csv", META_HEADER)
    errors = listings.errors + meta.errors
    if errors:
        raise PairSetError("\n".join(errors))
    family = {values["listing_id"]: values["product_family"] for _, values in listings.rows}
    facts: dict[str, tuple[frozenset[str], str]] = {}
    for line, values in meta.rows:
        if values["pair_id"] in facts:
            continue  # a pair repeated in a later batch: the same two listings
        missing = [values[key] for key in ("left_listing", "right_listing") if values[key] not in family]
        if missing:
            raise PairSetError(f"{meta.path}:{line}: listing {missing[0]} is not in listings.csv")
        families = frozenset({family[values["left_listing"]], family[values["right_listing"]]})
        facts[values["pair_id"]] = (families, values["stratum"])
    return facts


def read_gold(directory: Path, files: Sequence[Path] | None = None) -> Gold:
    """Combine every label file by pair_id, keeping only the pairs whose labels agree (DECIDED 2)."""
    paths = list(files) if files is not None else label_files(directory)
    if not paths:
        raise PairSetError(f"no label files in {directory}")
    results = _checked(paths)
    by_id: dict[str, list[tuple[str, str, str]]] = {}
    for result in results:
        for row in result.pairs:
            by_id.setdefault(row.pair_id, []).append((row.left_title.strip(), row.right_title.strip(), row.label))
    facts = _pair_facts(directory)
    dropped = {"unsure": 0, "labellers disagree": 0, "texts differ": 0}
    pairs: list[GoldPair] = []
    for pair_id in sorted(by_id):
        rows = by_id[pair_id]
        labels = [label for _, _, label in rows]
        if len({(left, right) for left, right, _ in rows}) > 1:
            dropped["texts differ"] += 1
        elif "unsure" in labels:
            dropped["unsure"] += 1
        elif len({label == "same" for label in labels}) > 1:
            dropped["labellers disagree"] += 1
        else:
            if pair_id not in facts:
                raise PairSetError(f"pair {pair_id} is not in {directory / 'batches' / 'pairs.meta.csv'}")
            families, stratum = facts[pair_id]
            left, right, _ = rows[0]
            pairs.append(GoldPair(pair_id, left, right, labels[0] == "same", _kind(labels), families, stratum))
    return Gold(pairs, len(by_id), [str(path) for path in paths], dropped)


def _kind(labels: Sequence[str]) -> Kind:
    if labels[0] == "same":
        return "same"
    if all(label == "variant" for label in labels):
        return "variant"
    if all(label == "different" for label in labels):
        return "different"
    return "mixed"


def agreement_kappa(directory: Path) -> float | None:
    """agree-1's four-class kappa between its two labellers' files. None when there aren't exactly two."""
    paths = sorted(directory.glob(f"{AGREEMENT_BATCH}.*.csv"))
    if len(paths) != 2:
        return None
    first, second = _checked(paths)
    by_id = {row.pair_id: row.label for row in second.pairs}
    both = [(row.label, by_id[row.pair_id]) for row in first.pairs if row.pair_id in by_id]
    return cohen_kappa(both, LABELS) if both else None


def gate_problems(kappa: float | None, labelled: int) -> list[str]:
    """Why the evaluation may not run (DECIDED 1). Empty when it may."""
    problems = []
    if kappa is None:
        problems.append(f"{AGREEMENT_BATCH} has no four-class kappa: it needs two labellers' files of the same pairs")
    elif kappa < KAPPA_GATE:
        problems.append(f"{AGREEMENT_BATCH}'s four-class kappa is {kappa:.3f}, under {KAPPA_GATE:.2f}")
    if labelled < MIN_LABELLED:
        problems.append(f"{labelled} labelled pairs, under {MIN_LABELLED}")
    return problems


# --- the numbers -------------------------------------------------------------------------------------------------


def _metrics(outcomes: Sequence[tuple[bool, bool]]) -> dict[str, float | None]:
    tp = sum(gold and called for gold, called in outcomes)
    fp = sum(called and not gold for gold, called in outcomes)
    fn = sum(gold and not called for gold, called in outcomes)
    counts = Counts(tp=tp, fp=fp, fn=fn, tn=len(outcomes) - tp - fp - fn)
    return {"precision": counts.precision, "recall": counts.recall, "f1": counts.f1}


def _quantile(values: Sequence[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = q * (len(ordered) - 1)
    low, high = math.floor(position), math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def bootstrap(
    outcomes: Sequence[tuple[bool, bool]], resamples: int = RESAMPLES, seed: int = SEED
) -> dict[str, Interval]:
    """95% percentile intervals for precision, recall and F1, from resampling the (gold, called) pairs."""
    values: dict[str, list[float]] = {metric: [] for metric in METRICS}
    undefined: dict[str, int] = dict.fromkeys(METRICS, 0)
    rng = random.Random(seed)
    for _ in range(resamples if outcomes else 0):
        sample = [outcomes[rng.randrange(len(outcomes))] for _ in outcomes]
        for metric, value in _metrics(sample).items():
            if value is None:
                undefined[metric] += 1
            else:
                values[metric].append(value)
    if not outcomes:
        undefined = dict.fromkeys(METRICS, resamples)
    return {
        metric: Interval(_quantile(values[metric], 0.025), _quantile(values[metric], 0.975), undefined[metric])
        for metric in METRICS
    }


def called_match_rates(items: Sequence[GoldPair], called: Sequence[bool]) -> Rates:
    """How often each kind of pair is called a match, over all and by stratum (DECIDED 4)."""
    by_kind: dict[str, dict[str, float | int | None]] = {}
    for kind in KINDS:
        picked = [hit for item, hit in zip(items, called, strict=True) if item.kind == kind]
        if picked:
            by_kind[kind] = {
                "pairs": len(picked),
                "called_match": sum(picked),
                "share": round(sum(picked) / len(picked), 6),
            }
    by_stratum: dict[str, dict[str, float | int | None]] = {}
    for item, hit in zip(items, called, strict=True):
        row = by_stratum.setdefault(item.stratum, {"pairs": 0})
        row["pairs"] = int(row["pairs"] or 0) + 1
        row[item.kind] = int(row.get(item.kind) or 0) + 1
        key = f"{item.kind}_called_match"
        row[key] = int(row.get(key) or 0) + hit
    for row in by_stratum.values():
        for kind in KINDS:
            if row.get(kind):
                row[f"{kind}_called_match_share"] = round(
                    int(row[f"{kind}_called_match"] or 0) / int(row[kind] or 1), 6
                )
    return {"by_kind": by_kind, "by_stratum": dict(sorted(by_stratum.items()))}


def _at(choice: Choice, items: Sequence[GoldPair], resamples: int, seed: int) -> AtThreshold:
    pairs = [item.as_pair("test") for item in items]
    scores = SCORER_BY_NAME[choice.scorer].score_pairs(pairs)
    called = [bucket(score) >= choice.k for score in scores]
    sweep = Sweep.from_scores(scores, [pair.label for pair in pairs], [pair.is_hard_negative for pair in pairs])
    outcomes = [(item.label, hit) for item, hit in zip(items, called, strict=True)]
    return AtThreshold(
        evaluate_at(choice, sweep), bootstrap(outcomes, resamples, seed), called_match_rates(items, called)
    )


def evaluate_own(gold: Gold, wdc80: Choice, resamples: int = RESAMPLES, seed: int = SEED) -> OwnEvaluation:
    """Split by family, choose on dev, score test at that choice and at the WDC choice (DECIDED 3 and 4)."""
    split = split_by_family(gold.pairs, lambda item: item.families)
    if not split.dev or not split.test:
        raise PairSetError("the split by family left one side empty")
    dev_pairs = [item.as_pair("dev") for item in split.dev]
    choice = choose_on_dev(SCORERS, lambda scorer: sweep_pairs(scorer, dev_pairs))
    if choice is None:
        raise PairSetError("no threshold gives a defined F1 on the dev side")
    return OwnEvaluation(
        split, _at(choice, split.test, resamples, seed), _at(wdc80, split.test, resamples, seed), resamples, seed
    )


def wdc80_choice(results_file: Path) -> Choice:
    """The scorer and threshold chosen on WDC Products 80% corner cases (title), from the recorded results (D-56)."""
    try:
        data = json.loads(results_file.read_text(encoding="utf-8"))
        entry = next(
            row
            for row in data["results"]
            if row.get("dataset") == "wdc_products"
            and row.get("subset") == OWN_THRESHOLD_SUBSET
            and row.get("text") == "title"
        )
        return Choice(str(entry["scorer"]), round(float(entry["threshold"]) * 100), float(entry["dev_f1"]))
    except (OSError, ValueError, KeyError, TypeError, StopIteration) as error:
        raise PairSetError(f"{results_file}: no WDC {OWN_THRESHOLD_SUBSET} title result to read ({error!r})") from error


# --- the record --------------------------------------------------------------------------------------------------


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 6)


def _interval_json(interval: Interval) -> dict[str, float | int | None]:
    return {"low": _rounded(interval.low), "high": _rounded(interval.high), "undefined_resamples": interval.undefined}


def json_entries(
    evaluation: OwnEvaluation, gold: Gold, *, kappa: float | None, commit: str, date: str, seconds: float = 0.0
) -> list[dict[str, object]]:
    """The two own_pairs entries of the results file: at the dev choice, and at the WDC 80% choice (DECIDED 5)."""
    split = evaluation.split
    common: dict[str, object] = {
        "labels": LABELS_NOTE,
        "date": date,
        "commit": commit,
        "gate": {
            "agree1_four_class_kappa": None if kappa is None else round(kappa, 3),
            "kappa_needed": KAPPA_GATE,
            "labelled_needed": MIN_LABELLED,
        },
        "pairs": {
            "labelled": gold.labelled,
            "usable": len(gold.pairs),
            "dropped": dict(gold.dropped),
            "dev": len(split.dev),
            "test": len(split.test),
            "left_out_across_the_split": len(split.straddling),
            "dev_families": len(split.dev_families),
            "test_families": len(split.test_families),
        },
        "bootstrap": {"resamples": evaluation.resamples, "seed": evaluation.seed, "interval": "95% percentile"},
        "intervals_note": SMALL_NOTE,
    }
    entries = []
    for at, subset, dev_source in (
        (evaluation.at_dev_choice, "family split, threshold chosen on dev", "the dev side of our own pairs"),
        (
            evaluation.at_wdc80,
            f"family split, at the WDC {OWN_THRESHOLD_SUBSET} threshold",
            f"WDC Products {OWN_THRESHOLD_SUBSET} validation set (D-56)",
        ),
    ):
        notes = (LABELS_NOTE, SMALL_NOTE)
        result = Result(
            OWN_PAIRS_DATASET, subset, "title", SPLIT_RULE, dev_source, at.evaluation, seconds, False, notes
        )
        intervals = {metric: _interval_json(interval) for metric, interval in at.intervals.items()}
        entries.append({**_result_json(result), **common, "intervals": intervals, "called_match_rates": at.rates})
    return entries


def merge_results(path: Path, entries: Sequence[dict[str, object]]) -> dict[str, object]:
    """Replace the own_pairs entries of the results file with these, keeping every other entry as it is."""
    data: dict[str, object] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"results": []}
    earlier = data.get("results")
    kept = [row for row in earlier if row.get("dataset") != OWN_PAIRS_DATASET] if isinstance(earlier, list) else []
    data["results"] = [*kept, *entries]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")
    return data


def current_commit() -> str:
    """The commit the labels were read at, marked when ml/labelling has changes that are not committed."""
    try:
        head = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ML_DIR, capture_output=True, text=True, timeout=10, check=True
        ).stdout.strip()
        changes = subprocess.run(
            ["git", "status", "--porcelain", "--", str(LABELLING_DIR)],
            cwd=ML_DIR,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return f"{head} (with label changes not committed)" if changes else head


# --- the report --------------------------------------------------------------------------------------------------


def _with_interval(value: float | None, interval: Interval) -> str:
    return f"{show(value)} [{show(interval.low)}, {show(interval.high)}]"


def _threshold_lines(title: str, at: AtThreshold) -> list[str]:
    c, choice = at.evaluation.test, at.evaluation.choice
    lines = [
        f"{title}: {choice.scorer} at {choice.threshold:.2f}",
        f"  precision {_with_interval(c.precision, at.intervals['precision'])}",
        f"  recall    {_with_interval(c.recall, at.intervals['recall'])}",
        f"  F1        {_with_interval(c.f1, at.intervals['f1'])}",
        f"  TP {c.tp}, FP {c.fp}, FN {c.fn}, TN {c.tn}",
        "  called a match, by kind: "
        + "; ".join(f"{kind} {row['called_match']} of {row['pairs']}" for kind, row in at.rates["by_kind"].items()),
    ]
    for stratum, row in at.rates["by_stratum"].items():
        parts = [f"{kind} {row.get(f'{kind}_called_match', 0)} of {row[kind]}" for kind in KINDS if row.get(kind)]
        lines.append(f"    {stratum} ({row['pairs']} pairs): " + "; ".join(parts))
    return lines


def report(evaluation: OwnEvaluation, gold: Gold) -> str:
    split, dev = evaluation.split, evaluation.at_dev_choice.evaluation.choice
    test = evaluation.at_dev_choice.evaluation
    dropped = gold.dropped
    lines = [
        f"Dropped: {dropped['unsure']} with an unsure label, {dropped['labellers disagree']} the labellers "
        f"disagree on, {dropped['texts differ']} whose texts differ between files",
        f"Split by product family: dev {len(split.dev)} pairs in {len(split.dev_families)} families; test "
        f"{len(split.test)} pairs in {len(split.test_families)} families; {len(split.straddling)} pairs left out, "
        "as their two families fall on different sides",
        f"Chosen on dev: {dev.scorer} at {dev.threshold:.2f} (dev F1 {show(dev.dev_f1)})",
        "",
        f"Test side: {test.test_pairs} pairs, {test.test.tp + test.test.fn} true matches. 95% bootstrap intervals "
        f"({evaluation.resamples} resamples, seed {evaluation.seed}); {SMALL_NOTE}.",
        "",
        *_threshold_lines("At the threshold chosen on dev", evaluation.at_dev_choice),
        "",
        *_threshold_lines(f"At the WDC {OWN_THRESHOLD_SUBSET} threshold (D-56)", evaluation.at_wdc80),
    ]
    return "\n".join(lines)


# --- command line ------------------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="own_eval.py", description="The fuzzy baseline on our own pairs (L2).")
    parser.add_argument("--labels", type=Path, default=LABELLING_DIR, help="labelling folder (default ml/labelling)")
    parser.add_argument("--results", type=Path, default=RESULTS_FILE, help="where the WDC choice is read from")
    parser.add_argument("--out", type=Path, help="merge the two own_pairs entries into this results file")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    use_utf8_stdout()
    args = build_parser().parse_args(argv)
    directory: Path = args.labels
    print(f"Our own labelled pairs in {directory}: the fuzzy title baseline. These are {LABELS_NOTE}.")
    try:
        _refuse_interim([directory])
        ensure_present([directory / "listings.csv", directory / "batches" / "pairs.meta.csv"])
        kappa = agreement_kappa(directory)
        gold = read_gold(directory)
    except MissingData as missing:
        for path in missing.paths:
            print(f"Missing file: {path}")
        return 2
    except PairSetError as error:
        print(f"error: {error}")
        return 1
    print(
        f"Gate: {AGREEMENT_BATCH} four-class kappa {show(kappa)} (needs {KAPPA_GATE:.2f}); labelled pairs "
        f"{gold.labelled} (needs {MIN_LABELLED}); usable pairs {len(gold.pairs)}"
    )
    problems = gate_problems(kappa, gold.labelled)
    if problems:
        print("Refused: " + "; ".join(problems))
        return 1
    started = time.perf_counter()
    try:
        evaluation = evaluate_own(gold, wdc80_choice(args.results))
    except PairSetError as error:
        print(f"error: {error}")
        return 1
    seconds = time.perf_counter() - started
    print(report(evaluation, gold))
    out: Path | None = args.out
    if out is not None:
        date = datetime.now(UTC).date().isoformat()
        entries = json_entries(evaluation, gold, kappa=kappa, commit=current_commit(), date=date, seconds=seconds)
        merge_results(out, entries)
        print(f"\nWrote the own_pairs entries to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
