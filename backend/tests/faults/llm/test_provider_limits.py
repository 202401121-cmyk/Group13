"""Fault tests (US-46): dead keys, cool-downs and spent budgets never stop a search; the stage fallback answers."""

from __future__ import annotations

from time import monotonic

import pytest
from pydantic import BaseModel

from app.contracts import DataClass, Difficulty, IntakeInput, LLMTask, LLMUnavailable, Provider, StageContext
from app.intake import IntakeStage
from app.llm import InMemoryCallLog, WrapperLLMClient, load_llm_config
from app.llm.errors import AuthFailed, NoCredit, RateLimited
from app.llm.limits import DailyTokenBudget
from app.llm.providers import Adapter
from tests.fakes.llm import FakeAdapter


class Claims(BaseModel):
    claims: list[str]


def test_dead_and_cooling_providers_are_skipped_quickly_and_the_call_gives_up_cleanly() -> None:
    adapters: dict[Provider, Adapter] = {
        Provider.GROQ: FakeAdapter(Provider.GROQ, replies=(AuthFailed(Provider.GROQ, "HTTP 401"),)),
        Provider.CEREBRAS: FakeAdapter(Provider.CEREBRAS, replies=(NoCredit(Provider.CEREBRAS, "HTTP 402"),)),
        Provider.GEMINI: FakeAdapter(Provider.GEMINI, replies=(RateLimited(Provider.GEMINI, "HTTP 429"),)),
    }
    client = WrapperLLMClient(load_llm_config(), adapters, InMemoryCallLog(), limiters={})
    for _ in range(2):
        with pytest.raises(LLMUnavailable):
            client.complete(
                LLMTask.EXTRACT_CLAIMS,
                "Extract claims.",
                "Nova Book 14 has a great keyboard.",
                Claims,
                data_class=DataClass.PUBLIC,
                difficulty=Difficulty.LONG,
            )
    started = monotonic()
    with pytest.raises(LLMUnavailable):
        client.complete(
            LLMTask.EXTRACT_CLAIMS, "x", "y", Claims, data_class=DataClass.PUBLIC, difficulty=Difficulty.LONG
        )
    assert monotonic() - started < 0.5  # nothing is sent: every provider is dead or cooling down
    assert {p: len(a.calls) for p, a in adapters.items()} == {  # type: ignore[attr-defined]
        Provider.GROQ: 1,
        Provider.CEREBRAS: 1,
        Provider.GEMINI: 1,
    }


def test_intake_falls_back_to_its_rules_when_every_private_budget_is_spent() -> None:
    adapters: dict[Provider, Adapter] = {p: FakeAdapter(p) for p in Provider}
    budget = DailyTokenBudget({Provider.GROQ: 1, Provider.CEREBRAS: 1})
    log = InMemoryCallLog()
    client = WrapperLLMClient(load_llm_config(), adapters, log, budget=budget, limiters={})

    intent = IntakeStage(client).run(IntakeInput(raw_query="gaming laptop under 80k"), StageContext("srch_fault00046"))

    assert (intent.category, intent.detected_by) == ("laptop", "rules")
    assert all(a.calls == [] for a in adapters.values())  # type: ignore[attr-defined]
    assert {(r.provider, r.outcome, r.data_class) for r in log.records} == {
        (Provider.GROQ, "skipped_budget", DataClass.PRIVATE),
        (Provider.CEREBRAS, "skipped_budget", DataClass.PRIVATE),
    }
