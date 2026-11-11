"""The keys verify is asked to resolve (US-17): the criteria, the price when there is a budget, then rule attributes."""

from __future__ import annotations

from typing import Any

import app.contracts as C
from app.contracts import examples as ex
from app.pipeline import InMemoryResultStore, PipelineRunner, default_search_stages
from app.verify import VerifyStage
from tests.unit.decide.build import criteria


class _Asked:
    """The verify stub, noting the keys it was asked for."""

    name = C.StageName.VERIFY
    is_placeholder = True

    def __init__(self) -> None:
        self.keys: list[str] = []

    def run(self, inp: C.VerifyInput, ctx: C.StageContext) -> C.VerifyResult:
        self.keys = list(inp.criterion_keys)
        return VerifyStage().run(inp, ctx)

    def fallback(self, inp: C.VerifyInput, ctx: C.StageContext, error: Exception) -> C.VerifyResult:
        return VerifyStage().fallback(inp, ctx, error)


def _keys(rubric: C.Rubric) -> list[str]:
    stages = default_search_stages()
    asked = _Asked()
    stages.verify = asked
    request = C.SearchRequest(session_id=ex.SESSION_ID, raw_query=ex.QUERY, rubric=rubric)
    PipelineRunner(stages, InMemoryResultStore()).run(request)
    return asked.keys


def _without_budget(**changes: Any) -> C.Rubric:
    return C.Rubric.model_validate({**ex.rubric().model_dump(), "budget": None, **changes})


def test_price_inr_is_asked_for_when_there_is_a_budget() -> None:
    weights = [w.key for w in ex.rubric().weights]
    assert _keys(ex.rubric()) == [*weights, "price_inr", "ram_gb"]


def test_no_budget_no_price_key() -> None:
    assert "price_inr" not in _keys(_without_budget())


def test_a_price_criterion_or_rule_is_not_asked_twice() -> None:
    rule = C.HardRule(attribute="price_inr", operator="<=", value=70000, source_text="under 70k")
    rubric = C.Rubric.model_validate({**ex.rubric().model_dump(), "hard_rules": [rule.model_dump()]})
    assert _keys(rubric).count("price_inr") == 1


def test_the_keys_stay_within_thirty() -> None:
    weights = [(f"criterion_{i}", 5.0) for i in range(10)]
    rules = [
        C.HardRule(attribute=f"spec_{i}", operator="==", value=True, source_text=f"rule {i}").model_dump()
        for i in range(20)
    ]
    rubric = C.Rubric.model_validate(
        {**ex.rubric().model_dump(), "weights": [w.model_dump() for w in criteria(weights)], "hard_rules": rules}
    )
    keys = _keys(rubric)
    assert len(keys) == 30
    assert keys[10] == "price_inr"  # the budget comes before the rules, so it is never the one cut
    assert "spec_19" not in keys
