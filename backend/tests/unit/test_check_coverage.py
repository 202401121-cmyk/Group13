"""The coverage gates (US-32): 70% on each core module, 80% on app/decide/."""

from __future__ import annotations

from pathlib import Path

import coverage
import pytest

from scripts.check_coverage import (
    CORE_GATE,
    CORE_MODULES,
    DECIDE_GATE,
    DECIDE_MODULE,
    ENTRY_POINTS,
    check,
    main,
    module_of,
    read_totals,
)

ALL_GOOD = {module: (100, 100) for module in [*CORE_MODULES, DECIDE_MODULE]}


def test_the_gates_are_the_agreed_ones() -> None:
    assert (CORE_GATE, DECIDE_GATE) == (70, 80)
    assert DECIDE_MODULE == "app/decide"
    assert DECIDE_MODULE not in CORE_MODULES
    for module in ("app/criteria", "app/intake", "app/interview", "app/llm", "app/research", "app/pipeline.py"):
        assert module in CORE_MODULES


def test_it_passes_at_the_gates() -> None:
    totals = {**ALL_GOOD, "app/llm": (70, 100), DECIDE_MODULE: (80, 100)}
    assert check(totals)[1] == []


def test_it_fails_below_70_on_a_core_module() -> None:
    _, failures = check({**ALL_GOOD, "app/llm": (69, 100)})
    assert failures == ["app/llm: 69.0% is below the 70% gate"]


def test_it_fails_below_80_on_decide() -> None:
    _, failures = check({**ALL_GOOD, DECIDE_MODULE: (79, 100)})
    assert failures == ["app/decide: 79.0% is below the 80% gate"]


def test_a_core_module_with_no_data_fails() -> None:
    totals = {k: v for k, v in ALL_GOOD.items() if k != "app/intake"}
    assert check(totals)[1] == ["app/intake: no coverage data"]


def test_decide_with_no_data_fails() -> None:
    totals = {k: v for k, v in ALL_GOOD.items() if k != DECIDE_MODULE}
    assert check(totals)[1] == ["app/decide: no coverage data"]


def test_it_prints_every_module() -> None:
    lines, _ = check(ALL_GOOD)
    text = "\n".join(lines)
    for module in [*CORE_MODULES, DECIDE_MODULE]:
        assert module in text
    assert "100.0%" in text


def test_a_new_module_on_no_list_fails() -> None:
    """A rename is caught by "no data"; a new folder is caught here, or it would sit outside every gate."""
    _, failures = check({**ALL_GOOD, "app/ranking": (0, 100)})
    assert failures == ["app/ranking: on no list. Add it to CORE_MODULES or ENTRY_POINTS in scripts/check_coverage.py"]


def test_the_entry_points_are_outside_the_module_gates() -> None:
    totals = {**ALL_GOOD, **dict.fromkeys(ENTRY_POINTS, (0, 100))}
    assert check(totals)[1] == []
    assert {"app/api", "app/cli.py", "app/main.py"} <= set(ENTRY_POINTS)


def test_a_module_with_no_statements_counts_as_covered() -> None:
    assert check({**ALL_GOOD, "app/verify": (0, 0)})[1] == []


@pytest.mark.parametrize(
    ("path", "module"),
    [
        ("/repo/backend/app/llm/client.py", "app/llm"),
        ("C:\\repo\\backend\\app\\llm\\client.py", "app/llm"),
        ("/repo/backend/app/research/reddit/base.py", "app/research"),
        ("/repo/backend/app/pipeline.py", "app/pipeline.py"),
        ("/repo/backend/app/__init__.py", "app/__init__.py"),
        ("/repo/backend/scripts/dev.py", None),
    ],
)
def test_a_file_maps_to_its_module(path: str, module: str | None) -> None:
    assert module_of(path) == module


def _measured(tmp_path: Path) -> Path:
    """Real coverage data for one small file: two of its three statements ran."""
    source = tmp_path / "app" / "llm" / "client.py"
    source.parent.mkdir(parents=True)
    source.write_text("a = 1\nb = 2\nc = 3\n", encoding="utf-8")
    data_file = tmp_path / ".coverage"
    data = coverage.CoverageData(basename=str(data_file))
    data.add_lines({str(source): [1, 2]})
    data.write()
    return data_file


def test_it_reads_real_coverage_data(tmp_path: Path) -> None:
    assert read_totals(_measured(tmp_path)) == {"app/llm": (2, 3)}


def test_it_exits_non_zero_when_a_gate_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--data-file", str(_measured(tmp_path))]) == 1
    out = capsys.readouterr().out
    assert "app/llm: 66.7% is below the 70% gate" in out
    assert "app/decide: no coverage data" in out


def test_it_exits_zero_when_every_gate_passes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    data_file = _measured(tmp_path)
    monkeypatch.setattr("scripts.check_coverage.read_totals", lambda path: ALL_GOOD)
    assert main(["--data-file", str(data_file)]) == 0


def test_missing_coverage_data_fails_with_a_clear_message(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--data-file", str(tmp_path / "nothing")]) == 1
    assert "Run pytest with --cov first" in capsys.readouterr().err
