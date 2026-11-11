"""US-15, criterion 6 and build rule 9: when the match stage fails, its fallback keeps every name apart, the report says
FALLBACK, one line is logged, and the search still finishes with a valid result.
"""

from __future__ import annotations

import logging

import pytest

import app.contracts as C
from app.contracts import examples as ex
from app.entities import FuzzyMatchStage
from app.pipeline import InMemoryResultStore, PipelineRunner, default_search_stages


class _Exploding(FuzzyMatchStage):
    """A matcher that fails to start, as a model that will not load does (S3-15)."""

    def run(self, inp: C.MatchInput, ctx: C.StageContext) -> C.MatchResult:
        raise RuntimeError("the made-up model would not load")


def _request() -> C.SearchRequest:
    return C.SearchRequest(session_id=ex.SESSION_ID, raw_query=ex.QUERY, rubric=ex.rubric())


def test_a_failing_match_falls_back_and_the_report_says_so(caplog: pytest.LogCaptureFixture) -> None:
    stages = default_search_stages()
    stages.match = _Exploding()
    with caplog.at_level(logging.WARNING):
        result = PipelineRunner(stages, InMemoryResultStore()).run(_request())
    report = next(r for r in result.stage_reports if r.stage is C.StageName.MATCH)
    assert report.status is C.StageStatus.FALLBACK
    assert result.coverage_note is not None and "fallback" in result.coverage_note.casefold()
    names = {s.product.name for s in result.products}
    assert {"Nova Book 14", "Nova 14"} <= names  # kept apart: the fallback merges nothing
    lines = [r.getMessage() for r in caplog.records if r.name == "app.entities.stage"]
    assert lines == ["match fell back: every name is kept apart (RuntimeError)"]
    assert all("would not load" not in r.getMessage() for r in caplog.records)  # the type only, never the text
    assert result.decision is not None  # the search went on to the end


def test_a_failing_match_keeps_no_aliases() -> None:
    from app.db import InMemoryAliasStore

    aliases = InMemoryAliasStore()
    stages = default_search_stages()
    stages.match = _Exploding()
    PipelineRunner(stages, InMemoryResultStore(), aliases=aliases).run(_request())
    assert aliases.load(ex.rubric().category) == {}
