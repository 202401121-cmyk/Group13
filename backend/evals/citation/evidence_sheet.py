"""Measure (b) of S5-4b DECIDED 6 and 7: evidence sentences this pipeline writes, labelled by people, against the check.

Run from backend/:
  python -m evals.citation.evidence_sheet --write     live: public-route LLM calls only; writes the blank sheet
  python -m evals.citation.evidence_sheet --measure   offline: the labelled sheet against the check

--write: the five recorded runs are replayed offline (evals/gate.py). Their products are taken in turn by rank (every
run's #1, then every #2, and so on), and each gets the stage's own evidence call: its name and its public quotes,
through the real LLM wrapper on the public route, then code's rules (app.explain.parsing). Until COUNT sentences are
kept. The stage asks for its top 3 only, but the five runs' top 3 hold public quotes for 8 criteria at most, one
sentence each, so the sheet goes on down each ranking with the same call. Rows already written to an unlabelled sheet
are kept and their products not asked again. Each sentence goes to the sheet with the quotes it cites
(evidence_sheet.csv), and to evidence_sheet.json with where it came from. A sheet that already holds a label is never
written over.
--measure: every row labelled supported or unsupported, alone and by hand, by Rushiraj Navar or Jhanvi Barot (D-55).
Each sentence goes through the model check (its threshold, code's numbers taken out, check.py) and the 6-word rule.
For each: the share of the supported kept and the share of the unsupported caught. NLI turns on by default only when the
model keeps at least 80% of the supported and catches at least 80% of the unsupported (DECIDED 7). Writes
evals/results/citation-evidence.json.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.contracts import Claim, DataClass, Difficulty, LLMClient, LLMTask, LLMUnavailable
from app.explain.check import checked_by_model, checked_by_quotes
from app.explain.evidence import community_quotes, evidence_request
from app.explain.facts import ProductFacts, facts_for
from app.explain.parsing import Draft, RawEvidence, drafts_from
from app.explain.prompts import SYSTEM_PROMPT, evidence_message
from app.explain.stage import OUTPUT_TOKENS

from ..run_baselines import RESULTS_DIR
from . import metrics
from .pairs import LABELS, WRITERS, SheetProblem, writer_name

HERE = Path(__file__).resolve().parent
SHEET = HERE / "evidence_sheet.csv"
RECORDS = HERE / "evidence_sheet.json"
EVIDENCE_OUT = RESULTS_DIR / "citation-evidence.json"
COLUMNS = ("id", "quote", "sentence", "label", "written_by")
COUNT = 20
JOIN = " | "  # between the quotes a sentence cites, in the sheet's one quote cell
KEPT_TARGET = CAUGHT_TARGET = 0.80  # DECIDED 7


@dataclass(frozen=True)
class Row:
    """One evidence sentence the pipeline kept, with where it came from."""

    id: str
    run: str
    product_id: str
    key: str
    claim_ids: tuple[str, ...]
    quotes: tuple[str, ...]
    sentence: str

    def draft(self) -> Draft:
        return Draft(self.key, self.sentence, self.claim_ids, self.quotes)


Product = tuple[str, ProductFacts, list[Claim]]  # its run, its facts, the run's claims
Ask = Callable[[ProductFacts, list[Claim]], list[Draft]]


def recorded_products() -> list[Product]:
    """Every ranked product of every recorded run, taken in turn by rank: every run's #1, then every #2, and so on."""
    from ..gate import recordings, replay

    by_run: list[list[Product]] = []
    for folder in recordings():
        done = replay(folder)
        claims = [c for result in done.results for c in result.claims]
        ranked = done.result.decision.ranked if done.result.decision is not None else []
        by_run.append([(folder.name, facts_for(r, claims), claims) for r in ranked])
    deepest = max((len(run) for run in by_run), default=0)
    return [run[rank] for rank in range(deepest) for run in by_run if rank < len(run)]


