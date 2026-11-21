"""US-22's 100 citation pairs (S5-4 DECIDED 5, by-hand job 2): the blank sheet people fill in.

Run from backend/:  python -m evals.citation.sheet

It draws 100 quotes, seeded, from the recorded runs' public sources (tests/fixtures/recorded/*/pipeline.json):
- first, every quote extraction took from a public web page (found word for word in the source's text) with at least
  MIN_WORDS words: the evidence the check really sees;
- then, as there are fewer than 100 of those, sentences drawn by the seed from the same public pages' text (8 to 60
  words, a whole sentence, no links, markup or site boilerplate).

Never a Reddit or YouTube quote: a source counts only when its data class is public, its type is not reddit or youtube,
and its address is on neither site. The two kinds are shuffled together. It writes:
- evals/citation/pairs.csv, with the columns id, quote, source_address, sentence, label and written_by, the last three
  blank;
- evals/citation/split.json: 40 ids to tune the threshold on and 60 to report on, by a fixed seed.

People write the sentences (D-55: Rushiraj Navar and Jhanvi Barot), one per quote, about half supported by it and half
deliberately wrong, each labelled supported or unsupported, with their name in written_by. No tool writes or suggests
one, and a sheet that already holds a sentence is never written over.
"""

from __future__ import annotations

import csv
import json
import random
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..run_baselines import BACKEND

RECORDED = BACKEND / "tests" / "fixtures" / "recorded"
HERE = Path(__file__).resolve().parent
SHEET = HERE / "pairs.csv"
SPLIT = HERE / "split.json"
COLUMNS = ("id", "quote", "source_address", "sentence", "label", "written_by")
COUNT = 100
TUNE = 40
SEED = 22  # US-22: which quotes are drawn
SPLIT_SEED = 63  # task #63: which ids tune and which report
MIN_WORDS = 5  # an extraction quote shorter than this gives a person too little to write a sentence about
PASSAGE_WORDS = (8, 60)  # a sentence drawn from a page's text: long enough to say something, short like a quote
NOT_PUBLIC_TYPES = {"reddit", "youtube"}
NOT_PUBLIC_HOSTS = ("reddit.com", "redd.it", "youtube.com", "youtu.be")
BOILERPLATE = ("affiliate", "cookie", "privacy policy", "subscribe", "newsletter", "sign up", "all rights reserved")
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
_MARKUP = re.compile(r"https?:|www\.|[\[\]|#*<>{}\\]|�")


class SheetStarted(Exception):
    """The sheet already holds a sentence someone wrote: it is never written over."""


@dataclass(frozen=True)
class Quote:
    quote: str
    source_address: str
    run: str
    kind: str = "extraction"  # or "page": a whole sentence of a public page's text


def _public(source: dict[str, Any]) -> bool:
    host = (urlparse(str(source.get("url", ""))).hostname or "").lower()
    return (
        source.get("data_class") == "public"
        and str(source.get("type", "")).lower() not in NOT_PUBLIC_TYPES
        and not any(host == h or host.endswith("." + h) for h in NOT_PUBLIC_HOSTS)
    )


def key(text: str) -> str:
    """A quote as compared for repeats: its words, in lower case."""
    return " ".join(text.split()).casefold()


def _records(root: Path) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    for path in sorted(root.glob("*/pipeline.json")):
        out += [(path.parent.name, record) for record in json.loads(path.read_text(encoding="utf-8"))]
    return out


def _public_sources(record: dict[str, Any]) -> list[dict[str, Any]]:
    return sorted((s for s in record.get("sources", []) if _public(s)), key=lambda s: str(s["url"]))


def candidates(root: Path = RECORDED) -> list[Quote]:
    """Every distinct extraction quote found word for word in a public source's text, in a fixed order."""
    found: dict[str, Quote] = {}
    for run, record in _records(root):
        sources = _public_sources(record)
        for reply in record.get("llm_replies", []):
            if reply.get("task") != "extract_claims":
                continue
            for claim in (reply.get("reply") or {}).get("claims") or []:
                quote = str(claim.get("quote", "")).strip()
                if len(quote.split()) < MIN_WORDS or key(quote) in found:
                    continue
                where = next((s for s in sources if quote in str(s.get("text") or "")), None)
                if where is not None:
                    found[key(quote)] = Quote(quote, str(where["url"]), run)
    return sorted(found.values(), key=lambda q: (q.run, q.source_address, q.quote))


