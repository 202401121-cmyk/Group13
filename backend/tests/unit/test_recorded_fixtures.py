"""Recorded fixtures (US-32): each one explains itself, holds nothing it shouldn't and replays through real code."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import httpx
import pytest
from pydantic import BaseModel

from app.contracts import DataClass, Difficulty, IntakeInput, LLMTask, Provider, StageContext
from app.db.breaker import CircuitBreaker
from app.intake import IntakeStage
from app.llm import InMemoryCallLog, WrapperLLMClient, load_llm_config
from app.llm.errors import RateLimited
from app.llm.providers import Adapter, OpenAICompatibleAdapter
from app.research.reddit.arctic_shift import ArcticShiftSource
from app.research.reddit.base import IntervalLimiter
from app.research.reddit.cache import RedditCache
from scripts.record_run import RECORDINGS, SECRET_NAME_PARTS, RedditMemory, clean_reddit_text, find_leaks
from tests.fakes.recorded import RecordedAdapter, Recording, load_recording, recorded_transport

NAMES = sorted(p.name for p in RECORDINGS.iterdir() if p.is_dir()) if RECORDINGS.is_dir() else []
COMMITTED = "laptop_coding_70k"
SYNTHETIC_THREADS = RECORDINGS.parent / "reddit" / "arctic_shift_posts.json"
SECRET_SHAPES = re.compile(
    r"\bgsk_[A-Za-z0-9]{8,}|\bsk-[A-Za-z0-9-]{8,}|\bhf_[A-Za-z0-9]{8,}|postgres(ql)?://|\bBearer [\w.-]{8,}"
)


class Answer(BaseModel):
    product: str


def secret_variables() -> dict[str, str]:
    """This machine's secrets. Other long values, such as a folder, can't be in a file recorded somewhere else."""
    named = {k: v for k, v in os.environ.items() if SECRET_NAME_PARTS & set(k.upper().split("_")) and len(v) >= 8}
    return {k: v.strip() for k, v in named.items()}


def _call(content: str, service: str = "groq") -> dict[str, object]:
    return {
        "run": 1,
        "service": service,
        "method": "POST",
        "url": "https://api.groq.com/openai/v1/chat/completions",
        "request_headers": {},
        "request_body": {},
        "status": 200,
        "response_headers": {},
        "response_body": {
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 3},
        },
        "elapsed_ms": 5,
    }


def _write(root: Path, name: str, service: str, calls: list[dict[str, object]]) -> None:
    (root / name).mkdir(parents=True)
    (root / name / f"{service}.json").write_text(json.dumps(calls), encoding="utf-8")
    (root / name / "manifest.json").write_text(json.dumps({"request": "x"}), encoding="utf-8")


def test_the_committed_recording_exists() -> None:
    assert COMMITTED in NAMES


@pytest.mark.parametrize("name", NAMES)
def test_every_recording_has_a_readme_line(name: str) -> None:
    line = (RECORDINGS / name / "README.md").read_text(encoding="utf-8")
    manifest = json.loads((RECORDINGS / name / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["recorded_by"] in line and manifest["recorded_at"][:10] in line
    assert f"- `{name}`" in (RECORDINGS / "README.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("name", NAMES)
def test_the_committed_recordings_hold_no_secret_or_reddit_text(name: str) -> None:
    for path in (RECORDINGS / name).iterdir():
        text = path.read_text(encoding="utf-8")
        assert not SECRET_SHAPES.search(text), path.name
        assert "authorization" not in text.lower(), path.name
        assert find_leaks(text, secret_variables()) == [], path.name  # names only, never values
        if path.stem == "reddit":
            for call in json.loads(text):  # cleaning a clean body changes nothing
                for body in (call["request_body"], call["response_body"]):
                    assert clean_reddit_text(body, RedditMemory()) == body


@pytest.mark.parametrize("word", ["task-specific", "disk-intensive", "Bearer in mind"])
def test_ordinary_words_do_not_look_like_secrets(word: str) -> None:
    assert not SECRET_SHAPES.search(word)


def test_load_recording_reads_each_service(tmp_path: Path) -> None:
    _write(tmp_path, "demo", "groq", [_call("one"), _call("two")])
    recording = load_recording("demo", tmp_path)
    assert recording.services == ["groq"]
    assert len(recording.calls("groq")) == 2
    assert recording.calls("mistral") == []


def test_a_missing_recording_says_how_to_make_one(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match=r"scripts\.record_run"):
        load_recording("nothing_here", tmp_path)


def test_recorded_adapter_replays_in_order(tmp_path: Path) -> None:
    _write(tmp_path, "demo", "groq", [_call("one"), _call("two")])
    adapter = RecordedAdapter(load_recording("demo", tmp_path), Provider.GROQ)
    first = adapter.call("m", "s", "u", 100, 1.0)
    second = adapter.call("m", "s", "u", 100, 1.0)
    assert (first.text, second.text) == ("one", "two")
    assert (first.input_tokens, first.output_tokens) == (12, 3)


def test_a_recording_that_runs_out_fails_the_test_through_the_real_client(tmp_path: Path) -> None:
    """The client swallows every Exception and falls back, so running out must not be one."""
    _write(tmp_path, "demo", "groq", [_call('{"product": "Nova Book 14"}')])
    client = _replay(load_recording("demo", tmp_path))

    def ask() -> Answer:
        return client.complete(
            LLMTask.DISCOVER_ALIASES, "s", "u", Answer, data_class=DataClass.PRIVATE, difficulty=Difficulty.SHORT
        )

    assert ask().product == "Nova Book 14"
    with pytest.raises(pytest.fail.Exception, match="only 1 call"):
        ask()


def test_recorded_transport_answers_a_source_adapter(tmp_path: Path) -> None:
    """The real Arctic Shift source reads a replayed reply. Its threads are the synthetic ones, not a recording's."""
    threads = json.loads(SYNTHETIC_THREADS.read_text(encoding="utf-8"))
    _write(tmp_path, "demo", "reddit", [{**_call(""), "response_body": threads}])
    source = ArcticShiftSource(
        httpx.Client(transport=recorded_transport(load_recording("demo", tmp_path), "reddit")),
        salt="test-salt",
        cache=RedditCache(folder=None),
        limiter=IntervalLimiter(0.0),
        breaker=CircuitBreaker(3, 600),
    )
    result = source.search_threads("laptop coding", ["SuggestALaptop"])
    assert result.failure is None
    assert "Laptop for coding under 70k?" in [found.title for found in result.sources]


