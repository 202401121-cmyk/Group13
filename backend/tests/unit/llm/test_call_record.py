"""Every LLM call leaves one record: provider, model, task, start, duration, status and tokens, never text (US-13)."""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime

import pytest
from pydantic import BaseModel

from app.contracts import DataClass, Difficulty, LLMCallRecord, LLMTask, LLMUnavailable, Provider
from app.llm import InMemoryCallLog, WrapperLLMClient, load_llm_config
from app.llm.errors import ProviderTimeout, RateLimited
from tests.fakes.llm import FakeAdapter

SECRET_SYSTEM = "made-up-secret-system-prompt"
SECRET_TEXT = "made-up-secret-shopper-words"
SECRET_REPLY = '{"product": "made-up-secret-reply"}'


class Answer(BaseModel):
    product: str


def _client(*replies: object) -> WrapperLLMClient:
    adapter = FakeAdapter(Provider.GROQ, replies=replies or (SECRET_REPLY,))  # type: ignore[arg-type]
    return WrapperLLMClient(load_llm_config(), {Provider.GROQ: adapter}, InMemoryCallLog(), limiters={})


def _ask(client: WrapperLLMClient) -> None:
    client.complete(
        LLMTask.DETECT_INTENT,
        SECRET_SYSTEM,
        SECRET_TEXT,
        Answer,
        data_class=DataClass.PRIVATE,
        difficulty=Difficulty.SHORT,
    )


def _records(client: WrapperLLMClient) -> list[LLMCallRecord]:
    assert isinstance(client.call_log, InMemoryCallLog)
    return client.call_log.records


def test_every_call_records_provider_model_task_start_duration_status_and_tokens() -> None:
    client = _client()
    _ask(client)
    (record,) = _records(client)
    assert (record.provider, record.model) == (Provider.GROQ, "openai/gpt-oss-120b")
    assert record.task is LLMTask.DETECT_INTENT
    assert record.outcome == "ok"
    assert record.duration_ms >= 0
    assert record.input_tokens > 0 and record.output_tokens > 0
    assert record.at.tzinfo is not None


def test_a_records_start_is_when_the_call_began() -> None:
    client = _client()
    seen: list[datetime] = []
    adapter = client.adapters[Provider.GROQ]
    real = adapter.call

    def slow(*args: object, **kwargs: object) -> object:
        seen.append(datetime.now(UTC))
        time.sleep(0.05)
        return real(*args, **kwargs)  # type: ignore[arg-type]

    adapter.call = slow  # type: ignore[method-assign]
    _ask(client)
    (record,) = _records(client)
    assert record.at <= seen[0]  # it began before the call went out, and doesn't record when it ended
    assert record.duration_ms >= 20  # less than the 50 slept: a Windows timer can come up a little short


@pytest.mark.parametrize(
    ("failure", "outcome"),
    [
        (ProviderTimeout(Provider.GROQ, "no reply within 9s"), "timeout"),
        (RateLimited(Provider.GROQ, "HTTP 429"), "rate_limited"),
    ],
)
def test_a_failed_call_is_recorded_too(failure: Exception, outcome: str) -> None:
    client = _client(failure)
    with pytest.raises(LLMUnavailable):
        _ask(client)
    records = _records(client)
    assert records
    assert {record.outcome for record in records} == {outcome}
    assert all(record.input_tokens > 0 for record in records)  # it went out, unlike a call our own limits refused


def test_the_call_log_never_holds_text(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    client = _client()
    _ask(client)
    (record,) = _records(client)
    dumped = record.model_dump_json()
    for secret in (SECRET_SYSTEM, SECRET_TEXT, "made-up-secret-reply"):
        assert secret not in dumped
        assert secret not in caplog.text
    assert set(LLMCallRecord.model_fields) == {
        "search_id",
        "task",
        "data_class",
        "provider",
        "model",
        "input_tokens",
        "output_tokens",
        "duration_ms",
        "outcome",
        "fitted",
        "at",
    }
