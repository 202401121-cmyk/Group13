"""The per-provider summary --claims prints (US-13): numbers only, with real 429s told apart from our own pacing."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from app.cli import llm_summary, main
from app.contracts import DataClass, LLMCallRecord, LLMTask, Provider

AT = datetime(2026, 11, 9, 8, 0, tzinfo=UTC)


def _record(
    provider: Provider = Provider.GEMINI, ms: int = 1000, outcome: str = "ok", input_tokens: int = 100
) -> LLMCallRecord:
    return LLMCallRecord(
        task=LLMTask.EXTRACT_CLAIMS,
        data_class=DataClass.PUBLIC,
        provider=provider,
        model="made-up-model",
        input_tokens=input_tokens,
        output_tokens=10,
        duration_ms=ms,
        outcome=outcome,  # type: ignore[arg-type]
        at=AT,
    )


def _row(summary: str, provider: str) -> list[str]:
    return next(line.split() for line in summary.splitlines() if line.strip().startswith(provider))


def test_the_summary_gives_median_and_slowest_per_provider() -> None:
    summary = llm_summary(
        [
            _record(ms=1000),
            _record(ms=2000),
            _record(ms=9000),
            _record(Provider.GROQ, ms=500),
            _record(Provider.GROQ, ms=1500),
        ]
    )
    assert "provider" in summary and "median" in summary and "slowest" in summary
    assert _row(summary, "gemini") == ["gemini", "3", "2.0", "s", "9.0", "s", "0", "0"]
    assert _row(summary, "groq") == ["groq", "2", "1.0", "s", "1.5", "s", "0", "0"]


def test_the_summary_counts_timeouts_and_429s() -> None:
    summary = llm_summary(
        [
            _record(ms=9000, outcome="timeout"),
            _record(ms=9000, outcome="timeout"),
            _record(ms=100, outcome="rate_limited"),
            _record(ms=2000),
        ]
    )
    (_, calls, _, _, _, _, timeouts, limited) = _row(summary, "gemini")
    assert (calls, timeouts, limited) == ("4", "2", "1")


def test_our_own_pacing_refusal_is_not_counted_as_a_429() -> None:
    """Our limits said no, so nothing went out: it isn't a call, or a 429 from the provider."""
    summary = llm_summary([_record(ms=2000), _record(ms=0, outcome="rate_limited", input_tokens=0)])
    (_, calls, _, _, _, _, _, limited) = _row(summary, "gemini")
    assert (calls, limited) == ("1", "0")


def test_a_call_skipped_for_the_daily_budget_is_not_counted() -> None:
    summary = llm_summary([_record(ms=0, outcome="skipped_budget", input_tokens=0)])
    assert "gemini" not in summary


def test_a_provider_with_only_refusals_has_no_row() -> None:
    summary = llm_summary([_record(ms=0, outcome="rate_limited", input_tokens=0), _record(Provider.GROQ)])
    assert "gemini" not in summary
    assert "groq" in summary


def test_the_summary_says_so_when_no_call_went_out() -> None:
    assert "No LLM calls went out." in llm_summary([])


def test_the_summary_holds_numbers_only() -> None:
    summary = llm_summary([_record()])
    assert "made-up-model" not in summary


@pytest.mark.parametrize(
    ("times", "median"),
    [([1000], "1.0"), ([1000, 3000], "2.0"), ([1000, 2000, 30000], "2.0")],
)
def test_the_median_is_the_middle_time(times: list[int], median: str) -> None:
    summary = llm_summary([_record(ms=ms) for ms in times])
    assert _row(summary, "gemini")[2] == median


def test_the_summary_is_printed_with_claims(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    client: Any = SimpleNamespace(call_log=SimpleNamespace(records=[_record(ms=2000)]))
    monkeypatch.setattr("app.cli.default_llm", lambda: client)
    assert main(["best laptop for coding under 70k", "--claims", "--no-db"]) == 0
    out = capsys.readouterr().out
    assert "LLM calls per provider:" in out
    assert _row(out, "gemini")[1] == "1"


def test_the_summary_is_not_printed_without_claims(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["best laptop for coding under 70k", "--no-db"]) == 0
    assert "LLM calls per provider:" not in capsys.readouterr().out


def test_a_client_with_no_call_log_prints_no_calls(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["best laptop for coding under 70k", "--claims", "--no-db"]) == 0
    assert "No LLM calls went out." in capsys.readouterr().out
