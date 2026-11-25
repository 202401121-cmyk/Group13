"""D-101 (Om, 25 Nov 2026): enrich's one fact-reading call goes to the quickest model with room for public text, by
measured answer times, not a fixed order. One attempt: no retry, no second model, no hedge. Its caller leaves it the
chosen model's 90th percentile. Fake adapters only: no real API is called.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from app.contracts import DataClass, Difficulty, LLMTask, LLMUnavailable, Provider
from app.llm import InMemoryCallLog, WrapperLLMClient
from app.llm import load_llm_config as decided_llm_config
from app.llm.answer_times import AnswerTimes, percentile
from app.llm.config import LLMConfig
from app.llm.errors import AuthFailed, ModelForbidden, ProviderTimeout, RateLimited, ServerError
from app.llm.limits import RateLimiter
from app.llm.providers import Adapter
from tests.fakes.llm import FakeAdapter

TASK = LLMTask.ENRICH_EXTRACT
OK = '{"answer": "made up"}'
GEMINI, MISTRAL = "gemini", "mistral"


class Answer(BaseModel):
    answer: str


def _client(**replies: Any) -> tuple[WrapperLLMClient, dict[Provider, FakeAdapter]]:
    config = decided_llm_config()
    adapters = {p: FakeAdapter(p, replies=replies.get(p.value, (OK,))) for p in (Provider.GEMINI, Provider.MISTRAL)}
    typed: dict[Provider, Adapter] = dict(adapters)
    return WrapperLLMClient(config, typed, InMemoryCallLog(), limiters={}), adapters


def _model(client: WrapperLLMClient, provider: Provider) -> tuple[Provider, str]:
    found = next(m for m in client.config.models if m.provider is provider)
    return found.provider, found.model


def _times(client: WrapperLLMClient, provider: Provider, *seconds: float, task: LLMTask = TASK) -> None:
    for each in seconds:
        client.times.note(_model(client, provider), task, each)


def _ask(client: WrapperLLMClient) -> Answer:
    return client.complete(
        TASK, "Read.", "Made-up passages.", Answer, data_class=DataClass.PUBLIC, difficulty=Difficulty.LONG
    )


def test_the_one_call_goes_to_the_quickest_measured_model_not_the_routes_first() -> None:
    client, adapters = _client()
    _times(client, Provider.GEMINI, 4.0, 5.0, 4.5)
    _times(client, Provider.MISTRAL, 1.0, 1.2, 0.9, task=LLMTask.EXTRACT_CLAIMS)  # extraction's answers count too
    assert _ask(client).answer == "made up"
    assert (len(adapters[Provider.MISTRAL].calls), len(adapters[Provider.GEMINI].calls)) == (1, 0)


def test_models_not_measured_yet_follow_the_measured_ones_in_the_routes_order() -> None:
    client, _ = _client()
    _times(client, Provider.MISTRAL, 3.0, 3.0, 3.0)
    order = [m.provider for m in client.order(TASK, DataClass.PUBLIC, Difficulty.LONG)]
    assert order == [Provider.MISTRAL, Provider.GEMINI]  # the route is Gemini, Mistral, Groq; Groq has no key here


def test_with_nothing_measured_the_route_order_holds_and_no_rotation_moves() -> None:
    client, adapters = _client()
    for _ in range(3):
        _ask(client)
    assert len(adapters[Provider.GEMINI].calls) == 3  # never Mistral's turn: a fastest task takes no turn


@pytest.mark.parametrize(
    "reply",
    ["not json at all", ProviderTimeout(Provider.MISTRAL, "no reply"), ServerError(Provider.MISTRAL, "HTTP 503")],
)
def test_one_attempt_a_bad_answer_or_a_failure_ends_the_call(reply: object) -> None:
    client, adapters = _client(mistral=(reply, OK))
    _times(client, Provider.MISTRAL, 1.0, 1.0, 1.0)
    with pytest.raises(LLMUnavailable):
        _ask(client)
    assert (len(adapters[Provider.MISTRAL].calls), len(adapters[Provider.GEMINI].calls)) == (1, 0)


def test_a_quick_model_with_no_room_is_passed_over_for_the_next_quickest() -> None:
    client, adapters = _client()
    _times(client, Provider.MISTRAL, 1.0, 1.0, 1.0)
    _times(client, Provider.GEMINI, 2.0, 2.0, 2.0)
    client.health.record_failure(Provider.MISTRAL, AuthFailed(Provider.MISTRAL, "HTTP 401"))  # off for the run
    _ask(client)
    assert (len(adapters[Provider.MISTRAL].calls), len(adapters[Provider.GEMINI].calls)) == (0, 1)


def test_an_input_no_model_takes_whole_sends_nothing() -> None:
    client, adapters = _client()
    model = next(m for m in client.config.models if m.provider is Provider.GEMINI)
    plan = client._plan(_call(client), model, "x", 100)
    assert plan is not None
    chunked = type(plan)(plan.model, type(plan.fitted)("chunked", ("a", "b")), plan.reserve, plan.planned)
    with pytest.raises(LLMUnavailable):  # it could only go as several requests: never one batched call
        client._one_call(_call(client), [chunked], 100)
    assert not adapters[Provider.GEMINI].calls and not adapters[Provider.MISTRAL].calls


def test_models_that_refuse_before_sending_pass_the_call_on_and_none_is_waited_for() -> None:
    client, adapters = _client()
    asked: list[str] = []

    def busy(call: object, plan: Any) -> tuple[str, None]:
        asked.append(plan.model.provider.value)
        return "busy", None

    client._may_call = busy  # type: ignore[method-assign]
    with pytest.raises(LLMUnavailable):
        _ask(client)
    assert asked == ["gemini", "mistral"]  # each asked once, in the route's order (none measured yet)
    assert not adapters[Provider.GEMINI].calls and not adapters[Provider.MISTRAL].calls


def test_a_cooling_model_whose_room_looks_sooner_refuses_and_a_paced_one_answers() -> None:
    """Found in review: a model cooling after a 429 says its room comes in 1 s, sooner than Gemini's paced 1.5 s. It is
    asked first and refuses before sending; the call then goes to Gemini, which waits for its pace and answers."""
    config = decided_llm_config()
    gemini, groq = FakeAdapter(Provider.GEMINI, replies=(OK,)), FakeAdapter(Provider.GROQ, replies=(OK,))
    limiter = RateLimiter(requests_per_second=1 / 1.5)
    assert limiter.try_book(10) is not None  # Gemini's next slot comes in 1.5 s
    adapters: dict[Provider, Adapter] = {Provider.GEMINI: gemini, Provider.GROQ: groq}
    client = WrapperLLMClient(config, adapters, InMemoryCallLog(), limiters={Provider.GEMINI: limiter})
    members = [m.model for m in config.models if m.provider is Provider.GROQ]
    for member in members[:-1]:
        client.health.record_failure(Provider.GROQ, ModelForbidden(Provider.GROQ, "HTTP 403"), member)  # off
    client.health.record_failure(Provider.GROQ, RateLimited(Provider.GROQ, "HTTP 429"), members[-1])  # cooling
    assert _ask(client).answer == "made up"
    assert (len(gemini.calls), len(groq.calls)) == (1, 0)


# --- the time the caller leaves it ----------------------------------------------------------------------------------


def test_the_answer_time_is_the_chosen_models_90th_percentile() -> None:
    client, _ = _client()
    _times(client, Provider.MISTRAL, *[float(n) for n in range(1, 11)])
    assert client.answer_time_s(TASK, DataClass.PUBLIC, Difficulty.LONG, 2000, 3000, 10.0) == 9.0


def test_a_model_not_measured_yet_gets_the_configured_time() -> None:
    client, _ = _client()
    assert client.answer_time_s(TASK, DataClass.PUBLIC, Difficulty.LONG, 2000, 3000, 10.0) == 5.0


def test_no_answer_time_for_a_task_that_isnt_fastest_or_when_no_model_has_room() -> None:
    client, _ = _client()
    assert client.answer_time_s(LLMTask.EXTRACT_CLAIMS, DataClass.PUBLIC, Difficulty.LONG, 2000, 3000, 10.0) is None
    for provider in (Provider.GEMINI, Provider.MISTRAL, Provider.GROQ):
        client.health.record_failure(provider, AuthFailed(provider, "HTTP 401"))
    assert client.answer_time_s(TASK, DataClass.PUBLIC, Difficulty.LONG, 2000, 3000, 10.0) is None


def test_the_quickest_whose_room_and_answer_fit_goes_first_then_those_with_room_now(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Found in review: the estimate took the first model with room now, though a quicker one's room came in time, and
    the call could then go to another. Now both pick by one rule: quickest first, if its wait and answer fit."""
    client, _ = _client()
    _times(client, Provider.MISTRAL, 1.0, 1.0, 1.2)  # quick, but its room comes in 0.5 s
    _times(client, Provider.GEMINI, 6.0, 6.0, 6.0)  # slow, with room now
    quick, slow = client.order(TASK, DataClass.PUBLIC, Difficulty.LONG)
    waits: dict[str, float | None] = {quick.model: 0.5, slow.model: 0.0}
    monkeypatch.setattr(client, "_room_in", lambda model, *_a, **_k: waits[model.model])
    sizes = [(quick, 100, 100), (slow, 100, 100)]
    assert client._in_time(TASK, DataClass.PUBLIC, sizes, 4.0) == [(quick, 1.2), (slow, 6.0)]  # slow: room now only
    assert client.answer_time_s(TASK, DataClass.PUBLIC, Difficulty.LONG, 100, 100, 4.0) == 1.2
    assert client._in_time(TASK, DataClass.PUBLIC, sizes, 1.5) == [(slow, 6.0)]  # the quick one's wait doesn't fit
    waits[slow.model] = None  # it could never take the call
    assert client._in_time(TASK, DataClass.PUBLIC, sizes, 1.5) == []
    assert client.answer_time_s(TASK, DataClass.PUBLIC, Difficulty.LONG, 100, 100, 1.5) is None


