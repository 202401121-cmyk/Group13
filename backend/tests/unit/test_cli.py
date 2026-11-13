"""The command-line search (US-52)."""

from __future__ import annotations

import pytest

import app.contracts as C
from app.cli import main, render
from app.contracts import examples as ex
from app.db import InMemoryRequestStore
from app.pipeline import InMemoryResultStore, PipelineRunner
from tests.fakes.live_run import use_live_shaped_run
from tests.unit.test_pipeline_live_decide import _OnlyRam, live_stages


def test_cli_prints_a_ranked_list_with_sources(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real verify and decide (S3-10b) on a made-up live-shaped run: ranked, filtered out and can't tell."""
    use_live_shaped_run(monkeypatch)
    assert main(["best laptop for coding under 70k"]) == 0
    out = capsys.readouterr().out
    assert "Nova Book 14" in out
    assert "Vela Pro 14: We couldn't find its price" in out  # the can't-tell product and its reason
    assert "1. Nova Book 14: score " in out
    assert "source: https://" in out
    assert "Filtered out:" in out and "Aurora Air 13: ₹84,990 is above your ₹70,000 budget" in out
    assert "₹70,000" in out  # the rupee sign survives, not "?"
    assert "Coverage note: These are sample results" in out  # enrich, explain and commerce are stubs still
    assert "timed out" not in out  # no search ran, so nothing can have failed


def test_cli_with_the_stub_research_finds_no_price_so_cant_tell(capsys: pytest.CaptureFixture[str]) -> None:
    """The stubs' example claims carry no price, so with a budget the real decide can't tell for any product."""
    assert main(["best laptop for coding under 70k"]) == 0
    out = capsys.readouterr().out
    assert "No product passed your rules." in out
    assert "Can't tell:" in out and "Nova Book 14: We couldn't find its price" in out
    assert "Hint: Removing your ₹70,000 budget would let " in out


def test_cli_says_not_ranked_instead_of_a_missing_score() -> None:
    """Unknown is never shown as 0 (DR-09): with nothing scored, each product is listed as not ranked."""
    stages = live_stages()
    stages.extract = _OnlyRam()
    rubric = C.Rubric.model_validate({**ex.rubric().model_dump(), "budget": None, "hard_rules": []})
    request = C.SearchRequest(session_id=ex.SESSION_ID, raw_query="a made-up request", rubric=rubric)
    out = render(PipelineRunner(stages, InMemoryResultStore()).run(request))
    assert "  1. Aurora Air 13: not ranked, coverage 0%" in out
    assert "score None" not in out and "score 0" not in out
    assert "Not ranked: we found no scores for your criteria." in out


def test_cli_prints_the_relaxation_hint() -> None:
    result = ex.search_result()
    decision = C.DecideResult(
        ranked=[],
        filtered=[
            C.FilteredProduct(
                product_id="prd_aurora_air_13",
                group="filtered_out",
                reasons=["₹84,990 is above your ₹70,000 budget"],
                rule_attribute="price_inr",
            )
        ],
        relaxation_hint="Removing your ₹70,000 budget would let 1 product through.",
        method="weighted",
    )
    out = render(C.SearchResult.model_validate({**result.model_dump(), "decision": decision.model_dump()}))
    assert "No product passed your rules." in out
    assert "\nHint: Removing your ₹70,000 budget would let 1 product through." in out


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
    assert "Q: Processor speed: how much does it matter to you?" in out
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


# --- saving the request (US-01); tests/conftest.py keeps the real database out -------------------------------


def _store(monkeypatch: pytest.MonkeyPatch, fail: bool = False) -> InMemoryRequestStore:
    store = InMemoryRequestStore(fail=fail)
    monkeypatch.setattr("app.db.default_request_store", lambda: store)
    return store


def test_cli_saves_the_request_once_with_a_new_session_id(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    store = _store(monkeypatch)
    assert main(["best laptop for coding under 70k"]) == 0
    [row] = store.rows
    assert f"Saved: yes (session {row.session_id})" in capsys.readouterr().out
    assert row.request_text == "best laptop for coding under 70k"
    assert row.intent is not None and row.intent.category == "laptop"
    assert main(["best laptop for coding under 70k"]) == 0
    assert store.rows[1].session_id != row.session_id  # every run is its own session


def test_cli_no_db_does_not_save(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    store = _store(monkeypatch)
    assert main(["best laptop for coding under 70k", "--no-db"]) == 0
    assert "Saved: no, the database is off (no DATABASE_URL)" in capsys.readouterr().out
    assert store.rows == []


def test_cli_still_searches_when_the_database_is_down(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    use_live_shaped_run(monkeypatch)
    _store(monkeypatch, fail=True)
    assert main(["best laptop for coding under 70k"]) == 0
    out = capsys.readouterr().out
    assert "Saved: no, the database could not be reached. The search goes on." in out
    assert "1. Nova Book 14" in out


def test_cli_saves_an_unclear_request_without_an_intent(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    store = _store(monkeypatch)
    assert main(["watch under 5k"]) == 1
    [row] = store.rows
    assert row.intent is None and row.intent_source == "rules"
