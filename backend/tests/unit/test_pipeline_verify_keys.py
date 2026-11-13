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


class _Told(_Asked):
    """Also notes the category and the filter keys the pipeline gives it (S3-10b)."""

    def __init__(self) -> None:
        super().__init__()
        self.category: str | None = None
        self.filters: dict[str, list[str]] = {}
        self.search_id = ""

    def use_filter_keys(self, search_id: str, keys: list[str]) -> None:
        self.filters[search_id] = list(keys)

    def run(self, inp: C.VerifyInput, ctx: C.StageContext) -> C.VerifyResult:
        self.category, self.search_id = inp.category, ctx.search_id
        return super().run(inp, ctx)


def test_verify_is_told_the_category_and_which_keys_are_filters() -> None:
    """Price and the hard rules' attributes are filters: a value only community gives for one stays Unknown."""
    stages = default_search_stages()
    told = _Told()
    stages.verify = told
    request = C.SearchRequest(session_id=ex.SESSION_ID, raw_query=ex.QUERY, rubric=ex.rubric())
    PipelineRunner(stages, InMemoryResultStore()).run(request)
    assert told.category == "laptop"
    assert told.filters == {told.search_id: ["price_inr", "ram_gb"]}  # the criteria are not filters


def test_a_rule_attribute_that_is_also_a_weight_is_a_filter_key() -> None:
    """DR-01: "battery life at least 8 hours" filters, so Reddit alone must never settle its value."""
    rule = C.HardRule(attribute="battery_life", operator=">=", value=8, unit="h", source_text="at least 8 hours")
    rubric = C.Rubric.model_validate({**ex.rubric().model_dump(), "hard_rules": [rule.model_dump()]})
    assert "battery_life" in [w.key for w in rubric.weights]
    stages = default_search_stages()
    told = _Told()
    stages.verify = told
    PipelineRunner(stages, InMemoryResultStore()).run(
        C.SearchRequest(session_id=ex.SESSION_ID, raw_query=ex.QUERY, rubric=rubric)
    )
    assert told.filters == {told.search_id: ["price_inr", "battery_life"]}


class _Forgets(_Told):
    def __init__(self) -> None:
        super().__init__()
        self.forgotten: list[str] = []

    def forget(self, search_id: str) -> None:
        self.forgotten.append(search_id)


class _FailsAndForgets(_Forgets):
    def run(self, inp: C.VerifyInput, ctx: C.StageContext) -> C.VerifyResult:
        self.search_id = ctx.search_id
        raise RuntimeError("made-up failure")


def test_the_filter_keys_are_forgotten_once_verify_is_done() -> None:
    for verify in (_Forgets(), _FailsAndForgets()):  # after a run, and after a fallback
        stages = default_search_stages()
        stages.verify = verify
        request = C.SearchRequest(session_id=ex.SESSION_ID, raw_query=ex.QUERY, rubric=ex.rubric())
        PipelineRunner(stages, InMemoryResultStore()).run(request)
        assert verify.forgotten == [verify.search_id]


def test_the_real_verify_keeps_no_filter_keys_after_a_search() -> None:
    from app.pipeline import new_search_id
    from app.verify import TrustVerifyStage

    stages = default_search_stages()
    assert isinstance(stages.verify, TrustVerifyStage)
    search_id = new_search_id()
    request = C.SearchRequest(session_id=ex.SESSION_ID, raw_query=ex.QUERY, rubric=ex.rubric())
    PipelineRunner(stages, InMemoryResultStore()).run(request, search_id=search_id)
    assert stages.verify.filter_keys(search_id) == frozenset({"price_inr"})  # the default: its own keys are gone
