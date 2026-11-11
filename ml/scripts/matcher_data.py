"""The matcher's training and dev pairs (S3-4, DECIDED 3). Standard library plus rapidfuzz; no model, no network.

Sources, and what each may be used for (ml/DATA.md, D-54):
- Abt-Buy and Amazon-Google (CC BY 4.0): training and dev. Only the dev side of the Leipzig split
  (pairsets.leipzig_split)
  is used, so the test side stays for evaluation. Of those records, those whose second sha256 byte is below TRAIN_BELOW
  (about 80%) train; the rest are the dev part that picks the threshold.
  The right-hand records are split the same way, so no record is in both parts, on either side; a match across the two
  parts is left out. Every positive gets hard negatives:
  the non-matching records of the other file that look most alike by fuzzy score.
  They are a harder dev set than the whole cross product, so the threshold picked on it is the careful one.
- Our own labelled pairs (machine labels, ml/DATA.md 4.1): split by product family with pairsets.family_side; the test
  side is never used. Of the dev side's families, about 55% (sha256 second byte) train and the rest are dev. A pair of
  two families that landed in different parts is left out and counted.
- WDC Products: never. Evaluation only (D-54).
Draft labels (proposed-draft files, anything under ml/data/interim/) are refused.
"""

from __future__ import annotations

import hashlib
import random
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from match_baseline import normalise
from own_eval import DRAFT_LABELLER, INTERIM_DIR, label_files, read_gold
from pairsets import ML_DIR, PairSetError, family_side, leipzig_split, load_leipzig
from rapidfuzz import fuzz, process

TRAIN_SOURCES = ("abt_buy", "amazon_google")
TRAIN_BELOW = 205  # second byte of sha256(record id) under this: train (about 80%), else dev
OWN_TRAIN_BELOW = 140  # the same for a dev-side family: about 55% train, so the own dev part has 12 matches
NEGATIVES_PER_POSITIVE = 3
LABELLING_DIR = ML_DIR / "labelling"


@dataclass(frozen=True)
class TrainPair:
    left: str
    right: str
    label: bool  # True: the same product
    source: str


@dataclass(frozen=True)
class Part:
    train: list[TrainPair]
    dev: list[TrainPair]


@dataclass(frozen=True)
class OwnPart(Part):
    families_train: frozenset[str] = frozenset()
    families_dev: frozenset[str] = frozenset()
    straddling: int = 0  # pairs of two families on different sides of the test split: left out
    cross_part: int = 0  # pairs of a train family and a dev family: left out
    test_side: int = 0  # pairs on the test side: never used here


@dataclass
class TrainingData:
    train: list[TrainPair] = field(default_factory=list)
    dev: list[TrainPair] = field(default_factory=list)
    counts: dict[str, dict[str, int]] = field(default_factory=dict)


def _byte(text: str, index: int) -> int:
    return hashlib.sha256(text.encode("utf-8")).digest()[index]


def refuse_for_training(path: Path) -> None:
    """WDC Products (evaluation only), a proposed-draft file and anything under ml/data/interim/ are refused."""
    if any(part.casefold().startswith("wdc") for part in path.parts):
        raise PairSetError(f"{path}: WDC Products is for evaluation only, never training (D-54): refused")
    if path.resolve().is_relative_to(INTERIM_DIR.resolve()):
        raise PairSetError(f"{path}: a file under ml/data/interim/ is a draft, never a label: refused")
    if path.name.casefold().endswith(f".{DRAFT_LABELLER}.csv"):
        raise PairSetError(f"{path}: its labeller is {DRAFT_LABELLER}, a proposed label, never a label: refused")


# --- Abt-Buy and Amazon-Google ---------------------------------------------------------------------------------------


