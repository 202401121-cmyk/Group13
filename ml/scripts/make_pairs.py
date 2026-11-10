"""Make a seeded batch of product pairs to label, from hand-collected listings (ML-1, D-55).

Usage: python ml/scripts/make_pairs.py LISTINGS.csv [...] --batch agree-1 --seed 101 [--size 50] [--overlap N]
       [--overlap-from BATCH ...] [--dir ml/labelling/batches]

A batch of 50 mixes five kinds of pair, so the labels cover the cases the matcher must get right:

    same_family_cross_source  35%  two store titles of one product family, from different stores
    same_family_same_source   15%  two store titles of one family, from the same store
    mention_vs_title          20%  a short mention (as people write it) against a store title of its family
    hard_different            20%  two store titles of different families in one category, the most alike by words
    random                    10%  any two listings

Each count is its share of the new pairs, rounded so the counts add up (largest remainder): within one pair of the
exact share. --overlap N repeats N pairs from earlier batches (or only from --overlap-from BATCH ...), with their
pair_ids and titles, so labellers can be compared; they count inside the batch size and keep their first batch's
stratum. Pair ids carry on across batches (p0001-p0050, then p0051 on) and are given out after the shuffle, so an
id says nothing about how its pair was chosen.

Writes two files in --dir: <batch>.pairs.csv, the pairs to label (no stratum, score or other hint), and
pairs.meta.csv, one row per pair with its listings and stratum, which labellers never see. The same seed, listings
and earlier batches give the same batch on the same Python version. Standard library only; nothing is downloaded and
no model is called, so no label is ever suggested.
"""

from __future__ import annotations

import argparse
import difflib
import random
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from check_labels import use_utf8_stdout
from listings import Listing, check_listings_file, read_table, write_rows

BATCH_HEADER = ("pair_id", "left_text", "right_text", "left_url", "right_url", "category", "product_family")
META_HEADER = ("batch", "pair_id", "left_listing", "right_listing", "stratum", "seed", "overlap_of")
META_FILE = "pairs.meta.csv"
STRATA = (
    ("same_family_cross_source", 35),
    ("same_family_same_source", 15),
    ("mention_vs_title", 20),
    ("hard_different", 20),
    ("random", 10),
)
BATCH_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
DEFAULT_DIR = Path(__file__).resolve().parents[1] / "labelling" / "batches"
HARD_POOL = 3  # hard_different draws from the 3 x needed most alike candidates, so the seed still matters


class PairingError(Exception):
    """The batch cannot be made as asked; the message says why and what to do."""


@dataclass(frozen=True)
class MadePair:
    pair_id: str
    left: Listing | None  # None for an overlap pair, which is copied from its earlier batch
    right: Listing | None
    stratum: str
    overlap_of: str
    row: tuple[str, ...]  # the line written to <batch>.pairs.csv


_Draft = MadePair | tuple[Listing, Listing, str]  # a repeat as it is, or a new pair (left, right, stratum) to number


def stratum_counts(new_pairs: int) -> dict[str, int]:
    """How many new pairs each stratum gets: its share, floored, then the leftover to the largest remainders."""
    exact = {name: new_pairs * share / 100 for name, share in STRATA}
    counts = {name: int(value) for name, value in exact.items()}
    leftover = new_pairs - sum(counts.values())
    by_remainder = sorted((name for name, _ in STRATA), key=lambda name: -(exact[name] - counts[name]))
    for name in by_remainder[:leftover]:  # sorted() is stable, so ties go in STRATA order
        counts[name] += 1
    return counts


