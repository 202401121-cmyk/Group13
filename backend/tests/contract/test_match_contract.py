"""US-15: the real match stage keeps to the MatchResult contract, from run and from its fallback."""

from __future__ import annotations

import re
from collections.abc import Sequence

import pytest

from app.contracts import MatchInput, MatchResult, StageContext, StageName
from app.contracts import examples as ex
from app.entities import FuzzyMatchStage, ModelMatchStage
from app.entities.model import ModelHolder
from app.entities.settings import MatcherSettings

CTX = StageContext(search_id="srch_aaaaaaaaaaaaaaaaaaaaaaaa")


def _input() -> MatchInput:
    return MatchInput(mentions=ex.mentions() * 3, recommender_counts=ex.recommender_counts())


@pytest.mark.parametrize("path", ["run", "fallback"])
def test_the_fuzzy_stage_gives_a_valid_match_result(path: str) -> None:
    stage = FuzzyMatchStage()
    out = stage.run(_input(), CTX) if path == "run" else stage.fallback(_input(), CTX, RuntimeError("made up"))
    assert MatchResult.model_validate_json(out.model_dump_json()) == out
    assert out.method == "fuzzy"
    assert set(out.alias_map) == {m.raw_name for m in ex.mentions()}
    assert all(re.fullmatch(r"prd_[A-Za-z0-9_-]{3,64}", p.id) for p in out.products)
    assert all(re.fullmatch(r"var_[A-Za-z0-9_-]{3,64}", v.id) for p in out.products for v in p.variants)


def test_the_fuzzy_stage_is_the_real_match_stage() -> None:
    stage = FuzzyMatchStage()
    assert stage.name is StageName.MATCH
    assert stage.is_placeholder is False


def test_the_recommender_counts_reach_the_products() -> None:
    out = FuzzyMatchStage().run(_input(), CTX)
    counted = {c.product_mention: c.distinct_recommenders for c in ex.recommender_counts()}
    by_name = {p.name: p.recommender_count for p in out.products}
    for name, people in counted.items():
        if name in by_name:
            assert by_name[name] >= people


class _Sure:
    """A made-up model: every open pair is the same product."""

    threshold = 0.9646

    def score(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
        return [1.0] * len(pairs)


@pytest.mark.parametrize("path", ["run", "fallback"])
def test_the_model_stage_gives_a_valid_match_result(path: str) -> None:
    """S3-15: the model stage's output keeps to the contract too, with method "model" when the model scored."""
    settings = MatcherSettings(repo="made-up/matcher", revision="0" * 40, threshold=0.9646, use="model")
    holder = ModelHolder(settings, load=lambda s: _Sure())
    holder.get(timeout=5)
    stage = ModelMatchStage(settings=settings, model=holder)
    ctx = StageContext(search_id=CTX.search_id)  # a fresh clock: the model's share is cut to the search's time left
    out = stage.run(_input(), ctx) if path == "run" else stage.fallback(_input(), ctx, RuntimeError("made up"))
    assert MatchResult.model_validate_json(out.model_dump_json()) == out
    assert out.method == ("model" if path == "run" else "fuzzy")
    assert set(out.alias_map) == {m.raw_name for m in ex.mentions()}
    assert stage.name is StageName.MATCH and stage.is_placeholder is False
