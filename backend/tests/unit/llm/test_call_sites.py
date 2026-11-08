"""Every LLM call site declares what its prompt carries and how big the job is (US-46).

mypy already refuses a call without them; this test also catches a call that slips past type checking (for
example through an untyped helper) and lists every call site, so a reviewer sees new ones.
"""

from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[3] / "app"
REQUIRED = {"data_class", "difficulty"}


def _complete_calls() -> list[tuple[str, int, set[str]]]:
    calls = []
    for path in sorted(APP.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "complete":
                keywords = {k.arg for k in node.keywords if k.arg is not None}
                calls.append((str(path.relative_to(APP.parent)).replace("\\", "/"), node.lineno, keywords))
    return calls


def test_every_llm_call_declares_its_data_class_and_difficulty() -> None:
    calls = _complete_calls()
    missing = [f"{path}:{line}" for path, line, keywords in calls if not keywords >= REQUIRED]
    assert missing == []


def test_the_known_call_sites() -> None:
    # A new call site is fine: add it here, and to the call-site table in docs/api-setup.md, with its class.
    assert sorted(path for path, _, _ in _complete_calls()) == [
        "app/criteria/stage.py",
        "app/extract/stage.py",
        "app/intake/stage.py",
    ]
