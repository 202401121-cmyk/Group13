"""Cohen's kappa between two labellers who labelled the same product pairs (ML-0).

Usage: python ml/scripts/kappa.py A.csv B.csv [--min 0.8]

The agreement rule in ml/DATA.md, section 4: before anyone labels alone, two labellers label the same 50 pairs and
the four-class kappa must be 0.8 or more. Kappa corrects plain agreement for the agreement two people would reach
by chance: kappa = (p_o - p_e) / (1 - p_e). Both files go through check_labels.py first, so kappa is never computed
on rows that break the format. Standard library only.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction

from check_labels import LABELS, CheckResult, check_file, use_utf8_stdout

BINARY_LABELS = ("same", "not same")
LabelPair = tuple[str, str]  # (label from A, label from B) for one pair


def cohen_kappa(pairs: Sequence[LabelPair], classes: Sequence[str]) -> float | None:
    """Kappa for a list of (A, B) labels, or None when it is undefined.

    Fractions keep the sums exact, so "p_e == 1" is a true equality test, not a float comparison. p_e is 1 only
    when both labellers put every pair in the same one class; kappa is then 1.0 if they agree on every pair and
    undefined otherwise. No pairs at all is undefined too. Nothing here can divide by zero.
    """
    unknown = {label for pair in pairs for label in pair} - set(classes)
    if unknown:
        raise ValueError(f"labels outside the classes {list(classes)}: {sorted(unknown)}")
    n = len(pairs)
    if n == 0:
        return None
    p_o = Fraction(sum(1 for a, b in pairs if a == b), n)
    count_a = Counter(a for a, _ in pairs)
    count_b = Counter(b for _, b in pairs)
    p_e = sum((Fraction(count_a[c], n) * Fraction(count_b[c], n) for c in classes), Fraction(0))
    if p_e == 1:
        return 1.0 if p_o == 1 else None
    return float((p_o - p_e) / (1 - p_e))


def to_binary(pairs: Sequence[LabelPair]) -> tuple[list[LabelPair], int]:
    """The training view: same against (variant or different). Pairs unsure on either side are dropped.

    Returns the kept pairs and how many were dropped.
    """
    kept = [(_binary(a), _binary(b)) for a, b in pairs if "unsure" not in (a, b)]
    return kept, len(pairs) - len(kept)


def _binary(label: str) -> str:
    return "same" if label == "same" else "not same"


@dataclass(frozen=True)
class Comparison:
    """The pairs both files labelled, in file A's order, and what did not line up."""

    pair_ids: list[str]
    labels: list[LabelPair]
    only_in_a: list[str]
    only_in_b: list[str]
    title_mismatches: list[str]


def compare(a: CheckResult, b: CheckResult) -> Comparison:
    """Line up two checked files by pair_id."""
    by_id_b = {pair.pair_id: pair for pair in b.pairs}
    ids_a = {pair.pair_id for pair in a.pairs}
    shared = [pair for pair in a.pairs if pair.pair_id in by_id_b]
    mismatches = [
        pair.pair_id
        for pair in shared
        if (pair.left_title.strip(), pair.right_title.strip())
        != (by_id_b[pair.pair_id].left_title.strip(), by_id_b[pair.pair_id].right_title.strip())
    ]
    return Comparison(
        pair_ids=[pair.pair_id for pair in shared],
        labels=[(pair.label, by_id_b[pair.pair_id].label) for pair in shared],
        only_in_a=[pair.pair_id for pair in a.pairs if pair.pair_id not in by_id_b],
        only_in_b=[pair.pair_id for pair in b.pairs if pair.pair_id not in ids_a],
        title_mismatches=mismatches,
    )


def format_kappa(kappa: float | None) -> str:
    return "undefined" if kappa is None else f"{kappa:.3f}"