def test_a_model_cooling_after_a_failure_is_never_picked() -> None:
    client, _ = _client()
    client.health.record_failure(Provider.GEMINI, RateLimited(Provider.GEMINI, "HTTP 429"))  # cooling
    order = client.order(TASK, DataClass.PUBLIC, Difficulty.LONG)
    sizes = [(model, 100, 100) for model in order]
    assert [m.provider for m, _ in client._in_time(TASK, DataClass.PUBLIC, sizes, 10.0)] == [Provider.MISTRAL]


# --- what is measured -------------------------------------------------------------------------------------------------


def test_only_public_single_answers_to_the_measured_tasks_are_timed() -> None:
    client, _ = _client()
    key = _model(client, Provider.GEMINI)
    client.complete(
        LLMTask.EXTRACT_CLAIMS, "E.", "A page.", Answer, data_class=DataClass.PUBLIC, difficulty=Difficulty.LONG
    )
    assert len(client.times.times(key, [LLMTask.EXTRACT_CLAIMS])) == 1  # timed by the real attempt
    plan = client._plan(_call(client), next(m for m in client.config.models if m.provider is Provider.GEMINI), "x", 9)
    assert plan is not None
    client._time(_call(client, LLMTask.WRITE_EVIDENCE), plan, "ok", False, 1000)  # no fastest task is ranked by it
    client._time(_call(client, data_class=DataClass.REDDIT), plan, "ok", False, 1000)  # Reddit text, not public
    client._time(_call(client), plan, "error", False, 1000)  # a failure before any answer says nothing about speed
    assert len(client.times.times(key, [TASK, LLMTask.EXTRACT_CLAIMS, LLMTask.WRITE_EVIDENCE])) == 1


