"""The match stage on our own labelled pairs (S3-18): how often it merges what the labels say it should.

The pairs are read as S3-5's ml/scripts/own_eval.py reads them, from the CSV files alone (the backend never imports
ml code). A pair's labels are combined by pair_id. A pair is dropped when its texts differ between files, when a label
is "unsure", or when the labels disagree on same against not same. The labels are machine labels (ml/DATA.md 4.1).

The stage merges a pair when both names land in one product. "same" pairs should merge; "different" pairs should not;
"variant" pairs merge on purpose, as variants of one product (DR-02). So each kind is reported on its own, and no
single precision mixes them.
"""

from __future__ import annotations

import csv
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.contracts import MatchInput, ProductMention
from app.entities.clustering import match_names
from app.entities.scorer import FuzzyScorer, PairScorer

Kind = Literal["same", "variant", "different", "mixed"]
KINDS: tuple[Kind, ...] = ("same", "variant", "different", "mixed")
DRAFT_LABELLER = "proposed-draft"  # own_eval's: a proposed label, never a label
MAX_MENTION_CHARS = 150  # ProductMention.raw_name: a longer name never reaches the match stage


@dataclass(frozen=True)
class LabelledPair:
    pair_id: str
    left: str
    right: str
    kind: Kind


def label_files(directory: Path) -> list[Path]:
    """Every <batch>.<labeller>.csv for a batch in directory/batches, in batch order (as own_eval picks them)."""
    batches = sorted(path.name.removesuffix(".pairs.csv") for path in (directory / "batches").glob("*.pairs.csv"))
    return [path for batch in batches for path in sorted(directory.glob(f"{batch}.*.csv"))]


def load_pairs(directory: Path) -> tuple[list[LabelledPair], dict[str, int]]:
    """The pairs whose labels agree, and how many were dropped for each reason."""
    by_id: dict[str, list[tuple[str, str, str]]] = {}
    for path in label_files(directory):
        with path.open(encoding="utf-8-sig", newline="") as file:
            for row in csv.DictReader(file):
                if (row.get("labeller") or "").strip().casefold() == DRAFT_LABELLER:
                    continue
                left, right = (row.get("left_title") or "").strip(), (row.get("right_title") or "").strip()
                by_id.setdefault(row["pair_id"], []).append((left, right, (row.get("label") or "").strip()))
    dropped = {"unsure": 0, "labellers disagree": 0, "texts differ": 0}
    found: list[LabelledPair] = []
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
            left, right, _ = rows[0]
            found.append(LabelledPair(pair_id, left, right, _kind(labels)))
    return found, dropped


def _kind(labels: Sequence[str]) -> Kind:
    if labels[0] == "same":
        return "same"
    if all(label == "variant" for label in labels):
        return "variant"
    if all(label == "different" for label in labels):
        return "different"
    return "mixed"


def fits_a_mention(pair: LabelledPair) -> bool:
    """Both names fit a ProductMention. A longer store title is never a mention in the app, so it isn't scored."""
    return 0 < len(pair.left) <= MAX_MENTION_CHARS and 0 < len(pair.right) <= MAX_MENTION_CHARS


def merged(pair: LabelledPair, scorer: PairScorer) -> bool:
    """Does the match stage put the two names in one product? Run on the pair alone, as two mentions."""
    mentions = [
        ProductMention(raw_name=pair.left, is_product=True),
        ProductMention(raw_name=pair.right, is_product=True),
    ]
    result = match_names(MatchInput(mentions=mentions), scorer)
    return result.alias_map[pair.left] == result.alias_map[pair.right]


def stage_rates(found: Sequence[LabelledPair], scorer: PairScorer | None = None) -> dict[Kind, tuple[int, int]]:
    """For each kind of pair: (pairs the stage merged, pairs of that kind)."""
    use = scorer if scorer is not None else FuzzyScorer()
    rates: dict[Kind, tuple[int, int]] = dict.fromkeys(KINDS, (0, 0))
    for pair in found:
        merged_now, total = rates[pair.kind]
        rates[pair.kind] = (merged_now + int(merged(pair, use)), total + 1)
    return rates