def report_lines(a: CheckResult, b: CheckResult, comparison: Comparison) -> list[str]:
    """Everything the agreement review needs: counts, both kappas, the confusion matrix and each disagreement."""
    labels = comparison.labels
    n = len(labels)
    agree = sum(1 for label_a, label_b in labels if label_a == label_b)
    binary, dropped = to_binary(labels)

    lines = [
        f"A: {a.path} (labeller: {_labellers(a)})",
        f"B: {b.path} (labeller: {_labellers(b)})",
        f"Pairs compared: {n} (only in A: {len(comparison.only_in_a)}, only in B: {len(comparison.only_in_b)})",
    ]
    if comparison.only_in_a:
        lines.append(f"  only in A: {', '.join(comparison.only_in_a)}")
    if comparison.only_in_b:
        lines.append(f"  only in B: {', '.join(comparison.only_in_b)}")
    if comparison.title_mismatches:
        lines.append(
            "Warning: these pair_ids have different titles in the two files, so the labellers did not see the same"
            f" pair: {', '.join(comparison.title_mismatches)}"
        )
    lines += [
        f"Agreement: {agree} of {n} pairs ({100 * agree / n:.1f}%)",
        f"Four-class kappa: {format_kappa(cohen_kappa(labels, LABELS))}",
        f"Binary kappa (same vs variant or different): {format_kappa(cohen_kappa(binary, BINARY_LABELS))}"
        f" ({dropped} pair{'' if dropped == 1 else 's'} dropped because a label is unsure)",
        "",
        "Confusion matrix (rows: A, columns: B)",
        "  " + " " * 10 + "".join(f"{label:>11}" for label in LABELS),
    ]
    counts = Counter(labels)
    for label_a in LABELS:
        lines.append(f"  {label_a:<10}" + "".join(f"{counts[(label_a, label_b)]:>11}" for label_b in LABELS))

    disagreements = [
        (pair_id, label_a, label_b)
        for pair_id, (label_a, label_b) in zip(comparison.pair_ids, labels, strict=True)
        if label_a != label_b
    ]
    lines += ["", f"Disagreements ({len(disagreements)}):"]
    lines += [f"  {pair_id}  A: {label_a:<10} B: {label_b}" for pair_id, label_a, label_b in disagreements]
    if not disagreements:
        lines.append("  none")
    return lines


def _labellers(result: CheckResult) -> str:
    return ", ".join(sorted({pair.labeller for pair in result.pairs})) or "none"


def _threshold(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a number") from None
    if not 0 <= value <= 1:
        raise argparse.ArgumentTypeError(f"{text} must be between 0 and 1")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    use_utf8_stdout()
    parser = argparse.ArgumentParser(
        prog="kappa.py", description="Cohen's kappa between two labellers who labelled the same pairs."
    )
    parser.add_argument("file_a", metavar="A.csv", help="the first labeller's file")
    parser.add_argument("file_b", metavar="B.csv", help="the second labeller's file")
    parser.add_argument(
        "--min", type=_threshold, metavar="KAPPA", help="exit with 1 if the four-class kappa is below this, e.g. 0.8"
    )
    args = parser.parse_args(argv)
    minimum: float | None = args.min

    a, b = check_file(args.file_a), check_file(args.file_b)
    if not (a.ok and b.ok):
        for error in a.errors + b.errors:
            print(error)
        print("\nFix the errors above (see ml/DATA.md, section 2), then run kappa.py again.")
        return 1

    comparison = compare(a, b)
    if not comparison.labels:
        print(f"No pair_id is in both {a.path} and {b.path}, so there is nothing to compare.")
        return 1
    for line in report_lines(a, b, comparison):
        print(line)

    if minimum is None:
        return 0
    kappa = cohen_kappa(comparison.labels, LABELS)
    print()
    if kappa is None:
        print(f"FAIL: the four-class kappa is undefined, so it cannot reach {minimum:.3f}.")
        return 1
    if kappa < minimum:
        print(f"FAIL: four-class kappa {kappa:.3f} is below {minimum:.3f}. Discuss the disagreements, update the guide")
        print("in ml/DATA.md and repeat with 50 new pairs.")
        return 1
    print(f"PASS: four-class kappa {kappa:.3f} is at least {minimum:.3f}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
