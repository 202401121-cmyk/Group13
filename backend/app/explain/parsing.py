"""The evidence call's answer (D-91), read leniently, then checked by code one sentence at a time (US-22, US-20).

A sentence is kept only when every rule passes, and never repaired. One that fails is dropped alone, and its reason is
logged in code's own words, never the LLM's:
- it cites at least one ref it was given, and only those: an invented ref, or another product's claim, is never one;
- all its refs are quotes of one criterion, and it is the first sentence kept for that criterion;
- every number in it is in a quote it cites, or one of code's own numbers for that criterion (DECIDED 3), and a
  phrase stating a score, points or rank states that kind's own value (codes.py);
- it holds no run of `quoted_words` words from a community quote of this product, which was never sent (D-53);
- nothing in it reads as an instruction (build rule 3);
- it fits the contract (3 to 400 characters) and reads at Grade 9 or under (DECIDED 4).

The old private explanation call and its parser (US-20's RawExplanations) were removed with D-91: code now writes the
personal part, and nothing else read that answer.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from app.llm.output import looks_like_instruction

from .codes import misstated
from .evidence import EvidenceRequest
from .facts import ProductFacts, allowed_numbers
from .numbers import numbers_in, unmatched
from .quoted import quotes_words
from .readability import GRADE_LIMIT, grade

TEXT_MIN, TEXT_MAX = 3, 400  # ExplanationSentence.text


class RawEvidenceSentence(BaseModel):
    model_config = ConfigDict(extra="ignore")

    topic: str | None = None  # the group it was given (t1, t2 ...); code goes by the refs cited
    text: str
    cites: list[str] = []


class RawEvidence(BaseModel):
    """What the LLM answers: at most one sentence per criterion it was given."""

    model_config = ConfigDict(extra="ignore")

    sentences: list[RawEvidenceSentence]


@dataclass(frozen=True)
class Draft:
    """An evidence sentence that passed code's rules, waiting for the citation check."""

    key: str  # its criterion
    text: str
    claim_ids: tuple[str, ...]
    quotes: tuple[str, ...]


def drafts_from(
    answer: RawEvidence,
    request: EvidenceRequest,
    facts: ProductFacts,
    community: Sequence[str] = (),
    quoted_words: int = 6,
) -> tuple[list[Draft], list[str]]:
    """The sentences that pass every rule, in the order of their criteria, and why each other one was dropped."""
    refs = request.by_ref()
    kept: dict[str, Draft] = {}
    dropped: list[str] = []
    for sentence in answer.sentences:
        text = " ".join(sentence.text.split())
        cites = list(dict.fromkeys(ref.strip() for ref in sentence.cites))
        if not cites or any(ref not in refs for ref in cites):
            dropped.append("it cites no quote, or one it wasn't given")
            continue
        keys = {refs[ref].key for ref in cites}
        if len(keys) != 1:
            dropped.append("it cites quotes of more than one criterion")
            continue
        (key,) = keys
        quotes = tuple(refs[ref].quote for ref in cites)
        reason = _broken(text, key, quotes, facts, community, quoted_words)
        if reason is None and key in kept:
            reason = "its criterion already has a sentence"
        if reason is not None:
            dropped.append(reason)
            continue
        kept[key] = Draft(key, text, tuple(refs[ref].claim_id for ref in cites), quotes)
    order = list(request.labels)
    return sorted(kept.values(), key=lambda d: order.index(d.key)), dropped


def _broken(
    text: str, key: str, quotes: Sequence[str], facts: ProductFacts, community: Sequence[str], quoted_words: int
) -> str | None:
    """Why this sentence can't be kept, or None."""
    if not TEXT_MIN <= len(text) <= TEXT_MAX:
        return "it is empty or too long"
    cited = [f for f in facts.facts() if f.key == key]
    allowed = [n.value for quote in quotes for n in numbers_in(quote)] + allowed_numbers(facts, cited)
    if unmatched(text, allowed):
        return "it names a number neither its quotes nor the scores hold"
    if misstated(text, facts, key, quotes):
        return "it states a score, points or rank that isn't code's"
    if community and quotes_words(text, community, quoted_words):
        return "it repeats words of a community post"
    if looks_like_instruction(text):
        return "it reads as an instruction"
    if grade(text) > GRADE_LIMIT:
        return f"it reads above Grade {GRADE_LIMIT:g}"
    return None
