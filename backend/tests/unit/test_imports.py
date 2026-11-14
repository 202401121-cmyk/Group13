"""Every package under app/ imports on its own, and every script answers --help, each in a fresh interpreter.

An import cycle (app.intake -> app.research.reddit -> app.intake) once broke `import app.research`, `import
app.pipeline` and `python -m scripts.smoke_sources --help` while every test still passed, because the test process had
already imported things in a lucky order. A fresh interpreter per item finds that. No network: an import and --help do
no work, and python-dotenv reads no .env.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
APP = BACKEND / "app"

PACKAGES = sorted(f"app.{p.name}" for p in APP.iterdir() if (p / "__init__.py").is_file())
# The top-level modules too: app.pipeline was one that broke.
MODULES = sorted(f"app.{p.stem}" for p in APP.glob("*.py") if p.stem != "__init__")
SCRIPTS = sorted(f"scripts.{p.stem}" for p in (BACKEND / "scripts").glob("*.py") if p.stem != "__init__")


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHON_DOTENV_DISABLED": "1", "PYTHONUTF8": "1"}
    return subprocess.run(
        [sys.executable, *args], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=120, check=False
    )


def test_the_lists_are_not_empty() -> None:
    assert "app.research" in PACKAGES and "app.pipeline" in MODULES and "scripts.smoke_sources" in SCRIPTS


@pytest.mark.parametrize("module", PACKAGES + MODULES)
def test_each_package_imports_alone(module: str) -> None:
    done = _run("-c", f"import {module}")
    assert done.returncode == 0, done.stderr[-2000:]


@pytest.mark.parametrize("script", SCRIPTS)
def test_each_script_answers_help(script: str) -> None:
    done = _run("-m", script, "--help")
    assert done.returncode == 0, done.stderr[-2000:]
    assert "usage" in done.stdout.lower()
