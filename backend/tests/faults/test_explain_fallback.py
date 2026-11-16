"""The explain stage's fallback (build rule 9, US-20): any error hands over to the template, built from code's numbers.
The template is real, so the result is not marked "sample", and the report says FALLBACK with a reason."""

from __future__ import annotations

import logging

import pytest

from app.contracts import ExplainInput, ExplainResult, LLMUnavailable, StageContext, StageStatus
from app.explain import ExplainStage, TemplateExplainStage
from app.pipeline import made_by_placeholder, run_stage
from tests.fakes.llm import FakeLLMClient
from tests.unit.explain.build import explain_input


def _ctx() -> StageContext:
    """A fresh context each time, so a long test run can't spend its 150 seconds (see test_decide_fallback.py)."""
    return StageContext(search_id="srch_explain002")


class _Broken(ExplainStage):
    def run(self, inp: ExplainInput, ctx: StageContext) -> ExplainResult:
        raise ValueError("secret shopper text that must not reach the log")


def test_an_error_hands_over_to_the_template(caplog: pytest.LogCaptureFixture) -> None:
    stage = _Broken(FakeLLMClient())
    inp = explain_input()
    with caplog.at_level(logging.WARNING):
        out, report = run_stage(stage, inp, _ctx(), 15.0)
    assert report.status is StageStatus.FALLBACK
    assert out == TemplateExplainStage().run(inp, _ctx())
    assert out is not None and all(e.method == "template" for e in out.explanations)
    assert isinstance(stage.baseline, TemplateExplainStage)
    assert not made_by_placeholder(stage, report)  # the template is real numbers, not a sample
    assert "ValueError" in caplog.text
    assert "secret shopper text" not in caplog.text


def test_every_llm_provider_down_gives_the_template_as_intake_and_criteria_give_their_rules() -> None:
    """No provider answering is not a crash: the stage gives the template itself, and each explanation says so in its
    method. (The search's own note already says when every LLM call failed, US-31.)"""
    stage = ExplainStage(FakeLLMClient())
    out, report = run_stage(stage, explain_input(), _ctx(), 15.0)
    assert report.status is StageStatus.OK
    assert out is not None and [e.method for e in out.explanations] == ["template"] * 3
    assert out == TemplateExplainStage().run(explain_input(), _ctx())
    assert ExplainResult.model_validate_json(out.model_dump_json()) == out


def test_the_template_stage_itself_never_needs_the_llm() -> None:
    out, report = run_stage(TemplateExplainStage(), explain_input(), _ctx(), 15.0)
    assert report.status is StageStatus.OK
    assert out is not None and len(out.explanations) == 3


def test_a_raised_llm_error_is_the_reason_not_its_text() -> None:
    class _Down(ExplainStage):
        def run(self, inp: ExplainInput, ctx: StageContext) -> ExplainResult:
            raise LLMUnavailable("shopper words inside an error")

    _, report = run_stage(_Down(FakeLLMClient()), explain_input(), _ctx(), 15.0)
    assert report.status is StageStatus.FALLBACK
    assert "shopper words" not in (report.fallback_reason or "")
