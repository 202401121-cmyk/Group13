"""Fuzzy title-matching baseline on every labelled pair set (ML-0, #13): the numbers later matchers must beat.

Usage (from the repository root, after pip install -r ml/requirements.txt):
  python ml/scripts/match_baseline.py own A.csv [B.csv]
  python ml/scripts/match_baseline.py wdc --level 80          (or 50, 20, all)
  python ml/scripts/match_baseline.py abt_buy
  python ml/scripts/match_baseline.py amazon_google
  python ml/scripts/match_baseline.py all [--own A.csv [B.csv]] --out ml/results/matching_baseline.json
Every command also takes --raw-dir DIR (default ml/data/raw) and --out FILE (the same numbers as JSON).

Three rapidfuzz scorers (ratio, token_sort_ratio, token_set_ratio) on normalised text. On each dev split the scorer
and threshold with the best F1 are chosen, and the test split is reported at that choice. Exit codes: 0 done,
2 a raw file is missing (nothing is ever downloaded), 1 anything else.
Everything runs on this machine. No model service is called, so WDC data never leaves it (ml/DATA.md section 7).
"""

from __future__ import annotations

import argparse
import functools
import json
import platform
import sys
import time
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn

import numpy as np
import rapidfuzz
from check_labels import use_utf8_stdout
from match_metrics import (
    THRESHOLDS,
    Choice,
    Evaluation,
    Sweep,
    choose_on_dev,
    evaluate,
    evaluate_at,
    show,
    sweep_cross,
    sweep_pairs,
)
from numpy.typing import NDArray
from pairsets import (
    DEFAULT_RAW_DIR,
    DOWNLOAD_HINT,
    LEIPZIG,
    WDC_LEVELS,
    CrossPairSet,
    MissingData,
    Pair,
    PairScorer,
    PairSetError,
    Split,
    ensure_present,
    leipzig_split,
    load_leipzig,
    load_own,
    load_wdc,
    required_files,
    wdc_subsets,
)
from rapidfuzz import fuzz, process, utils

SYNTHETIC_WARNING = "SYNTHETIC EXAMPLE: these numbers mean nothing"
OWN_THRESHOLD_SUBSET = "80cc20rnd000un"  # our pairs have no dev split yet, so they use the choice made here
WDC_RULE = "test = the subset's _gs file; dev = the validation set of the same corner-case level and unseen share"
LEIPZIG_RULE = (
    "every left record against every right record; left records split by the parity of the first byte of "
    "sha256(id): even = dev, odd = test"
)
OWN_RULE = (
    "every pair is test (no dev split yet); scored at the scorer and threshold chosen on WDC Products 80% "
    f"({OWN_THRESHOLD_SUBSET}, title, validation set)"
)


@functools.cache
def normalise(text: str) -> str:
    """Unicode NFKC, then rapidfuzz's default_process: lower case, non-alphanumerics to spaces, trimmed."""
    return utils.default_process(unicodedata.normalize("NFKC", text))


@dataclass(frozen=True)
class FuzzyScorer:
    """A PairScorer built on one rapidfuzz scorer, divided by 100 so scores run from 0 to 1."""

    name: str
    func: Callable[..., float]  # a rapidfuzz.fuzz scorer: (text, text) -> 0 to 100

    def score_pairs(self, pairs: Sequence[Pair]) -> list[float]:
        return [self.func(normalise(pair.left_text), normalise(pair.right_text)) / 100 for pair in pairs]

    def score_matrix(self, left: Sequence[str], right: Sequence[str]) -> NDArray[np.float64]:
        """Every left text against every right text at once. float64 gives exactly what score_pairs gives."""
        scores = process.cdist(
            [normalise(text) for text in left],
            [normalise(text) for text in right],
            scorer=self.func,
            dtype=np.float64,
            workers=-1,
        )
        return np.asarray(scores, dtype=np.float64) / 100


SCORERS: tuple[FuzzyScorer, ...] = (
    FuzzyScorer("ratio", fuzz.ratio),
    FuzzyScorer("token_sort_ratio", fuzz.token_sort_ratio),
    FuzzyScorer("token_set_ratio", fuzz.token_set_ratio),
)
SCORER_BY_NAME = {scorer.name: scorer for scorer in SCORERS}
_THRESHOLD_ARRAY = np.asarray(THRESHOLDS, dtype=np.float64)


def sweep_matrix(scores: NDArray[np.float64], truth: NDArray[np.bool_]) -> Sweep:
    """Count a score matrix the way Sweep.from_scores counts a list: a match when score >= threshold."""
    if not bool(np.all((scores >= 0.0) & (scores <= 1.0))):  # also rejects NaN
        raise ValueError("scores must be from 0 to 1")
    buckets = np.searchsorted(_THRESHOLD_ARRAY, scores, side="right") - 1
    positives = np.bincount(buckets[truth], minlength=len(THRESHOLDS))
    negatives = np.bincount(buckets[~truth], minlength=len(THRESHOLDS))
    return Sweep.from_histograms([int(n) for n in positives], [int(n) for n in negatives])


