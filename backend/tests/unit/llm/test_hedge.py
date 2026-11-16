"""hedge (US-39): classify_reply, and only it, sends the same request to gpt-oss-20b when gpt-oss-120b hasn't answered
by 1.5 s. The first valid answer wins; the other is ignored; both are booked and recorded. Groq only, inside the
classifier's 4 s deadline. The real wrapper, with a scripted Groq that answers each model after its own delay."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

import pytest
from pydantic import BaseModel

from app.contracts import DataClass, Difficulty, LLMTask, LLMUnavailable, Provider
from app.llm import InMemoryCallLog, WrapperLLMClient, load_llm_config
from app.llm.config import LLMConfig
from app.llm.errors import ProviderTimeout
from app.llm.providers import RawCompletion
from app.llm.usage import TokenMeter
from tests.fakes.llm import FakeAdapter

BIG, SMALL, QWEN = "openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b"
SPENT_S = 1.0  # the interview's headroom: the classifier's deadline is 4 s of the turn's 5 s


class Answer(BaseModel):
    answer: str


class _Groq:
    """Each model answers after its own delay, or hangs (None) until its attempt's timeout."""

    def __init__(self, delays: Mapping[str, float | None], fails: Mapping[str, Exception | str] | None = None) -> None:
        self.delays = dict(delays)
        self.fails = dict(fails or {})  # model -> an error to raise, or a reply that isn't the schema's JSON
        self.started: list[tuple[str, float]] = []
        self.t0 = time.monotonic()

    def call(
        self,
        model: str,
        system: str,
        user: str,
        max_output_tokens: int,
        timeout_s: float,
        extra: Mapping[str, object] | None = None,
    ) -> Any:
        self.started.append((model, time.monotonic() - self.t0))
        delay = self.delays.get(model)
        if delay is None or delay >= timeout_s:
            time.sleep(timeout_s)
            raise ProviderTimeout(Provider.GROQ, f"no reply in {timeout_s:.1f} s")
        time.sleep(delay)
        failure = self.fails.get(model)
        if isinstance(failure, Exception):
            raise failure
        text = failure if isinstance(failure, str) else f'{{"answer": "{model}"}}'
        return RawCompletion(text=text, input_tokens=20, output_tokens=8)

    def models(self) -> list[str]:
        return [model for model, _ in self.started]


def _client(groq: _Groq) -> tuple[WrapperLLMClient, InMemoryCallLog]:
    adapters: dict[Provider, Any] = {p: FakeAdapter(p) for p in Provider}
    adapters[Provider.GROQ] = groq
    log = InMemoryCallLog()
    return WrapperLLMClient(load_llm_config(), adapters, log, limiters={}), log


def _ask(
    client: WrapperLLMClient, task: LLMTask = LLMTask.CLASSIFY_REPLY, groq: _Groq | None = None
) -> tuple[str, float]:
    started = time.monotonic()
    if groq is not None:
        groq.t0 = started
    with TokenMeter(already_spent_s=SPENT_S).counting():
        answer = client.complete(
            task, "A prompt.", "A made-up reply.", Answer, data_class=DataClass.PRIVATE, difficulty=Difficulty.SHORT
        )
    return answer.answer, time.monotonic() - started


def _records_settle(log: InMemoryCallLog, count: int) -> None:
    """The ignored attempt finishes in the background; wait (at most 5 s) for its record."""
    deadline = time.monotonic() + 5
    while len(log.records) < count and time.monotonic() < deadline:
        time.sleep(0.05)


def test_only_the_reply_classifier_hedges_to_gpt_oss_20b_after_1_5_s() -> None:
    hedge = load_llm_config().hedge
    assert set(hedge) == {LLMTask.CLASSIFY_REPLY}
    assert (hedge[LLMTask.CLASSIFY_REPLY].model, hedge[LLMTask.CLASSIFY_REPLY].after_s) == (SMALL, 1.5)


def test_a_quick_first_answer_sends_no_second_request() -> None:
    groq = _Groq({BIG: 0.2, SMALL: 0.2})
    client, log = _client(groq)
    answer, took = _ask(client)
    assert answer == BIG
    assert groq.models() == [BIG]
    assert took < 1.0
    assert [r.model for r in log.records] == [BIG]


def test_first_slow_second_wins() -> None:
    groq = _Groq({BIG: 3.0, SMALL: 0.2})
    client, log = _client(groq)
    answer, took = _ask(client, groq=groq)
    assert answer == SMALL
    assert groq.models() == [BIG, SMALL]
    second_started = dict(groq.started)[SMALL]
    assert 1.4 <= second_started <= 1.8  # sent at 1.5 s
    assert took < 2.3  # it did not wait for the slow first model
    _records_settle(log, 2)
    assert sorted(r.model for r in log.records) == sorted([BIG, SMALL])  # both booked and counted


def test_first_wins_second_ignored() -> None:
    groq = _Groq({BIG: 2.0, SMALL: 1.8})  # both sent; the first answers at 2.0 s, the second would at 3.3 s
    client, log = _client(groq)
    answer, took = _ask(client)
    assert answer == BIG
    assert groq.models() == [BIG, SMALL]
    assert took < 2.6
    _records_settle(log, 2)
    assert {r.model: r.outcome for r in log.records} == {BIG: "ok", SMALL: "ok"}  # answered, and ignored


