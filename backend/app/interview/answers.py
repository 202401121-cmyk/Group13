"""Understand a reply to an interview question (US-05, US-08). No LLM.

A reply is a rough level, an exact weight, or text. Text is understood when it is a table phrase, a whole number or
"skip"; everything else is UNCLEAR, so the interview asks again. answer_weight() is the one place an answer becomes a
weight, and ROUGH_LEVEL_WEIGHTS is the one table it reads. US-39 replaces the reading of text with a real classifier
that also spots questions, corrections and off-topic replies.
"""

from __future__ import annotations

import re
from typing import Literal

from app.contracts import (
    ROUGH_LEVEL_WEIGHTS,
    InterviewQuestion,
    InterviewState,
    ReplyClassification,
    ReplyType,
    RoughLevel,
)

# The buttons under every question, in the order they are shown: the table's levels, then Skip.
QUICK_REPLIES = ["Low", "Medium", "High", "Good enough", "Skip"]

SKIP_WORD = "skip"

# A whole number from 0 to 10, optionally written "out of 10" style: "7", "7/10", "10 / 10".
_NUMBER = re.compile(r"^(\d{1,2})(?:\s*/\s*10)?$")

ReplyPlace = Literal["current", "repeat", "conflict"]


def answer_weight(level: RoughLevel | None, exact_weight: float | None) -> float | None:
    """The one place an answer becomes a weight. None means the answer gave no weight."""
    if exact_weight is not None:
        return exact_weight
    return ROUGH_LEVEL_WEIGHTS[level] if level is not None else None


def understand_reply(
    text: str, question: InterviewQuestion, level: RoughLevel | None = None, exact_weight: float | None = None
) -> ReplyClassification:
    """Classify one reply. The fields win over the text. `question` is unused for now; US-39 needs it."""
    if level is None and exact_weight is None:
        normal = _normal(text)
        if normal == SKIP_WORD:
            return ReplyClassification(reply_type=ReplyType.SKIP, classified_by="fallback")
        level = _level_in(normal)
        exact_weight = _whole_number_in(normal)
    weight = answer_weight(level, exact_weight)
    if weight is None:
        return ReplyClassification(reply_type=ReplyType.UNCLEAR, classified_by="fallback")
    return ReplyClassification(reply_type=ReplyType.ANSWER, weight=weight, classified_by="fallback")


def place_reply(
    state: InterviewState,
    question_id: str,
    reply_text: str,
    level: RoughLevel | None = None,
    exact_weight: float | None = None,
) -> ReplyPlace:
    """Where a reply that names its question belongs: the current question, a safe repeat, or neither.

    A network retry or a double click sends the same reply again after the interview has moved on, and it must not
    answer the next question. A repeat says the same words and gives the same weight: the same words with another
    exact value are a different answer. Changing a settled answer goes through "go back", which makes that question
    current.
    """
    asked = next((entry for entry in state.history if entry.question.id == question_id), None)
    if asked is None:
        return "conflict"
    if asked.status == "pending":
        return "current"
    same_words = asked.reply_text is not None and _normal(asked.reply_text) == _normal(reply_text)
    earlier = asked.classification.weight if asked.classification else None
    same_weight = understand_reply(reply_text, asked.question, level, exact_weight).weight == earlier
    return "repeat" if same_words and same_weight else "conflict"


def _normal(text: str) -> str:
    return " ".join(text.lower().split()).strip(" .!")


def _squeezed(text: str) -> str:
    return re.sub(r"[\s_-]", "", text)


def _level_in(normal: str) -> RoughLevel | None:
    """The level this text is, ignoring spaces and hyphens (_normal has lowered it). Containing one is not enough."""
    return next((level for level in RoughLevel if _squeezed(level.value) == _squeezed(normal)), None)


def _whole_number_in(normal: str) -> float | None:
    number = _NUMBER.match(normal)
    return float(number.group(1)) if number and 0 <= int(number.group(1)) <= 10 else None
