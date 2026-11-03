"""Check that this machine can run ShopSense, and say how to fix what is missing.

Run from the repository root, with the backend's virtual environment active:
    python scripts/doctor.py           every check
    python scripts/doctor.py --ping    also run the API smoke test (python -m scripts.smoke_apis)

Prints one row per check (OK, WARN or FAIL) with a one-line fix, and exits 0 when nothing FAILs. It never prints a
key, a password or DATABASE_URL: a variable is only ever "set" or "missing", and the database only "reachable" (with
its Postgres major version) or "not reachable". Standard library only, so it runs before anything is installed.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
BACKEND_PORT = 8000
FRONTEND_PORT = 3000
DEFAULT_ORIGIN = f"http://localhost:{FRONTEND_PORT}"
LLM_KEYS = ("GROQ_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY", "CEREBRAS_API_KEY", "MISTRAL_API_KEY")
OTHER_KEYS = ("SERPER_API_KEY", "YOUTUBE_API_KEY", "JINA_API_KEY", "HF_TOKEN", "REDDIT_USER_SALT")
MIN_PYTHON = (3, 11)
MIN_NODE = (20, 19)

OK, WARN, FAIL = "OK", "WARN", "FAIL"


@dataclass(frozen=True)
class Result:
    name: str
    status: str
    detail: str
    fix: str = ""


# --- reading settings without ever showing them ---------------------------------------------------------------------


def find_env_file(start: Path = BACKEND / "app") -> Path | None:
    """The .env the backend loads: python-dotenv walks up from the backend's code to the first .env it finds."""
    for folder in (start, *start.parents):
        candidate = folder / ".env"
        if candidate.is_file():
            return candidate
    return None


def read_env_file(path: Path | None) -> dict[str, str]:
    """NAME=value pairs from a .env file, kept in memory only. Inline comments after an unquoted value are dropped."""
    values: dict[str, str] = {}
    if path is None or not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, raw = line.split("=", 1)
        name = name.removeprefix("export ").strip()
        raw = raw.strip()
        if raw[:1] in ("'", '"') and raw[1:].find(raw[0]) >= 0:
            value = raw[1 : 1 + raw[1:].find(raw[0])]
        elif raw.startswith("#"):  # `KEY=   # comment`, as in .env.example: no value at all
            value = ""
        else:
            value = re.split(r"\s+#", raw, maxsplit=1)[0].strip()
        values[name] = value
    return values


def settings(env_file: Path | None, environ: Mapping[str, str]) -> dict[str, str]:
    """What the backend sees: the .env file, with real environment variables winning (as python-dotenv does)."""
    return {**read_env_file(env_file), **environ}


def is_set(values: Mapping[str, str], name: str) -> bool:
    return bool((values.get(name) or "").strip())


def frontend_origins(values: Mapping[str, str]) -> list[str]:
    """The same rule as the backend's allowed_origins (app/main.py), without importing the app."""
    raw = values.get("FRONTEND_ORIGINS") or values.get("FRONTEND_ORIGIN") or ""
    origins = [part.strip().rstrip("/") for part in raw.split(",") if part.strip() and part.strip() != "*"]
    return origins or [DEFAULT_ORIGIN]


def port_in_use(port: int) -> bool:
    """True if something already listens on the port, on IPv4 or IPv6 localhost."""
    for host in ("127.0.0.1", "::1"):
        family = socket.AF_INET6 if ":" in host else socket.AF_INET
        try:
            with socket.socket(family, socket.SOCK_STREAM) as probe:
                probe.settimeout(0.3)
                if probe.connect_ex((host, port)) == 0:
                    return True
        except OSError:
            continue
    return False


def backend_python() -> str:
    """The backend's virtual environment if there is one (backend/.venv or .venv), else this Python."""
    for venv in (BACKEND / ".venv", ROOT / ".venv"):
        for exe in (venv / "Scripts" / "python.exe", venv / "bin" / "python"):
            if exe.is_file():
                return str(exe)
    return sys.executable


Runner = Callable[..., subprocess.CompletedProcess[str]]


def run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **kwargs)  # type: ignore[call-overload,no-any-return]


# --- the checks -----------------------------------------------------------------------------------------------------


def check_python(version: tuple[int, int] = (sys.version_info.major, sys.version_info.minor)) -> Result:
    shown = f"{version[0]}.{version[1]}"
    if version < MIN_PYTHON:
        return Result("Python", FAIL, f"{shown} (3.11 or later needed; CI uses 3.11)", "Install Python 3.11 or later")
    return Result("Python", OK, shown)


def check_node(runner: Runner = run) -> Result:
    if shutil.which("node") is None:
        return Result("Node", FAIL, "not found", "Install Node 20.19 or later (nodejs.org)")
    found = re.match(r"v?(\d+)\.(\d+)", runner(["node", "--version"]).stdout.strip())
    if not found:
        return Result("Node", WARN, "version not readable", "Check that `node --version` works")
    version = (int(found.group(1)), int(found.group(2)))
    shown = f"{version[0]}.{version[1]}"
    if version < MIN_NODE:
        return Result("Node", FAIL, f"{shown} (20.19 or later needed by Vite)", "Install Node 20.19 or later")
    return Result("Node", OK, shown)


