"""Technical words, plain words and plain explanations (US-07; S4-22, P-25).

- A criterion is technical, so its question gets "What does this mean?", when its label or key holds a word or phrase
  from config/technical_terms.yaml, or when its label has any word that is not in config/plain_words.yaml (technical
  by default: "Sensor Size" and "Keyboard Travel" get the link; "Price" and "Value for money" don't).
- A text is plain when its Flesch-Kincaid grade is at most 9 (app/explain/readability.py, S4-8) and it uses no listed
  word except the term's own: "RAM" may be explained with "RAM", never with "volatile memory".
- A listed word may carry `plain:`, a people-reviewed explanation used when a criterion's own definition is not plain.

Code only: no LLM is asked.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import yaml  # type: ignore[import-untyped]  # PyYAML ships no type stubs
from pydantic import BaseModel, ConfigDict, Field

from app.contracts import MAX_EXPLANATION_WORDS, Criterion
from app.explain.readability import GRADE_LIMIT, grade

CONFIG = Path(__file__).resolve().parents[2] / "config"
TERMS_PATH = CONFIG / "technical_terms.yaml"
PLAIN_WORDS_PATH = CONFIG / "plain_words.yaml"


class _Word(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    word: str = Field(min_length=1, max_length=40)
    also: list[str] = Field(default_factory=list)
    plain: str | None = Field(default=None, min_length=1, max_length=400)


class _WordFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    words: list[_Word] = Field(min_length=1)


class _PlainFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    words: list[str] = Field(min_length=1)


@dataclass(frozen=True)
class Term:
    """One listed word: its forms ("ram"; "ois", "optical image stabilisation"), each as words, and its plain text."""

    word: str
    forms: tuple[str, ...]
    plain: str | None


def _fold(word: str) -> str:
    """A plural "s" folded, as the interview's replies do: "sensors" is "sensor", "SSDs" is "ssd"; "less" stays."""
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


def words_of(text: str) -> list[str]:
    """Letters and digits apart, so a unit glued to a number is found: "5000mAh" is "5000 mah", "IP68" is "ip 68".
    Plurals are folded, on both sides of every comparison."""
    return [_fold(word) for word in re.findall(r"[a-z]+|[0-9]+", text.casefold())]


def _padded(text: str) -> str:
    return f" {' '.join(words_of(text))} "


@cache
def terms(path: Path = TERMS_PATH) -> tuple[Term, ...]:
    """Every listed word in config/technical_terms.yaml, with its other forms and its plain explanation."""
    data = _WordFile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    found = []
    for w in data.words:
        forms = tuple(dict.fromkeys(" ".join(words_of(form)) for form in (w.word, *w.also)))
        plain = " ".join(w.plain.split()) if w.plain else None
        found.append(Term(word=" ".join(words_of(w.word)), forms=forms, plain=plain))
    return tuple(found)


@cache
def technical_words(path: Path = TERMS_PATH) -> frozenset[str]:
    """Every technical word and phrase, in lowercase, each written out in full."""
    data = _WordFile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    return frozenset(" ".join(form.casefold().split()) for w in data.words for form in (w.word, *w.also))


@cache
def plain_words(path: Path = PLAIN_WORDS_PATH) -> frozenset[str]:
    """The everyday words in config/plain_words.yaml: a label made only of these needs no explanation."""
    data = _PlainFile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    return frozenset(word for entry in data.words for word in words_of(entry))


def _first_at(padded: str, term: Term) -> int | None:
    """Where the term first appears in the padded text, as whole words, or None."""
    spots = [padded.find(f" {form} ") for form in term.forms]
    hits = [spot for spot in spots if spot >= 0]
    return min(hits) if hits else None


def terms_in(text: str, listed: Sequence[Term] | None = None) -> list[Term]:
    """The listed words the text holds, as whole words, in the order they first appear."""
    padded = _padded(text)
    found = [(spot, t) for t in (terms() if listed is None else listed) if (spot := _first_at(padded, t)) is not None]
    return [t for _, t in sorted(found, key=lambda pair: pair[0])]


def own_terms(criterion: Criterion) -> list[Term]:
    """The listed words the criterion's label names, then those only its key names."""
    found = terms_in(criterion.label)
    return found + [t for t in terms_in(criterion.key.replace("_", " ")) if t not in found]


def label_terms(criterion: Criterion) -> list[Term]:
    """The listed words the label names: the shopper reads the label, never the key, so only these may explain
    it. "Storage speed" is never explained with "SSD throughput", even when its key is ssd_throughput."""
    return terms_in(criterion.label)


def is_technical(criterion: Criterion) -> bool:
    """A listed word in the label or key, or a label word not in the plain list. A number alone is not a word."""
    if own_terms(criterion):
        return True
    plain = plain_words()
    return any(word not in plain for word in words_of(criterion.label) if not word.isdigit())


def jargon_in(text: str, own: Sequence[Term]) -> list[str]:
    """The listed words in the text other than the term's own. The term's own forms are taken out first, so the
    "noise cancellation" inside "active noise cancellation" is not counted against it."""
    padded = _padded(text)
    for form in sorted((f for t in own for f in t.forms), key=len, reverse=True):
        padded = padded.replace(f" {form} ", " | ")
    return [t.word for t in terms() if t not in own and _first_at(padded, t) is not None]


def is_plain(text: str, own: Sequence[Term]) -> bool:
    """Grade 9 or under, and no listed word but the term's own (P-25 item 2)."""
    return bool(text.strip()) and grade(text) <= GRADE_LIMIT and not jargon_in(text, own)


def is_plain_definition(criterion: Criterion) -> bool:
    return is_plain(criterion.definition, label_terms(criterion))


def plain_for(criterion: Criterion) -> str | None:
    """The people-reviewed explanation of the first listed word the label names (then the key), if it has one
    short enough to show."""
    return next((t.plain for t in own_terms(criterion) if t.plain and short_enough(t.plain)), None)


def short_enough(text: str) -> bool:
    return 0 < len(text.split()) <= MAX_EXPLANATION_WORDS
