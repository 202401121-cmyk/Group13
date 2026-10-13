"""The words of each interview question (US-05)."""

from __future__ import annotations

import re

from app.contracts import Criterion, InterviewQuestion

from .answers import QUICK_REPLIES

MAX_ID_KEY = 40  # InterviewQuestion.id is "q_" plus at most 40 lowercase letters, digits or underscores
MAX_WHY = 200  # InterviewQuestion.why_asking limit


def question_id(criterion_key: str) -> str:
    return "q_" + re.sub(r"[^a-z0-9_]", "_", criterion_key.lower())[:MAX_ID_KEY]


def question_for(criterion: Criterion) -> InterviewQuestion:
    """'How much do you care about battery life?', with the criterion's definition as the reason for asking."""
    return InterviewQuestion(
        id=question_id(criterion.key),
        criterion_key=criterion.key,
        text=f"How much do you care about {in_sentence(criterion.label)}?",
        quick_replies=QUICK_REPLIES,
        why_asking=criterion.definition[:MAX_WHY].rstrip(),
    )


def in_sentence(label: str) -> str:
    """The label in lower case for the middle of a sentence, keeping acronyms: 'RAM and storage', 'GPS accuracy'."""
    return " ".join(word if _is_acronym(word) else word.lower() for word in label.split())


def _is_acronym(word: str) -> bool:
    letters = [ch for ch in word if ch.isalpha()]
    return len(letters) >= 2 and all(ch.isupper() for ch in letters)
