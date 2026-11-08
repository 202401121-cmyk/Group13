"""Measure the two baselines that learned source trust must beat on the seeded conflicts (US-16, #30).

Usage (from the repository root):
  python ml/scripts/trust_baseline.py [FILE] [--out FILE.json]
The defaults are ml/labelling/conflicts.csv and ml/results/trust_baseline.json.

Majority vote: the value most claims state. A tie is no answer.
Tier order: the highest tier present wins (manufacturer, lab, editorial, community, retailer: the backend's SourceTier),
by its most common value. A tie inside the tier falls back to the whole conflict's majority, and if that ties too there
is no answer.

Each answer is scored against the known answer, which a person set: numbers within the conflict's tolerance, text after
ignoring case and spacing. The accuracy table comes twice: for all conflicts, and for only those where the known
answer's source is not one of the claims, which is the fairer test of tier order. Synthetic examples never reach
ml/results/. Quotes and claim URLs are never shown or saved.
Exit codes: 0 done; 1 stopped with a message and nothing written (input errors, a results file that is refused or can't
be written, a usage error); 2 the file is missing. No network. Standard library only.
"""

from __future__ import annotations

import json
import platform
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from check_conflicts import TIERS, CheckResult, Claim, Conflict, Parser, check_file, host_is_under, hosts_in_text
from check_labels import use_utf8_stdout

ML = Path(__file__).resolve().parents[1]
REPOSITORY = ML.parent
DEFAULT_FILE = ML / "labelling" / "conflicts.csv"
RESULTS_DIR = ML / "results"
DEFAULT_OUT = RESULTS_DIR / "trust_baseline.json"

SYNTHETIC_WARNING = "SYNTHETIC EXAMPLE: these numbers mean nothing"
SCORING_RULE = (
    "a number is correct within the conflict's tolerance, text after ignoring case and spacing; "
    "no answer is never correct; accuracy = correct / the conflicts in that table"
)
TIES_RULE = (
    "majority: an even top vote, which is also no answer; tier order: the top tier's vote was even, "
    "even when the fallback to the whole conflict's majority then answered"
)
TARGETS = "targets: 15 by the Sprint 2 review, 30 in Sprint 3"
ALL_CONFLICTS_TITLE = "All conflicts"
SOURCE_NOT_A_CLAIM_TITLE = "Known answer's source is not a claim"
MAX_CELL_WIDTH = 28


# --- the two baselines --------------------------------------------------------------------------------


def _most_common_value(conflict: Conflict, claims: Sequence[Claim]) -> str | None:
    """The value most of these claims state, as the first of them wrote it. None when the top count is shared."""
    counts = Counter(conflict.vote(claim.value) for claim in claims)
    best = max(counts.values())
    winners = [vote for vote, count in counts.items() if count == best]
    if len(winners) != 1:
        return None
    return next(claim.value for claim in claims if conflict.vote(claim.value) == winners[0])


def majority_answer(conflict: Conflict) -> str | None:
    return _most_common_value(conflict, conflict.claims)


@dataclass(frozen=True)
class TierAnswer:
    answer: str | None
    decided_by: str
    tied: bool  # the top tier's vote was even, whether or not the fallback then answered


def tier_order_answer(conflict: Conflict) -> TierAnswer:
    top_tier = min((claim.tier for claim in conflict.claims), key=TIERS.index)
    in_top_tier = [claim for claim in conflict.claims if claim.tier == top_tier]
    answer = _most_common_value(conflict, in_top_tier)
    if answer is not None:
        return TierAnswer(answer, top_tier, tied=False)
    fallback = majority_answer(conflict)
    if fallback is not None:
        return TierAnswer(fallback, "majority (tier tie)", tied=True)
    return TierAnswer(None, "none (tier tie)", tied=True)


def is_correct(conflict: Conflict, answer: str | None) -> bool:
    if answer is None:
        return False
    known, given = conflict.vote(conflict.known_answer), conflict.vote(answer)
    if isinstance(known, Decimal) and isinstance(given, Decimal):
        # Decimal, not float: 10.3 against 10 with tolerance 0.3 must count as right.
        return abs(given - known) <= (conflict.tolerance or Decimal(0))
    return known == given


def answer_source_is_a_claim(conflict: Conflict) -> bool:
    """The known answer's source shares a site with a claim, which leans the test towards tier order (DATA.md 9.2)."""
    return any(
        host_is_under(host, claim.site) or host_is_under(claim.site, host)
        for host in hosts_in_text(conflict.how_established)
        for claim in conflict.claims
    )


@dataclass(frozen=True)
class Scored:
    conflict: Conflict
    majority: str | None
    majority_correct: bool
    tier: TierAnswer
    tier_correct: bool
    all_agree: bool
    answer_source_is_a_claim: bool


