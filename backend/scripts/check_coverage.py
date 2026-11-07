"""Check the per-module coverage gates (US-32): 70% on each core module and 80% on app/decide/.

    pytest -m "not live" --cov
    python -m scripts.check_coverage

pyproject.toml's fail_under = 70 only sees the overall figure, where a large well-tested module can hide a poor one.
This reads the same coverage data module by module and fails below either gate. A core module with no data fails too,
so a rename can't drop one quietly, and so does a module on no list, so a new one can't sit outside every gate.
app/api/, app/cli.py and app/main.py are entry points: only the overall gate covers them.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping
from pathlib import Path

import coverage
from coverage.exceptions import NoDataError

CORE_GATE = 70
DECIDE_GATE = 80
DECIDE_MODULE = "app/decide"
CORE_MODULES = (
    "app/commerce",
    "app/contracts",
    "app/criteria",
    "app/db",
    "app/enrich",
    "app/entities",
    "app/explain",
    "app/extract",
    "app/intake",
    "app/interview",
    "app/llm",
    "app/pipeline.py",
    "app/presearch.py",
    "app/research",
    "app/verify",
)

ENTRY_POINTS = ("app/__init__.py", "app/api", "app/cli.py", "app/main.py")

Totals = Mapping[str, tuple[int, int]]  # module: (statements run, statements)


def module_of(path: str) -> str | None:
    """ "app/llm" for a file in that folder, "app/pipeline.py" for a file directly in app/, None for anything else."""
    _, found, rest = ("/" + path.replace("\\", "/")).rpartition("/app/")
    return f"app/{rest.split('/')[0]}" if found else None


def check(totals: Totals) -> tuple[list[str], list[str]]:
    """One line per module, and one line per module that failed its gate."""
    lines: list[str] = []
    failures: list[str] = []
    gates = [(module, CORE_GATE) for module in CORE_MODULES] + [(DECIDE_MODULE, DECIDE_GATE)]
    for module, gate in gates:
        if module not in totals:
            lines.append(f"{module:<20} no data   (gate {gate}%)")
            failures.append(f"{module}: no coverage data")
            continue
        covered, statements = totals[module]
        percent = 100.0 if statements == 0 else covered * 100 / statements
        lines.append(f"{module:<20} {percent:5.1f}%   ({covered} of {statements} statements, gate {gate}%)")
        if covered * 100 < gate * statements:
            failures.append(f"{module}: {percent:.1f}% is below the {gate}% gate")
    for module in sorted(set(totals) - {module for module, _ in gates} - set(ENTRY_POINTS)):
        lines.append(f"{module:<20} on no list")
        failures.append(f"{module}: on no list. Add it to CORE_MODULES or ENTRY_POINTS in scripts/check_coverage.py")
    return lines, failures


def read_totals(data_file: Path) -> dict[str, tuple[int, int]]:
    cov = coverage.Coverage(data_file=str(data_file))
    cov.load()
    totals: dict[str, tuple[int, int]] = {}
    for path in cov.get_data().measured_files():
        module = module_of(path)
        if module is None:
            continue
        _, statements, _, missing, _ = cov.analysis2(path)
        covered, total = totals.get(module, (0, 0))
        totals[module] = (covered + len(statements) - len(missing), total + len(statements))
    return totals


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.check_coverage", description=__doc__)
    parser.add_argument(
        "--data-file", type=Path, default=Path(".coverage"), help="the coverage data (default: .coverage)"
    )
    args = parser.parse_args(argv)
    if not args.data_file.is_file():
        print(f"No coverage data at {args.data_file}. Run pytest with --cov first.", file=sys.stderr)
        return 1
    try:
        totals = read_totals(args.data_file)
    except NoDataError as error:
        print(f"Could not read the coverage data: {error}", file=sys.stderr)
        return 1
    lines, failures = check(totals)
    print("\n".join(lines))
    if failures:
        print("\nCoverage gates failed:\n" + "\n".join(f"- {failure}" for failure in failures))
        return 1
    print(f"\nCoverage gates passed: {CORE_GATE}% on core modules, {DECIDE_GATE}% on {DECIDE_MODULE}/.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