def asker(llm: LLMClient) -> Ask:
    """The stage's own evidence call for one product, then code's rules: its drafts ([] when no provider answered)."""

    def ask(facts: ProductFacts, claims: list[Claim]) -> list[Draft]:
        request = evidence_request(facts, claims) if facts.score is not None and facts.top else None
        if request is None:
            return []
        try:
            answer = llm.complete(
                LLMTask.WRITE_EVIDENCE,
                SYSTEM_PROMPT,
                evidence_message(request),
                RawEvidence,
                data_class=DataClass.PUBLIC,
                difficulty=Difficulty.MEDIUM,
                max_output_tokens=OUTPUT_TOKENS,
            )
        except LLMUnavailable:
            return []
        drafts, _ = drafts_from(answer, request, facts, community_quotes(facts, claims))
        return drafts

    return ask


def collect(products: Sequence[Product], ask: Ask, count: int = COUNT, done: Sequence[Row] = ()) -> list[Row]:
    """The first `count` sentences the pipeline keeps, product by product, in the order given. Rows `done` before are
    kept, and their products not asked again."""
    rows: list[Row] = list(done)[:count]
    asked = {(r.run, r.product_id) for r in rows}
    for run, facts, claims in products:
        if len(rows) == count:
            break
        if (run, facts.product_id) in asked:
            continue
        for draft in ask(facts, claims):
            rows.append(
                Row(
                    f"e{len(rows) + 1:03d}", run, facts.product_id, draft.key, draft.claim_ids, draft.quotes, draft.text
                )
            )
            if len(rows) == count:
                return rows
    return rows


def _labelled(path: Path) -> bool:
    if not path.exists():
        return False
    with path.open(encoding="utf-8", newline="") as file:
        return any((row.get("label") or "").strip() for row in csv.DictReader(file))


def write(rows: Sequence[Row], sheet: Path = SHEET, records: Path = RECORDS) -> None:
    """The blank sheet and its records. Refuses to write over a sheet that holds a label."""
    if _labelled(sheet):
        raise SheetProblem([f"{sheet.name} already holds labels; it is never written over"])
    with sheet.open("w", encoding="utf-8", newline="") as file:
        out = csv.writer(file, lineterminator="\n")
        out.writerow(COLUMNS)
        out.writerows([r.id, JOIN.join(r.quotes), r.sentence, "", ""] for r in rows)
    records.write_bytes((json.dumps([asdict(r) for r in rows], indent=2, ensure_ascii=False) + "\n").encode("utf-8"))


def rows_of(records: Path = RECORDS) -> list[Row]:
    return [
        Row(**{**r, "claim_ids": tuple(r["claim_ids"]), "quotes": tuple(r["quotes"])})
        for r in json.loads(records.read_text(encoding="utf-8"))
    ]


def load(sheet: Path = SHEET, records: Path = RECORDS) -> list[tuple[Row, bool, str]]:
    """Each row with its label (True: supported) and who labelled it, or SheetProblem naming every row not ready."""
    rows = {row.id: row for row in rows_of(records)}
    with sheet.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        if tuple(reader.fieldnames or ()) != COLUMNS:
            raise SheetProblem([f"the columns are {reader.fieldnames}, not {list(COLUMNS)}"])
        found = list(reader)
    problems: list[str] = []
    out: list[tuple[Row, bool, str]] = []
    for line in found:
        id_ = (line.get("id") or "").strip()
        row = rows.get(id_)
        if row is None:
            problems.append(f"{id_ or 'a row with no id'}: not one of the sheet's ids")
            continue
        label = (line.get("label") or "").strip().casefold()
        writer = writer_name(line.get("written_by") or "")
        same = (line.get("sentence") or "").strip() == row.sentence and (line.get("quote") or "").strip() == JOIN.join(
            row.quotes
        )
        wrong = [
            why
            for bad, why in (
                (not same, "its sentence or quote is not the one written"),
                (label not in LABELS, "the label is not supported or unsupported"),
                (writer is None, f"written_by is not {' or '.join(WRITERS)}"),
            )
            if bad
        ]
        problems += [f"{id_}: {why}" for why in wrong]
        if not wrong and writer is not None:
            out.append((row, LABELS[label], writer))
    missing = sorted(set(rows) - {(line.get("id") or "").strip() for line in found})
    if missing:
        problems.append(f"{len(missing)} ids are missing: {', '.join(missing)}")
    if problems:
        raise SheetProblem(problems)
    return out


