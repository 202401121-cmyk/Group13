"""US-15, criterion 6 and build rule 9: when the match stage fails, its fallback keeps every name apart, the report says
FALLBACK, one line is logged, and the search still finishes with a valid result.
"""

from __future__ import annotations

import logging

import pytest

import app.contracts as C
from app.contracts import examples as ex
from app.entities import FuzzyMatchStage, ModelMatchStage
from app.pipeline import InMemoryResultStore, PipelineRunner, default_search_stages
from app.research import ResearchStage
from tests.fakes.research import review_source


class _Exploding(FuzzyMatchStage):
    """A matcher that fails to start, as a model that will not load does (S3-15)."""

    def run(self, inp: C.MatchInput, ctx: C.StageContext) -> C.MatchResult:
        raise RuntimeError("the made-up model would not load")


class _AlsoNamesNova14(ResearchStage):
    """The example sources, and one made-up page that calls Nova Book 14 "Nova 14": a name no source holds would be
    dropped before decide (US-40), and this test needs both names to reach the result."""

    def run(self, inp: C.ResearchInput, ctx: C.StageContext) -> C.ResearchResult:
        out = super().run(inp, ctx)
        page = review_source("https://made-up-reviews.example/nova-14", text="The Nova 14 is a fine laptop.")
        return C.ResearchResult.model_validate({**out.model_dump(), "sources": [*out.model_dump()["sources"], page]})


def _request() -> C.SearchRequest:
    return C.SearchRequest(session_id=ex.SESSION_ID, raw_query=ex.QUERY, rubric=ex.rubric())


def test_a_failing_match_falls_back_and_the_report_says_so(caplog: pytest.LogCaptureFixture) -> None:
    stages = default_search_stages()
    stages.research = _AlsoNamesNova14()
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


class _ModelExploding(ModelMatchStage):
    """The model stage failing past its own guards, as a bug in it would."""

    def run(self, inp: C.MatchInput, ctx: C.StageContext) -> C.MatchResult:
        raise RuntimeError("a made-up bug in the model stage")


def test_the_model_stage_falls_back_to_fuzzy_matching(caplog: pytest.LogCaptureFixture) -> None:
    """S3-15: the model stage's fallback is fuzzy matching (its baseline), so a fallback still merges what fuzzy
    merges; the report says FALLBACK and the runner logs the error's type only."""
    stages = default_search_stages()
    stages.research = _AlsoNamesNova14()
    stages.match = _ModelExploding()
    with caplog.at_level(logging.WARNING):
        result = PipelineRunner(stages, InMemoryResultStore()).run(_request())
    report = next(r for r in result.stage_reports if r.stage is C.StageName.MATCH)
    assert report.status is C.StageStatus.FALLBACK
    assert result.coverage_note is not None and "fallback" in result.coverage_note.casefold()
    inp = C.MatchInput(mentions=[C.ProductMention(raw_name=n, is_product=True) for n in ("Nova Book 14", "Nova 14")])
    assert _ModelExploding().fallback(inp, C.StageContext(search_id=ex.SEARCH_ID), RuntimeError()) == (
        FuzzyMatchStage().run(inp, C.StageContext(search_id=ex.SEARCH_ID))
    )
    assert any(r.getMessage() == "stage match: RuntimeError; using its fallback" for r in caplog.records)
    assert all("made-up bug" not in r.getMessage() for r in caplog.records)
    assert result.decision is not None
