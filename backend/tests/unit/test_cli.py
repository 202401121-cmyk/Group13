"""The command-line search (US-52)."""

from __future__ import annotations

import pytest

from app.cli import main


def test_cli_prints_a_ranked_list_with_sources(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["best laptop for coding under 70k"]) == 0
    out = capsys.readouterr().out
    assert "Nova Book 14" in out
    assert "Vela Pro 14" in out  # the can't-tell product and its reason
    assert "1. Nova Book 14" in out
    assert "source: https://" in out
    assert "Filtered out:" in out and "Aurora Air 13" in out
    assert "₹70,000" in out  # the rupee sign survives, not "?"
    assert "Coverage note: YouTube timed out." in out


def test_cli_shows_what_it_understood(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["best laptop for coding under 70k"]) == 0
    assert "Understood: Laptop · budget up to ₹70,000 · region IN" in capsys.readouterr().out


def test_cli_asks_instead_of_searching_when_the_product_is_unclear(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["watch under 5k"]) == 1
    out = capsys.readouterr().out
    assert "Which kind of watch do you mean?" in out
    assert "1. Smartwatch" in out
    assert "Results for" not in out  # no research until the category is known


def test_cli_lists_the_criteria_with_their_definitions(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["standing desk under 20k"]) == 0
    out = capsys.readouterr().out
    assert "Criteria (fallback):" in out  # no LLM in tests, so the standing desk's own set
    assert "  - Height range: How low and high the desk goes" in out
    assert "  - Value for money: " in out


def test_cli_skips_every_question_by_default_and_prints_the_rubric(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["best laptop for coding under 70k"]) == 0
    out = capsys.readouterr().out
    assert "Q: How much do you care about processor speed?" in out
    assert out.count("A: Skip") == 8  # the laptop set has 8 criteria
    assert "Rubric (what matters to you):" in out
    assert "  - Processor speed: 9/10, " in out and "(skipped)" in out


def test_cli_interactive_reads_answers(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    answers = iter(["High", "not sure", "3"])

    def fake_input(prompt: str = "") -> str:
        try:
            return next(answers)
        except StopIteration:
            raise EOFError from None  # out of answers: the CLI treats the rest as skips

    monkeypatch.setattr("builtins.input", fake_input)
    assert main(["best laptop for coding under 70k", "--interactive"]) == 0
    out = capsys.readouterr().out
    assert "Please pick one of the buttons, or type a number from 0 to 10." in out
    assert "  - Processor speed: 8/10, " in out and "(answered)" in out
    assert "  - RAM and storage: 3/10, " in out
