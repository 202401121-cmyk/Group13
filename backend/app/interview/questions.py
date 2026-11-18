"""The words of each interview question (US-05), with technical terms explained in plain words on request (US-07).

Which criteria count as technical, and what is plain, live in terms.py (S4-22, P-25)."""

from __future__ import annotations

import logging
import re

from app.contracts import Criterion, InterviewQuestion, TermExplanation

from .answers import QUICK_REPLIES
from .terms import TERMS_PATH, is_plain, is_technical, label_terms, plain_for, short_enough, technical_words

log = logging.getLogger(__name__)
__all__ = ["TERMS_PATH", "is_technical", "question_for", "question_id", "technical_words", "term_for"]

MAX_ID_KEY = 40  # InterviewQuestion.id is "q_" plus at most 40 lowercase letters, digits or underscores
MAX_WHY = 200  # InterviewQuestion.why_asking limit
MAX_TERM_CHARS = 40  # TermExplanation.term limit
# A sentence ends at ".", "!" or "?" before a capital letter, so "e.g. a phone" is not an end.
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")


def term_for(criterion: Criterion) -> TermExplanation | None:
    """The criterion's label, explained in plain words (P-25 item 4), at most 40 words. None for a plain criterion,
    or when no short enough explanation exists. No LLM is asked: the text comes from the saved criterion or the
    reviewed word list.

    The order (P-25 item 4): the saved definition when it is plain (its first sentence when the whole is over 40
    words); else the reviewed plain explanation of the first listed word the label names, then the key; else the
    definition's first sentence, logged as not plain.
    """
    if not is_technical(criterion):
        return None
    definition = " ".join(criterion.definition.split())
    first = _SENTENCE.split(definition, maxsplit=1)[0]
    shown = definition if short_enough(definition) else first  # never a plain fragment of a short definition
    explanation = shown if short_enough(shown) and is_plain(shown, label_terms(criterion)) else None
    if explanation is None:
        explanation = plain_for(criterion)
    if explanation is None and short_enough(first):
        log.info("interview: %s has no plain explanation; its definition's first sentence is not plain", criterion.key)
        explanation = first
    if explanation is None:
        return None
    term = _whole_words(criterion.label, MAX_TERM_CHARS)
    return TermExplanation(term=term, criterion_key=criterion.key, explanation=explanation)


def _whole_words(text: str, limit: int) -> str:
    """The text, or as many whole words of it as fit the limit: never a word cut in half."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[: limit + 1].rsplit(" ", 1)[0]
    return cut if cut and len(cut) <= limit else text[:limit].rstrip()


def question_id(criterion_key: str) -> str:
    return "q_" + re.sub(r"[^a-z0-9_]", "_", criterion_key.lower())[:MAX_ID_KEY]


def question_for(criterion: Criterion) -> InterviewQuestion:
    """'Battery life: how much does it matter to you?', with the criterion's definition as the reason for asking, and
    the criterion's term explained when it is technical (US-07).

    The title opens the sentence, so it stands exactly as given. Never change its case (D-44): only the writer of
    a title knows that "USB-C/Thunderbolt" and "iPhone" are spelled that way.
    """
    term = term_for(criterion)
    return InterviewQuestion(
        id=question_id(criterion.key),
        criterion_key=criterion.key,
        text=f"{criterion.label}: how much does it matter to you?",
        quick_replies=QUICK_REPLIES,
        why_asking=criterion.definition[:MAX_WHY].rstrip(),
        terms=[term] if term is not None else [],
    )
