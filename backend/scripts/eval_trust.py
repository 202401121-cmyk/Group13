"""Score source trust on the seeded conflicts (US-16): learned, tier order and majority vote, side by side.

    python -m scripts.eval_trust                                        # from backend/: print the table
    python -m scripts.eval_trust --out ../ml/results/trust_learned.json # and write the record

It runs the verify stage's own combiner (app/verify/combine.py) on ml/labelling/conflicts.csv (ml/DATA.md section 9).
All conflicts are one run, so the learned method learns each site's reliability across them. Each row becomes one claim:
its site, its tier, its value in the conflict's unit, and the site's starting trust for the conflict's category
(backend/config/trust.yaml). Majority vote counts claims, and an even top vote is no answer, as
ml/scripts/trust_baseline.py does. An answer is right within the conflict's tolerance (numbers) or after ignoring case
and spacing (text). No quote, claim URL or note is ever printed or saved. No network.
Exit codes: 0 done; 1 the file can't be read or written; 2 the file is missing.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.contracts import SourceTier, SourceType
from app.decide.units import Quantity, in_base, unit_key
from app.verify.combine import Evidence, Tolerance, measure, resolve_all
from app.verify.trust import TrustTable, load_trust_table, main_domain, site_trust

REPOSITORY = Path(__file__).resolve().parents[2]
CONFLICTS = REPOSITORY / "ml" / "labelling" / "conflicts.csv"
METHODS = ("learned", "tier_order", "majority_vote")
TITLES = {"learned": "learned", "tier_order": "tier order", "majority_vote": "majority"}
RESULTS_DIR = REPOSITORY / "ml" / "results"
# What the record's commit depends on: the labels, and the code and settings that scored them.
TRACKED = ("ml/labelling", "backend/app/verify", "backend/config/trust.yaml", "backend/scripts/eval_trust.py")
_LINK_HOST = re.compile(r"https?://([A-Za-z0-9.-]+)")
# A row's tier gives the source type whose default trust an unlisted site starts from.
TYPE_OF_TIER = {
    SourceTier.MANUFACTURER: SourceType.MANUFACTURER,
    SourceTier.LAB: SourceType.LAB,
    SourceTier.EDITORIAL: SourceType.REVIEW_SITE,
    SourceTier.COMMUNITY: SourceType.REDDIT,
    SourceTier.RETAILER: SourceType.RETAILER,
}


@dataclass(frozen=True)
class Row:
    site: str
    tier: SourceTier
    value: str
    path: str


@dataclass(frozen=True)
class Conflict:
    conflict_id: str
    category: str
    criterion: str
    unit: str
    known_answer: str
    tolerance: str
    rows: tuple[Row, ...]
    answer_hosts: frozenset[str] = frozenset()

    @property
    def answer_is_a_claim(self) -> bool:
        """Does the page that set the known answer also make one of the claims? (ml/DATA.md 9.2: then tier order rarely
        loses, so the fairer table leaves the conflict out.)"""
        return any(host == row.site or host.endswith(f".{row.site}") for host in self.answer_hosts for row in self.rows)


@dataclass
class Tally:
    correct: int = 0
    wrong: int = 0
    no_answer: int = 0

    def add(self, verdict: bool | None) -> None:
        if verdict is None:
            self.no_answer += 1
        elif verdict:
            self.correct += 1
        else:
            self.wrong += 1

    def as_json(self, conflicts: int) -> dict[str, Any]:
        accuracy = round(self.correct / conflicts, 6) if conflicts else None
        return {"correct": self.correct, "wrong": self.wrong, "no_answer": self.no_answer, "accuracy": accuracy}


@dataclass(frozen=True)
class PerConflict:
    conflict: Conflict
    answers: dict[str, str | None]
    correct: dict[str, bool | None]


@dataclass
class Report:
    per_conflict: list[PerConflict] = field(default_factory=list)
    totals: dict[str, Tally] = field(default_factory=lambda: {m: Tally() for m in METHODS})
    fair: dict[str, Tally] = field(default_factory=lambda: {m: Tally() for m in METHODS})  # answer's source not a claim
    weights: dict[str, float] = field(default_factory=dict)
    rounds: int = 0

    @property
    def fair_count(self) -> int:
        return sum(1 for p in self.per_conflict if not p.conflict.answer_is_a_claim)


def load_conflicts(path: Path) -> list[Conflict]:
    """The conflicts, in file order, each with its claim rows. ml/scripts/check_conflicts.py checks the file itself."""
    grouped: dict[str, list[dict[str, str]]] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for record in csv.DictReader(handle):
            grouped.setdefault(record["conflict_id"].strip(), []).append(record)
    conflicts = []
    for conflict_id, records in grouped.items():
        first = records[0]
        rows = tuple(
            Row(
                site=r["claim_site"].strip().lower(),
                tier=SourceTier[r["claim_tier"].strip().upper()],
                value=r["claim_value"].strip(),
                path="/" + r["claim_url"].strip().split("/", 3)[-1] if r["claim_url"].count("/") >= 3 else "/",
            )
            for r in records
        )
        conflicts.append(
            Conflict(
                conflict_id=conflict_id,
                category=first["category"].strip(),
                criterion=first["criterion"].strip(),
                unit=first["unit"].strip(),
                known_answer=first["known_answer"].strip(),
                tolerance=first["tolerance"].strip(),
                rows=rows,
                answer_hosts=frozenset(_hosts(first.get("how_established") or "")),
            )
        )
    return conflicts


def _hosts(text: str) -> list[str]:
    """The hosts of the http(s) links in a note, without "www."."""
    return [m.lower().removeprefix("www.") for m in _LINK_HOST.findall(text)]


def _tolerance(conflict: Conflict) -> Tolerance:
    """The conflict's tolerance, in base units, so it compares with the combiner's numbers."""
    if not conflict.tolerance:
        return Tolerance()
    _, base = in_base(Quantity(float(conflict.tolerance), unit_key(conflict.unit)), None)
    return Tolerance(absolute=abs(base))


def _evidence(conflicts: Sequence[Conflict], table: TrustTable) -> list[Evidence]:
    out = []
    for conflict in conflicts:
        for n, row in enumerate(conflict.rows):
            trust = site_trust(row.site, row.path, TYPE_OF_TIER[row.tier], row.tier, table, conflict.category)
            claim_id = "clm_" + hashlib.sha256(f"{conflict.conflict_id}|{n}".encode()).hexdigest()[:12]
            out.append(
                Evidence.of(
                    claim_id,
                    conflict.conflict_id,
                    conflict.criterion,
                    main_domain(row.site),
                    row.tier,
                    trust,
                    value=row.value,
                    unit=conflict.unit or None,
                )
            )
    return out


def _same(answer: str | None, conflict: Conflict) -> bool | None:
    """Is this answer the known one? None when there is no answer."""
    if answer is None:
        return None
    tolerance = _tolerance(conflict)
    got = measure(answer, conflict.unit or None, conflict.criterion)
    want = measure(conflict.known_answer, conflict.unit or None, conflict.criterion)
    if got.number is not None and want.number is not None:
        return got.dimension == want.dimension and tolerance.allows(got.number, want.number)
    return got.text is not None and got.text == want.text


def _majority(conflict: Conflict) -> str | None:
    """The value most claims state, as the first of them wrote it. An even top vote is no answer."""

    def key(value: str) -> str:
        read = measure(value, conflict.unit or None, conflict.criterion)
        return f"{read.dimension}:{read.number!r}" if read.number is not None else f"text:{read.text}"

    votes = Counter(key(row.value) for row in conflict.rows)
    ranked = votes.most_common()
    if not ranked or (len(ranked) > 1 and ranked[0][1] == ranked[1][1]):
        return None
    return next(row.value for row in conflict.rows if key(row.value) == ranked[0][0])


def evaluate(conflicts: Sequence[Conflict], table: TrustTable) -> Report:
    evidence = _evidence(conflicts, table)
    tolerances = {c.conflict_id: _tolerance(c) for c in conflicts}

    def tolerance_for(product: str, _key: str) -> Tolerance:
        return tolerances[product]

    learned = resolve_all(evidence, table, "learned", tolerance_for)
    tier_order = resolve_all(evidence, table, "tier_order", tolerance_for)
    report = Report(weights=dict(learned.weights), rounds=learned.rounds)
    for conflict in conflicts:
        pair = (conflict.conflict_id, conflict.criterion)
        answers: dict[str, str | None] = {}
        for method, outcome in (("learned", learned), ("tier_order", tier_order)):
            found = outcome.resolutions.get(pair)
            answers[method] = None if found is None or found.value is None else str(found.value)
        answers["majority_vote"] = _majority(conflict)
        correct = {method: _same(answers[method], conflict) for method in METHODS}
        for method in METHODS:
            report.totals[method].add(correct[method])
            if not conflict.answer_is_a_claim:
                report.fair[method].add(correct[method])
        report.per_conflict.append(PerConflict(conflict, answers, correct))
    return report


def json_report(report: Report, table: TrustTable, *, commit: str, date: str, file: str) -> dict[str, Any]:
    n = len(report.per_conflict)
    return {
        "date": date,
        "tool": "backend/scripts/eval_trust.py",
        "commit": commit,
        "conflicts_file": file,
        "conflicts": n,
        "claims": sum(len(p.conflict.rows) for p in report.per_conflict),
        "methods": {m: report.totals[m].as_json(n) for m in METHODS},
        "source_not_a_claim": {
            "conflicts": report.fair_count,
            "methods": {m: report.fair[m].as_json(report.fair_count) for m in METHODS},
            "rule": "only conflicts whose known answer's page is not a claim (ml/DATA.md 9.2): the fairer test",
        },
        "scoring_rule": (
            "a number is correct within the conflict's tolerance, text after ignoring case and spacing; no answer is "
            "never correct; accuracy = correct / conflicts"
        ),
        "majority_rule": "the value most claims state; an even top vote is no answer (as ml/scripts/trust_baseline.py)",
        "learned_rounds": report.rounds,
        "learned_weights": {site: round(w, 6) for site, w in sorted(report.weights.items())},
        "thresholds": {
            "unknown_below": table.unknown_below,
            "dispute_share": table.dispute_share,
            "prior_strength": table.prior_strength,
            "max_rounds": table.max_rounds,
            "min_weight_change": table.min_weight_change,
            "min_sites_to_learn": table.min_sites_to_learn,
        },
        "per_conflict": [
            {
                "conflict_id": p.conflict.conflict_id,
                "category": p.conflict.category,
                "criterion": p.conflict.criterion,
                "known_answer": p.conflict.known_answer,
                "claims": len(p.conflict.rows),
                "answer_source_is_a_claim": p.conflict.answer_is_a_claim,
                **{f"{m}_answer": p.answers[m] for m in METHODS},
                **{f"{m}_correct": p.correct[m] for m in METHODS},
            }
            for p in report.per_conflict
        ],
    }


def _cell(answer: str | None, correct: bool | None) -> str:
    mark = {True: "right", False: "wrong", None: "no answer"}[correct]
    return f"{answer or '-'} ({mark})"


def table_text(report: Report) -> str:
    head = f"{'conflict':<9} {'known':<10} " + " ".join(f"{TITLES[m]:<22}" for m in METHODS)
    lines = [head, "-" * len(head)]
    for p in report.per_conflict:
        cells = " ".join(f"{_cell(p.answers[m], p.correct[m]):<22}" for m in METHODS)
        lines.append(f"{p.conflict.conflict_id:<9} {p.conflict.known_answer:<10} {cells}")
    n = len(report.per_conflict)
    lines.append("-" * len(head))
    totals = " ".join(f"{f'{report.totals[m].correct} of {n} right':<22}" for m in METHODS)
    lines.append(f"{'total':<9} {'':<10} {totals}")
    fair = report.fair_count
    totals = " ".join(f"{f'{report.fair[m].correct} of {fair} right':<22}" for m in METHODS)
    lines.append(f"{'fairer':<9} {'':<10} {totals}  (the known answer's source is not a claim)")
    return "\n".join(lines)


def current_commit() -> str:
    """The commit the labels and the scoring code were read at, marked when either has changes not committed."""

    def git(*args: str) -> str:
        done = subprocess.run(["git", *args], cwd=REPOSITORY, capture_output=True, text=True, check=False)
        return done.stdout.strip() if done.returncode == 0 else ""

    commit = git("rev-parse", "--short", "HEAD") or "unknown"
    changed = git("status", "--porcelain", "--", *TRACKED)
    return commit + (" (with changes not committed to the labels or the trust code)" if changed else "")


def _into_results(path: Path) -> bool:
    try:
        path.resolve().relative_to(RESULTS_DIR.resolve())
    except ValueError:
        return False
    return True


def main(argv: Sequence[str] | None = None) -> int:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        prog="python -m scripts.eval_trust", description="Score source trust on the seeded conflicts (US-16)."
    )
    parser.add_argument("--file", type=Path, default=CONFLICTS, help="the conflicts file (default: the seeded one)")
    parser.add_argument("--out", type=Path, help="write the results here as JSON")
    args = parser.parse_args(argv)
    if not args.file.is_file():
        print(f"{args.file} not found", file=sys.stderr)
        return 2
    table = load_trust_table()
    try:
        conflicts = load_conflicts(args.file)
        report = evaluate(conflicts, table)
    except (OSError, KeyError, ValueError) as exc:
        print(f"{args.file.name} can't be read: {type(exc).__name__}", file=sys.stderr)
        return 1
    print(table_text(report))
    if args.out is not None and _into_results(args.out) and any(c.conflict_id.startswith("syn") for c in conflicts):
        print("Made-up (syn) conflicts never go to ml/results/ (ml/DATA.md section 9.3)", file=sys.stderr)
        return 1
    if args.out is not None:
        try:
            shown = args.file.resolve().relative_to(REPOSITORY).as_posix()
        except ValueError:
            shown = args.file.name
        data = json_report(
            report, table, commit=current_commit(), date=datetime.now(UTC).date().isoformat(), file=shown
        )
        try:
            args.out.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        except OSError as exc:
            print(f"{args.out} can't be written: {type(exc).__name__}", file=sys.stderr)
            return 1
        print(f"\nWrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