def check_backend_installed(python: str, runner: Runner = run) -> Result:
    probe = runner([python, "-c", "import app, fastapi, uvicorn, psycopg"], cwd=str(ROOT))
    if probe.returncode != 0:
        return Result(
            "Backend installed",
            FAIL,
            "the backend package or its dependencies are missing",
            'From backend/, in the venv: pip install -e ".[dev]"',
        )
    return Result("Backend installed", OK, f"with {Path(python).name}")


def check_frontend_installed() -> Result:
    if not (FRONTEND / "node_modules" / ".package-lock.json").is_file():
        return Result("Frontend installed", FAIL, "frontend/node_modules is missing", "cd frontend && npm ci")
    return Result("Frontend installed", OK, "node_modules present")


def check_env_file(env_file: Path | None, runner: Runner = run) -> list[Result]:
    results = []
    if env_file is None:
        results.append(
            Result(
                "Root .env",
                WARN,
                "not found (the app still runs: keyword rules, fixed criteria, no database)",
                "Copy .env.example to .env in the repository root and add your keys (docs/api-setup.md)",
            )
        )
    else:
        ignored = runner(["git", "check-ignore", "-q", env_file.name], cwd=str(env_file.parent)).returncode == 0
        if not ignored:
            results.append(Result("Root .env", FAIL, "found, but git does not ignore it", "Add .env to .gitignore"))
        else:
            where = "repository root" if env_file.parent == ROOT else f"found above the checkout ({env_file.parent})"
            results.append(Result("Root .env", OK, f"{where}; ignored by git"))
    if (BACKEND / ".env").is_file():
        results.append(
            Result(
                "No backend/.env",
                FAIL,
                "backend/.env exists",
                "Move its lines into the root .env, then delete backend/.env",
            )
        )
    else:
        results.append(Result("No backend/.env", OK, "none"))
    return results


def check_keys(values: Mapping[str, str]) -> list[Result]:
    llm_set = [name for name in LLM_KEYS if is_set(values, name)]
    if llm_set:
        llm = Result("LLM keys", OK, f"{len(llm_set)} of {len(LLM_KEYS)} set: {', '.join(llm_set)}")
    else:
        llm = Result(
            "LLM keys",
            WARN,
            "none set (intake uses keyword rules, criteria use fixed sets)",
            "Add at least GROQ_API_KEY to the root .env (docs/api-setup.md)",
        )
    others = [name for name in OTHER_KEYS if is_set(values, name)]
    missing = [name for name in OTHER_KEYS if name not in others]
    detail = f"set: {', '.join(others) or 'none'}; missing: {', '.join(missing) or 'none'} (all optional in Sprint 1)"
    return [llm, Result("Other keys", OK, detail)]


def check_ping(python: str, runner: Runner = run) -> Result:
    smoke = runner([python, "-m", "scripts.smoke_apis"], cwd=str(BACKEND), timeout=300)
    print(smoke.stdout.rstrip())  # the smoke test's own table: statuses only, never a key
    if smoke.returncode != 0:
        return Result(
            "API smoke test",
            WARN,
            "a configured service failed (see the table above)",
            "docs/api-setup.md says what each failure means",
        )
    return Result("API smoke test", OK, "every configured service answered")


DB_PROBE = """
import os, sys
import psycopg
try:
    with psycopg.connect(os.environ["SHOPSENSE_DOCTOR_DB"], connect_timeout=5) as conn:
        version = conn.execute("select current_setting('server_version_num')").fetchone()[0]
    print("reachable", int(version) // 10000)
except Exception as exc:
    print("unreachable", type(exc).__name__)
"""


def check_database(values: Mapping[str, str], python: str, runner: Runner = run) -> Result:
    url = (values.get("DATABASE_URL") or "").strip()
    if not url:
        return Result("Database", OK, "off (DATABASE_URL is empty: nothing is saved, everything else works)")
    # The URL travels in the child's environment, never on its command line, and is never printed.
    probe = runner([python, "-c", DB_PROBE], cwd=str(ROOT), env={**os.environ, "SHOPSENSE_DOCTOR_DB": url}, timeout=20)
    words = probe.stdout.split()
    if words[:1] == ["reachable"] and len(words) == 2:
        return Result("Database", OK, f"reachable, Postgres {words[1]}")
    reason = words[1] if len(words) == 2 else "no answer"
    return Result(
        "Database",
        WARN,
        f"paused or unreachable ({reason}); searches still finish without it",
        "Resume the project in the Supabase dashboard, and check DATABASE_URL is the Session pooler string "
        "with ?sslmode=require (docs/database.md)",
    )


