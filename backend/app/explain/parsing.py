"""The LLM's explanations, read leniently, then checked against code's facts before they become the contract (US-20).

A paragraph is kept only when every check passes, and never repaired: a guess is how a wrong number reaches a shopper.
- 2 to 4 sentences;
- every sentence cites at least one of this product's refs, and only those; they map back to the real claim ids. The
  one exception is code's own "We could not check its ..." sentence, given to the LLM word for word: nothing can back
  it, so written exactly and uncited, it carries the paragraph's other claims, as the contract asks every written
  sentence to cite one;
- every number it names is one code worked out (DECIDED 2), for a fact that sentence cites (or the product's rank,
  score or "out of" scale, or a fact with no claim to cite), in its sentences and its trade-off;
- a sentence that names a score says how it was made: "compared" for a compared one (P-20), "estimate" for an estimate
  (DR-09) and "disagree" for a Disputed one;
- it covers its top 3: each one that has claims is cited (criterion 1);
- it names everything code could not verify, in code's own words (criterion 3);
- nothing reads as an instruction (build rule 3);
- the contract accepts it;
- it reads at Grade 9 or under (DECIDED 3). Only this one is worth asking again, so it says so.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.contracts import Explanation, ExplanationSentence
from app.llm.output import looks_like_instruction

from .facts import OUT_OF, Fact, ProductFacts, allowed_numbers
from .numbers import matches, numbers_in, unmatched
from .readability import GRADE_LIMIT, grade
from .templates import trade_off_text, unverified_sentences

MIN_SENTENCES, MAX_SENTENCES = 2, 4
# How a score that names its number must be described, by what made it: words that must then be in the sentence.
COMPARED_WORDS = ("compar",)
ESTIMATED_WORDS = ("estimat",)
DISPUTED_WORDS = ("disagree", "disput")


class RawSentence(BaseModel):
    model_config = ConfigDict(extra="ignore")

    text: str
    cites: list[str] = Field(default_factory=list)


class RawParagraph(BaseModel):
    model_config = ConfigDict(extra="ignore")

    product: str
    sentences: list[RawSentence]
    trade_off: str | None = None


class RawExplanations(BaseModel):
    """What the LLM answers: one paragraph per product it was asked about."""

    model_config = ConfigDict(extra="ignore")

    paragraphs: list[RawParagraph]


class Rejected(Exception):
    """Why a paragraph was not kept, in code's own words: never the LLM's text."""

    def __init__(self, reason: str, *, too_hard: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        self.too_hard = too_hard  # every other check passed: a rewrite may fix it


def to_contract(paragraph: RawParagraph, facts: ProductFacts, refs: Mapping[str, str]) -> Explanation:
    """The paragraph as an Explanation (method llm_unchecked), or Rejected."""
    own = {refs[claim_id]: claim_id for claim_id in facts.claim_ids() if claim_id in refs}
    count = len(paragraph.sentences)
    if not MIN_SENTENCES <= count <= MAX_SENTENCES:
        raise Rejected(f"it has {count} sentences, not {MIN_SENTENCES} to {MAX_SENTENCES}")
    ours = set(unverified_sentences([facts.labels[key] for key in facts.unverified]))
    written: list[tuple[str, list[str]]] = []  # each sentence, with its claim ids; [] for code's own sentence
    for sentence in paragraph.sentences:
        text = sentence.text.strip()
        cites = list(dict.fromkeys(ref.strip() for ref in sentence.cites))
        if not cites and text in ours:  # code's own words naming what it couldn't check: nothing can back them
            written.append((text, []))
            continue
        if not cites or any(ref not in own for ref in cites):
            raise Rejected("a sentence cites no claim, or one that isn't about this product")
        claim_ids = [own[ref] for ref in cites]
        cited = [f for f in facts.facts() if set(f.claim_ids) & set(claim_ids)]
        _check(text, facts, cited)
        written.append((text, claim_ids))
    # The contract asks every written sentence to cite a claim, so code's own sentence carries the paragraph's: the
    # sources that were checked, next to the words saying what wasn't.
    everything = list(dict.fromkeys(claim_id for _, ids in written for claim_id in ids))
    sentences = [_sentence(text, ids or everything) for text, ids in written]
    trade_text = (paragraph.trade_off or "").strip()
    if facts.trade_off is None:
        trade = None
    elif trade_text:
        _check(trade_text, facts, [facts.trade_off.fact])
        trade = trade_text
    else:  # none written: the template's, from the same fact
        trade = trade_off_text(facts.trade_off)
    explanation = _explanation(facts, sentences, trade)
    _check_covered(explanation, facts)
    text = " ".join([*(s.text for s in explanation.sentences), explanation.trade_off or ""])
    if grade(text) > GRADE_LIMIT:
        raise Rejected(f"it reads above Grade {GRADE_LIMIT:g}", too_hard=True)
    return explanation


def _explanation(facts: ProductFacts, sentences: list[ExplanationSentence], trade: str | None) -> Explanation:
    try:
        return Explanation(
            product_id=facts.product_id,
            sentences=sentences,
            trade_off=trade,
            unverified=list(facts.unverified),
            method="llm_unchecked",
        )
    except ValidationError:
        raise Rejected("it doesn't fit the explanation contract") from None


def _sentence(text: str, claim_ids: list[str]) -> ExplanationSentence:
    try:
        return ExplanationSentence(text=text, claim_ids=claim_ids)
    except ValidationError:
        raise Rejected("a sentence is empty or too long") from None


def _check(text: str, facts: ProductFacts, cited: Sequence[Fact]) -> None:
    """One sentence, or the trade-off, about the facts it cites: its numbers, its words and nothing that orders."""
    if unmatched(text, allowed_numbers(facts, cited)):
        raise Rejected("a sentence names a number the scores don't have")
    if looks_like_instruction(text):
        raise Rejected("a sentence reads as an instruction")
    lower = text.lower()
    for fact in _scores_named(text, facts, cited):
        for needed, words, reason in (
            (fact.basis == "compared", COMPARED_WORDS, "a compared score isn't called compared"),
            (fact.estimated, ESTIMATED_WORDS, "an estimated score isn't called an estimate"),
            (fact.disputed, DISPUTED_WORDS, "a disputed score doesn't say sources disagree"),
        ):
            if needed and not any(word in lower for word in words):
                raise Rejected(reason)


def _scores_named(text: str, facts: ProductFacts, cited: Sequence[Fact]) -> list[Fact]:
    """The facts whose score this text names: one it cites, or one with no claim to cite whose score is the only
    reading of a number here (a number the cited facts or the product's rank and score explain is theirs)."""
    named = numbers_in(text)
    explained = [*OUT_OF, float(facts.rank), *(v for f in cited for v in (f.score, float(f.points)))]
    if facts.score is not None:
        explained.append(float(facts.score))
    found: list[Fact] = []
    for fact in facts.facts():
        if fact in cited:
            hit = any(matches(n, fact.score) for n in named)
        elif not fact.claim_ids:
            hit = any(matches(n, fact.score) and not any(matches(n, v) for v in explained) for n in named)
        else:
            hit = False
        if hit:
            found.append(fact)
    return found


def _check_covered(explanation: Explanation, facts: ProductFacts) -> None:
    """Criterion 1: its top 3 are all there (each one with claims is cited). Criterion 3: what code couldn't verify
    is named, in code's own words, as the template names it."""
    cited = {claim_id for s in explanation.sentences for claim_id in s.claim_ids}
    if any(fact.claim_ids and not set(fact.claim_ids) & cited for fact in facts.top):
        raise Rejected("it leaves out one of its top 3")
    text = " ".join([*(s.text for s in explanation.sentences), explanation.trade_off or ""]).lower()
    if any(facts.labels[key].lower() not in text for key in facts.unverified):
        raise Rejected("it doesn't name everything we could not check")
