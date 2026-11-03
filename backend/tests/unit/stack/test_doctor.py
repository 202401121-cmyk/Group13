"""scripts/doctor.py: each check with fakes, and no value ever in its output."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import doctor
from doctor import FAIL, OK, WARN, Result

SECRET = "sk-fake-DO-NOT-PRINT-42"
URL = "postgresql://postgres.ref:hunter2-DO-NOT-PRINT@pooler.example:5432/postgres?sslmode=require"


def completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


def test_read_env_file_handles_comments_quotes_and_export(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "# a comment\nGROQ_API_KEY=abc   # inline comment\nexport A='x # y'\nB=\"two words\"\nEMPTY=\nnot a line\n",
        encoding="utf-8",
    )
    assert doctor.read_env_file(env) == {"GROQ_API_KEY": "abc", "A": "x # y", "B": "two words", "EMPTY": ""}
    assert doctor.read_env_file(None) == {}


def test_the_environment_wins_over_the_file(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("DATABASE_URL=from-file\nGROQ_API_KEY=k\n", encoding="utf-8")
    assert doctor.settings(env, {"DATABASE_URL": ""}) == {"DATABASE_URL": "", "GROQ_API_KEY": "k"}


def test_find_env_file_walks_up_like_python_dotenv(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("X=1\n", encoding="utf-8")
    deep = tmp_path / "backend" / "app"
    deep.mkdir(parents=True)
    assert doctor.find_env_file(deep) == tmp_path / ".env"


def test_key_checks_name_keys_but_never_show_values() -> None:
    results = doctor.check_keys({"GROQ_API_KEY": SECRET, "MISTRAL_API_KEY": SECRET, "SERPER_API_KEY": SECRET})
    text = doctor.render(results)
    assert results[0].status == OK and "2 of 5 set: GROQ_API_KEY, MISTRAL_API_KEY" in text
    assert "set: SERPER_API_KEY" in text
    assert SECRET not in text


def test_no_llm_key_is_a_warning_not_a_failure() -> None:
    assert doctor.check_keys({})[0].status == WARN


def test_the_database_is_optional() -> None:
    result = doctor.check_database({}, "python")
    assert (result.status, result.detail.split()[0]) == (OK, "off")


def test_a_reachable_database_shows_only_its_major_version() -> None:
    calls: list[dict[str, Any]] = []

    def runner(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append({"cmd": cmd, **kwargs})
        return completed("reachable 17\n")

    result = doctor.check_database({"DATABASE_URL": URL}, "python", runner)
    assert (result.status, result.detail) == (OK, "reachable, Postgres 17")
    assert URL not in " ".join(calls[0]["cmd"])  # never on a command line
    assert calls[0]["env"]["SHOPSENSE_DOCTOR_DB"] == URL
    assert URL not in doctor.render([result]) and "hunter2" not in doctor.render([result])


def test_an_unreachable_database_says_how_to_fix_it() -> None:
    result = doctor.check_database(
        {"DATABASE_URL": URL}, "python", lambda *a, **k: completed("unreachable OperationalError")
    )
    text = doctor.render([result])
    assert result.status == WARN
    assert "paused or unreachable (OperationalError)" in text and "Resume the project" in text
    assert "hunter2" not in text and "pooler.example" not in text


def test_cors_must_allow_the_frontend_origin() -> None:
    assert doctor.check_cors({}).status == OK
    assert doctor.check_cors({"FRONTEND_ORIGINS": "*"}).status == OK  # a wildcard is ignored, the default stays
    assert doctor.check_cors({"FRONTEND_ORIGINS": "https://staging.example.org"}).status == FAIL
    assert doctor.check_cors({"FRONTEND_ORIGIN": "http://localhost:3000"}).status == OK


def test_frontend_settings_are_checked() -> None:
    assert doctor.check_frontend_env({"VITE_API_MODE": "live"}).status == OK
    assert doctor.check_frontend_env({}).status == WARN
    wrong = doctor.check_frontend_env({"VITE_API_MODE": "live", "VITE_API_BASE_URL": "http://localhost:9000"})
    assert wrong.status == FAIL and "9000" in wrong.detail


def test_busy_ports_are_named() -> None:
    results = doctor.check_ports(lambda port: port == 8000)
    assert [(r.name, r.status) for r in results] == [("Backend port", WARN), ("Frontend port", OK)]
    assert "8000" in results[0].fix


def test_an_old_python_fails() -> None:
    assert doctor.check_python((3, 10)).status == FAIL
    assert doctor.check_python((3, 11)).status == OK


def test_a_missing_backend_install_fails_with_the_fix() -> None:
    result = doctor.check_backend_installed("python", lambda *a, **k: completed(returncode=1))
    assert result.status == FAIL and "pip install" in result.fix


def test_render_lists_fixes_only_for_problems() -> None:
    text = doctor.render([Result("A", OK, "fine", "never shown"), Result("B", FAIL, "broken", "do this")])
    assert "do this" in text and "never shown" not in text