def check_contracts(python: str, runner: Runner = run) -> Result:
    with tempfile.TemporaryDirectory() as tmp:
        export = runner(
            [
                python,
                "-c",
                f"from pathlib import Path; from scripts.export_contracts import main; main(Path(r'{tmp}'))",
            ],
            cwd=str(BACKEND),
        )
        if export.returncode != 0:
            return Result("Contracts in sync", FAIL, "the export did not run", "Install the backend first")
        fresh = {p.relative_to(tmp): p.read_bytes() for p in Path(tmp).rglob("*.json")}
        committed = {
            p.relative_to(ROOT / "shared" / "contracts"): p.read_bytes()
            for p in (ROOT / "shared" / "contracts").rglob("*.json")
        }
    if fresh != committed:
        return Result(
            "Contracts in sync",
            FAIL,
            "shared/contracts differs from the Pydantic models",
            "From backend/: python -m scripts.export_contracts, then commit shared/contracts",
        )
    return Result("Contracts in sync", OK, f"{len(fresh)} files match the models")


def check_frontend_types() -> Result:
    schemas = sorted((ROOT / "shared" / "contracts" / "schema").glob("*.json"))
    out = FRONTEND / "src" / "types" / "contracts"
    stale = [
        s.name
        for s in schemas
        if not (out / s.with_suffix(".d.ts").name).is_file()
        or (out / s.with_suffix(".d.ts").name).stat().st_mtime < s.stat().st_mtime
    ]
    if stale:
        return Result(
            "Frontend contract types",
            FAIL,
            f"{len(stale)} of {len(schemas)} missing or older than their schema",
            "cd frontend && npm run types:contracts",
        )
    return Result("Frontend contract types", OK, f"{len(schemas)} up to date")


def frontend_env() -> dict[str, str]:
    """Vite's files, lowest priority first: .env, then .env.local."""
    return {**read_env_file(FRONTEND / ".env"), **read_env_file(FRONTEND / ".env.local")}


def check_frontend_env(values: Mapping[str, str], backend_port: int = BACKEND_PORT) -> Result:
    mode = (values.get("VITE_API_MODE") or "mock").strip()
    base = (values.get("VITE_API_BASE_URL") or f"http://localhost:{BACKEND_PORT}").strip().rstrip("/")
    port = re.search(r":(\d+)$", base)
    if port and int(port.group(1)) != backend_port:
        return Result(
            "Frontend settings",
            FAIL,
            f"VITE_API_BASE_URL points at port {port.group(1)}, the backend uses {backend_port}",
            f"Set VITE_API_BASE_URL=http://localhost:{backend_port} in frontend/.env.local",
        )
    if mode != "live":
        return Result(
            "Frontend settings",
            WARN,
            f"VITE_API_MODE is {mode}: npm run dev alone shows sample data",
            "scripts/dev.py starts it in live mode for you; or set VITE_API_MODE=live in frontend/.env.local",
        )
    return Result("Frontend settings", OK, f"live mode, backend at {base}")


def check_cors(values: Mapping[str, str], frontend_port: int = FRONTEND_PORT) -> Result:
    wanted = f"http://localhost:{frontend_port}"
    origins = frontend_origins(values)
    if wanted not in origins:
        return Result(
            "CORS",
            FAIL,
            f"FRONTEND_ORIGINS does not include {wanted}",
            f"Add {wanted} to FRONTEND_ORIGINS in the root .env (comma-separated)",
        )
    return Result("CORS", OK, f"{wanted} is allowed")


def check_ports(busy: Callable[[int], bool] = port_in_use) -> list[Result]:
    results = []
    for name, port in (("Backend port", BACKEND_PORT), ("Frontend port", FRONTEND_PORT)):
        if busy(port):
            results.append(
                Result(name, WARN, f"{port} is in use", f"Stop whatever runs on port {port} before scripts/dev.py")
            )
        else:
            results.append(Result(name, OK, f"{port} is free"))
    return results


# --- report ---------------------------------------------------------------------------------------------------------


def render(results: list[Result]) -> str:
    width = max(len(r.name) for r in results)
    lines = [f"{'check':<{width}}  status  detail"]
    for r in results:
        lines.append(f"{r.name:<{width}}  {r.status:<6}  {r.detail}")
    fixes = [r for r in results if r.status != OK and r.fix]
    if fixes:
        lines.append("\nHow to fix:")
        lines += [f"  {r.name}: {r.fix}" for r in fixes]
    return "\n".join(lines)


def collect(ping: bool, environ: Mapping[str, str] | None = None) -> list[Result]:
    env_file = find_env_file()
    values = settings(env_file, os.environ if environ is None else environ)
    python = backend_python()
    results = [
        check_python(),
        check_node(),
        check_backend_installed(python),
        check_frontend_installed(),
        *check_env_file(env_file),
        *check_keys(values),
    ]
    if ping:
        results.append(check_ping(python))
    results += [
        check_database(values, python),
        check_contracts(python),
        check_frontend_types(),
        check_frontend_env(frontend_env()),
        check_cors(values),
        *check_ports(),
    ]
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--ping", action="store_true", help="also run the API smoke test (calls real services)")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    results = collect(args.ping)
    print(render(results))
    failed = sum(r.status == FAIL for r in results)
    print(f"\n{failed} check(s) failed." if failed else "\nNothing failed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
