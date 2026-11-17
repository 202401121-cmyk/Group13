"""US-39, criterion 10: 70 made-up replies, 10 per type, and the share the interview classifies correctly.

The live classifier is run once, by hand (`python -m tests.golden.reply_samples --live`), never here: its record is
committed, and these tests check it is of this very set and meets the 90% target. Offline, they report what code alone
reads, check it never misreads a reply it places, and check the measuring itself with a classifier that is always right.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any, TypeVar

from pydantic import BaseModel

from app.contracts import DataClass, Difficulty, LLMTask, ReplyType, examples
from app.interview.answers import understand_reply
from app.interview.parsing import RawReply

from .reply_samples import RECORD_PATH, TARGET, is_correct, load_samples, measure, samples_digest, situation

T = TypeVar("T", bound=BaseModel)
KEYS = {c.key for c in examples.criteria().criteria}


def test_the_set_is_70_made_up_replies_10_per_type() -> None:
    samples = load_samples()
    assert len(samples) == 70
    assert Counter(s["type"] for s in samples) == {t.value: 10 for t in ReplyType}
    assert all(s["written_for_tests"] is True for s in samples)
    assert len({s["id"] for s in samples}) == 70
    assert len({s["reply"] for s in samples}) == 70


def test_every_sample_names_a_real_situation() -> None:
    for sample in load_samples():
        assert sample["asked"] in KEYS, sample["id"]
        assert set(sample["answered"]) <= KEYS - {sample["asked"]}, sample["id"]
        if sample["type"] == "correction":
            assert sample["corrects"] in sample["answered"], sample["id"]
        else:
            assert sample["corrects"] is None, sample["id"]
        state, pending = situation(sample)
        assert pending.status == "pending" and state.history[-1] == pending


def test_code_alone_never_misreads_a_reply_it_places(capsys: Any) -> None:
    """Code reads buttons, numbers and "skip"; whatever it places must be right. The rest is the classifier's."""
    placed = 0
    for sample in load_samples():
        _state, pending = situation(sample)
        reply = understand_reply(sample["reply"], pending.question)
        if reply.reply_type is not ReplyType.UNCLEAR:
            placed += 1
            assert is_correct(sample, reply), sample["id"]
    offline = measure(None)
    with capsys.disabled():
        print(
            f"\nUS-39 samples, code alone: placed {placed} of 70; correct {offline['correct']} ({offline['share']:.0%})"
        )


class _AlwaysRight:
    """A classifier that knows each sample's answer, to check the measuring, not the model."""

    def __init__(self, samples: list[dict[str, Any]]) -> None:
        self.by_reply = {s["reply"]: s for s in samples}

    def complete(self, task: LLMTask, system: str, user: str, schema: type[T], **_: Any) -> T:
        assert task is LLMTask.CLASSIFY_REPLY
        sample = next(s for reply, s in self.by_reply.items() if reply in user)
        fields: dict[str, Any] = {"reply_type": sample["type"]}
        if sample["type"] in ("answer", "answer_with_question", "correction"):
            fields["level"] = "high"
        if sample["type"] in ("question", "answer_with_question"):
            fields["user_question"] = sample["reply"]
        if sample["type"] == "correction":
            fields["corrects"] = f"q_{sample['corrects']}"
        answer = RawReply.model_validate(fields)
        assert isinstance(answer, schema)
        return answer


def test_the_measuring_counts_an_always_right_classifier_as_100_percent() -> None:
    samples = load_samples()
    result = measure(_AlwaysRight(samples), samples)
    assert (result["correct"], result["share"], result["misses"]) == (70, 1.0, [])
    assert result["by_type"] == {t.value: 1.0 for t in ReplyType}


def test_a_correction_of_the_wrong_question_is_a_miss() -> None:
    sample = next(s for s in load_samples() if s["type"] == "correction" and len(s["answered"]) > 1)
    state, pending = situation(sample)
    wrong = next(k for k in sample["answered"] if k != sample["corrects"])
    reply = RawReply(reply_type="correction", level="low", corrects=f"q_{wrong}")
    from app.interview.parsing import to_classification

    assert not is_correct(sample, to_classification(reply, sample["reply"], state, pending))


def test_a_classifier_failure_is_never_counted_correct_even_on_an_answer_sample() -> None:
    """Criterion 11 makes every failure an answer, so an answer sample would always look right: it must not count."""
    sample = next(s for s in load_samples() if s["type"] == "answer")
    state, pending = situation(sample)
    from app.interview.replies import criterion_of, fallback_answer

    reply = fallback_answer(criterion_of(state, pending))
    assert reply.reply_type is ReplyType.ANSWER
    assert not is_correct(sample, reply, "failed")
    assert is_correct(sample, reply, "llm")


def test_the_live_record_is_of_this_set_and_meets_the_target() -> None:
    record = json.loads(RECORD_PATH.read_text(encoding="utf-8"))
    assert record["samples_sha256"] == samples_digest(), "the set changed: run the live check again"
    assert record["samples"] == 70
    assert set(record["by_type"]) == {t.value for t in ReplyType}
    assert record["target"] == TARGET == 0.9
    assert record["share"] >= TARGET
    assert record["meets_target"] is True
    assert record["llm_requests_sent"] <= record["budget"] <= 100  # the run's budget (Om: 100 with the hedge)
    results = record["results"]
    assert set(results) == {s["id"] for s in load_samples()}  # every reply, each read once
    assert record["correct"] == sum(1 for r in results.values() if r["correct"])
    assert not any(r["correct"] for r in results.values() if r["by"] == "failed")  # a failure never counts
    assert all(p.startswith("groq/") for p in record["providers"])  # private text: Groq only (D-51)
    assert record["correct"] == round(record["share"] * 70)


def test_the_live_record_keeps_each_replys_model_latency_and_timeouts() -> None:
    """Per reply: the model that answered, how long the turn's reading took, and whether an attempt timed out; and the
    pace, read from config/models.yaml, under Groq's per-minute limits."""
    record = json.loads(RECORD_PATH.read_text(encoding="utf-8"))
    asked = [r for r in record["results"].values() if r["by"] != "code"]
    assert asked
    for r in asked:
        assert isinstance(r["latency_ms"], int) and r["latency_ms"] >= 0
        assert isinstance(r["timed_out"], bool)
        assert r["model"] is None or r["model"].startswith("groq/")
        assert all(a["model"].startswith("groq/") for a in r["attempts"])
    assert record["timed_out"] == sum(1 for r in asked if r["timed_out"])
    assert record["latency_ms"]["turn_limit"] == 5000
    pace = record["pace"]
    assert pace["source"].startswith("config/models.yaml")
    assert pace["pace_s"] >= max(pace["seconds_by_requests"], pace["seconds_by_tokens"])


def test_the_samples_are_classified_as_private_short_calls() -> None:
    """The measuring goes through the same classify() the interview uses: private and SHORT."""
    seen: list[tuple[DataClass, Difficulty]] = []

    class _Record:
        def complete(self, task: LLMTask, system: str, user: str, schema: type[T], **kw: Any) -> T:
            seen.append((kw["data_class"], kw["difficulty"]))
            return schema.model_validate({"reply_type": "unclear"})

    measure(_Record(), [s for s in load_samples() if s["type"] == "unclear"][:2])
    assert seen == [(DataClass.PRIVATE, Difficulty.SHORT)] * 2