class CrossScoring:
    """Scores an all-pairs set once per scorer. The dev and test sweeps then come from rows of the same matrix."""

    def __init__(self, cross: CrossPairSet) -> None:
        self.cross = cross
        self.dev_rows = np.array([leipzig_split(record.id) == "dev" for record in cross.left], dtype=np.bool_)
        row = {record.id: i for i, record in enumerate(cross.left)}
        column = {record.id: j for j, record in enumerate(cross.right)}
        self.truth = np.zeros((len(cross.left), len(cross.right)), dtype=np.bool_)
        for left_id, right_id in cross.matches:
            self.truth[row[left_id], column[right_id]] = True
        self._matrices: dict[str, NDArray[np.float64]] = {}

    def sweep(self, scorer: PairScorer, split: Split) -> Sweep:
        if not isinstance(scorer, FuzzyScorer):
            return sweep_cross(scorer, self.cross, split)  # any other matcher takes the general path
        if scorer.name not in self._matrices:
            left = [record.text for record in self.cross.left]
            right = [record.text for record in self.cross.right]
            self._matrices[scorer.name] = scorer.score_matrix(left, right)
        rows = self.dev_rows if split == "dev" else ~self.dev_rows
        return sweep_matrix(self._matrices[scorer.name][rows], self.truth[rows])


@dataclass(frozen=True)
class Result:
    dataset: str
    subset: str
    text: str
    split_rule: str
    dev_source: str
    evaluation: Evaluation
    seconds: float
    synthetic: bool = False
    notes: tuple[str, ...] = ()


def _on(pairs: Sequence[Pair]) -> Callable[[PairScorer], Sweep]:
    return lambda scorer: sweep_pairs(scorer, pairs)


def run_wdc(raw_dir: Path, levels: Sequence[int]) -> list[Result]:
    """Up to nine test sets (three levels by three unseen shares). Brand + title gets a row only if it helps on dev."""
    results = []
    for subset in wdc_subsets(raw_dir, levels):
        start = time.perf_counter()
        title = evaluate(SCORERS, _on(load_wdc(subset.dev_file, "dev")), _on(load_wdc(subset.test_file, "test")))
        if title is None:
            raise PairSetError(f"WDC {subset.name}: no threshold gives a defined F1 on the dev split")
        dev_name = subset.dev_file.name
        results.append(Result("wdc_products", subset.name, "title", WDC_RULE, dev_name, title, _since(start)))
        start = time.perf_counter()
        brand = choose_on_dev(SCORERS, _on(load_wdc(subset.dev_file, "dev", with_brand=True)))
        if brand is not None and brand.dev_f1 > title.choice.dev_f1:
            test = load_wdc(subset.test_file, "test", with_brand=True)
            scored = evaluate_at(brand, sweep_pairs(SCORER_BY_NAME[brand.scorer], test))
            note = f"brand + title beat title alone on dev (F1 {brand.dev_f1:.3f} vs {title.choice.dev_f1:.3f})"
            results.append(
                Result(
                    "wdc_products",
                    subset.name,
                    "brand + title",
                    WDC_RULE,
                    dev_name,
                    scored,
                    _since(start),
                    False,
                    (note,),
                )
            )
        print(f"  done: wdc_products {subset.name}", flush=True)
    return results


def run_leipzig(raw_dir: Path, name: str) -> Result:
    start = time.perf_counter()
    cross = load_leipzig(raw_dir, name)
    scoring = CrossScoring(cross)
    result = evaluate(SCORERS, lambda s: scoring.sweep(s, "dev"), lambda s: scoring.sweep(s, "test"))
    if result is None:
        raise PairSetError(f"{name}: no threshold gives a defined F1 on the dev split")
    spec = LEIPZIG[name]
    dev_left, test_left = len(cross.left_in("dev")), len(cross.left_in("test"))
    notes = (
        f"dev: {dev_left} left records x {len(cross.right)} right = {cross.pair_count('dev'):,} pairs",
        f"test: {test_left} left records x {len(cross.right)} right = {cross.pair_count('test'):,} pairs",
    )
    dev_source = f"dev split ({cross.pair_count('dev'):,} pairs)"
    text = f"{spec.left_text} / {spec.right_text}"
    print(f"  done: {name}", flush=True)
    return Result(name, "all pairs", text, LEIPZIG_RULE, dev_source, result, _since(start), notes=notes)


