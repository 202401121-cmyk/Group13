"""Candidate pairs for later labelling, and a seeded hard-negative miner (US-15, S3-4, DECIDED 1 and 2).

Usage: python ml/scripts/candidates.py [--recording DIR ...] [--seed 20261111] [--out ml/labelling/candidates.csv]

- Mentions come from recorded runs (backend/tests/fixtures/recorded/<name>/serper.json): the titles of web and review
  site results, never Reddit (D-53). Each is paired with the most alike listing titles from ml/labelling/listings.csv,
  skipping any pair already in a batch (batches/*.pairs.csv).
- The miner pairs listings of different product families in one category that look most alike by fuzzy score: the
  hardest "different" pairs for a matcher.
This tool NEVER labels. It writes pairs and a score, with no label column, for a person or a machine labeller to read
through label_pairs.py later (ml/DATA.md section 4). Seeded: the same inputs and seed give the same file. Standard
library plus rapidfuzz; no network.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from check_labels import use_utf8_stdout
from listings import write_rows
from match_baseline import normalise
from pairsets import ML_DIR, PairSetError
from rapidfuzz import fuzz

LABELLING_DIR = ML_DIR / "labelling"
RECORDED_DIR = ML_DIR.parent / "backend" / "tests" / "fixtures" / "recorded"
DEFAULT_OUT = LABELLING_DIR / "candidates.csv"
CANDIDATE_HEADER = (
    "kind",
    "left_text",
    "right_text",
    "left_url",
    "right_url",
    "category",
    "left_family",
    "right_family",
    "score",
    "seed",
)
REVIEW_SITES = (
    "91mobiles.com",
    "smartprix.com",
    "digit.in",
    "gadgets360.com",
    "notebookcheck.net",
    "gsmarena.com",
    "rtings.com",
)
SEED = 20261111


@dataclass(frozen=True)
class Listing:
    listing_id: str
    text: str
    family: str
    category: str
    url: str = ""


@dataclass(frozen=True)
class Mention:
    text: str
    source_type: str  # review_site or web_search; reddit is refused
    url: str


@dataclass(frozen=True)
class Candidate:
    kind: str
    left_text: str
    right_text: str
    score: float
    left_id: str = ""
    right_id: str = ""
    left_url: str = ""
    right_url: str = ""


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def _is_reddit(host: str) -> bool:
    return host in ("reddit.com", "redd.it") or host.endswith((".reddit.com", ".redd.it"))


def check_mention(mention: Mention) -> None:
    """Reddit text is never used for training (D-53), so a Reddit mention is refused before anything is paired."""
    if mention.source_type == "reddit" or _is_reddit(_host(mention.url)):
        raise PairSetError(
            f"{mention.url or mention.text[:30]}: a Reddit mention is refused: Reddit text is never used for training"
        )


def pair_key(left: str, right: str) -> frozenset[str]:
    """A pair without its order, on normalised text, so a pair already in a batch is found either way round."""
    return frozenset((normalise(left), normalise(right)))


def _similar(left: str, right: str) -> float:
    return fuzz.token_set_ratio(normalise(left), normalise(right)) / 100


def mine_hard_negatives(
    listings: Sequence[Listing], seed: int, limit: int, skip: set[frozenset[str]] | None = None
) -> list[Candidate]:
    """The `limit` most alike pairs of different families in one category, most alike first. Ties: by the seed."""
    rng = random.Random(seed)
    ordered = sorted(listings, key=lambda item: item.listing_id)
    found: list[tuple[float, float, Listing, Listing]] = []
    for i, left in enumerate(ordered):
        for right in ordered[i + 1 :]:
            if left.category == right.category and left.family != right.family:
                if skip and pair_key(left.text, right.text) in skip:
                    continue  # already in a batch: the quota goes to the next most alike
                found.append((_similar(left.text, right.text), 0.0, left, right))
    tiebreak = {id(entry): rng.random() for entry in found}
    found.sort(key=lambda entry: (-entry[0], tiebreak[id(entry)], entry[2].listing_id, entry[3].listing_id))
    return [
        Candidate("hard_different", a.text, b.text, score, a.listing_id, b.listing_id, a.url, b.url)
        for score, _, a, b in found[:limit]
    ]


def candidate_pairs(
    mentions: Sequence[Mention], listings: Sequence[Listing], skip: set[frozenset[str]], seed: int, per_mention: int
) -> list[Candidate]:
    """Each mention against its `per_mention` most alike listing titles that are not already paired in a batch."""
    rng = random.Random(seed)
    out: list[Candidate] = []
    for mention in mentions:
        check_mention(mention)
        scored = [(_similar(mention.text, item.text), rng.random(), item) for item in listings]
        scored.sort(key=lambda entry: (-entry[0], entry[1], entry[2].listing_id))
        taken = 0
        for score, _, item in scored:
            if taken == per_mention:
                break
            if pair_key(mention.text, item.text) in skip:
                continue
            out.append(
                Candidate(
                    "mention_vs_title", mention.text, item.text, score, "", item.listing_id, mention.url, item.url
                )
            )
            taken += 1
    return out


def mentions_from_recording(folder: Path) -> list[Mention]:
    """The titles of the web and review-site results in a recorded Serper search. Reddit links are left out."""
    found: list[Mention] = []
    path = folder / "serper.json"
    if not path.is_file():
        return found
    for call in json.loads(path.read_text(encoding="utf-8")):
        body = call.get("response_body")
        for item in body.get("organic", []) if isinstance(body, dict) else []:
            title, link = str(item.get("title") or "").strip(), str(item.get("link") or "")
            if not title or _is_reddit(_host(link)):
                continue
            kind = (
                "review_site"
                if any(_host(link) == s or _host(link).endswith(f".{s}") for s in REVIEW_SITES)
                else "web_search"
            )
            found.append(Mention(title, kind, link))
    return found


def read_listings(path: Path = LABELLING_DIR / "listings.csv") -> list[Listing]:
    import csv

    with path.open(encoding="utf-8", newline="") as file:
        return [
            Listing(row["listing_id"], row["text"], row["product_family"], row["category"], row["url"])
            for row in csv.DictReader(file)
            if row["kind"] == "title"
        ]


def existing_pairs(directory: Path = LABELLING_DIR / "batches") -> set[frozenset[str]]:
    import csv

    done: set[frozenset[str]] = set()
    for path in sorted(directory.glob("*.pairs.csv")):
        with path.open(encoding="utf-8", newline="") as file:
            done |= {pair_key(row["left_text"], row["right_text"]) for row in csv.DictReader(file)}
    return done


def write_candidates(path: Path, pairs: Sequence[Candidate], listings: Sequence[Listing], seed: int = SEED) -> None:
    info = {item.listing_id: item for item in listings}

    def family(listing_id: str) -> str:
        return info[listing_id].family if listing_id in info else ""

    rows = [
        (
            c.kind,
            c.left_text,
            c.right_text,
            c.left_url,
            c.right_url,
            info[c.right_id].category if c.right_id in info else "",
            family(c.left_id),
            family(c.right_id),
            f"{c.score:.4f}",
            str(seed),
        )
        for c in pairs
    ]
    write_rows(path, CANDIDATE_HEADER, rows)


def main(argv: Sequence[str] | None = None) -> int:
    use_utf8_stdout()
    parser = argparse.ArgumentParser(description="Candidate pairs for later labelling. Never labels.")
    parser.add_argument("--recording", type=Path, action="append", help="a recorded run folder (default: all of them)")
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--per-mention", type=int, default=3)
    parser.add_argument("--hard", type=int, default=40, help="how many hard negatives to mine")
    args = parser.parse_args(argv)
    folders = args.recording or sorted(p for p in RECORDED_DIR.iterdir() if p.is_dir())
    mentions = [m for folder in folders for m in mentions_from_recording(folder)]
    listings = read_listings()
    skip = existing_pairs()
    pairs = candidate_pairs(mentions, listings, skip, args.seed, args.per_mention)
    pairs += [
        c
        for c in mine_hard_negatives(listings, args.seed, args.hard, skip)
        if pair_key(c.left_text, c.right_text) not in skip
    ]
    write_candidates(args.out, pairs, listings, args.seed)
    print(
        f"{len(mentions)} web and review-site mentions, {len(pairs)} candidate pairs written to "
        f"{args.out} (none labelled)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