def leipzig_train_dev(raw_dir: Path, name: str, seed: int) -> Part:
    if name.casefold().startswith("wdc"):
        refuse_for_training(raw_dir / name)
    if name not in TRAIN_SOURCES:
        raise PairSetError(f"{name} is not a training set; the training sets are {TRAIN_SOURCES}")
    cross = load_leipzig(raw_dir, name)
    rng = random.Random(f"{seed}|{name}")
    right_text = {record.id: record.text for record in cross.right}
    # The right records are split by the same rule, so a record is on one side only, left or right: train and dev share
    # none. A match whose two records land on different sides is left out of both.
    pools = {
        "train": [r.id for r in cross.right if _byte(r.id, 1) < TRAIN_BELOW],
        "dev": [r.id for r in cross.right if _byte(r.id, 1) >= TRAIN_BELOW],
    }
    normalised = {i: normalise(right_text[i]) for i in right_text}
    matches_of: dict[str, set[str]] = {}
    for left_id, right_id in cross.matches:
        matches_of.setdefault(left_id, set()).add(right_id)
    train: list[TrainPair] = []
    dev: list[TrainPair] = []
    for left in cross.left:
        if leipzig_split(left.id) != "dev":
            continue  # the test side is kept for evaluation
        side = "train" if _byte(left.id, 1) < TRAIN_BELOW else "dev"
        into = train if side == "train" else dev
        right_ids = pools[side]
        choices = [normalised[i] for i in right_ids]
        own = {i for i in matches_of.get(left.id, set()) if i in set(right_ids)}
        all_own = matches_of.get(left.id, set())
        for right_id in sorted(own):
            into.append(TrainPair(left.text, right_text[right_id], True, name))
        wanted = NEGATIVES_PER_POSITIVE * max(1, len(own))
        found = process.extract(normalise(left.text), choices, scorer=fuzz.token_set_ratio, limit=wanted + len(own))
        hard = [right_ids[index] for _, _, index in found if right_ids[index] not in all_own][:wanted]
        extra = rng.choice([i for i in right_ids if i not in all_own and i not in hard])  # one at random, for variety
        for right_id in [*hard, extra]:
            into.append(TrainPair(left.text, right_text[right_id], False, name))
    rng.shuffle(train)
    rng.shuffle(dev)
    return Part(train, dev)


# --- our own pairs ----------------------------------------------------------------------------------------------------


def own_train_dev(directory: Path = LABELLING_DIR, files: Sequence[Path] | None = None) -> OwnPart:
    paths = list(files) if files is not None else label_files(directory)
    for path in paths:
        refuse_for_training(path)
    gold = read_gold(directory, paths)
    train: list[TrainPair] = []
    dev: list[TrainPair] = []
    fam_train: set[str] = set()
    fam_dev: set[str] = set()
    straddling = cross_part = test_side = 0
    for pair in gold.pairs:
        sides = {family_side(family) for family in pair.families}
        if sides == {"test"}:
            test_side += 1
            continue
        if len(sides) > 1:
            straddling += 1
            continue
        parts = {_byte(family, 1) < OWN_TRAIN_BELOW for family in pair.families}
        if len(parts) > 1:
            cross_part += 1
            continue
        into, families = (train, fam_train) if parts == {True} else (dev, fam_dev)
        into.append(TrainPair(pair.left, pair.right, pair.label, "own_pairs"))
        families |= pair.families
    return OwnPart(train, dev, frozenset(fam_train), frozenset(fam_dev), straddling, cross_part, test_side)


def build_all(raw_dir: Path, labelling_dir: Path, seed: int) -> TrainingData:
    """Every training source together, with the counts to record."""
    data = TrainingData()
    for name in TRAIN_SOURCES:
        part = leipzig_train_dev(raw_dir, name, seed)
        data.train += part.train
        data.dev += part.dev
        data.counts[name] = _counts(part.train, part.dev)
    own = own_train_dev(labelling_dir)
    data.train += own.train
    data.dev += own.dev
    data.counts["own_pairs (machine labels)"] = _counts(own.train, own.dev) | {
        "left_out_straddling": own.straddling,
        "left_out_across_train_and_dev": own.cross_part,
        "test_side_not_used": own.test_side,
    }
    rng = random.Random(seed)
    rng.shuffle(data.train)
    return data


def _counts(train: Sequence[TrainPair], dev: Sequence[TrainPair]) -> dict[str, int]:
    return {
        "train": len(train),
        "train_matches": sum(p.label for p in train),
        "dev": len(dev),
        "dev_matches": sum(p.label for p in dev),
    }
