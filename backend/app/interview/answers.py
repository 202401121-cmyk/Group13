"""Understand a reply to an interview question, Sprint 1 version (US-05). No LLM.

Only the quick-reply buttons and plain numbers are understood here; everything else is UNCLEAR, so the interview
asks again. US-08 extends REPLY_WEIGHTS with rough answers ("good enough"), and US-39 replaces understand_reply()
with a real classifier that also spots questions, corrections and off-topic replies.
"""

from __future__ import annotations

import re
from typing import Literal

from app.contracts import InterviewQuestion, InterviewState, ReplyClassification, ReplyType

# The buttons under every question, in the order they are shown.
QUICK_REPLIES = ["Low", "Medium", "High", "Skip"]

# One table for every place that turns words into weights (US-08 adds rows). None means "skip this question".
REPLY_WEIGHTS: dict[str, float | None] = {
    "low": 2.0,
    "medium": 5.0,
    "high": 8.0,
    "skip": None,
}

# A whole number from 0 to 10, optionally written "out of 10" style: "7", "7/10", "10 / 10".
_NUMBER = re.compile(r"^(\d{1,2})(?:\s*/\s*10)?$")

ReplyPlace = Literal["current", "repeat", "conflict"]


def understand_reply(text: str, question: InterviewQuestion) -> ReplyClassification:
    """Classify one reply. `question` is unused for now; US-39 needs it to understand questions about the topic."""
    normal = _normal(text)
    if normal in REPLY_WEIGHTS:
        weight = REPLY_WEIGHTS[normal]
        if weight is None:
            return ReplyClassification(reply_type=ReplyType.SKIP, classified_by="fallback")
        return ReplyClassification(reply_type=ReplyType.ANSWER, weight=weight, classified_by="fallback")
    number = _NUMBER.match(normal)
    if number and 0 <= int(number.group(1)) <= 10:
        return ReplyClassification(reply_type=ReplyType.ANSWER, weight=float(number.group(1)), classified_by="fallback")
    return ReplyClassification(reply_type=ReplyType.UNCLEAR, classified_by="fallback")


def place_reply(state: InterviewState, question_id: str, reply_text: str) -> ReplyPlace:
    """Where a reply that names its question belongs: the current question, a safe repeat, or neither.

    A network retry or a double click sends the same reply again after the interview has moved on, and it must not
    answer the next question. Changing a settled answer goes through "go back", which makes that question current.
    """
    asked = next((entry for entry in state.history if entry.question.id == question_id), None)
    if asked is None:
        return "conflict"
    if asked.status == "pending":
        return "current"
    same = asked.reply_text is not None and _normal(asked.reply_text) == _normal(reply_text)
    return "repeat" if same else "conflict"


def _normal(text: str) -> str:
    return " ".join(text.lower().split()).strip(" .!")
