"""Build rule 8 for US-20's task: the explanation prompt holds the shopper's priorities (D-51), so it reaches only a
provider that does not train on what we send. The real wrapper runs here, with a fake reply from every provider."""

from __future__ import annotations

import json

import pytest

from app.contracts import DataClass, Difficulty, LLMTask, Provider
from app.explain import ExplainStage
from app.explain.parsing import RawExplanations
from app.llm import InMemoryCallLog, WrapperLLMClient, load_llm_config
from tests.fakes.llm import FakeAdapter

from .build import CTX, explain_input

REPLY = json.dumps({"paragraphs": []})


def _wrapper() -> tuple[WrapperLLMClient, dict[Provider, FakeAdapter]]:
    adapters = {provider: FakeAdapter(provider, replies=(REPLY,)) for provider in Provider}
    return WrapperLLMClient(load_llm_config(), dict(adapters), InMemoryCallLog(), limiters={}), adapters


def test_the_explanation_prompt_reaches_only_providers_that_do_not_train_on_it() -> None:
    client, adapters = _wrapper()
    ExplainStage(client).run(explain_input(), CTX)
    config = load_llm_config()
    trains = {p.name for p in config.providers if p.trains_on_inputs}
    assert trains, "the check proves nothing without a provider that trains on inputs"
    assert {p for p, a in adapters.items() if a.calls} == {Provider.GROQ}
    assert all(not adapters[p].calls for p in trains)


def test_the_task_can_never_be_sent_as_public_text() -> None:
    client, adapters = _wrapper()
    with pytest.raises(ValueError, match="private"):
        client.complete(
            LLMTask.WRITE_EXPLANATION,
            "A prompt.",
            "Made-up facts.",
            RawExplanations,
            data_class=DataClass.PUBLIC,
            difficulty=Difficulty.MEDIUM,
        )
    assert not any(a.calls for a in adapters.values())