def score(conflict: Conflict) -> Scored:
    majority = majority_answer(conflict)
    tier = tier_order_answer(conflict)
    return Scored(
        conflict=conflict,
        majority=majority,
        majority_correct=is_correct(conflict, majority),
        tier=tier,
        tier_correct=is_correct(conflict, tier.answer),
        all_agree=conflict.all_agree,
        answer_source_is_a_claim=answer_source_is_a_claim(conflict),
    )


# --- totals -------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Totals:
    correct: int
    wrong: int
    ties: int
    no_answer: int

    @property
    def accuracy(self) -> float | None:
        """Correct out of the conflicts counted, so no answer counts against it. None for 0/0, never a made-up zero."""
        conflicts = self.correct + self.wrong + self.no_answer
        return self.correct / conflicts if conflicts else None


def _totals(answers: Sequence[tuple[str | None, bool]], ties: int) -> Totals:
    correct = sum(right for _, right in answers)
    no_answer = sum(answer is None for answer, _ in answers)
    return Totals(correct, len(answers) - correct - no_answer, ties, no_answer)


def majority_totals(scored: Sequence[Scored]) -> Totals:
    answers = [(item.majority, item.majority_correct) for item in scored]
    return _totals(answers, ties=sum(answer is None for answer, _ in answers))


def tier_totals(scored: Sequence[Scored]) -> Totals:
    answers = [(item.tier.answer, item.tier_correct) for item in scored]
    return _totals(answers, ties=sum(item.tier.tied for item in scored))


# --- the report ---------------------------------------------------------------------------------------


def _short(text: str) -> str:
    return text if len(text) <= MAX_CELL_WIDTH else f"{text[: MAX_CELL_WIDTH - 3]}..."


def _known_cell(conflict: Conflict) -> str:
    known = _short(conflict.known_answer)
    return f"{known} ±{conflict.tolerance}" if conflict.tolerance else known


def _answer_cell(answer: str | None, correct: bool) -> str:
    if answer is None:
        return "no answer"
    return f"{_short(answer)} {'right' if correct else 'wrong'}"