def test_a_recorded_failure_replays_as_the_same_error(tmp_path: Path) -> None:
    _write(tmp_path, "demo", "groq", [{**_call(""), "status": None, "error": "ConnectTimeout"}])
    transport = recorded_transport(load_recording("demo", tmp_path), "groq")
    with httpx.Client(transport=transport) as http, pytest.raises(httpx.ConnectTimeout):
        http.get("https://api.groq.com/")


def test_a_refused_call_replays_as_a_network_error(tmp_path: Path) -> None:
    _write(tmp_path, "demo", "groq", [{**_call(""), "status": None, "error": "Refused"}])
    transport = recorded_transport(load_recording("demo", tmp_path), "groq")
    with httpx.Client(transport=transport) as http, pytest.raises(httpx.ConnectError):
        http.get("https://api.groq.com/")


def test_recorded_transport_answers_an_llm_adapter(tmp_path: Path) -> None:
    _write(tmp_path, "demo", "groq", [_call("a reply")])
    transport = recorded_transport(load_recording("demo", tmp_path), "groq")
    with httpx.Client(transport=transport) as http:
        adapter = OpenAICompatibleAdapter(Provider.GROQ, "https://api.groq.com/openai/v1", "fake-key", http)
        assert adapter.call("m", "s", "u", 100, 1.0).text == "a reply"


def _replay(recording: Recording) -> WrapperLLMClient:
    adapters: dict[Provider, Adapter] = {Provider.GROQ: RecordedAdapter(recording, Provider.GROQ)}
    return WrapperLLMClient(load_llm_config(), adapters, InMemoryCallLog(), limiters={})


def test_intake_runs_from_the_recording() -> None:
    """A real reply from the real service, through the real stage, with no network."""
    recording = load_recording(COMMITTED)
    intent = IntakeStage(_replay(recording)).run(
        IntakeInput(raw_query=recording.request), StageContext("srch_replay0001")
    )
    assert intent.detected_by == "llm"
    assert intent.category == "laptop"
    assert intent.budget is not None and intent.budget.max is not None
    assert intent.budget.max.amount == 70_000


def test_a_recorded_rate_limit_acts_like_a_live_one(tmp_path: Path) -> None:
    _write(tmp_path, "demo", "groq", [{**_call(""), "status": 429}])
    adapter = RecordedAdapter(load_recording("demo", tmp_path), Provider.GROQ)
    with pytest.raises(RateLimited):
        adapter.call("m", "s", "u", 100, 1.0)


def test_a_replayed_reply_is_checked_like_a_live_one(tmp_path: Path) -> None:
    _write(tmp_path, "demo", "groq", [_call('{"product": "Nova Book 14"}')])
    client = _replay(load_recording("demo", tmp_path))
    answer = client.complete(
        LLMTask.DISCOVER_ALIASES, "s", "u", Answer, data_class=DataClass.PRIVATE, difficulty=Difficulty.SHORT
    )
    assert answer.product == "Nova Book 14"
