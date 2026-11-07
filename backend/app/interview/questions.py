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
    """'Battery life: how much does it matter to you?', with the criterion's definition as the reason for asking.

    The title opens the sentence, so it stands exactly as given. Never change its case (D-44): only the writer of
    a title knows that "USB-C/Thunderbolt" and "iPhone" are spelled that way.
    """
    return InterviewQuestion(
        id=question_id(criterion.key),
        criterion_key=criterion.key,
        text=f"{criterion.label}: how much does it matter to you?",
        quick_replies=QUICK_REPLIES,
        why_asking=criterion.definition[:MAX_WHY].rstrip(),
    )
