"""Build rule 8 for D-91's task: the evidence call carries a product's name and its public quotes only, so it takes the
public route, and nothing the shopper typed or chose ever reaches it. The real wrapper runs here, with a fake reply from
every provider; the whole search runs too, with a made-up private word in the shopper's request."""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

import app.contracts as C
from app.contracts import DataClass, Difficulty, LLMTask, Provider, examples
from app.explain import ExplainStage
from app.explain.nli_settings import NliSettings
from app.llm import InMemoryCallLog, WrapperLLMClient, load_llm_config
from app.pipeline import InMemoryResultStore, PipelineRunner, SearchStages
from tests.fakes.llm import FakeAdapter
from tests.unit.test_sample_label import stub_stages

from .build import explain_input

REPLY = json.dumps({"sentences": []})
PRIVATE_WORD = "zorblaxian"  # made up: in the shopper's request only


def _wrapper() -> tuple[WrapperLLMClient, dict[Provider, FakeAdapter]]:
    adapters = {provider: FakeAdapter(provider, replies=(REPLY,)) for provider in Provider}
    return WrapperLLMClient(load_llm_config(), dict(adapters), InMemoryCallLog(), limiters={}), adapters


def _sent(adapters: dict[Provider, FakeAdapter]) -> list[str]:
    return [call.system + call.user for adapter in adapters.values() for call in adapter.calls]


def test_the_evidence_call_takes_the_public_route_and_carries_no_shoppers_choice() -> None:
    client, adapters = _wrapper()
    inp = explain_input()
    ExplainStage(client, settings=NliSettings(enabled=False)).run(inp, C.StageContext(search_id="srch_privacy1"))
    sent = _sent(adapters)
    assert len(sent) == 3  # one call for each of the top 3
    config = load_llm_config()
    first = config.routes[DataClass.PUBLIC][Difficulty.MEDIUM][0]
    assert {p for p, a in adapters.items() if a.calls} == {first}  # the public route's first member answered
    for text in sent:
        assert all(key not in text for key in inp.user_priorities)
        assert "priorit" not in text and "out of" not in text


def test_a_private_word_in_the_request_never_reaches_any_public_prompt() -> None:
    client, adapters = _wrapper()
    stages = SearchStages(
        **{**vars(stub_stages()), "explain": ExplainStage(client, settings=NliSettings(enabled=False))}
    )
    request = C.SearchRequest(
        session_id=examples.SESSION_ID, raw_query=f"a {PRIVATE_WORD} laptop for coding", rubric=examples.rubric()
    )
    result = PipelineRunner(stages, InMemoryResultStore()).run(request)
    sent = _sent(adapters)
    assert sent, "the check proves nothing without an evidence call"
    assert all(PRIVATE_WORD not in text and request.raw_query not in text for text in sent)
    reddit = [c.quote for c in examples.claims() if "reddit.com" in str(c.source_url)]
    assert reddit, "the check proves nothing without a Reddit quote in the search"
    assert all(quote not in text for quote in reddit for text in sent)  # community words are never sent
    assert result.explanations


class _Answer(BaseModel):
    ok: bool = True


def test_the_old_explanation_task_can_still_never_be_sent_as_public_text() -> None:
    client, adapters = _wrapper()
    with pytest.raises(ValueError, match="private"):
        client.complete(
            LLMTask.WRITE_EXPLANATION,
            "A prompt.",
            "Made-up facts.",
            _Answer,
            data_class=DataClass.PUBLIC,
            difficulty=Difficulty.MEDIUM,
        )
    assert not any(a.calls for a in adapters.values())
