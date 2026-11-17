"""The two LLM calls of the interview (US-39): what kind of reply this is, and the answer to a shopper's question.

Both are private (the shopper's own words, D-51: Groq only), SHORT, and carry the shopper's text only as data. Each
has its fallback in code, so a turn never waits on a provider that is down:
- classify(): a reply the classifier can't read, or a provider that doesn't answer, becomes an answer at the
  criterion's default weight, classified_by "fallback": the shopper can go back and change it (criterion 11);
- answer_question(): the criterion's saved definition first, when its words cover the question (no call); else one
  call of at most 60 words; else, if that fails, the definition after all.
A turn has 5 seconds (StageName.INTERVIEW), so the second call gets only what the first left (TokenMeter).
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Literal

from app.contracts import (
    AskedQuestion,
    Criterion,
    DataClass,
    Difficulty,
    InterviewState,
    LLMClient,
    LLMTask,
    LLMUnavailable,
    ReplyClassification,
    ReplyType,
)
from app.llm.usage import TokenMeter

from .parsing import MAX_ANSWER_WORDS, RawAnswer, RawReply, Refused, to_answer, to_classification
from .prompts import ANSWER_SYSTEM, CLASSIFY_SYSTEM, answer_message, classify_message

log = logging.getLogger(__name__)

__all__ = ["MAX_ANSWER_WORDS", "Answered", "answer_question", "classify", "definition_covers", "fallback_answer"]

# The longest valid RawReply (an answer_with_question carrying a 300-character question) is about 140 tokens; low
# reasoning on Groq's gpt-oss models (task_settings) thinks within the same limit. A smaller cap books fewer of each
# model's 8,000 tokens a minute.
CLASSIFY_TOKENS = 256
# Of the turn's 5 s, kept back from the calls: the wrapper's deadline ends 1 s before the runner's, so a provider that
# hangs ends the call while the turn still has time to count the reply as an answer (criterion 11). Without it the
# runner's limit could come first, and its fallback skips the question.
HEADROOM_S = 1.0
ANSWER_TOKENS = 512  # at most 60 words

# Words that ask rather than name something: "what does battery life mean?" is about battery life.
_ASKING = frozenset(
    [
        "a",
        "about",
        "actually",
        "an",
        "and",
        "any",
        "are",
        "as",
        "at",
        "be",
        "by",
        "can",
        "could",
        "did",
        "do",
        "does",
        "doing",
        "exactly",
        "explain",
        "for",
        "good",
        "how",
        "i",
        "if",
        "in",
        "is",
        "it",
        "its",
        "it's",
        "just",
        "kind",
        "mean",
        "meaning",
        "means",
        "meant",
        "me",
        "my",
        "of",
        "on",
        "or",
        "please",
        "really",
        "s",
        "so",
        "sort",
        "tell",
        "term",
        "than",
        "that",
        "the",
        "this",
        "to",
        "us",
        "way",
        "we",
        "what",
        "what's",
        "whats",
        "when",
        "where",
        "which",
        "who",
        "why",
        "with",
        "word",
        "would",
        "you",
        "your",
        "matter",
        "matters",
        "important",
        "question",
        "criterion",
        "thing",
    ]
)


@dataclass(frozen=True)
class Answered:
    text: str
    source: Literal["definition", "llm"]


def classify(
    llm: LLMClient, text: str, state: InterviewState, pending: AskedQuestion, search_id: str, started: float
) -> ReplyClassification:
    """What kind of reply this is. Never raises: any failure is an answer at the default weight (criterion 11)."""
    criterion = criterion_of(state, pending)
    earlier = [e for e in state.history if e.status in ("answered", "skipped")]
    try:
        with TokenMeter(already_spent_s=_spent(started)).counting():
            raw = llm.complete(
                LLMTask.CLASSIFY_REPLY,
                CLASSIFY_SYSTEM,
                classify_message(pending, criterion, earlier, text),
                RawReply,
                data_class=DataClass.PRIVATE,
                difficulty=Difficulty.SHORT,
                search_id=search_id,
                max_output_tokens=CLASSIFY_TOKENS,
            )
        return to_classification(raw, text, state, pending)
    except LLMUnavailable:
        log.info("interview: no LLM provider classified the reply; it counts as an answer at the default weight")
    except Refused as refused:
        log.info("interview: the reply's classification was refused (%s); it counts as an answer", refused)
    except Exception as exc:  # any other failure is a classifier failure too (criterion 11): the type only, never text
        log.warning("interview: the classifier failed (%s); the reply counts as an answer", type(exc).__name__)
    return fallback_answer(criterion)


def fallback_answer(criterion: Criterion) -> ReplyClassification:
    """The reply as an answer at the criterion's default weight: what a skip would give, but counted, and changeable."""
    return ReplyClassification(reply_type=ReplyType.ANSWER, weight=criterion.default_weight, classified_by="fallback")


def answer_question(
    llm: LLMClient, question: str, criterion: Criterion, category: str, search_id: str, started: float
) -> Answered:
    """The answer to the shopper's question: the saved definition when it covers it, else one short LLM answer."""
    definition = " ".join(criterion.definition.split())
    if definition_covers(question, criterion):
        return Answered(definition, "definition")
    try:
        with TokenMeter(already_spent_s=_spent(started)).counting():
            raw = llm.complete(
                LLMTask.ANSWER_USER_QUESTION,
                ANSWER_SYSTEM,
                answer_message(criterion, category, question),
                RawAnswer,
                data_class=DataClass.PRIVATE,
                difficulty=Difficulty.SHORT,
                search_id=search_id,
                max_output_tokens=ANSWER_TOKENS,
            )
        return Answered(to_answer(raw), "llm")
    except LLMUnavailable:
        log.info("interview: no LLM provider answered the question; the definition stands in")
    except Refused as refused:
        log.info("interview: the answer was refused (%s); the definition stands in", refused)
    except Exception as exc:  # never lose the turn to it: the definition stands in (the type only, never text)
        log.warning("interview: the answer call failed (%s); the definition stands in", type(exc).__name__)
    return Answered(f"Here's what we mean by {criterion.label}: {definition}", "definition")


def _spent(started: float) -> float:
    """What the wrapper takes as already spent of the turn's limit: the time gone, plus HEADROOM_S kept back."""
    return time.monotonic() - started + HEADROOM_S


def definition_covers(question: str, criterion: Criterion) -> bool:
    """Every word the question asks about is in the criterion's label, key or definition (plurals folded)."""
    known = set(_stems(" ".join((criterion.label, criterion.key.replace("_", " "), criterion.definition))))
    words = re.findall(r"[a-z0-9]+(?:'[a-z]+)?", question.lower())
    asked = [_stem(w) for w in words if w not in _ASKING and _stem(w) not in _ASKING]  # "does" is no "doe"
    return all(w in known for w in asked)


def criterion_of(state: InterviewState, entry: AskedQuestion) -> Criterion:
    return next(c for c in state.criteria.criteria if c.key == entry.question.criterion_key)


def _stems(text: str) -> list[str]:
    return [_stem(w) for w in re.findall(r"[a-z0-9]+(?:'[a-z]+)?", text.lower())]


def _stem(word: str) -> str:
    """Plural or third-person "s" folded: "runs" is "run", "tools" is "tool"; "less", "class" and short words stay."""
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word
