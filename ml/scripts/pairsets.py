"""Load every labelled pair set we have into one shape, so every matcher is scored the same way (ML-0, #13).

Sources, in the formats ml/DATA.md describes (section 2 for ours, section 7 for the public ones):
- our labelling CSVs, read only through check_labels.check_file();
- WDC Products: gzipped JSON lines, one pair per line;
- Abt-Buy and Amazon-Google: Latin-1 CSVs plus a "perfect mapping" of the true matches. Every left record is
  paired with every right record, because any candidate filter we invented would bias the numbers.

Standard library only. Nothing here downloads a file or calls a service.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from check_labels import check_file

Split = Literal["dev", "test"]
SPLITS: tuple[Split, ...] = ("dev", "test")

ML_DIR = Path(__file__).resolve().parents[1]
EXAMPLES_DIR = ML_DIR / "examples"
DEFAULT_RAW_DIR = ML_DIR / "data" / "raw"
DOWNLOAD_HINT = "Download it with the steps in ml/DATA.md section 7.4."


@dataclass(frozen=True)
class Pair:
    """Two listing texts and whether they are the same product (True) or not (a variant or a different product)."""

    pair_id: str
    left_text: str
    right_text: str
    label: bool
    source: str
    split: Split
    is_hard_negative: bool | None = None  # None when the source does not say


class PairScorer(Protocol):
    """Anything that scores pairs from 0 (different) to 1 (same): the fuzzy baseline now, the trained matcher later."""

    @property
    def name(self) -> str: ...

    def score_pairs(self, pairs: Sequence[Pair]) -> list[float]: ...


class PairSetError(Exception):
    """A labelling file or a raw file is not in the expected format."""


class MissingData(Exception):
    """One or more raw files are not on this machine."""

    def __init__(self, paths: Sequence[Path]) -> None:
        super().__init__(", ".join(str(path) for path in paths))
        self.paths = list(paths)


def ensure_present(paths: Sequence[Path]) -> None:
    """Check every file up front, so a run never stops halfway because one is missing."""
    missing = [path for path in paths if not path.is_file()]
    if missing:
        raise MissingData(missing)


# --- Our own labelled pairs ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class OwnPairs:
    pairs: list[Pair]
    files: list[str]
    synthetic: bool  # True when a file is under ml/examples/: those numbers mean nothing
    unsure_dropped: int
    disagreements_dropped: int = 0
    only_in_one_file: int = 0
    title_mismatches: int = 0


def is_synthetic(path: Path) -> bool:
    return path.resolve().is_relative_to(EXAMPLES_DIR)


def load_own(paths: Sequence[Path]) -> OwnPairs:
    """One labeller's file, or two labellers' files of the same pairs (only pairs both call same or not same are kept).

    Everything is "test": our pairs have no dev split yet. A "variant" label marks a hard negative.
    """
    if not 1 <= len(paths) <= 2:
        raise PairSetError("give one labelling file, or two files from two labellers")
    results = [check_file(path) for path in paths]
    errors = [error for result in results for error in result.errors]
    if errors:
        raise PairSetError("\n".join(errors))
    files = [str(path) for path in paths]
    source = "own:" + "+".join(path.name for path in paths)
    synthetic = any(is_synthetic(path) for path in paths)

    if len(results) == 1:
        rows = results[0].pairs
        kept = [row for row in rows if row.label != "unsure"]
        pairs = [
            Pair(
                row.pair_id,
                row.left_title,
                row.right_title,
                row.label == "same",
                source,
                "test",
                row.label == "variant",
            )
            for row in kept
        ]
        return OwnPairs(pairs, files, synthetic, unsure_dropped=len(rows) - len(kept))

    first, second = results
    by_id = {row.pair_id: row for row in second.pairs}
    first_ids = {row.pair_id for row in first.pairs}
    only_in_one = sum(row.pair_id not in by_id for row in first.pairs)
    only_in_one += sum(row.pair_id not in first_ids for row in second.pairs)
    unsure = disagreements = mismatches = 0
    pairs = []
    for a in first.pairs:
        b = by_id.get(a.pair_id)
        if b is None:
            continue
        if (a.left_title.strip(), a.right_title.strip()) != (b.left_title.strip(), b.right_title.strip()):
            mismatches += 1  # the labellers did not see the same pair
        elif "unsure" in (a.label, b.label):
            unsure += 1
        elif (a.label == "same") != (b.label == "same"):
            disagreements += 1
        else:
            hard = a.label == "variant" and b.label == "variant"
            pairs.append(Pair(a.pair_id, a.left_title, a.right_title, a.label == "same", source, "test", hard))
    return OwnPairs(pairs, files, synthetic, unsure, disagreements, only_in_one, mismatches)


# --- WDC Products ------------------------------------------------------------------------------------------------

WDC_LEVELS = (80, 50, 20)  # share of corner cases
WDC_UNSEEN = ("000", "050", "100")  # share of products unseen in training
_WDC_FIELDS = ("id", "brand", "title", "description", "price", "priceCurrency", "cluster_id")
WDC_KEYS = (
    *(f"{field}_{side}" for side in ("left", "right") for field in _WDC_FIELDS),
    "pair_id",
    "label",
    "is_hard_negative",
)


@dataclass(frozen=True)
class WdcSubset:
    """One WDC test set and the validation set of the same corner-case level and unseen share."""

    level: int
    unseen: str
    dev_file: Path
    test_file: Path

    @property
    def name(self) -> str:
        return f"{self.level}cc{100 - self.level}rnd{self.unseen}un"


def wdc_subsets(raw_dir: Path, levels: Sequence[int] = WDC_LEVELS) -> list[WdcSubset]:
    """Each _gs test file with its validation set; of the small, medium and large ones, the large is used."""
    folder = raw_dir / "wdc_products"
    subsets = []
    for level in levels:
        if level not in WDC_LEVELS:
            raise PairSetError(f"WDC level must be one of {WDC_LEVELS}, not {level}")
        for unseen in WDC_UNSEEN:
            stem = f"wdcproducts{level}cc{100 - level}rnd{unseen}un"
            subsets.append(
                WdcSubset(level, unseen, folder / f"{stem}_valid_large.json.gz", folder / f"{stem}_gs.json.gz")
            )
    return subsets


def load_wdc(path: Path, split: Split, *, with_brand: bool = False) -> list[Pair]:
    """Read one gzipped JSON-lines file. Text is the title; with_brand puts the brand (when there is one) in front."""
    pairs = []
    try:
        with gzip.open(path, "rt", encoding="utf-8") as file:
            for line_number, line in enumerate(file, start=1):
                if line.strip():
                    pairs.append(_wdc_pair(json.loads(line), path, line_number, split, with_brand))
    except (OSError, EOFError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PairSetError(f"{path}: cannot read it as gzipped JSON lines ({exc})") from None
    return pairs


def _wdc_pair(record: object, path: Path, line_number: int, split: Split, with_brand: bool) -> Pair:
    if not isinstance(record, dict):
        raise PairSetError(f"{path}:{line_number}: each line must be a JSON object")
    missing = [key for key in WDC_KEYS if key not in record]
    if missing:
        raise PairSetError(f"{path}:{line_number}: missing keys {missing}")
    if record["label"] not in (0, 1) or not isinstance(record["is_hard_negative"], bool):
        raise PairSetError(f"{path}:{line_number}: label must be 0 or 1 and is_hard_negative true or false")
    return Pair(
        pair_id=str(record["pair_id"]),
        left_text=_wdc_text(record, "left", with_brand),
        right_text=_wdc_text(record, "right", with_brand),
        label=record["label"] == 1,
        source=f"wdc_products:{path.name}",
        split=split,
        is_hard_negative=record["is_hard_negative"],
    )


def _wdc_text(record: dict[str, object], side: str, with_brand: bool) -> str:
    title = record[f"title_{side}"]
    brand = record[f"brand_{side}"]
    text = "" if title is None else str(title)
    return f"{brand} {text}" if with_brand and brand else text


# --- Abt-Buy and Amazon-Google -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class LeipzigSpec:
    left_file: str
    right_file: str
    mapping_file: str
    left_text: str
    right_text: str
    left_key: str
    right_key: str


LEIPZIG: dict[str, LeipzigSpec] = {
    "abt_buy": LeipzigSpec("Abt.csv", "Buy.csv", "abt_buy_perfectMapping.csv", "name", "name", "idAbt", "idBuy"),
    "amazon_google": LeipzigSpec(
        "Amazon.csv",
        "GoogleProducts.csv",
        "Amzon_GoogleProducts_perfectMapping.csv",  # spelled "Amzon" in the publisher's archive
        "title",
        "name",
        "idAmazon",
        "idGoogleBase",
    ),
}


@dataclass(frozen=True)
class Record:
    id: str
    text: str


@dataclass(frozen=True)
class CrossPairSet:
    """Every left record against every right record; the true matches come from the perfect mapping."""

    source: str
    left: list[Record]
    right: list[Record]
    matches: frozenset[tuple[str, str]]

    def left_in(self, split: Split) -> list[Record]:
        return [record for record in self.left if leipzig_split(record.id) == split]

    def pair_count(self, split: Split) -> int:
        return len(self.left_in(split)) * len(self.right)

    def iter_pairs(self, split: Split) -> Iterator[Pair]:
        """The same Pair type as every other source, produced lazily: Amazon-Google alone has 4.4 million."""
        for left in self.left_in(split):
            for right in self.right:
                label = (left.id, right.id) in self.matches
                yield Pair(f"{left.id}#{right.id}", left.text, right.text, label, self.source, split)


def leipzig_split(record_id: str) -> Split:
    """Even first byte of sha256(id) is dev, odd is test: the same split on every machine, with no seed to keep."""
    return "dev" if hashlib.sha256(record_id.encode("utf-8")).digest()[0] % 2 == 0 else "test"


def load_leipzig(raw_dir: Path, name: str) -> CrossPairSet:
    spec = LEIPZIG[name]
    folder = raw_dir / name
    left = _records(folder / spec.left_file, spec.left_text)
    right = _records(folder / spec.right_file, spec.right_text)
    mapping = _read_latin1_csv(folder / spec.mapping_file, (spec.left_key, spec.right_key))
    matches = frozenset((row[spec.left_key], row[spec.right_key]) for row in mapping)
    left_ids = {record.id for record in left}
    right_ids = {record.id for record in right}
    unknown = [pair for pair in matches if pair[0] not in left_ids or pair[1] not in right_ids]
    if unknown:
        raise PairSetError(f"{folder / spec.mapping_file}: {len(unknown)} matches name unknown ids, e.g. {unknown[0]}")
    return CrossPairSet(name, left, right, matches)


def _records(path: Path, text_column: str) -> list[Record]:
    rows = _read_latin1_csv(path, ("id", text_column))
    records = [Record(row["id"], row[text_column]) for row in rows]
    if len({record.id for record in records}) != len(records):
        raise PairSetError(f"{path}: ids are not unique")
    return records


def _read_latin1_csv(path: Path, columns: Sequence[str]) -> list[dict[str, str]]:
    """The Leipzig CSVs are Latin-1, not UTF-8 (ml/DATA.md 7.2, 7.3), with CRLF endings and quoted fields."""
    with open(path, encoding="latin-1", newline="") as file:
        reader = csv.DictReader(file)
        header = list(reader.fieldnames or [])
        missing = [column for column in columns if column not in header]
        if missing:
            raise PairSetError(f"{path}: missing columns {missing}; the header is {header}")
        return [{column: row.get(column) or "" for column in columns} for row in reader]


def required_files(raw_dir: Path, dataset: str, levels: Sequence[int] = WDC_LEVELS) -> list[Path]:
    """The raw files one dataset needs, so they can all be checked before anything runs."""
    if dataset == "wdc_products":
        return [path for subset in wdc_subsets(raw_dir, levels) for path in (subset.dev_file, subset.test_file)]
    spec = LEIPZIG[dataset]
    return [raw_dir / dataset / name for name in (spec.left_file, spec.right_file, spec.mapping_file)]
