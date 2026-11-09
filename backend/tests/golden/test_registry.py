"""The rule that makes "new parsers must add their cases before merging" automatic (US-48). See README.md."""

from __future__ import annotations

import json
import re
from importlib import import_module
from pathlib import Path

import pytest

from .registry import (
    GOLDEN,
    KINDS,
    MIN_CASES,
    PARSERS,
    Parser,
    cases_of,
    kinds_missing,
    llm_calls_found,
    parsers_short_of_cases,
    parsing_modules,
    unregistered_calls,
    unregistered_modules,
)

MODEL_OUTPUT_PARSERS = [parser for parser in PARSERS.values() if parser.model_output]


def test_every_parsing_module_is_registered() -> None:
    missing = unregistered_modules()
    assert not missing, f"add these to PARSERS in tests/golden/registry.py, with their cases: {sorted(missing)}"


@pytest.mark.parametrize("parser", PARSERS.values(), ids=lambda parser: parser.name)
def test_every_registered_module_still_exists(parser: Parser) -> None:
    assert import_module(parser.module) is not None


def test_every_registered_parser_has_at_least_15_cases() -> None:
    short = parsers_short_of_cases()
    assert not short, f"each parser needs at least {MIN_CASES} cases: {short}"


@pytest.mark.parametrize("parser", MODEL_OUTPUT_PARSERS, ids=lambda parser: parser.name)
def test_every_model_output_parser_covers_every_kind(parser: Parser) -> None:
    assert not kinds_missing(parser)


def test_every_llm_call_has_a_registered_parser() -> None:
    missing = unregistered_calls()
    assert not missing, f"these calls have no parser in tests/golden/registry.py: {sorted(missing)}"


def test_the_llm_calls_are_found() -> None:
    assert llm_calls_found() >= {
        "DETECT_INTENT: RawIntent",
        "GENERATE_CRITERIA: RawCriteria",
        "EXTRACT_CLAIMS: RawExtraction",
    }


def test_a_parser_that_reads_a_model_can_not_skip_the_kinds() -> None:
    assert not [parser.name for parser in PARSERS.values() if parser.calls and not parser.model_output]


@pytest.mark.parametrize("parser", MODEL_OUTPUT_PARSERS, ids=lambda parser: parser.name)
def test_each_kind_label_is_true_of_its_output(parser: Parser) -> None:
    """A label is only worth counting if the bytes match it. A lost byte-order mark would otherwise still pass."""
    checks = {
        "bom": lambda raw: raw.startswith("\ufeff"),
        "empty": lambda raw: raw == "",
        "fenced": lambda raw: "```" in raw,
        "trailing_comma": lambda raw: re.search(r",\s*[}\]]", raw) is not None,
        "prose": lambda raw: "{" not in raw,
        "huge": lambda raw: len(raw) >= 5000,
    }
    for case in cases_of(parser):
        check = checks.get(case.expected["kind"])
        assert check is None or check(case.raw), f"{case.name} isn't {case.expected['kind']}"


@pytest.mark.parametrize("parser", MODEL_OUTPUT_PARSERS, ids=lambda parser: parser.name)
def test_every_model_output_parser_has_a_100_kb_output(parser: Parser) -> None:
    assert any(len(case.raw) >= 100_000 for case in cases_of(parser))


@pytest.mark.parametrize("parser", PARSERS.values(), ids=lambda parser: parser.name)
def test_every_case_has_both_files_and_a_known_outcome(parser: Parser) -> None:
    folder = GOLDEN / parser.name / "cases"
    raws = {path.stem for path in folder.glob("*.raw")}
    expected = {path.name.removesuffix(".expected.json") for path in folder.glob("*.expected.json")}
    assert raws == expected, "each case is a .raw file and an .expected.json file"
    for case in cases_of(parser):
        assert case.expected["kind"] in KINDS, case.name
        if case.expected["outcome"] == "canonical":
            assert "object" in case.expected, case.name
        else:
            assert case.expected["outcome"] == "fallback", case.name
            assert case.expected["fallback"] in parser.fallbacks, case.name