def test_both_slow_ends_inside_the_deadline_with_no_third_model() -> None:
    groq = _Groq({BIG: None, SMALL: None})
    client, _ = _client(groq)
    started = time.monotonic()
    with pytest.raises(LLMUnavailable):
        _ask(client)
    took = time.monotonic() - started
    assert groq.models() == [BIG, SMALL]  # never qwen, never a retry
    assert took <= 5.0 - SPENT_S + 0.5


@pytest.mark.parametrize("task", [LLMTask.ANSWER_USER_QUESTION, LLMTask.DETECT_INTENT, LLMTask.WRITE_QUESTION])
def test_every_other_task_never_sends_a_second_request_at_once(task: LLMTask) -> None:
    """The same slow first model: an unlisted task waits for it, as before, and never asks gpt-oss-20b alongside."""
    groq = _Groq({BIG: 2.0, SMALL: 0.2})
    client, log = _client(groq)
    answer, _ = _ask(client, task)
    assert answer == BIG
    assert groq.models() == [BIG]
    assert [r.model for r in log.records] == [BIG]


def test_a_hedge_must_name_a_known_model() -> None:
    config = load_llm_config().model_dump()
    with pytest.raises(ValueError, match="hedge names no such model"):
        LLMConfig.model_validate(
            {**config, "hedge": {LLMTask.CLASSIFY_REPLY: {"after_s": 1.5, "model": "made/up-model"}}}
        )
    with pytest.raises(ValueError):
        LLMConfig.model_validate({**config, "hedge": {LLMTask.CLASSIFY_REPLY: {"after_s": 0, "model": SMALL}}})


def test_private_text_never_hedges_to_a_provider_that_trains_on_it() -> None:
    """The second request comes only from the models the route and the data class allow: Groq's, for private text."""
    config = load_llm_config()
    gemini = next(m.model for m in config.models if m.provider is Provider.GEMINI)
    changed = LLMConfig.model_validate(
        {**config.model_dump(), "hedge": {LLMTask.CLASSIFY_REPLY: {"after_s": 0.2, "model": gemini}}}
    )
    groq = _Groq({BIG: 0.6, SMALL: 0.2})
    adapters: dict[Provider, Any] = {p: FakeAdapter(p) for p in Provider}
    adapters[Provider.GROQ] = groq
    client = WrapperLLMClient(changed, adapters, InMemoryCallLog(), limiters={})
    answer, _ = _ask(client)
    assert answer == BIG
    assert not adapters[Provider.GEMINI].calls


def test_a_first_model_that_fails_fast_hands_over_at_once() -> None:
    """A 5xx at 0.2 s: the second request goes out then, not at 1.5 s, and wins."""
    from app.llm.errors import ServerError

    groq = _Groq({BIG: 0.2, SMALL: 0.2}, fails={BIG: ServerError(Provider.GROQ, "HTTP 503")})
    client, _ = _client(groq)
    answer, took = _ask(client, groq=groq)
    assert answer == SMALL
    assert dict(groq.started)[SMALL] < 1.0
    assert took < 1.2


def test_an_invalid_first_answer_lets_a_valid_second_win_with_no_retry() -> None:
    groq = _Groq({BIG: 0.2, SMALL: 0.2}, fails={BIG: "not json at all"})
    client, log = _client(groq)
    answer, _ = _ask(client, groq=groq)
    assert answer == SMALL
    assert groq.models() == [BIG, SMALL]  # one request a model: no retry of the invalid one
    assert [r.outcome for r in log.records] == ["invalid_output", "ok"]


def test_both_failing_fast_leaves_the_time_to_groqs_other_model() -> None:
    """Quick failures on both hedged models: the rest of the route (qwen, Groq's third) still gets its turn."""
    from app.llm.errors import ServerError

    down = ServerError(Provider.GROQ, "HTTP 503")
    groq = _Groq({BIG: 0.1, SMALL: 0.1, QWEN: 0.2}, fails={BIG: down, SMALL: down})
    client, _ = _client(groq)
    answer, took = _ask(client, groq=groq)
    assert answer == QWEN
    assert took < 4.0


def test_an_answer_landing_just_after_the_second_request_is_still_used() -> None:
    """The first model answers at 1.55 s, just after the second went out, and the second hangs: the first answer is
    read and wins, never lost (the race at the hand-over)."""
    groq = _Groq({BIG: 1.55, SMALL: None})
    client, _ = _client(groq)
    answer, took = _ask(client, groq=groq)
    assert answer == BIG
    assert took < 2.2


def test_a_full_length_timeout_counts_against_the_model_and_a_hedged_short_one_does_not() -> None:
    """gpt-oss-120b had its whole 4 s: its timeout counts toward a cool-down (three in a minute). gpt-oss-20b, sent at
    1.5 s with what was left, was cut short: its timeout doesn't count."""
    groq = _Groq({BIG: None, SMALL: None})
    client, _ = _client(groq)
    counted: list[str | None] = []
    real = client.health.record_failure

    def spy(provider: Provider, error: Exception, model: str | None = None) -> bool:
        counted.append(model)
        return real(provider, error, model)

    client.health.record_failure = spy  # type: ignore[method-assign]
    with pytest.raises(LLMUnavailable):
        _ask(client, groq=groq)
    deadline = time.monotonic() + 2  # the attempt's worker records its failure just after the call has given up
    while BIG not in counted and time.monotonic() < deadline:
        time.sleep(0.05)
    assert BIG in counted
    assert SMALL not in counted