def _passage(text: str) -> bool:
    lower = text.casefold()
    return (
        PASSAGE_WORDS[0] <= len(text.split()) <= PASSAGE_WORDS[1]
        and text[:1].isupper()
        and text[-1:] in ".!?"
        and not _MARKUP.search(text)
        and not any(b in lower for b in BOILERPLATE)
    )


def passages(root: Path = RECORDED, taken: set[str] | None = None) -> list[Quote]:
    """Whole sentences of the public pages' text, distinct and not among `taken` (keys), in a fixed order."""
    found: dict[str, Quote] = {}
    skip = taken or set()
    for run, record in _records(root):
        for source in _public_sources(record):
            for part in _SENTENCE.split(str(source.get("text") or "")):
                text = part.strip()
                if _passage(text) and key(text) not in skip and key(text) not in found:
                    found[key(text)] = Quote(text, str(source["url"]), run, "page")
    return sorted(found.values(), key=lambda q: (q.run, q.source_address, q.quote))


def draw(found: list[Quote], extra: list[Quote] | None = None, count: int = COUNT, seed: int = SEED) -> list[Quote]:
    """`count` quotes: all of `found` (a seeded sample, if there are more), then seeded `extra` to fill, shuffled
    together by the seed."""
    rng = random.Random(seed)
    picked = rng.sample(found, count) if len(found) >= count else list(found)
    more = extra or []
    if len(picked) + len(more) < count:
        raise ValueError(f"only {len(picked) + len(more)} public quotes in the recorded runs, not {count}")
    picked += rng.sample(more, count - len(picked))
    rng.shuffle(picked)
    return picked


def split_ids(ids: list[str], tune: int = TUNE, seed: int = SPLIT_SEED) -> dict[str, list[str]]:
    ordered = sorted(ids)
    picked = set(random.Random(seed).sample(ordered, tune))
    return {"tune": [i for i in ordered if i in picked], "test": [i for i in ordered if i not in picked]}


def _started(path: Path) -> bool:
    if not path.exists():
        return False
    with path.open(encoding="utf-8", newline="") as file:
        return any((row.get("sentence") or "").strip() for row in csv.DictReader(file))


def write_sheet(path: Path, quotes: list[Quote]) -> list[str]:
    """The blank sheet. Raises SheetStarted rather than write over a sentence someone wrote. Returns the ids."""
    if _started(path):
        raise SheetStarted(f"{path.name} already holds sentences; it is never written over")
    ids = [f"c{i:03d}" for i in range(1, len(quotes) + 1)]
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file, lineterminator="\n")
        writer.writerow(COLUMNS)
        for id_, q in zip(ids, quotes, strict=True):
            writer.writerow([id_, q.quote, q.source_address, "", "", ""])
    return ids


def main() -> int:
    found = candidates()
    quotes = draw(found, passages(taken={key(q.quote) for q in found}))
    try:
        ids = write_sheet(SHEET, quotes)
    except SheetStarted as started:
        print(started)
        return 1
    split = {"draw_seed": SEED, "split_seed": SPLIT_SEED, **split_ids(ids)}
    SPLIT.write_text(json.dumps(split, indent=2) + "\n", encoding="utf-8", newline="\n")
    runs = sorted({q.run for q in quotes})
    real = sum(q.kind == "extraction" for q in quotes)
    print(
        f"{len(quotes)} quotes from {len(runs)} recorded runs ({', '.join(runs)}): {real} extraction quotes and "
        f"{len(quotes) - real} sentences of the same public pages"
    )
    print(f"Written: {SHEET.relative_to(BACKEND).as_posix()} and {SPLIT.relative_to(BACKEND).as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
