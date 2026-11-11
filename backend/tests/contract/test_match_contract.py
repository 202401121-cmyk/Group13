"""US-15: the real match stage keeps to the MatchResult contract, from run and from its fallback."""

from __future__ import annotations

import re

import pytest

from app.contracts import MatchInput, MatchResult, StageContext, StageName
from app.contracts import examples as ex
from app.entities import FuzzyMatchStage

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
