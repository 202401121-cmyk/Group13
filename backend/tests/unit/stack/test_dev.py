"""scripts/dev.py: start-up, refusal on a busy port, and clean-up, with fake processes; plus one real tree kill."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import dev
import pytest

SECRET = "sk-fake-DO-NOT-PRINT-42"
ENV = {"GROQ_API_KEY": SECRET, "PATH": os.environ.get("PATH", "")}


@pytest.fixture(autouse=True)
def _no_real_env_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dev, "find_env_file", lambda: None)


class FakeProcess:
    def __init__(self, plan: dev.Plan) -> None:
        self.plan = plan
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode


class Harness:
    def __init__(self) -> None:
        self.started: list[FakeProcess] = []
        self.stopped: list[FakeProcess] = []

    def start(self, plan: dev.Plan) -> Any:
        process = FakeProcess(plan)
        self.started.append(process)
        return process

    def stop(self, process: Any) -> None:
        self.stopped.append(process)


def interrupt(_: list[Any]) -> None:
    raise KeyboardInterrupt


def run(harness: Harness, argv: list[str], ready: Callable[..., str | None] = lambda checks, procs: None) -> int:
    return dev.main(
        argv,
        environ=ENV,
        start=harness.start,
        stop=harness.stop,
        busy=lambda port: False,
        ready=ready,
        health=lambda port: {"status": "ok", "database": "off"},
        block=interrupt,
    )


def test_a_busy_port_stops_it_before_anything_starts(capsys: pytest.CaptureFixture[str]) -> None:
    harness = Harness()
    code = dev.main([], environ=ENV, start=harness.start, stop=harness.stop, busy=lambda port: port == 8000)
    assert code == 1 and harness.started == []
    assert "Port 8000 (backend) is already in use" in capsys.readouterr().out


def test_both_start_and_ctrl_c_stops_both(capsys: pytest.CaptureFixture[str]) -> None:
    harness = Harness()
    assert run(harness, []) == 0
    assert [p.plan.name for p in harness.started] == ["backend", "frontend"]
    assert {id(p) for p in harness.stopped} == {id(p) for p in harness.started}
    out = capsys.readouterr().out
    assert "http://localhost:3000  (live mode)" in out and "database: off" in out
    assert "LLM providers with a key: 1 of 5" in out
    assert SECRET not in out


def test_a_start_up_problem_stops_everything(capsys: pytest.CaptureFixture[str]) -> None:
    harness = Harness()
    code = run(harness, [], ready=lambda checks, procs: "not ready after 60 s: backend /api/health :8000")
    assert code == 1 and len(harness.stopped) == 2
    assert "Could not start ShopSense: not ready after 60 s" in capsys.readouterr().out


def test_live_mode_points_the_frontend_at_the_backend() -> None:
    harness = Harness()
    run(harness, ["--backend-port", "8100"])
    frontend = harness.started[1].plan
    assert frontend.env["VITE_API_MODE"] == "live"
    assert frontend.env["VITE_API_BASE_URL"] == "http://localhost:8100"
    assert frontend.cmd[-3:] == ["--port", "3000", "--strictPort"]


def test_the_backend_stops_within_5_seconds_even_with_a_progress_stream_open() -> None:
    """Without the flag, uvicorn waits for an open progress stream (US-12) to end, which can take a whole search."""
    harness = Harness()
    run(harness, [])
    backend = harness.started[0].plan
    assert backend.name == "backend"
    assert backend.cmd[backend.cmd.index("--timeout-graceful-shutdown") + 1] == "5"


def test_no_db_turns_the_database_off_for_the_backend_only() -> None:
    harness = Harness()
    run(harness, ["--no-db"])
    assert harness.started[0].plan.env["DATABASE_URL"] == ""
    assert "DATABASE_URL" not in harness.started[1].plan.env


def test_mock_starts_only_the_frontend_in_mock_mode() -> None:
    harness = Harness()
    run(harness, ["--mock"])
    assert [p.plan.name for p in harness.started] == ["frontend"]
    assert harness.started[0].plan.env["VITE_API_MODE"] == "mock"


def test_another_frontend_port_is_allowed_by_cors() -> None:
    harness = Harness()
    run(harness, ["--frontend-port", "3005"])
    assert harness.started[0].plan.env["FRONTEND_ORIGINS"] == "http://localhost:3005"


def test_waiting_gives_up_and_names_what_is_not_ready() -> None:
    now = [0.0]

    def tick(seconds: float) -> None:
        now[0] += seconds

    problem = dev.wait_until_ready(
        {"backend": lambda: False, "frontend": lambda: True}, [], timeout_s=2, clock=lambda: now[0], sleep=tick
    )
    assert problem == "not ready after 2 s: backend"


def test_waiting_stops_when_a_process_dies() -> None:
    harness = Harness()
    dead = harness.start(dev.Plan("backend", [], Path("."), {}))
    dead.returncode = 1
    assert dev.wait_until_ready({"backend": lambda: False}, [dead], sleep=lambda s: None) is not None


def _alive(pid: int) -> bool:
    if os.name == "nt":
        listing = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True, check=False)
        return str(pid) in listing.stdout
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def test_stop_tree_leaves_no_process_behind(tmp_path: Path) -> None:
    pid_file = tmp_path / "grandchild.pid"
    parent = (
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"open(r'{pid_file}', 'w').write(str(child.pid))\n"
        "time.sleep(60)\n"
    )
    process = dev.start_process(dev.Plan("test", [sys.executable, "-c", parent], tmp_path, dict(os.environ)))
    deadline = time.monotonic() + 20
    while not pid_file.exists() or not pid_file.read_text():
        assert time.monotonic() < deadline, "the child never started"
        time.sleep(0.1)
    grandchild = int(pid_file.read_text())
    dev.stop_tree(process)
    assert process.poll() is not None
    deadline = time.monotonic() + 10
    while _alive(grandchild) and time.monotonic() < deadline:
        time.sleep(0.2)
    assert not _alive(grandchild)