def _table(rows: Sequence[Sequence[str]]) -> list[str]:
    widths = [max(len(row[column]) for row in rows) for column in range(len(rows[0]))]
    return ["  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)).rstrip() for row in rows]


def _accuracy_cell(totals: Totals) -> str:
    return "n/a" if totals.accuracy is None else f"{totals.accuracy:.3f}"


def source_not_a_claim(scored: Sequence[Scored]) -> list[Scored]:
    """The conflicts whose known answer came from somewhere other than the claims being judged."""
    return [item for item in scored if not item.answer_source_is_a_claim]


def _accuracy_table(title: str, scored: Sequence[Scored], mark: str) -> list[str]:
    methods = [["Method", "Correct", "Wrong", "Ties", "No answer", "Accuracy"]]
    for name, totals in (("majority", majority_totals(scored)), ("tier order", tier_totals(scored))):
        methods.append(
            [
                name,
                str(totals.correct),
                str(totals.wrong),
                str(totals.ties),
                str(totals.no_answer),
                _accuracy_cell(totals),
            ]
        )
    first, *rest = _table(methods)
    return [f"{title} ({len(scored)})", first, *(f"{line}{mark}" for line in rest)]


def report(label: str, scored: Sequence[Scored]) -> str:
    synthetic = any(item.conflict.synthetic for item in scored)
    mark = "  SYNTHETIC" if synthetic else ""
    claims = sum(len(item.conflict.claims) for item in scored)
    lines = [f"Trust baselines on {label}"]
    if synthetic:
        lines.append(SYNTHETIC_WARNING)
    lines += [
        f"Conflicts: {len(scored)} ({TARGETS}). Claims: {claims}.",
        f"All sources agree on: {sum(item.all_agree for item in scored)}. "
        f"Known answer's source is also a claim on: {sum(item.answer_source_is_a_claim for item in scored)}.",
        "",
    ]

    lines += _accuracy_table(ALL_CONFLICTS_TITLE, scored, mark)
    lines.append("")
    lines += _accuracy_table(SOURCE_NOT_A_CLAIM_TITLE, source_not_a_claim(scored), mark)

    if scored:
        lines.append("")
        rows = [["Conflict", "Product", "Criterion", "Known", "Majority", "Tier order", "Decided by"]]
        for item in scored:
            conflict = item.conflict
            rows.append(
                [
                    conflict.conflict_id,
                    _short(conflict.product),
                    _short(conflict.criterion),
                    _known_cell(conflict),
                    _answer_cell(item.majority, item.majority_correct),
                    _answer_cell(item.tier.answer, item.tier_correct),
                    item.tier.decided_by,
                ]
            )
        lines += _table(rows)
    return "\n".join(lines)


# --- the results file ---------------------------------------------------------------------------------


def _totals_json(totals: Totals) -> dict[str, object]:
    accuracy = totals.accuracy
    return {
        "correct": totals.correct,
        "wrong": totals.wrong,
        "ties": totals.ties,
        "no_answer": totals.no_answer,
        "accuracy": None if accuracy is None else round(accuracy, 6),
    }


def _table_json(scored: Sequence[Scored]) -> dict[str, object]:
    return {
        "conflicts": len(scored),
        "majority_vote": _totals_json(majority_totals(scored)),
        "tier_order_baseline": _totals_json(tier_totals(scored)),
    }


def _conflict_json(item: Scored) -> dict[str, object]:
    conflict = item.conflict
    return {
        "conflict_id": conflict.conflict_id,
        "category": conflict.category,
        "product": conflict.product,
        "criterion": conflict.criterion,
        "known": conflict.known_answer,
        "tolerance": None if conflict.tolerance is None else str(conflict.tolerance),
        "claims": len(conflict.claims),
        "all_agree": item.all_agree,
        "answer_source_is_a_claim": item.answer_source_is_a_claim,
        "majority": item.majority,
        "majority_correct": item.majority_correct,
        "tier_order": item.tier.answer,
        "tier_order_correct": item.tier_correct,
        "decided_by": item.tier.decided_by,
    }


def to_json(label: str, scored: Sequence[Scored], today: date) -> dict[str, object]:
    synthetic = any(item.conflict.synthetic for item in scored)
    data: dict[str, object] = {
        "date": today.isoformat(),
        "tool": "ml/scripts/trust_baseline.py",
        "python_version": platform.python_version(),
        "conflicts_file": label,
        "synthetic": synthetic,
        "conflicts": len(scored),
        "claims": sum(len(item.conflict.claims) for item in scored),
        "all_agree": sum(item.all_agree for item in scored),
        "answer_source_is_a_claim": sum(item.answer_source_is_a_claim for item in scored),
        "tier_order": list(TIERS),
        "scoring_rule": SCORING_RULE,
        "ties_rule": TIES_RULE,
        "undefined": "null means 0/0",
        "all_conflicts": _table_json(scored),
        "source_not_a_claim": _table_json(source_not_a_claim(scored)),
        "per_conflict": [_conflict_json(item) for item in scored],
    }
    if synthetic:
        data["warning"] = SYNTHETIC_WARNING
    return data


def today_utc() -> date:
    return datetime.now(UTC).date()


def _label(path: Path) -> str:
    """The file's name for the report and the results: never a full path, which would carry someone's user name."""
    try:
        return path.resolve().relative_to(REPOSITORY).as_posix()
    except ValueError:
        return path.name


def _inside_results(path: Path) -> bool:
    return path.resolve().is_relative_to(RESULTS_DIR.resolve())


def _print_problems(result: CheckResult) -> None:
    for line in result.errors:
        print(line)
    print(f"\n{len(result.errors)} input error(s). Fix them and run this again; nothing was written.")


def main(argv: Sequence[str] | None = None) -> int:
    use_utf8_stdout()
    parser = Parser(
        prog="trust_baseline.py", description="Measure majority vote and tier order on the seeded conflicts."
    )
    parser.add_argument(
        "file", nargs="?", type=Path, help=f"a conflicts CSV (default: {DEFAULT_FILE.name} in labelling)"
    )
    parser.add_argument("--out", type=Path, help=f"where to write the numbers (default: {DEFAULT_OUT.name} in results)")
    args = parser.parse_args(argv)
    path: Path = args.file if args.file is not None else DEFAULT_FILE
    chosen_out: Path | None = args.out

    result = check_file(path)
    if result.missing:
        print(f"Missing file: {path}")
        print("Real conflicts go in ml/labelling/conflicts.csv (ml/DATA.md section 9).")
        return 2
    if not result.ok:
        _print_problems(result)
        return 1

    synthetic = any(conflict.synthetic for conflict in result.conflicts)
    if synthetic and chosen_out is not None and _inside_results(chosen_out):
        print(f"Refused: {SYNTHETIC_WARNING}, so they never go in ml/results/ ({chosen_out}).")
        return 1

    for warning in result.warnings:
        print(warning)
    label = _label(path)
    scored = [score(conflict) for conflict in result.conflicts]
    print(report(label, scored))

    if not scored:
        print("\nNo conflicts yet, so no results file was written.")
        return 0
    out = chosen_out if chosen_out is not None or synthetic else DEFAULT_OUT
    if out is None:
        print("\nSynthetic run: no results file was written. Use --out to choose one outside ml/results/.")
        return 0
    text = json.dumps(to_json(label, scored, today_utc()), indent=2, ensure_ascii=False)
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8", newline="\n")
    except OSError as exc:
        print(f"\nCannot write {out} ({exc.strerror or exc}). Close it in any other program, or choose another --out.")
        return 1
    print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