def shares(labelled: Sequence[tuple[bool, bool]], resamples: int = metrics.RESAMPLES) -> dict[str, Any]:
    """(the person's label, the check's verdict) per sentence: the supported kept and the unsupported caught, each
    with a 95% bootstrap interval (each kind resampled with replacement on its own, seeded)."""
    supported = [kept for label, kept in labelled if label]
    caught = [not kept for label, kept in labelled if not label]
    return {
        "supported": len(supported),
        "unsupported": len(caught),
        "kept_supported": _share(supported),
        "caught_unsupported": _share(caught),
        "intervals_95": {
            "kept_supported": _bootstrap(supported, resamples),
            "caught_unsupported": _bootstrap(caught, resamples),
        },
    }


def _share(hits: Sequence[bool]) -> float | None:
    return round(sum(hits) / len(hits), 4) if hits else None


def _bootstrap(hits: Sequence[bool], resamples: int) -> tuple[float, float] | None:
    if not hits:
        return None
    rng = random.Random(metrics.SEED)
    found = [sum(hits[rng.randrange(len(hits))] for _ in hits) / len(hits) for _ in range(resamples)]
    return metrics.interval(found)


def meets(found: Mapping[str, Any]) -> bool:
    kept, caught = found["kept_supported"], found["caught_unsupported"]
    return kept is not None and caught is not None and kept >= KEPT_TARGET and caught >= CAUGHT_TARGET


def measure(sheet: Path = SHEET, records: Path = RECORDS) -> dict[str, Any]:
    from app.explain.nli import load_nli
    from app.explain.nli_settings import load_nli_settings

    settings = load_nli_settings(env={})
    if settings.threshold is None:
        raise SystemExit("config/nli.yaml has no threshold")
    scorer = load_nli(settings)
    labelled = load(sheet, records)
    facts = {(run, f.product_id): f for run, f, _ in recorded_products()}
    verdicts: list[dict[str, Any]] = []
    for row, label, writer in labelled:
        product = facts[(row.run, row.product_id)]
        (by_model,) = checked_by_model([row.draft()], product, scorer, settings.threshold, 60.0)
        (by_quote,) = checked_by_quotes([row.draft()], settings.quoted_words)
        verdicts.append(
            {
                "id": row.id,
                "supported": label,
                "labelled_by": writer,
                "model_kept": by_model,
                "quote_rule_kept": by_quote,
            }
        )
    model = shares([(bool(v["supported"]), bool(v["model_kept"])) for v in verdicts])
    quoted = shares([(bool(v["supported"]), bool(v["quote_rule_kept"])) for v in verdicts])
    return {
        "generated": datetime.now(UTC).date().isoformat(),
        "runner": "python -m evals.citation.evidence_sheet --measure (offline; the model from backend/.cache/nli/)",
        "sentences": len(verdicts),
        "threshold": settings.threshold,
        "model": model,
        "quote_rule": quoted,
        "targets": {"kept_supported": KEPT_TARGET, "caught_unsupported": CAUGHT_TARGET},
        "nli_on_by_default": meets(model),
        "labelled_by": dict(
            sorted(
                {w: sum(v["labelled_by"] == w for v in verdicts) for w in {v["labelled_by"] for v in verdicts}}.items()
            )
        ),
        "verdicts": verdicts,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m evals.citation.evidence_sheet", description=(__doc__ or "").split("\n")[0]
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="live: write the blank sheet from public-route calls")
    mode.add_argument("--measure", action="store_true", help="offline: the labelled sheet against the check")
    args = parser.parse_args(argv)
    if args.write:
        from app.presearch import default_llm

        done = [] if _labelled(SHEET) or not RECORDS.exists() else rows_of(RECORDS)
        rows = collect(recorded_products(), asker(default_llm()), done=done)
        write(rows)
        print(f"{len(rows)} sentences written to {SHEET.as_posix()} (and {RECORDS.name})")
        return 0 if len(rows) == COUNT else 1
    record = measure()
    EVIDENCE_OUT.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE_OUT.write_bytes((json.dumps(record, indent=2) + "\n").encode("utf-8"))
    print(
        json.dumps(
            {k: record[k] for k in ("sentences", "threshold", "model", "quote_rule", "nli_on_by_default")}, indent=2
        )
    )
    print(f"Written: {EVIDENCE_OUT.as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
