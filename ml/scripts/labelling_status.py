"""Where labelling stands, and what to do next (ML-1, D-55).

Usage: python ml/scripts/labelling_status.py [ml/labelling]

Reads the batches in <dir>/batches/ and the labellers' files <dir>/<batch>.<name>.csv, then prints:
- each batch, with how many of its pairs each labeller has labelled;
- labelled rows and distinct pairs, against the targets (150 by the Sprint 2 review, 250 in Sprint 3);
- the label mix;
- for each batch everyone on it has finished: agreement between every two of its labellers, four-class and binary
  Cohen's kappa (from kappa.py) and every disagreement. An unfinished batch shows only counts, so nobody sees the
  other person's answers while they can still change their own;
- repeated pairs (overlap): the same pair labelled again in a later batch, by another person or by the same one;
- the next step: label agree-N; discuss the disagreements and make a new agreement batch; or, once every labeller
  of the latest 50-pair agreement batch agrees with every other at a four-class kappa of 0.80 or more, start or
  carry on the solo batches.

It never reads pairs.meta.csv. A broken labeller file, or a row whose titles differ from its batch, is reported as
FILE:ROW and left out; the rest of the report still runs, and the exit code is 1. Standard library only.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path

from check_labels import LABELS, check_file, use_utf8_stdout
from kappa import BINARY_LABELS, cohen_kappa, format_kappa, to_binary
from listings import read_table
from make_pairs import BATCH_HEADER

AGREEMENT_GATE = 0.80
AGREEMENT_BATCH_SIZE = 50
TARGETS = "target: 150 by the Sprint 2 review, 250 in Sprint 3"
AGREE_SEED = 101
SOLO_SEED = 200  # solo-N uses seed 200 + N
DEFAULT_DIR = Path(__file__).resolve().parents[1] / "labelling"
BATCH_NUMBER = re.compile(r"(agree|solo)-([0-9]+)")


@dataclass
class Batch:
    name: str
    titles: dict[str, tuple[str, str]]  # pair_id -> (left, right), in the batch file's order
    labels: dict[str, dict[str, str]] = field(default_factory=dict)  # labeller -> pair_id -> label

    @property
    def pair_ids(self) -> list[str]:
        return list(self.titles)

    @property
    def size(self) -> int:
        return len(self.titles)

    def left_for(self, labeller: str) -> int:
        return sum(1 for pair_id in self.titles if pair_id not in self.labels.get(labeller, {}))

    @property
    def finished(self) -> bool:
        """Everyone with a file for this batch has labelled all of it (a batch with no readable pairs never is).

        An agreement batch also needs two labellers: the second one has no file until their first answer, and the
        first one's labels must not show (even as a label mix) before the second has labelled.
        """
        if self.name.startswith("agree-") and len(self.labels) < 2:
            return False
        return bool(self.titles) and bool(self.labels) and all(self.left_for(name) == 0 for name in self.labels)

    @property
    def made_after(self) -> int:
        """The highest pair id in the batch: new ids grow across batches, so this orders batches as they were made."""
        return max((int(pair_id[1:]) for pair_id in self.titles if pair_id[1:].isdigit()), default=10**9)


def status_report(directory: Path) -> tuple[list[str], int]:
    """The report's lines, and the exit code: 0, or 1 when a file could not be read cleanly."""
    errors: list[str] = []
    batches = _read_batches(directory, errors)
    lines = [f"Labelling status for {directory}", ""]
    if not batches:
        lines += [*errors, "No batches yet.", "", _next_step({}, errors)]
        return lines, 1 if errors else 0
    for batch in batches.values():
        _read_labels(directory, batch, errors)

    lines.append("Batches (pairs labelled per labeller):")
    for batch in batches.values():
        done = "  ".join(f"{name} {batch.size - batch.left_for(name)}/{batch.size}" for name in sorted(batch.labels))
        lines.append(f"  {batch.name}  {batch.size} pairs  {done or 'nobody has started'}")
    rows = [
        (labeller, pair_id, label)
        for batch in batches.values()
        for labeller, labels in batch.labels.items()
        for pair_id, label in labels.items()
    ]
    labellers = sorted({labeller for labeller, _, _ in rows})
    lines += [
        "",
        f"Labelled: {len(rows)} rows by {len(labellers)} labeller{'' if len(labellers) == 1 else 's'},"
        f" {len({pair_id for _, pair_id, _ in rows})} distinct pairs ({TARGETS})",
    ]
    # Only finished batches: counts from a batch still being labelled would hint at the other person's answers.
    mix = Counter(
        label
        for batch in batches.values()
        if batch.finished
        for labels in batch.labels.values()
        for label in labels.values()
    )
    lines.append("Label mix (finished batches): " + ", ".join(f"{label} {mix[label]}" for label in LABELS))
    lines += ["", *_within_batch_lines(batches), "", *_repeat_lines(batches)]
    if errors:
        lines += ["", "These files have errors; the rows with errors are left out above:", *errors]
    lines += ["", _next_step(batches, errors)]
    return lines, 1 if errors else 0


