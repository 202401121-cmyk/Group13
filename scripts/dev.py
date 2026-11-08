"""Start the whole ShopSense stack with one command: the backend API and the frontend in live mode.

Run from the repository root, with the backend's virtual environment active (or a backend/.venv in place):
    python scripts/dev.py              backend on :8000 and frontend on :3000, live mode
    python scripts/dev.py --no-db      the same, with the database off (nothing is saved)
    python scripts/dev.py --mock       only the frontend, with its built-in sample data

It waits up to 60 seconds for GET /api/health and the frontend's port, then prints both addresses and a health
summary. Ctrl+C stops both, with no processes left behind. It refuses to start if a port is already in use.
It never reads or prints a secret value: the backend reads the root .env itself, and only key names are counted.
Standard library only; works in PowerShell, Git Bash, macOS and Linux.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from doctor import (
    BACKEND,
    BACKEND_PORT,
    FRONTEND,
    FRONTEND_PORT,
    LLM_KEYS,
    backend_python,
    find_env_file,
    is_set,
    port_in_use,
    settings,
)

READY_TIMEOUT_S = 60.0
GRACEFUL_SHUTDOWN_S = 5  # Ctrl+C stops the backend within this, even with a progress stream open
WINDOWS = os.name == "nt"


@dataclass(frozen=True)
class Plan:
    name: str
    cmd: list[str]
    cwd: Path
    env: dict[str, str]


def backend_plan(port: int, frontend_port: int, no_db: bool, environ: Mapping[str, str]) -> Plan:
    env = dict(environ)
    if no_db:
        env["DATABASE_URL"] = ""  # an empty variable wins over the .env file, so the database is off
    if frontend_port != FRONTEND_PORT and "FRONTEND_ORIGINS" not in environ:
        env["FRONTEND_ORIGINS"] = f"http://localhost:{frontend_port}"
    # An open progress stream (US-12) would otherwise hold Ctrl+C until its search ends, up to 150 seconds.
    cmd = [backend_python(), "-m", "uvicorn", "app.main:app", "--port", str(port)]
    cmd += ["--timeout-graceful-shutdown", str(GRACEFUL_SHUTDOWN_S)]
    return Plan("backend", cmd, BACKEND, env)


def frontend_plan(port: int, backend_port: int, mock: bool, environ: Mapping[str, str]) -> Plan:
    env = {
        **environ,
        "VITE_API_MODE": "mock" if mock else "live",
        "VITE_API_BASE_URL": f"http://localhost:{backend_port}",
    }
    npm = shutil.which("npm") or "npm"
    return Plan("frontend", [npm, "run", "dev", "--", "--port", str(port), "--strictPort"], FRONTEND, env)


def start_process(plan: Plan) -> subprocess.Popen[bytes]:
    """Each child gets its own process group, so Ctrl+C reaches only this script and it can end each whole tree."""
    if WINDOWS:
        return subprocess.Popen(plan.cmd, cwd=plan.cwd, env=plan.env, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    return subprocess.Popen(plan.cmd, cwd=plan.cwd, env=plan.env, start_new_session=True)


def stop_tree(process: subprocess.Popen[bytes]) -> None:
    """End the child and everything it started (npm -> node -> vite; uvicorn's workers)."""
    if process.poll() is not None:
        return
    if WINDOWS:
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False)
    else:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        except ProcessLookupError:
            return
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        if not WINDOWS:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        process.kill()


def fetch_health(port: int) -> dict[str, str] | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as response:
            data = json.loads(response.read().decode("utf-8"))
            return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def wait_until_ready(
    checks: dict[str, Callable[[], bool]],
    processes: list[subprocess.Popen[bytes]],
    timeout_s: float = READY_TIMEOUT_S,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> str | None:
    """None when every check passes; otherwise what is still not ready (or which process stopped early)."""
    deadline = clock() + timeout_s
    pending = dict(checks)
    while pending:
        for name, ready in list(pending.items()):
            if ready():
                del pending[name]
        if not pending:
            return None
        if any(p.poll() is not None for p in processes):
            return "a process stopped while starting (see its output above)"
        if clock() >= deadline:
            return f"not ready after {timeout_s:g} s: {', '.join(pending)}"
        sleep(0.5)
    return None


def summary(args: argparse.Namespace, health: Mapping[str, str] | None, values: Mapping[str, str]) -> str:
    lines = [f"Frontend  http://localhost:{args.frontend_port}  ({'mock' if args.mock else 'live'} mode)"]
    if not args.mock:
        database = (health or {}).get("database", "unknown")
        lines.append(f"Backend   http://localhost:{args.backend_port}  (database: {database})")
        configured = sum(is_set(values, name) for name in LLM_KEYS)
        lines.append(f"LLM providers with a key: {configured} of {len(LLM_KEYS)}")
    lines.append("Press Ctrl+C to stop everything.")
    return "\n".join(lines)


def parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--mock", action="store_true", help="only the frontend, with its sample data")
    parser.add_argument("--no-db", action="store_true", help="run the backend with the database off")
    parser.add_argument("--backend-port", type=int, default=BACKEND_PORT)
    parser.add_argument("--frontend-port", type=int, default=FRONTEND_PORT)
    return parser.parse_args(argv)


def main(
    argv: list[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    start: Callable[[Plan], subprocess.Popen[bytes]] = start_process,
    stop: Callable[[subprocess.Popen[bytes]], None] = stop_tree,
    busy: Callable[[int], bool] = port_in_use,
    ready: Callable[..., str | None] = wait_until_ready,
    health: Callable[[int], dict[str, str] | None] = fetch_health,
    block: Callable[[list[subprocess.Popen[bytes]]], None] | None = None,
) -> int:
    args = parse(argv)
    environ = os.environ if environ is None else environ
    ports = [("frontend", args.frontend_port)] + ([] if args.mock else [("backend", args.backend_port)])
    for name, port in ports:
        if busy(port):
            print(f"Port {port} ({name}) is already in use. Stop what runs there, or pick another port.")
            return 1
    plans = [] if args.mock else [backend_plan(args.backend_port, args.frontend_port, args.no_db, environ)]
    plans.append(frontend_plan(args.frontend_port, args.backend_port, args.mock, environ))
    processes: list[subprocess.Popen[bytes]] = []
    try:
        for plan in plans:
            processes.append(start(plan))
        checks: dict[str, Callable[[], bool]] = {f"frontend :{args.frontend_port}": lambda: busy(args.frontend_port)}
        if not args.mock:
            checks[f"backend /api/health :{args.backend_port}"] = lambda: health(args.backend_port) is not None
        problem = ready(checks, processes)
        if problem:
            print(f"Could not start ShopSense: {problem}.")
            return 1
        values = settings(find_env_file(), environ)
        print("\n" + summary(args, None if args.mock else health(args.backend_port), values) + "\n", flush=True)
        (block or wait_for_exit)(processes)
        return 1 if any(p.poll() not in (None, 0) for p in processes) else 0
    except KeyboardInterrupt:
        print("\nStopping ShopSense...")
        return 0
    finally:
        for process in reversed(processes):
            stop(process)


def wait_for_exit(processes: list[subprocess.Popen[bytes]]) -> None:
    """Block until Ctrl+C, or until either process stops on its own (then the other is stopped too)."""
    while all(p.poll() is None for p in processes):
        time.sleep(0.5)
    print("One part stopped on its own (see its output above); stopping the rest.")


if __name__ == "__main__":
    sys.exit(main())
