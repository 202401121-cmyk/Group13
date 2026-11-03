"""scripts/check_request_saved.py: "saved" or "not found", and never the URL or the row."""

from __future__ import annotations

from typing import Any

import check_request_saved as check
import pytest

# Built from parts so secret scanners don't flag a fake password in the source.
URL = "postgresql://postgres.ref:" + "hunter2-DO-NOT-PRINT" + "@pooler.example:5432/postgres?sslmode=require"
SESSION = "sess_0123456789abcdef01234567"


class FakeConnection:
    def __init__(self, found: bool) -> None:
        self.found = found
        self.sent: list[tuple[str, tuple[str, ...]]] = []

    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, query: str, params: tuple[str, ...]) -> FakeConnection:
        self.sent.append((query, params))
        return self

    def fetchone(self) -> tuple[bool]:
        return (self.found,)


@pytest.fixture(autouse=True)
def _no_real_env_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(check, "find_env_file", lambda: None)


def test_a_saved_request(capsys: pytest.CaptureFixture[str]) -> None:
    conn = FakeConnection(True)
    assert check.main([SESSION], {"DATABASE_URL": URL}, lambda url: conn) == 0
    assert capsys.readouterr().out == "saved\n"
    assert conn.sent == [(check.QUERY, (SESSION,))]  # a parameter, never pasted into the SQL


def test_a_missing_request(capsys: pytest.CaptureFixture[str]) -> None:
    assert check.main([SESSION], {"DATABASE_URL": URL}, lambda url: FakeConnection(False)) == 1
    assert capsys.readouterr().out == "not found\n"


def test_an_unreachable_database_shows_no_url(capsys: pytest.CaptureFixture[str]) -> None:
    def fail(url: str) -> Any:
        raise OSError(f"could not connect to {url}")

    assert check.main([SESSION], {"DATABASE_URL": URL}, fail) == 2
    out, err = capsys.readouterr()
    assert out == "" and "OSError" in err
    assert "hunter2" not in err and "pooler.example" not in err


def test_no_database_url(capsys: pytest.CaptureFixture[str]) -> None:
    assert check.main([SESSION], {}) == 2
    assert "DATABASE_URL is not set" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [[], ["not-a-session"], ["sess_x"], [SESSION, SESSION], ["sess_abc'; drop"]])
def test_only_one_well_formed_session_id_is_accepted(argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    assert check.main(argv, {"DATABASE_URL": URL}, lambda url: FakeConnection(True)) == 2
    assert capsys.readouterr().out == ""