def test_a_dropped_source_can_not_pass_as_a_clean_empty_answer() -> None:
    assert PARSERS["extract"].run("not json", {}).view != {"mentions": [], "claims": []}


# --- the rules themselves, on folders made for the purpose ------------------------------------------------------------


def _app(tmp_path: Path, files: dict[str, str]) -> Path:
    app = tmp_path / "app"
    for name, text in files.items():
        path = app / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return app


def test_a_new_parsing_module_is_found(tmp_path: Path) -> None:
    files = {
        "enrich/parsing.py": "",
        "explain/reply_parsing.py": "",
        "verify/parsing/__init__.py": "",
        "match/parser.py": "",
        "commerce/parse.py": "",
        "decide/parsers/one.py": "",
    }
    found = {
        "app.enrich.parsing",
        "app.explain.reply_parsing",
        "app.verify.parsing",
        "app.match.parser",
        "app.commerce.parse",
        "app.decide.parsers.one",
    }
    app = _app(tmp_path, files)
    assert parsing_modules(app) == found
    assert unregistered_modules(app) == found


def test_a_folder_above_the_app_does_not_count_as_a_parser(tmp_path: Path) -> None:
    assert parsing_modules(_app(tmp_path / "parsing", {"enrich/stage.py": ""})) == set()


def test_a_new_llm_call_is_found_wherever_its_model_lives(tmp_path: Path) -> None:
    code = "def run(llm):\n    return llm.complete(LLMTask.ENRICH_EXTRACT, 'system', 'user', Model)\n"
    assert unregistered_calls(_app(tmp_path, {"enrich/stage.py": code})) == {"ENRICH_EXTRACT: Model"}


def test_a_new_schema_on_an_old_task_is_found(tmp_path: Path) -> None:
    code = "def run(llm):\n    return llm.complete(LLMTask.DETECT_INTENT, 'system', 'user', RawOther)\n"
    assert unregistered_calls(_app(tmp_path, {"other/stage.py": code})) == {"DETECT_INTENT: RawOther"}


def test_a_call_with_keywords_is_read_the_same(tmp_path: Path) -> None:
    code = "def run(llm):\n    return llm.complete(task=LLMTask.ENRICH_EXTRACT, system='', user='', schema=Model)\n"
    assert unregistered_calls(_app(tmp_path, {"enrich/stage.py": code})) == {"ENRICH_EXTRACT: Model"}


def test_complete_passed_around_is_flagged(tmp_path: Path) -> None:
    code = "def run(pool, llm):\n    return pool.submit(llm.complete, LLMTask.ENRICH_EXTRACT, 's', 'u', Model)\n"
    assert unregistered_calls(_app(tmp_path, {"enrich/stage.py": code})) == {"<complete passed around in stage.py>"}


def test_an_llm_call_whose_task_is_not_written_out_is_flagged(tmp_path: Path) -> None:
    code = "def run(llm, task):\n    return llm.complete(task, 'system', 'user', Model)\n"
    assert unregistered_calls(_app(tmp_path, {"enrich/stage.py": code})) == {"<task not written out in stage.py>"}


def test_a_parser_with_too_few_cases_is_found(tmp_path: Path) -> None:
    for parser in PARSERS.values():
        folder = tmp_path / parser.name / "cases"
        folder.mkdir(parents=True)
        for i in range(MIN_CASES - 1):
            (folder / f"case-{i}.raw").write_text("x", encoding="utf-8")
            (folder / f"case-{i}.expected.json").write_text(json.dumps({"kind": "specific"}), encoding="utf-8")
    short = parsers_short_of_cases(tmp_path)
    assert short["intake"] == short["criteria"] == short["research_reddit"] == MIN_CASES - 1
    assert "extract" not in short  # its P10 cases count too


def test_a_missing_kind_is_found(tmp_path: Path) -> None:
    folder = tmp_path / "intake" / "cases"
    folder.mkdir(parents=True)
    (folder / "only.raw").write_text("x", encoding="utf-8")
    (folder / "only.expected.json").write_text(json.dumps({"kind": "fenced"}), encoding="utf-8")
    missing = kinds_missing(PARSERS["intake"], tmp_path)
    assert "fenced" not in missing and {"cut_off", "bom", "huge"} <= missing
