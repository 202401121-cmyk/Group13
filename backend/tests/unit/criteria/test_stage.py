"""The criteria stage (US-03) with a fake LLM client and an in-memory store. No real provider is called."""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel

from app.contracts import CriteriaInput, CriteriaSet, DataClass, Difficulty, LLMTask, LLMUnavailable, StageContext
from app.criteria import CriteriaStage, InMemoryCriteriaStore, fallback_set
from app.criteria.parsing import RawCriteria
from app.criteria.prompts import RETRY_NOTE
from tests.fakes.llm import FakeLLMClient

CTX = StageContext(search_id="srch_test00001")
GAMING = CriteriaInput(category="laptop", category_label="Laptop", use_case="gaming")
TRAVEL = CriteriaInput(category="laptop", category_label="Laptop", use_case="Travel ")

T = TypeVar("T", bound=BaseModel)


def crit(key: str, vfm: bool = False) -> dict[str, object]:
    return {
        "key": key,
        "label": key.replace("_", " ").capitalize(),
        "definition": f"How well the laptop does on {key.replace('_', ' ')} for the way you will use it.",
        "default_weight": 6,
        "is_value_for_money": vfm,
    }


def answer(*keys: str) -> RawCriteria:
    return RawCriteria.model_validate({"criteria": [crit(k, vfm=k == "value_for_money") for k in keys]})


SEVEN = answer("gpu_power", "cpu_speed", "cooling", "refresh_rate", "keyboard_feel", "battery_life", "value_for_money")
FOUR = answer("gpu_power", "cpu_speed", "cooling", "refresh_rate")


class ScriptedLLM:
    """Returns the given answers in order (the last repeats) and records every call."""

    def __init__(self, *answers: RawCriteria | Exception) -> None:
        self.answers = answers
        self.calls: list[tuple[LLMTask, str, str | None]] = []

    def complete(
        self,
        task: LLMTask,
        system: str,
        user: str,
        schema: type[T],
        *,
        data_class: DataClass,
        difficulty: Difficulty,
        search_id: str | None = None,
        max_output_tokens: int = 1024,
    ) -> T:
        self.calls.append((task, user, search_id))
        reply = self.answers[min(len(self.calls), len(self.answers)) - 1]
        if isinstance(reply, Exception):
            raise reply
        assert isinstance(reply, schema)
        return reply


def test_a_generated_set_is_returned_and_stored() -> None:
    store = InMemoryCriteriaStore()
    result = CriteriaStage(ScriptedLLM(SEVEN), store).run(GAMING, CTX)
    assert result.source == "generated"
    assert (result.category, result.use_case) == ("laptop", "gaming")
    assert len(result.criteria) == 7
    assert store.get("laptop", "gaming") == result


def test_the_second_call_is_cached_and_makes_no_llm_call() -> None:
    llm, store = ScriptedLLM(SEVEN), InMemoryCriteriaStore()
    first = CriteriaStage(llm, store).run(GAMING, CTX)
    again = CriteriaStage(llm, store).run(
        CriteriaInput(category="laptop", category_label="Laptop", use_case="Gaming "), CTX
    )
    assert len(llm.calls) == 1
    assert again.source == "cached"
    assert again.criteria == first.criteria
    assert again.use_case == "Gaming"  # the request's own wording (the contract strips the space)


def test_a_different_use_case_calls_the_llm_again_with_that_use() -> None:
    llm, store = ScriptedLLM(SEVEN), InMemoryCriteriaStore()
    stage = CriteriaStage(llm, store)
    stage.run(GAMING, CTX)
    stage.run(TRAVEL, CTX)
    assert len(llm.calls) == 2
    assert "use: gaming" in llm.calls[0][1]
    assert "use: Travel" in llm.calls[1][1]


def test_the_call_uses_generate_criteria_and_the_search_id() -> None:
    llm = ScriptedLLM(SEVEN)
    CriteriaStage(llm).run(GAMING, CTX)
    ((task, user, search_id),) = llm.calls
    assert task is LLMTask.GENERATE_CRITERIA
    assert search_id == "srch_test00001"
    assert "category: Laptop" in user


def test_too_few_criteria_retries_exactly_once_then_succeeds() -> None:
    llm, store = ScriptedLLM(FOUR, SEVEN), InMemoryCriteriaStore()
    result = CriteriaStage(llm, store).run(GAMING, CTX)
    assert len(llm.calls) == 2
    assert RETRY_NOTE not in llm.calls[0][1]
    assert RETRY_NOTE in llm.calls[1][1]
    assert result.source == "generated"
    assert store.get("laptop", "gaming") == result


def test_still_too_few_after_the_retry_uses_the_fallback_and_stores_nothing() -> None:
    llm, store = ScriptedLLM(FOUR), InMemoryCriteriaStore()
    result = CriteriaStage(llm, store).run(GAMING, CTX)
    assert len(llm.calls) == 2  # exactly one retry
    assert result == fallback_set(GAMING)
    assert result.source == "fallback"
    assert store.sets == {}


def test_llm_unavailable_uses_the_fallback_without_a_retry() -> None:
    llm = ScriptedLLM(LLMUnavailable("all providers down"))
    result = CriteriaStage(llm).run(GAMING, CTX)
    assert len(llm.calls) == 1
    assert result == fallback_set(GAMING)


def test_no_llm_uses_the_fallback() -> None:
    assert CriteriaStage().run(GAMING, CTX) == fallback_set(GAMING)
    assert CriteriaStage(None, InMemoryCriteriaStore()).run(GAMING, CTX).source == "fallback"


def test_the_fake_client_with_no_answer_falls_back() -> None:
    assert CriteriaStage(FakeLLMClient()).run(GAMING, CTX).source == "fallback"


def test_a_fallback_set_is_not_cached_so_the_next_search_tries_the_llm() -> None:
    store = InMemoryCriteriaStore()
    CriteriaStage(ScriptedLLM(LLMUnavailable("down")), store).run(GAMING, CTX)
    llm = ScriptedLLM(SEVEN)
    assert CriteriaStage(llm, store).run(GAMING, CTX).source == "generated"
    assert len(llm.calls) == 1


def test_an_unknown_category_still_gets_criteria() -> None:
    inp = CriteriaInput(category="yoga_mat", category_label="Yoga mat", use_case="beginners")
    result = CriteriaStage().run(inp, CTX)
    assert isinstance(result, CriteriaSet)
    assert (result.category, result.source) == ("yoga_mat", "fallback")