def wdc80_choice(raw_dir: Path) -> Choice:
    subset = next(s for s in wdc_subsets(raw_dir, [80]) if s.name == OWN_THRESHOLD_SUBSET)
    choice = choose_on_dev(SCORERS, _on(load_wdc(subset.dev_file, "dev")))
    if choice is None:
        raise PairSetError(f"WDC {subset.name}: no threshold gives a defined F1 on the dev split")
    return choice


def run_own(paths: Sequence[Path], raw_dir: Path, wdc80: Choice | None = None) -> Result:
    start = time.perf_counter()
    choice = wdc80 if wdc80 is not None else wdc80_choice(raw_dir)
    own = load_own(paths)
    scored = evaluate_at(choice, sweep_pairs(SCORER_BY_NAME[choice.scorer], own.pairs))
    dropped = f"{len(own.pairs)} pairs kept; dropped: {own.unsure_dropped} unsure"
    if len(paths) == 2:
        dropped += (
            f", {own.disagreements_dropped} the labellers disagree on, {own.only_in_one_file} in only one file, "
            f"{own.title_mismatches} with different titles in the two files"
        )
    subset = "+".join(Path(name).name for name in own.files)
    dev_source = f"WDC Products {OWN_THRESHOLD_SUBSET} validation set"
    return Result("own", subset, "title", OWN_RULE, dev_source, scored, _since(start), own.synthetic, (dropped,))


def _since(start: float) -> float:
    return time.perf_counter() - start


# --- Report and JSON ------------------------------------------------------------------------------------------------

HEADERS = (
    "dataset",
    "subset",
    "text",
    "scorer",
    "thr.",
    "dev F1",
    "pairs",
    "matches",
    "TP",
    "FP",
    "FN",
    "precision",
    "recall",
    "F1",
    "hard negatives called a match",
    "time",
)
_LEFT_ALIGNED = {"dataset", "subset", "text", "scorer", "hard negatives called a match"}


def _hard(evaluation: Evaluation) -> str:
    if evaluation.hard_negatives is None or evaluation.hard_called_match is None:
        return "n/a"
    share = evaluation.hard_share
    percent = "undefined" if share is None else f"{100 * share:.1f}%"
    return f"{evaluation.hard_called_match:,} of {evaluation.hard_negatives:,} ({percent})"


def _cells(result: Result) -> list[str]:
    e, c = result.evaluation, result.evaluation.test
    return [
        result.dataset,
        result.subset,
        result.text,
        e.choice.scorer,
        f"{e.choice.threshold:.2f}",
        f"{e.choice.dev_f1:.3f}",
        f"{e.test_pairs:,}",
        f"{c.tp + c.fn:,}",
        f"{c.tp:,}",
        f"{c.fp:,}",
        f"{c.fn:,}",
        show(c.precision),
        show(c.recall),
        show(c.f1),
        _hard(e),
        f"{result.seconds:.1f} s",
    ]


def report(results: Sequence[Result]) -> str:
    rows = [list(HEADERS)] + [_cells(result) for result in results]
    widths = [max(len(row[i]) for row in rows) for i in range(len(HEADERS))]

    def line(row: list[str]) -> str:
        return "  ".join(
            cell.ljust(width) if header in _LEFT_ALIGNED else cell.rjust(width)
            for cell, width, header in zip(row, widths, HEADERS, strict=True)
        ).rstrip()

    def mark(result: Result, text: str) -> str:
        return f"{SYNTHETIC_WARNING} | {text}" if result.synthetic else text

    lines = ["Fuzzy title baseline: test-split results (each scorer and threshold chosen on dev)", "", line(rows[0])]
    lines += [mark(result, line(row)) for result, row in zip(results, rows[1:], strict=True)]
    lines += ["", "Split rules and run times:"]
    for dataset in dict.fromkeys(result.dataset for result in results):
        group = [result for result in results if result.dataset == dataset]
        seconds = sum(result.seconds for result in group)
        lines.append(mark(group[0], f"- {dataset}: {group[0].split_rule}. Run time {seconds:.1f} s."))
        lines += [
            mark(result, f"  {result.subset} ({result.text}): {note}") for result in group for note in result.notes
        ]
    return "\n".join(lines)


def _number(value: float | None) -> float | None:
    return None if value is None else round(value, 6)


def _result_json(result: Result) -> dict[str, object]:
    e, c = result.evaluation, result.evaluation.test
    data: dict[str, object] = {
        "dataset": result.dataset,
        "subset": result.subset,
        "text": result.text,
        "split_rule": result.split_rule,
        "dev_source": result.dev_source,
        "synthetic": result.synthetic,
        "scorer": e.choice.scorer,
        "threshold": e.choice.threshold,
        "dev_f1": _number(e.choice.dev_f1),
        "test": {
            "pairs": e.test_pairs,
            "true_matches": c.tp + c.fn,
            "tp": c.tp,
            "fp": c.fp,
            "fn": c.fn,
            "tn": c.tn,
            "precision": _number(c.precision),
            "recall": _number(c.recall),
            "f1": _number(c.f1),
            "hard_negatives": e.hard_negatives,
            "hard_negatives_called_match": e.hard_called_match,
            "hard_negative_match_share": _number(e.hard_share),
        },
        "seconds": round(result.seconds, 2),
        "notes": list(result.notes),
    }
    if result.synthetic:
        data["warning"] = SYNTHETIC_WARNING
    return data