def _read_batches(directory: Path, errors: list[str]) -> dict[str, Batch]:
    """Every batch, in the order it was made: by its highest pair id, then by name."""
    found: list[Batch] = []
    for path in sorted((directory / "batches").glob("*.pairs.csv"), key=_batch_sort_key):
        table = read_table(path, BATCH_HEADER)
        errors.extend(table.errors)  # the caller's list: a damaged batch is reported with the rest
        titles = {values["pair_id"]: (values["left_text"], values["right_text"]) for _, values in table.rows}
        found.append(Batch(path.name.removesuffix(".pairs.csv"), titles))
    return {batch.name: batch for batch in sorted(found, key=lambda batch: batch.made_after)}


def _read_labels(directory: Path, batch: Batch, errors: list[str]) -> None:
    """Each labeller's rows for this batch. A row for a pair the batch lacks, or with other titles, is left out."""
    for path in sorted(directory.glob(f"{batch.name}.*.csv")):
        labeller = path.name.removesuffix(".csv").split(".", 1)[1]
        result = check_file(path)
        errors.extend(result.errors)
        kept: dict[str, str] = {}
        for pair in result.pairs:
            titles = batch.titles.get(pair.pair_id)
            if titles is None:
                errors.append(f"{path}:{pair.row}: pair {pair.pair_id} is not in batch {batch.name}")
            elif (pair.left_title, pair.right_title) != titles:
                errors.append(f"{path}:{pair.row}: the titles of {pair.pair_id} differ from batch {batch.name}")
            else:
                kept[pair.pair_id] = pair.label
        batch.labels[labeller] = kept


def _batch_sort_key(path: Path) -> tuple[int, int, str]:
    match = BATCH_NUMBER.fullmatch(path.name.removesuffix(".pairs.csv"))
    if match is None:
        return (2, 0, path.name)
    return (0 if match.group(1) == "agree" else 1, int(match.group(2)), path.name)


def _within_batch_lines(batches: dict[str, Batch]) -> list[str]:
    """Agreement inside each batch two or more people labelled, once all of them have finished it."""
    lines = ["Agreement within each batch (shown once everyone on it has finished):"]
    shown = False
    for batch in batches.values():
        if len(batch.labels) < 2:
            continue
        shown = True
        if not batch.finished:
            lines.append(f"  {batch.name}: hidden until everyone has finished it")
            continue
        for first, second in combinations(sorted(batch.labels), 2):
            labels = [(batch.labels[first][pair_id], batch.labels[second][pair_id]) for pair_id in batch.pair_ids]
            lines += _comparison_lines(f"{batch.name}, {first} and {second}", first, second, batch.pair_ids, labels)
    if not shown:
        lines.append("  none yet: two labellers label the same agreement batch, each alone")
    return lines


def _repeat_lines(batches: dict[str, Batch]) -> list[str]:
    """A pair labelled again in a later batch: compared with each labeller's latest earlier label of it, once.

    Only finished batches count, on both sides, so nobody sees a label its labeller could still change.
    """
    earlier_labels: dict[str, dict[str, str]] = {}  # pair_id -> labeller -> latest label from earlier batches
    across: dict[tuple[str, str], list[tuple[str, str, str]]] = {}  # (earlier, later) -> [(pair_id, a, b)]
    itself: dict[str, list[tuple[str, str, str]]] = {}  # labeller -> [(pair_id, first label, second label)]
    for batch in batches.values():
        if not batch.finished:
            continue
        for labeller in sorted(batch.labels):
            for pair_id, label in batch.labels[labeller].items():
                for earlier, earlier_label in sorted(earlier_labels.get(pair_id, {}).items()):
                    if earlier == labeller:
                        itself.setdefault(labeller, []).append((pair_id, earlier_label, label))
                    else:
                        across.setdefault((earlier, labeller), []).append((pair_id, earlier_label, label))
        for labeller, labels in batch.labels.items():
            for pair_id, label in labels.items():
                earlier_labels.setdefault(pair_id, {})[labeller] = label

    lines = ["Repeated pairs (labelled again in a later batch):"]
    for (earlier, later), found in sorted(across.items()):
        pair_ids = [pair_id for pair_id, _, _ in found]
        pairs = [(a, b) for _, a, b in found]
        lines += _comparison_lines(f"{earlier} first, {later} again", earlier, later, pair_ids, pairs)
    for labeller, found in sorted(itself.items()):
        same = sum(1 for _, a, b in found if a == b)
        changed = ", ".join(f"{pair_id} {a} then {b}" for pair_id, a, b in found if a != b)
        lines.append(
            f"  {labeller} labelled {_pairs(len(found))} twice, {same} the same" + (f" ({changed})" if changed else "")
        )
    if len(lines) == 1:
        lines.append("  none yet")
    return lines


