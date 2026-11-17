"""first_attempt_share (US-39): a task may let one attempt take more of its time limit than the wrapper's 60%. Only
classify_reply sets it, to its whole deadline; every other task keeps today's rule exactly. No real API is called."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest
from pydantic import BaseModel

from app.contracts import DataClass, Difficulty, LLMTask, LLMUnavailable, Provider
from app.llm import InMemoryCallLog, WrapperLLMClient, load_llm_config
from app.llm.config import LLMConfig
from app.llm.errors import ProviderTimeout
from app.llm.pool import ATTEMPT_SHARE
from app.llm.usage import TokenMeter


class Answer(BaseModel):
    answer: str


class _Records:
    """Records each attempt's timeout and fails at once, as a request that timed out would."""

    def __init__(self, provider: Provider) -> None:
        self.provider = provider
        self.timeouts: list[float] = []

    def call(
        self,
        model: str,
        system: str,
        user: str,
        max_output_tokens: int,
        timeout_s: float,
        extra: Mapping[str, object] | None = None,
    ) -> Any:
        self.timeouts.append(timeout_s)
        raise ProviderTimeout(self.provider, "no reply")


def _first_timeout(task: LLMTask, spent_s: float = 1.0) -> float:
    """The first attempt's timeout for a private, SHORT call of this task, with `spent_s` of its limit gone."""
    adapters = {p: _Records(p) for p in Provider}
    client = WrapperLLMClient(load_llm_config(), dict(adapters), InMemoryCallLog(), limiters={})
    with TokenMeter(already_spent_s=spent_s).counting(), pytest.raises(LLMUnavailable):
        client.complete(
            task, "A prompt.", "Made-up text.", Answer, data_class=DataClass.PRIVATE, difficulty=Difficulty.SHORT
        )
    return adapters[Provider.GROQ].timeouts[0]


def test_only_the_reply_classifier_sets_a_share_and_it_is_its_whole_deadline() -> None:
    assert load_llm_config().first_attempt_share == {LLMTask.CLASSIFY_REPLY: 1.0}


def test_the_reply_classifiers_first_attempt_may_use_its_whole_deadline() -> None:
    """The interview turn is 5 s and the classifier keeps 1 s back: its first attempt may take the other 4 s."""
    assert _first_timeout(LLMTask.CLASSIFY_REPLY, spent_s=1.0) == pytest.approx(4.0, abs=0.1)


@pytest.mark.parametrize("task", [LLMTask.WRITE_QUESTION, LLMTask.ANSWER_USER_QUESTION, LLMTask.DETECT_INTENT])
def test_an_unlisted_task_keeps_todays_60_percent_rule_exactly(task: LLMTask) -> None:
    """Same 5 s limit as the classifier (interview and intake), same time spent: 60% of 5 s, as before."""
    assert ATTEMPT_SHARE == 0.6
    assert _first_timeout(task, spent_s=1.0) == pytest.approx(5 * ATTEMPT_SHARE, abs=0.05)


def test_every_task_but_the_classifier_has_no_share_of_its_own() -> None:
    config = load_llm_config()
    assert all(config.first_attempt_share.get(task) is None for task in LLMTask if task is not LLMTask.CLASSIFY_REPLY)


@pytest.mark.parametrize("share", [0.0, -0.1, 1.5])
def test_a_share_must_be_above_0_and_at_most_1(share: float) -> None:
    config = load_llm_config().model_dump()
    with pytest.raises(ValueError, match="first_attempt_share"):
        LLMConfig.model_validate({**config, "first_attempt_share": {LLMTask.CLASSIFY_REPLY: share}})
