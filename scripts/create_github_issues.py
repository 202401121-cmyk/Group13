"""Create the ShopSense product backlog on GitHub from backlog/stories.yaml.

Needs the GitHub CLI (https://cli.github.com), logged in with `gh auth login`, and PyYAML.

    python scripts/create_github_issues.py --check                 # validate the backlog only
    python scripts/create_github_issues.py --dry-run               # print what would be created
    python scripts/create_github_issues.py --repo org/shopsense    # create labels, milestones, issues

Safe to re-run: labels and milestones are created only if missing, and a story whose issue
title already exists is skipped.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
BACKLOG = ROOT / "backlog" / "stories.yaml"

EXPECTED = {"core": (45, 216), "plus": (5, 15), "stretch": (1, 8)}
EXPECTED_SPRINT_CORE = {1: 36, 2: 37, 3: 40, 4: 36, 5: 36, 6: 31}
ALL_REQUIREMENTS = (
    [f"FR-{n:02d}" for n in range(1, 29)] + [f"NFR-{n:02d}" for n in range(1, 18)] + [f"DR-{n:02d}" for n in range(1, 11)]
)  # 28 + 17 + 10 = 55

EPICS = {
    1: "User Discovery & Personalization",
    2: "Multi-Source Research & Retrieval",
    3: "Evidence, Product Intelligence & Verification",
    4: "Personalized Recommendation & Explainability",
    5: "Commerce & Purchase Discovery",
    6: "Platform Engineering, Security & Reliability",
}
SPRINTS = {
    1: ("2026-10-04", "The whole pipeline runs end to end, and the app understands the shopper"),
    2: ("2026-10-18", "Real research and evidence, with the test safety net in place"),
    3: ("2026-11-01", "Trustworthy end to end: matched products, verified claims, correct filtering and ranking"),
    4: ("2026-11-15", "Smarter and safer: short interview, confidence, explanations, login, quality gate"),
    5: ("2026-11-29", "Complete: memory, enrichment, checked citations, compare, prices, YouTube"),
    6: ("2026-12-13", "Worst-case proof, measurement and presentation"),
}
LABELS = (
    [(f"epic:{n}", "5319e7", name) for n, name in EPICS.items()]
    + [(f"sprint:{n}", "0e8a16", SPRINTS[n][1][:100]) for n in SPRINTS]
    + [
        ("priority:must", "b60205", "Must have"),
        ("priority:should", "d93f0b", "Should have"),
        ("priority:could", "fbca04", "Could have"),
        ("plus", "c2e0c6", "Scheduled, but first to move if the sprint runs late"),
        ("stretch", "ededed", "Only if time allows"),
        ("ml", "1d76db", "Uses one of the ML components"),
        ("story", "0052cc", "User story"),
        ("bug", "d73a4a", "Something isn't working"),
    ]
)
REQUIRED_KEYS = {"id", "title", "req", "epic", "priority", "points", "sprint", "tier", "role", "goal", "benefit", "criteria"}


def load() -> list[dict]:
    return yaml.safe_load(BACKLOG.read_text(encoding="utf-8"))["stories"]


def validate(stories: list[dict]) -> list[str]:
    errors: list[str] = []
    ids = [s.get("id") for s in stories]
    for dup, n in Counter(ids).items():
        if n > 1:
            errors.append(f"{dup} appears {n} times")
    for s in stories:
        sid = s.get("id", "?")
        missing = REQUIRED_KEYS - s.keys()
        if missing:
            errors.append(f"{sid}: missing {sorted(missing)}")
            continue
        if s["points"] not in (1, 2, 3, 4, 5, 8, 13):
            errors.append(f"{sid}: {s['points']} points is not on the estimation scale")
        if s["epic"] not in EPICS or s["sprint"] not in SPRINTS:
            errors.append(f"{sid}: unknown epic or sprint")
        if s["tier"] not in EXPECTED:
            errors.append(f"{sid}: tier must be core, plus or stretch")
        if s["priority"] not in ("Must", "Should", "Could"):
            errors.append(f"{sid}: priority must be Must, Should or Could")
        if not s["criteria"]:
            errors.append(f"{sid}: no acceptance criteria")
    for tier, (count, points) in EXPECTED.items():
        rows = [s for s in stories if s.get("tier") == tier]
        got = (len(rows), sum(s.get("points", 0) for s in rows))
        if got != (count, points):
            errors.append(f"{tier}: expected {count} stories / {points} pts, found {got[0]} / {got[1]}")
    for sprint, points in EXPECTED_SPRINT_CORE.items():
        got = sum(s["points"] for s in stories if s.get("tier") == "core" and s.get("sprint") == sprint)
        if got != points:
            errors.append(f"sprint {sprint}: expected {points} core points, found {got}")
    covered = {r for s in stories for r in s.get("req", [])}
    uncovered = [r for r in ALL_REQUIREMENTS if r not in covered]
    unknown = sorted(covered - set(ALL_REQUIREMENTS))
    if uncovered:
        errors.append(f"requirements with no story: {uncovered}")
    if unknown:
        errors.append(f"stories cite unknown requirements: {unknown}")
    return errors


def body(s: dict) -> str:
    flags = ", ".join(f for f in ("ML" if s.get("ml") else "", s["tier"].title() if s["tier"] != "core" else "") if f)
    lines = [
        f"**Requirement:** {', '.join(s['req'])}  ",
        f"**EPIC {s['epic']}:** {EPICS[s['epic']]}  ",
        f"**Priority:** {s['priority']} · **Points:** {s['points']} · **Sprint:** {s['sprint']}"
        + (f" · **{flags}**" if flags else ""),
        "",
        "## Front of the card",
        "",
        f"As a **{s['role']}**, I want {s['goal']}, so that {s['benefit']}.",
        "",
        "## Back of the card: acceptance criteria",
        "",
        *[f"- [ ] {c}" for c in s["criteria"]],
        "",
        "---",
        "Definition of Done and the ten build rules: see CONTRIBUTING.md.",
    ]
    return "\n".join(lines)


def labels_for(s: dict) -> list[str]:
    out = ["story", f"epic:{s['epic']}", f"sprint:{s['sprint']}", f"priority:{s['priority'].lower()}"]
    if s["tier"] != "core":
        out.append(s["tier"])
    if s.get("ml"):
        out.append("ml")
    return out


def milestone(n: int) -> str:
    return f"Sprint {n}"


def gh(*args: str, repo: str) -> str:
    result = subprocess.run(["gh", *args, "--repo", repo], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def create(stories: list[dict], repo: str, dry: bool) -> None:
    ordered = sorted(stories, key=lambda s: (s["sprint"], s["tier"] != "core", int(s["id"][3:])))
    if dry:
        for name, color, desc in LABELS:
            print(f"label      {name:18s} #{color}  {desc}")
        for n, (due, goal) in SPRINTS.items():
            print(f"milestone  {milestone(n):18s} due {due}  {goal}")
        for s in ordered:
            print(f"issue      {s['id']}: {s['title']}  [{', '.join(labels_for(s))}] -> {milestone(s['sprint'])}")
        print(f"\n{len(LABELS)} labels, {len(SPRINTS)} milestones, {len(ordered)} issues")
        return

    existing_labels = {row["name"] for row in json.loads(gh("label", "list", "--limit", "200", "--json", "name", repo=repo))}
    for name, color, desc in LABELS:
        if name not in existing_labels:
            gh("label", "create", name, "--color", color, "--description", desc, repo=repo)

    existing_ms = {m["title"] for m in json.loads(gh("api", f"repos/{repo}/milestones?state=all&per_page=100", repo=repo))}
    for n, (due, goal) in SPRINTS.items():
        if milestone(n) not in existing_ms:
            gh("api", f"repos/{repo}/milestones", "-f", f"title={milestone(n)}", "-f", f"description={goal}",
               "-f", f"due_on={due}T23:59:59Z", repo=repo)

    titles = {i["title"] for i in json.loads(gh("issue", "list", "--state", "all", "--limit", "500", "--json", "title", repo=repo))}
    for s in ordered:
        title = f"{s['id']}: {s['title']}"
        if title in titles:
            print(f"skip   {title}")
            continue
        args = ["issue", "create", "--title", title, "--body", body(s), "--milestone", milestone(s["sprint"])]
        for label in labels_for(s):
            args += ["--label", label]
        gh(*args, repo=repo)
        print(f"create {title}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", help="owner/name, e.g. group13/shopsense")
    parser.add_argument("--dry-run", action="store_true", help="print what would be created")
    parser.add_argument("--check", action="store_true", help="validate backlog/stories.yaml and exit")
    args = parser.parse_args()

    stories = load()
    errors = validate(stories)
    if errors:
        print("Backlog problems:\n  " + "\n  ".join(errors))
        return 1
    print(f"Backlog OK: {len(stories)} stories")
    if args.check:
        return 0
    if not args.dry_run and not args.repo:
        parser.error("--repo is required unless --dry-run or --check")
    create(stories, args.repo or "", args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