def make_batch(
    listings_paths: Sequence[Path],
    batch: str,
    seed: int,
    out_dir: Path,
    size: int = 50,
    overlap: int = 0,
    overlap_from: Sequence[str] = (),
) -> list[MadePair]:
    """Make one batch and write its two files. Raises PairingError, before writing anything, if it cannot.

    `overlap_from` limits the repeats to those earlier batches, for example the other labeller's solo batch, so
    the repeats compare two labellers rather than one labeller with itself.
    """
    if not BATCH_NAME.fullmatch(batch):
        raise PairingError(f"batch name {batch!r} must be lowercase words and digits joined by -, such as agree-1")
    if not 0 <= overlap < size:
        raise PairingError(f"--overlap must be from 0 to {size - 1}, not {overlap}")
    listings = _load_listings(listings_paths)
    meta_path = out_dir / META_FILE
    batch_path = out_dir / f"{batch}.pairs.csv"
    earlier = _read_meta(meta_path)
    if any(row["batch"] == batch for row in earlier) or batch_path.exists():
        raise PairingError(f"batch {batch} already exists in {out_dir}; never remake or re-label a batch")
    if overlap and not earlier:
        raise PairingError("--overlap repeats pairs from earlier batches, but there is no earlier batch yet")

    rng = random.Random(seed)
    used = {frozenset((row["left_listing"], row["right_listing"])) for row in earlier}
    next_number = 1 + max((int(row["pair_id"][1:]) for row in earlier), default=0)
    if next_number + size - overlap - 1 > 9999:
        raise PairingError("pair ids would pass p9999; start a new meta file for a new round of labelling")

    drafts: list[_Draft] = _repeats(earlier, out_dir, overlap, overlap_from, rng)
    for stratum, count in stratum_counts(size - overlap).items():
        for left, right in _draw(stratum, count, listings, used, rng):
            drafts.append((right, left, stratum) if rng.random() < 0.5 else (left, right, stratum))
    rng.shuffle(drafts)

    # New pair ids are given out after the shuffle, so an id says nothing about how its pair was chosen.
    made: list[MadePair] = []
    for draft in drafts:
        if isinstance(draft, MadePair):
            made.append(draft)
            continue
        left, right, stratum = draft
        pair_id = f"p{next_number:04d}"
        next_number += 1
        row = (pair_id, left.text, right.text, left.url, right.url, left.category, left.product_family)
        made.append(MadePair(pair_id, left, right, stratum, "", row))

    write_rows(batch_path, BATCH_HEADER, [pair.row for pair in made])
    meta_rows = [[row[column] for column in META_HEADER] for row in earlier]
    for pair in made:
        if pair.left is not None and pair.right is not None:
            left_id, right_id = pair.left.listing_id, pair.right.listing_id
        else:
            original = next(row for row in earlier if row["pair_id"] == pair.pair_id and not row["overlap_of"])
            left_id, right_id = original["left_listing"], original["right_listing"]
        meta_rows.append([batch, pair.pair_id, left_id, right_id, pair.stratum, str(seed), pair.overlap_of])
    try:
        write_rows(meta_path, META_HEADER, meta_rows)
    except OSError:
        batch_path.unlink(missing_ok=True)  # without its meta rows the batch must not exist, or its ids get reused
        raise
    return made


def _load_listings(paths: Sequence[Path]) -> list[Listing]:
    listings: list[Listing] = []
    errors: list[str] = []
    for path in paths:
        result = check_listings_file(path)
        errors += result.errors
        listings += result.listings
    ids = [listing.listing_id for listing in listings]
    if len(set(ids)) != len(ids):
        errors.append("the same listing_id appears in two listings files")
    if errors:
        raise PairingError("fix the listings first (python ml/scripts/check_listings.py):\n" + "\n".join(errors))
    return sorted(listings, key=lambda listing: listing.listing_id)