def test_a_timeout_counts_at_full_length_or_as_the_fact_calls_own_and_not_when_cut_short() -> None:
    """A fastest task's own timeout is cut at the time its caller left it, so it says the model took at least that long:
    it counts, and a model that keeps timing out on the fact call ranks lower. Another task's cut-short timeout could
    be far shorter than an answer takes, so it would make a slow model look quick: it doesn't."""
    client, _ = _client()
    key = _model(client, Provider.MISTRAL)
    plan = client._plan(
        _call(client), next(m for m in client.config.models if m.provider is Provider.MISTRAL), "x", 100
    )
    assert plan is not None
    extraction = _call(client, LLMTask.EXTRACT_CLAIMS)
    client._time(extraction, plan, "timeout", False, 9000)
    client._time(extraction, plan, "timeout", True, 3000)
    client._time(_call(client), plan, "timeout", True, 4860)
    client._time(_call(client), plan, "rate_limited", False, 10)
    assert client.times.times(key, [TASK, LLMTask.EXTRACT_CLAIMS]) == [9.0, 4.86]


def test_a_fact_calls_cut_short_timeout_never_makes_a_model_look_quicker() -> None:
    """Found in review: cut at 1 s, a timeout says only "over 1 s", so it is kept at no less than the median."""
    client, _ = _client()
    key = _model(client, Provider.MISTRAL)
    _times(client, Provider.MISTRAL, 4.0, 5.0, 6.0)
    plan = client._plan(_call(client), next(m for m in client.config.models if m.provider is Provider.MISTRAL), "x", 9)
    assert plan is not None
    client._time(_call(client), plan, "timeout", True, 1000)
    assert client.times.times(key, [TASK])[-1] == 5.0  # the median it had, not 1 s


