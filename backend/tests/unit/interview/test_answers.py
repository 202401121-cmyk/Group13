"""Reply understanding, Sprint 1 (US-05): buttons and whole numbers 0-10; anything else is unclear."""

from __future__ import annotations

import pytest

from app.contracts import InterviewQuestion, ReplyType
from app.interview.answers import QUICK_REPLIES, REPLY_WEIGHTS, understand_reply

QUESTION = InterviewQuestion(
    id="q_battery_life",
    criterion_key="battery_life",
    text="How much do you care about battery life?",
    quick_replies=QUICK_REPLIES,
)


def test_every_question_offers_the_same_buttons() -> None:
    assert QUICK_REPLIES == ["Low", "Medium", "High", "Skip"]
    assert {b.lower() for b in QUICK_REPLIES} == set(REPLY_WEIGHTS)


@pytest.mark.parametrize(
    ("text", "weight"),
    [("Low", 2.0), ("low", 2.0), ("MEDIUM", 5.0), (" medium ", 5.0), ("High", 8.0), ("high!", 8.0)],
)
def test_buttons_map_to_weights(text: str, weight: float) -> None:
    reply = understand_reply(text, QUESTION)
    assert (reply.reply_type, reply.weight, reply.classified_by) == (ReplyType.ANSWER, weight, "fallback")


@pytest.mark.parametrize("text", ["Skip", "skip", "SKIP."])
def test_skip(text: str) -> None:
    reply = understand_reply(text, QUESTION)
    assert (reply.reply_type, reply.weight) == (ReplyType.SKIP, None)


@pytest.mark.parametrize(
    ("text", "weight"),
    [("0", 0.0), ("7", 7.0), ("10", 10.0), ("7/10", 7.0), ("7 / 10", 7.0), ("10/10", 10.0), ("03", 3.0)],
)
def test_whole_numbers_from_0_to_10(text: str, weight: float) -> None:
    reply = understand_reply(text, QUESTION)
    assert (reply.reply_type, reply.weight) == (ReplyType.ANSWER, weight)


@pytest.mark.parametrize(
    "text",
    ["11", "-1", "7.5", "7/5", "seven", "very important", "good enough", "what is battery life?", "", "   ", "lowish"],
)
def test_anything_else_is_unclear(text: str) -> None:
    reply = understand_reply(text, QUESTION)
    assert (reply.reply_type, reply.weight) == (ReplyType.UNCLEAR, None)
    assert reply.classified_by == "fallback"
