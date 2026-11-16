"""US-39 faults (build rule 9): a classifier or an answer that fails in any way never crashes or stalls a turn."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

import pytest

from app.contracts import (
    STAGE_TIME_LIMITS_S,
    InterviewTurn,
    LLMUnavailable,
    Provider,
    StageContext,
    StageName,
    StageStatus,
)
from app.interview import InterviewStage
from app.interview.replies import HEADROOM_S
from app.llm import InMemoryCallLog, WrapperLLMClient, load_llm_config
from app.llm.errors import ProviderTimeout
from app.pipeline import run_stage
from tests.unit.interview.test_stage import state_for


class _Raises:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def complete(self, *_: Any, **__: Any) -> Any:
        raise self.error


def _turn(text: str) -> tuple[InterviewStage, InterviewTurn]:
    stage = InterviewStage()
    first = stage.run(InterviewTurn(state=state_for()), StageContext(search_id="srch_replyfault1"))
    return stage, InterviewTurn(state=first.state, reply_text=text)


def test_an_unavailable_classifier_is_an_answer_and_the_turn_is_ok() -> None:
    """The wrapper turns a timeout or a provider error into LLMUnavailable; the turn then reads it as an answer."""
    _, turn = _turn("it is sort of whatever, maybe")
    stage = InterviewStage(llm=_Raises(LLMUnavailable("down")))
    out, report = run_stage(stage, turn, StageContext(search_id="srch_replyfault2"), 5.0)
    assert report.status is StageStatus.OK
    assert out is not None and out.state.history[0].status == "answered"


def test_any_other_classifier_error_is_a_classifier_failure_too() -> None:
    """Criterion 11: whatever the classifier raises, the reply is an answer the shopper can change, and the turn goes
    on: the runner's fallback, which skips the question, is never needed."""
    _, turn = _turn("it is sort of whatever, maybe")
    stage = InterviewStage(llm=_Raises(RuntimeError("a bug")))
    out, report = run_stage(stage, turn, StageContext(search_id="srch_replyfault3"), 5.0)
    assert report.status is StageStatus.OK
    assert out is not None
    assert out.state.history[0].status == "answered"


class _Hangs:
    """A provider that never answers: it waits out each request's timeout, as a stalled connection does."""

    def __init__(self, provider: Provider) -> None:
        self.provider = provider
        self.timeouts: list[float] = []
        self.models: list[str] = []

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
        self.models.append(model)
        time.sleep(timeout_s)
        raise ProviderTimeout(self.provider, f"no reply in {timeout_s:.1f} s")


def test_a_provider_that_hangs_ends_in_an_answer_inside_the_turn_never_a_skipped_question() -> None:
    """The classifier's deadline ends 1 s before the turn's 5 s (HEADROOM_S), so its timeout always reaches criterion
    11 (an answer the shopper can change). The runner's own limit never comes first, so its fallback, which skips the
    question, never runs. The real wrapper, the real 5 s."""
    adapters = {p: _Hangs(p) for p in Provider}
    client = WrapperLLMClient(load_llm_config(), dict(adapters), InMemoryCallLog(), limiters={})
    _, turn = _turn("it is sort of whatever, maybe")
    limit = STAGE_TIME_LIMITS_S[StageName.INTERVIEW]
    started = time.monotonic()
    out, report = run_stage(InterviewStage(llm=client), turn, StageContext(search_id="srch_replyfault4"), limit)
    took = time.monotonic() - started
    assert limit == 5
    assert report.status is StageStatus.OK  # not TIMEOUT: the runner's fallback never ran
    assert out is not None
    cpu = out.state.history[0]
    assert cpu.status == "answered"  # never "skipped"
    assert cpu.classification is not None and cpu.classification.classified_by == "fallback"
    assert adapters[Provider.GROQ].timeouts  # it did ask, and waited
    assert took <= limit - HEADROOM_S + 0.5  # the call ended by the 4 s line, with the turn's time to spare


def test_when_both_groq_models_hang_the_reply_is_still_an_answer_inside_the_turn() -> None:
    """classify_reply's first attempt may use its whole deadline (4 s: 5 s less the 1 s headroom), and its hedge asks
    gpt-oss-20b at 1.5 s with what is left. When both hang there is no third model and no retry: the reply counts as
    an answer inside the turn (criterion 11), never a skipped question."""
    adapters = {p: _Hangs(p) for p in Provider}
    client = WrapperLLMClient(load_llm_config(), dict(adapters), InMemoryCallLog(), limiters={})
    _, turn = _turn("it is sort of whatever, maybe")
    started = time.monotonic()
    out, report = run_stage(InterviewStage(llm=client), turn, StageContext(search_id="srch_replyfault5"), 5.0)
    took = time.monotonic() - started
    groq = adapters[Provider.GROQ]
    assert groq.models == ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]
    first, second = groq.timeouts
    assert first == pytest.approx(5.0 - HEADROOM_S, abs=0.15)  # the whole deadline
    assert second == pytest.approx(5.0 - HEADROOM_S - 1.5, abs=0.2)  # sent at 1.5 s, with what is left
    assert not any(a.timeouts for p, a in adapters.items() if p is not Provider.GROQ)
    assert report.status is StageStatus.OK
    assert out is not None and out.state.history[0].status == "answered"
    assert took <= 5.0 - HEADROOM_S + 0.5