def to_json(results: Sequence[Result], created: datetime) -> dict[str, object]:
    return {
        "created": created.isoformat(timespec="seconds"),
        "tool": "ml/scripts/match_baseline.py",
        "rapidfuzz_version": rapidfuzz.__version__,
        "numpy_version": np.__version__,
        "python_version": platform.python_version(),
        "text_normalisation": "Unicode NFKC, then rapidfuzz.utils.default_process",
        "scorers": [f"rapidfuzz.fuzz.{scorer.name} / 100" for scorer in SCORERS],
        "threshold_rule": (
            "a pair is a match when score >= threshold; thresholds 0.00 to 1.00 in steps of 0.01; the scorer and "
            "threshold with the best dev F1 are chosen, ties go to the higher threshold; test is scored once"
        ),
        "undefined": "null means 0/0",
        "results": [_result_json(result) for result in results],
    }


# --- Command line ---------------------------------------------------------------------------------------------------


class _Parser(argparse.ArgumentParser):
    """Usage errors exit with 1, so that exit code 2 always means "a raw file is missing"."""

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        self.exit(1, f"{self.prog}: error: {message}\n")


def build_parser() -> _Parser:
    common = _Parser(add_help=False)
    common.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR, help="raw data folder (default ml/data/raw)")
    common.add_argument("--out", type=Path, help="also write the numbers as JSON to this file")
    parser = _Parser(prog="match_baseline.py", description="Fuzzy title-matching baseline (ML-0, #13).")
    commands = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)
    own = commands.add_parser("own", parents=[common], help="our labelled pairs: one file, or two labellers' files")
    own.add_argument("files", nargs="+", type=Path, metavar="FILE")
    wdc = commands.add_parser("wdc", parents=[common], help="WDC Products")
    wdc.add_argument("--level", choices=["80", "50", "20", "all"], default="all", help="share of corner cases")
    commands.add_parser("abt_buy", parents=[common], help="Abt-Buy, all pairs")
    commands.add_parser("amazon_google", parents=[common], help="Amazon-Google, all pairs")
    everything = commands.add_parser("all", parents=[common], help="WDC, Abt-Buy and Amazon-Google")
    everything.add_argument("--own", nargs="+", type=Path, metavar="FILE", help="also score these labelling files")
    return parser


def _levels(args: argparse.Namespace) -> tuple[int, ...]:
    return WDC_LEVELS if args.command == "all" or args.level == "all" else (int(args.level),)


def _required(args: argparse.Namespace, raw_dir: Path) -> list[Path]:
    if args.command == "own":
        return [next(s.dev_file for s in wdc_subsets(raw_dir, [80]) if s.name == OWN_THRESHOLD_SUBSET)]
    if args.command == "wdc":
        return required_files(raw_dir, "wdc_products", _levels(args))
    if args.command in LEIPZIG:
        return required_files(raw_dir, args.command)
    paths = required_files(raw_dir, "wdc_products", WDC_LEVELS)
    return paths + [path for name in LEIPZIG for path in required_files(raw_dir, name)]


def _run(args: argparse.Namespace, raw_dir: Path) -> list[Result]:
    if args.command == "own":
        return [run_own(args.files, raw_dir)]
    if args.command == "wdc":
        return run_wdc(raw_dir, _levels(args))
    if args.command in LEIPZIG:
        return [run_leipzig(raw_dir, args.command)]
    results = run_wdc(raw_dir, WDC_LEVELS)
    results += [run_leipzig(raw_dir, name) for name in LEIPZIG]
    if args.own:
        wdc80 = next(r for r in results if r.subset == OWN_THRESHOLD_SUBSET and r.text == "title")
        results.append(run_own(args.own, raw_dir, wdc80.evaluation.choice))
    return results


def main(argv: Sequence[str] | None = None) -> int:
    use_utf8_stdout()
    args = build_parser().parse_args(argv)
    raw_dir: Path = args.raw_dir
    try:
        ensure_present(_required(args, raw_dir))
        results = _run(args, raw_dir)
    except MissingData as missing:
        for path in missing.paths:
            print(f"Missing raw file: {path}")
        print(DOWNLOAD_HINT)
        return 2
    except PairSetError as error:
        print(f"error: {error}")
        return 1
    print()
    print(report(results))
    out: Path | None = args.out
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        data = to_json(results, datetime.now(UTC))
        out.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")
        print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
