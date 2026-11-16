"""Build rule 8 for US-39's two calls: a reply is the shopper's own text (D-51), so it reaches only a provider that does
not train on what we send. The real wrapper runs here, with a fake reply from every provider."""

from __future__ import annotations

import json

import pytest

from app.contracts import DataClass, Difficulty, InterviewTurn, LLMTask, Provider, StageContext
from app.interview import InterviewStage
from app.interview.parsing import RawAnswer, RawReply
from app.llm import InMemoryCallLog, WrapperLLMClient, load_llm_config
from tests.fakes.llm import FakeAdapter

from .test_stage import state_for

CLASSIFIED = json.dumps({"reply_type": "question", "user_question": "does it matter for gaming?"})
ANSWERED = json.dumps({"answer": "It helps games too."})


def _wrapper() -> tuple[WrapperLLMClient, dict[Provider, FakeAdapter]]:
    adapters = {p: FakeAdapter(p, replies=(CLASSIFIED, ANSWERED)) for p in Provider}
    return WrapperLLMClient(load_llm_config(), dict(adapters), InMemoryCallLog(), limiters={}), adapters


def test_a_reply_and_its_question_reach_only_providers_that_do_not_train_on_them() -> None:
    client, adapters = _wrapper()
    stage = InterviewStage(llm=client)
    ctx = StageContext(search_id="srch_replyprivacy")
    first = stage.run(InterviewTurn(state=state_for()), ctx)
    step = stage.run(InterviewTurn(state=first.state, reply_text="does it matter for gaming?"), ctx)
    assert step.kind == "answer_then_repeat" and step.answer_source == "llm"
    trains = {p.name for p in load_llm_config().providers if p.trains_on_inputs}
    assert trains
    assert {p for p, a in adapters.items() if a.calls} == {Provider.GROQ}
    assert len(adapters[Provider.GROQ].calls) == 2  # the classification, then the answer


@pytest.mark.parametrize(
    ("task", "schema"), [(LLMTask.CLASSIFY_REPLY, RawReply), (LLMTask.ANSWER_USER_QUESTION, RawAnswer)]
)
def test_neither_task_can_be_sent_as_public_text(task: LLMTask, schema: type) -> None:
    client, adapters = _wrapper()
    with pytest.raises(ValueError, match="private"):
        client.complete(
            task, "A prompt.", "Made-up text.", schema, data_class=DataClass.PUBLIC, difficulty=Difficulty.SHORT
        )
    assert not any(a.calls for a in adapters.values())