def _call(client: WrapperLLMClient, task: LLMTask = TASK, data_class: DataClass = DataClass.PUBLIC) -> Any:
    from app.llm.client import _Call

    return _Call(task, data_class, "s", Answer, None, 20.0, client.pool.now() + 20.0, None)


def test_a_chunked_attempt_is_not_timed() -> None:
    client, _ = _client()
    model = next(m for m in client.config.models if m.provider is Provider.MISTRAL)
    plan = client._plan(_call(client), model, "x", 100)
    assert plan is not None
    chunked = type(plan)(plan.model, type(plan.fitted)("chunked", ("a", "b")), plan.reserve, plan.planned)
    client._time(_call(client), chunked, "ok", False, 4000)
    assert client.times.times((model.provider, model.model), [TASK]) == []


# --- the times themselves, and the config -----------------------------------------------------------------------------


def test_a_median_and_90th_percentile_need_three_answers_and_keep_the_latest_twenty() -> None:
    times = AnswerTimes()
    key = (Provider.GEMINI, "made-up")
    times.note(key, TASK, 2.0)
    times.note(key, TASK, -1.0)  # a clock that stepped back is read as 0
    assert times.median(key, [TASK]) is None
    times.note(key, LLMTask.WRITE_EVIDENCE, 9.0)
    assert times.median(key, [TASK]) is None  # another task's answer doesn't count for this one
    times.note(key, TASK, 4.0)
    assert (times.median(key, [TASK]), times.p90(key, [TASK])) == (2.0, 4.0)
    for _ in range(25):
        times.note(key, TASK, 1.0)
    assert times.times(key, [TASK]) == [1.0] * 20
    assert percentile([5.0, 1.0, 3.0], 0.5) == 3.0


def test_a_fastest_task_is_never_hedged() -> None:
    data = decided_llm_config().model_dump()
    data["hedge"] = {TASK: {"after_s": 1.0, "model": next(m["model"] for m in data["models"])}}
    with pytest.raises(ValidationError, match="never hedged"):
        LLMConfig.model_validate(data)


def test_the_decided_config_makes_enrichs_fact_call_a_fastest_task() -> None:
    fastest = decided_llm_config().fastest[TASK]
    assert set(fastest.measured_by) == {LLMTask.EXTRACT_CLAIMS, TASK}
    assert fastest.unmeasured_s == 5.0