def _comparison_lines(
    title: str, first: str, second: str, pair_ids: list[str], labels: list[tuple[str, str]]
) -> list[str]:
    agree = sum(1 for a, b in labels if a == b)
    binary, dropped = to_binary(labels)
    disagreements = [(pair_id, a, b) for pair_id, (a, b) in zip(pair_ids, labels, strict=True) if a != b]
    lines = [
        f"  {title}: {_pairs(len(labels))}, {agree} agree ({100 * agree / len(labels):.1f}%)",
        f"    four-class kappa {format_kappa(cohen_kappa(labels, LABELS))},"
        f" binary kappa {format_kappa(cohen_kappa(binary, BINARY_LABELS))}"
        f" ({dropped} dropped as unsure)",
        f"    disagreements ({len(disagreements)}):",
    ]
    lines += [f"      {pair_id}  {first}: {a:<10} {second}: {b}" for pair_id, a, b in disagreements]
    if not disagreements:
        lines.append("      none")
    return lines


def _next_step(batches: dict[str, Batch], errors: list[str]) -> str:
    if errors:
        return "Next step: fix the files listed above (python ml/scripts/check_labels.py <file>), then run this again."
    agree = [batch for name, batch in batches.items() if name.startswith("agree-")]
    if not agree:
        return (
            f"Next step: make agree-1: python ml/scripts/make_pairs.py ml/labelling/listings.csv --batch agree-1"
            f" --seed {AGREE_SEED}"
        )
    latest = agree[-1]
    waiting = [name for name in sorted(latest.labels) if latest.left_for(name) > 0]
    if waiting:
        return f"Next step: label {latest.name} ({_left(latest, waiting)})"
    if len(latest.labels) < 2:
        if latest.labels:
            return f"Next step: label {latest.name} (the second labeller has not started)"
        return f"Next step: label {latest.name} (two labellers, each alone, without talking about the pairs)"

    number = len(agree) + 1
    if latest.size < AGREEMENT_BATCH_SIZE:
        return (
            f"Next step: {latest.name} has only {_pairs(latest.size)}; the agreement rule needs"
            f" {AGREEMENT_BATCH_SIZE}. Make agree-{number} with --size {AGREEMENT_BATCH_SIZE} and label it"
            " the same way."
        )
    kappas = {
        (first, second): cohen_kappa(
            [(latest.labels[first][pair_id], latest.labels[second][pair_id]) for pair_id in latest.pair_ids], LABELS
        )
        for first, second in combinations(sorted(latest.labels), 2)
    }
    low = [(pair, kappa) for pair, kappa in kappas.items() if kappa is None or kappa < AGREEMENT_GATE]
    if low:
        which = ", ".join(f"{a} and {b} {format_kappa(kappa)}" for (a, b), kappa in low)
        return (
            f"Next step: discuss the disagreements on {latest.name} (four-class kappa: {which}; the gate is"
            f" {AGREEMENT_GATE:.2f}), write the clarified rule into ml/DATA.md, then make agree-{number} with a new"
            f" seed and label it the same way (never re-label {latest.name})."
        )
    return _solo_step(batches, latest.name, min(kappa for kappa in kappas.values() if kappa is not None))


def _left(batch: Batch, names: list[str]) -> str:
    """Such as "alex has 45 pairs left, sam has 1 pair left"."""
    return ", ".join(f"{name} has {_pairs(batch.left_for(name))} left" for name in names)


def _pairs(count: int) -> str:
    return f"{count} pair{'' if count == 1 else 's'}"


def _solo_step(batches: dict[str, Batch], agree_name: str, kappa: float) -> str:
    solo = [batch for name, batch in batches.items() if name.startswith("solo-")]
    for batch in solo:
        if not batch.labels:
            return f"Next step: label {batch.name} (nobody has started it)"
        unfinished = [name for name in sorted(batch.labels) if batch.left_for(name) > 0]
        if unfinished:
            return f"Next step: label {batch.name} ({_left(batch, unfinished)})"
    number = len(solo) + 1
    command = (
        f"python ml/scripts/make_pairs.py ml/labelling/listings.csv --batch solo-{number}"
        f" --seed {SOLO_SEED + number} --overlap 5"
    )
    if not solo:
        # Repeats from the agreement batch that passed, never from an earlier one whose pairs were discussed.
        return (
            f"Next step: start solo-1 ({agree_name} reached four-class kappa {format_kappa(kappa)}):"
            f" {command} --overlap-from {agree_name}"
        )
    return f"Next step: make solo-{number}: {command} --overlap-from <the other labeller's solo batch>"


def main(argv: Sequence[str] | None = None) -> int:
    use_utf8_stdout()
    parser = argparse.ArgumentParser(prog="labelling_status.py", description="Where labelling stands, and what next.")
    parser.add_argument("directory", nargs="?", type=Path, default=DEFAULT_DIR, help="default: ml/labelling")
    args = parser.parse_args(argv)
    lines, code = status_report(args.directory)
    print("\n".join(lines))
    return code


if __name__ == "__main__":
    sys.exit(main())