def _read_meta(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    table = read_table(path, META_HEADER)
    if not table.ok:
        raise PairingError(f"{path} is damaged; restore it from git before making a batch:\n" + "\n".join(table.errors))
    return [values for _, values in table.rows]


def _repeats(
    earlier: list[dict[str, str]], out_dir: Path, overlap: int, overlap_from: Sequence[str], rng: random.Random
) -> list[_Draft]:
    """Copy `overlap` pairs from earlier batches, each with its pair_id and the exact titles its labellers saw.

    Only pairs a batch made itself are repeated, never a repeat, and each pair at most once in all: so no pair
    appears twice in one batch, and no repeat is counted twice in the agreement report.
    """
    if not overlap:
        return []
    known = {row["batch"] for row in earlier}
    unknown = sorted(set(overlap_from) - known)
    if unknown:
        raise PairingError(f"--overlap-from names batches that do not exist yet: {', '.join(unknown)}")
    repeated = {row["pair_id"] for row in earlier if row["overlap_of"]}  # each pair is repeated at most once
    originals = [
        row
        for row in earlier
        if not row["overlap_of"]
        and row["pair_id"] not in repeated
        and (not overlap_from or row["batch"] in overlap_from)
    ]
    if len(originals) < overlap:
        raise PairingError(f"--overlap {overlap} asks for more pairs than the earlier batches hold ({len(originals)})")
    chosen = rng.sample(originals, overlap)
    repeats: list[_Draft] = []
    for meta in chosen:
        batch_file = out_dir / f"{meta['batch']}.pairs.csv"
        table = read_table(batch_file, BATCH_HEADER)
        rows = {values["pair_id"]: values for _, values in table.rows}
        if meta["pair_id"] not in rows:
            raise PairingError(f"{batch_file} is missing or damaged, so pair {meta['pair_id']} cannot be repeated")
        values = rows[meta["pair_id"]]
        row = tuple(values[column] for column in BATCH_HEADER)
        repeats.append(MadePair(meta["pair_id"], None, None, meta["stratum"], meta["batch"], row))
    return repeats


def _draw(
    stratum: str, count: int, listings: list[Listing], used: set[frozenset[str]], rng: random.Random
) -> list[tuple[Listing, Listing]]:
    """Pick `count` pairs for one stratum that no batch has used yet, and mark them used."""
    pool = [pair for pair in _candidates(stratum, listings) if frozenset(_ids(pair)) not in used]
    if len(pool) < count:
        raise PairingError(
            f"not enough listings for {stratum}: it needs {count} new pairs and has {len(pool)};"
            " add more listings (see ml/DATA.md, section 8)"
        )
    if stratum == "hard_different":
        pool.sort(key=lambda pair: -_alike(pair))  # stable, so equal scores keep listing order
        pool = pool[: max(count, HARD_POOL * count)]
    chosen = rng.sample(pool, count)
    used.update(frozenset(_ids(pair)) for pair in chosen)
    return chosen


def _candidates(stratum: str, listings: list[Listing]) -> list[tuple[Listing, Listing]]:
    pairs: list[tuple[Listing, Listing]] = []
    for index, left in enumerate(listings):
        for right in listings[index + 1 :]:
            if _fits(stratum, left, right):
                pairs.append((left, right))
    return pairs


def _fits(stratum: str, left: Listing, right: Listing) -> bool:
    same_family = left.product_family == right.product_family
    titles = left.kind == right.kind == "title"
    if stratum == "same_family_cross_source":
        return titles and same_family and left.source != right.source
    if stratum == "same_family_same_source":
        return titles and same_family and left.source == right.source
    if stratum == "mention_vs_title":
        return same_family and {left.kind, right.kind} == {"title", "mention"}
    if stratum == "hard_different":
        return titles and not same_family and left.category == right.category
    return True  # random: any two listings


def _alike(pair: tuple[Listing, Listing]) -> float:
    """How alike two titles read, word for word. Used only to choose hard pairs; never shown or stored."""
    left, right = (listing.text.lower().split() for listing in pair)
    return difflib.SequenceMatcher(None, left, right).ratio()


def _ids(pair: tuple[Listing, Listing]) -> tuple[str, str]:
    return pair[0].listing_id, pair[1].listing_id


def main(argv: Sequence[str] | None = None) -> int:
    use_utf8_stdout()
    parser = argparse.ArgumentParser(prog="make_pairs.py", description="Make a seeded batch of pairs to label.")
    parser.add_argument("listings", nargs="+", type=Path, metavar="LISTINGS.csv", help="hand-collected listings")
    parser.add_argument("--batch", required=True, help="the batch name, such as agree-1 or solo-1")
    parser.add_argument("--seed", required=True, type=int, help="the seed; the same seed gives the same batch")
    parser.add_argument("--size", type=int, default=50, help="pairs in the batch, overlap included (default 50)")
    parser.add_argument("--overlap", type=int, default=0, help="pairs to repeat from earlier batches (default 0)")
    parser.add_argument(
        "--overlap-from",
        nargs="+",
        default=[],
        metavar="BATCH",
        help="repeat pairs only from these batches, such as the other labeller's solo batch",
    )
    parser.add_argument("--dir", type=Path, default=DEFAULT_DIR, help="where batches live (ml/labelling/batches)")
    args = parser.parse_args(argv)
    try:
        made = make_batch(
            args.listings,
            args.batch,
            args.seed,
            args.dir,
            size=args.size,
            overlap=args.overlap,
            overlap_from=args.overlap_from,
        )
    except (PairingError, OSError) as exc:  # OSError: FileBusy, when a spreadsheet holds the meta file open
        print(f"Cannot make {args.batch}: {exc}")
        return 1
    repeats = sum(1 for pair in made if pair.overlap_of)
    print(f"Made {args.batch}: {len(made)} pairs ({len(made) - repeats} new, {repeats} repeated from earlier batches).")
    print(f"  to label: {args.dir / (args.batch + '.pairs.csv')}")
    print(f"  hidden:   {args.dir / META_FILE} (for the evaluation; labellers never open it)")
    print(f"Label it:   python ml/scripts/label_pairs.py {args.dir / (args.batch + '.pairs.csv')} --labeller <name>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
