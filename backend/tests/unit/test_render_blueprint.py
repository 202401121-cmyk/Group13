"""DEP-1 (S3-17, P-08): the Render Blueprint that runs the backend on staging, checked against the app and the runbook.

render.yaml sits at the repository root, where Render looks for it. Staging's secrets live only in Render's dashboard:
the file names each variable and holds no value but two settings that are no secret: the Python version, a build
setting, and NLI=off, so staging can never start the citation check by accident (P-48, P-10).
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]  # PyYAML ships no type stubs

from app.main import create_app

REPO = Path(__file__).resolve().parents[3]
BLUEPRINT = REPO / "render.yaml"
RUNBOOK = REPO / "docs" / "deploy.md"
BACKEND = REPO / "backend"


@pytest.fixture(scope="module")
def service() -> dict[str, Any]:
    blueprint = yaml.safe_load(BLUEPRINT.read_text(encoding="utf-8"))
    (web,) = [s for s in blueprint["services"] if s["type"] == "web"]
    return dict(web)


def _variables(service: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {entry["key"]: entry for entry in service["envVars"]}


def test_the_blueprint_runs_the_backend_on_python_3_11_from_its_folder(service: dict[str, Any]) -> None:
    assert (service["runtime"], service["plan"], service["rootDir"]) == ("python", "free", "backend")
    version = str(_variables(service)["PYTHON_VERSION"]["value"])
    assert re.fullmatch(r"3\.11\.\d+", version)  # fully qualified, as Render asks; its default is 3.14
    assert "pip install -e ." in service["buildCommand"]  # editable: the app reads backend/config/ from its source tree


def test_the_start_command_serves_the_app_on_renders_port(service: dict[str, Any]) -> None:
    words = shlex.split(service["startCommand"])
    assert words[:2] == ["uvicorn", "app.main:app"]
    assert words[words.index("--host") + 1] == "0.0.0.0"  # Render routes to the port, from outside the container
    assert words[words.index("--port") + 1] == "$PORT"
    assert "--reload" not in words and "--workers" not in words  # one process: 512 MB


def test_render_checks_health_at_the_apps_health_route(service: dict[str, Any]) -> None:
    path = service["healthCheckPath"]
    app = create_app(env={})
    routes = {
        (getattr(route, "path", None), method) for route in app.routes for method in getattr(route, "methods", ())
    }
    assert (path, "GET") in routes
    assert path == "/api/health"


def test_staging_deploys_only_commits_whose_checks_passed(service: dict[str, Any]) -> None:
    assert service["autoDeployTrigger"] == "checksPass"  # main is not protected: a red commit must not reach staging


COMMITTED = {"PYTHON_VERSION", "NLI"}  # settings, not secrets: their values are in the file


def test_no_value_is_written_for_any_variable_but_the_two_settings(service: dict[str, Any]) -> None:
    for key, entry in _variables(service).items():
        if key in COMMITTED:
            continue
        assert entry.get("sync") is False, key  # asked for in the dashboard when the Blueprint is created
        assert "value" not in entry and "generateValue" not in entry and "fromService" not in entry, key


def test_staging_runs_with_the_citation_check_off(service: dict[str, Any]) -> None:
    """The model doesn't fit the free plan's 512 MB (P-10), and real explanations need P-48's follow-up first."""
    nli = _variables(service)["NLI"]
    assert nli["value"] == "off"
    assert "sync" not in nli  # a value Render applies, never one to type into the dashboard


def test_every_variable_is_one_the_backend_reads(service: dict[str, Any]) -> None:
    sources = [p for folder in ("app", "config") for p in (BACKEND / folder).rglob("*") if p.suffix in (".py", ".yaml")]
    read = "\n".join(p.read_text(encoding="utf-8") for p in sources)
    for key in _variables(service):
        if key == "PYTHON_VERSION":
            continue
        assert re.search(rf"\b{key}\b", read), f"{key} is not read anywhere in backend/app or backend/config"


def test_the_staging_essentials_are_named(service: dict[str, Any]) -> None:
    names = set(_variables(service))
    assert {"FRONTEND_ORIGINS", "DATABASE_URL", "MATCHER", "SERPER_API_KEY", "REDDIT_SOURCE"} <= names
    assert {"GROQ_API_KEY", "GEMINI_API_KEY"} <= names  # the first providers of the routes in config/models.yaml
    assert not any(key.startswith("VITE_") for key in names)  # the frontend's, set on Vercel


def test_the_runbook_lists_exactly_the_blueprints_variables(service: dict[str, Any]) -> None:
    """docs/deploy.md's backend table names the same variables, so the two never drift apart."""
    text = RUNBOOK.read_text(encoding="utf-8")
    section = text.split("<!-- render-variables -->", 2)
    assert len(section) == 3, "docs/deploy.md must mark its Render variable table with <!-- render-variables -->"
    listed = set(re.findall(r"^\| `([A-Z][A-Z0-9_]+)`", section[1], flags=re.MULTILINE))
    assert listed == set(_variables(service))


def test_the_blueprint_lists_every_key_the_app_reads(service: dict[str, Any]) -> None:
    """Every provider or service key the backend reads is named, so staging can be given it (S4-17: DeepSeek)."""
    sources = [p for folder in ("app", "config") for p in (BACKEND / folder).rglob("*") if p.suffix in (".py", ".yaml")]
    read = set(re.findall(r"\b[A-Z][A-Z0-9_]*_API_KEY\b", "\n".join(p.read_text(encoding="utf-8") for p in sources)))
    assert "DEEPSEEK_API_KEY" in read
    assert read <= set(_variables(service)), sorted(read - set(_variables(service)))


def test_the_example_env_names_the_matcher_with_its_default() -> None:
    """.env.example names MATCHER with config/matcher.yaml's default (S4-17), each comment on its own line."""
    lines = (REPO / ".env.example").read_text(encoding="utf-8").splitlines()
    default = yaml.safe_load((BACKEND / "config" / "matcher.yaml").read_text(encoding="utf-8"))["default"]
    at = lines.index(f"MATCHER={default}")
    assert lines[at - 1].startswith("# ") and "fuzzy" in lines[at - 1] and "model" in lines[at - 1]
