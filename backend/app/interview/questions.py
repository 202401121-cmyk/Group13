"""The words of each interview question (US-05), with unfamiliar terms explained on request (US-07)."""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path

import yaml  # type: ignore[import-untyped]  # PyYAML ships no type stubs
from pydantic import BaseModel, ConfigDict, Field

from app.contracts import MAX_EXPLANATION_WORDS, Criterion, InterviewQuestion, TermExplanation

from .answers import QUICK_REPLIES

MAX_ID_KEY = 40  # InterviewQuestion.id is "q_" plus at most 40 lowercase letters, digits or underscores
MAX_WHY = 200  # InterviewQuestion.why_asking limit
MAX_TERM_CHARS = 40  # TermExplanation.term limit
TERMS_PATH = Path(__file__).resolve().parents[2] / "config" / "technical_terms.yaml"
# A sentence ends at ".", "!" or "?" before a capital letter, so "e.g. a phone" is not an end.
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")


class _Word(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    word: str = Field(min_length=1, max_length=40)
    also: list[str] = Field(default_factory=list)


class _WordFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    words: list[_Word] = Field(min_length=1)


@cache
def technical_words(path: Path = TERMS_PATH) -> frozenset[str]:
    """Every technical word and phrase in config/technical_terms.yaml, in lowercase, each written out in full."""
    data = _WordFile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    return frozenset(" ".join(form.casefold().split()) for w in data.words for form in (w.word, *w.also))


def _words(text: str) -> list[str]:
    """Letters and digits apart, so a unit glued to a number is found: "5000mAh" is "5000 mah", "IP68" is "ip 68"."""
    return re.findall(r"[a-z]+|[0-9]+", text.casefold())


def is_technical(criterion: Criterion) -> bool:
    """Its label or key holds a listed word or phrase as whole words ("ram" is not found in "programming")."""
    for text in (criterion.label, criterion.key.replace("_", " ")):
        padded = f" {' '.join(_words(text))} "
        if any(f" {' '.join(_words(word))} " in padded for word in technical_words()):
            return True
    return False


def _short_enough(text: str) -> bool:
    return 0 < len(text.split()) <= MAX_EXPLANATION_WORDS


def term_for(criterion: Criterion) -> TermExplanation | None:
    """The criterion's label, explained by its saved definition (US-03), or that definition's first sentence when the
    whole is over 40 words. None for a plain criterion, or when no short enough explanation exists. No LLM is asked."""
    if not is_technical(criterion):
        return None
    definition = " ".join(criterion.definition.split())
    first = _SENTENCE.split(definition, maxsplit=1)[0]
    explanation = next((text for text in (definition, first) if _short_enough(text)), None)
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
