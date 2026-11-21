"""Each written sentence against the quotes it cites (US-22, S5-4 DECIDED 2 and 4). Code decides; the model only
reads.

The check, with the model:
- every sentence the LLM wrote, and its trade-off, is read against each of its cited quotes in turn (a long quote in
  chunks, nli.py): the premise is the quote, the hypothesis the sentence, and the highest entailment probability
  counts. All of a product's pairs go in one batch;
- a sentence passes when that is at or above the threshold AND every number in it is in a cited quote or in the
  scoring numbers (numbers.py, as US-20 checks them);
- code's own sentences ("We could not check its ..." and template sentences) are never read: nothing can back them,
  and code wrote them from its own numbers.

The fallback, with NLI=on but no model (missing, wrong sha256, failing or out of time): a sentence is kept only when
it holds at least `quoted_words` words of a cited quote in a row, word for word (letters and digits, capitals aside).

Either way, a sentence that fails is replaced by the template's sentence for each point of the top 3 it cites (the LLM
may join strengths into one sentence), unless a kept sentence or an earlier replacement already says that point; with
none left to say, it is removed. A failed trade-off
becomes the template's. A paragraph with no written sentence left becomes the full template. The check's method is
llm_checked, with supported true on every sentence the model passed; the fallback's is llm_unchecked, supported None.
Code's own sentences are no verdict: supported None. So the contract's validator never sees an unsupported sentence
under llm_checked.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.contracts import Claim, Explanation, ExplanationSentence

from .facts import Fact, ProductFacts, allowed_numbers
from .nli import NliUnavailable, Pair, PairScorer, entailment_within
from .numbers import numbers_in, unmatched
from .templates import strength_sentence, template, trade_off_text, unverified_sentences

TRADE_OFF = -1  # the item index of a paragraph's trade-off
MAX_SENTENCES = 8  # Explanation.sentences' own limit: replacements never take a paragraph past it
_WORDS = re.compile(r"[^\W_]+(?:['’][^\W_]+)*")


@dataclass(frozen=True)
class Item:
    """One thing to check: a sentence (by its index) or the trade-off, with the claims and facts it cites."""

    index: int
    text: str
    claim_ids: tuple[str, ...]
    cited: tuple[Fact, ...]


def items(explanation: Explanation, facts: ProductFacts) -> list[Item]:
    """The written sentences and the written trade-off; never code's own words."""
    own = set(unverified_sentences([facts.labels[key] for key in facts.unverified]))
    out = [
        Item(i, s.text, tuple(s.claim_ids), _cited(s.claim_ids, facts))
        for i, s in enumerate(explanation.sentences)
        if s.text not in own
    ]
    trade = facts.trade_off
    if trade is not None and explanation.trade_off and explanation.trade_off != trade_off_text(trade):
        out.append(Item(TRADE_OFF, explanation.trade_off, trade.fact.claim_ids, (trade.fact,)))
    return out


def _cited(claim_ids: Sequence[str], facts: ProductFacts) -> tuple[Fact, ...]:
    return tuple(f for f in facts.facts() if set(f.claim_ids) & set(claim_ids))


def quotes_of(item: Item, claims: Mapping[str, Claim]) -> list[str]:
    return [claims[c].quote for c in item.claim_ids if c in claims]


def numbers_ok(text: str, facts: ProductFacts, cited: Sequence[Fact], quotes: Sequence[str]) -> bool:
    """Every number in the text is a scoring number of what it cites (or the product's own), or in a cited quote."""
    allowed = allowed_numbers(facts, cited) + [n.value for quote in quotes for n in numbers_in(quote)]
    return not unmatched(text, allowed)


def words(text: str) -> list[str]:
    return [w.casefold() for w in _WORDS.findall(text)]


def quotes_words(text: str, quotes: Sequence[str], n: int) -> bool:
    """The text holds `n` words of one of the quotes in a row, word for word."""
    mine = words(text)
    grams = {tuple(mine[i : i + n]) for i in range(len(mine) - n + 1)}
    for quote in quotes:
        theirs = words(quote)
        if any(tuple(theirs[i : i + n]) in grams for i in range(len(theirs) - n + 1)):
            return True
    return False


def check_explanation(
    explanation: Explanation,
    facts: ProductFacts,
    claims: Mapping[str, Claim],
    scorer: PairScorer,
    threshold: float,
    timeout_s: float,
) -> Explanation:
    """The paragraph checked by the model (method llm_checked), or NliUnavailable from the scoring."""
    todo = items(explanation, facts)
    pairs: list[Pair] = []
    spans: dict[int, tuple[int, int]] = {}
    try:
        for item in todo:
            start = len(pairs)
            for quote in quotes_of(item, claims):
                pairs += [(premise, item.text) for premise in scorer.premises(quote)]
            spans[item.index] = (start, len(pairs))
    except NliUnavailable:
        raise
    except Exception as exc:  # the tokenizer is part of the model: its failure gives the fallback too (DECIDED 4)
        raise NliUnavailable(f"the model could not read a quote ({type(exc).__name__})") from exc
    found = entailment_within(scorer, pairs, timeout_s) if pairs else []
    passed: dict[int, bool] = {}
    for item in todo:
        start, end = spans[item.index]
        best = max(found[start:end], default=0.0)
        passed[item.index] = best >= threshold and numbers_ok(item.text, facts, item.cited, quotes_of(item, claims))
    return rebuild(explanation, facts, passed, method="llm_checked", supported=True)


def quoted_only(explanation: Explanation, facts: ProductFacts, claims: Mapping[str, Claim], n: int) -> Explanation:
    """The fallback (method llm_unchecked): only sentences that quote a cited quote, `n` words in a row."""
    passed = {item.index: quotes_words(item.text, quotes_of(item, claims), n) for item in items(explanation, facts)}
    return rebuild(explanation, facts, passed, method="llm_unchecked", supported=None)


def rebuild(
    explanation: Explanation,
    facts: ProductFacts,
    passed: Mapping[int, bool],
    *,
    method: str,
    supported: bool | None,
) -> Explanation:
    """The paragraph with every failed sentence replaced by the template sentences of its points, or removed; the full
    template when no written sentence is left."""
    kept = {i for i, ok in passed.items() if ok and i != TRADE_OFF}
    if not kept:
        return template(facts)
    covered = {f.key for i in kept for f in _cited(explanation.sentences[i].claim_ids, facts) if f in facts.top}
    room = MAX_SENTENCES - sum(i in kept or i not in passed for i in range(len(explanation.sentences)))
    sentences: list[ExplanationSentence] = []
    for i, sentence in enumerate(explanation.sentences):
        if i in kept:
            sentences.append(_with(sentence, supported))
        elif i not in passed:  # code's own words: kept as they are, and no verdict
            sentences.append(_with(sentence, None))
        else:
            added = _replacements(sentence, facts, covered, room)
            room -= len(added)
            sentences += added
    trade = explanation.trade_off
    if passed.get(TRADE_OFF) is False:
        trade = trade_off_text(facts.trade_off)
    return Explanation.model_validate(
        {
            **explanation.model_dump(),
            "sentences": [s.model_dump() for s in sentences],
            "trade_off": trade,
            "method": method,
        }
    )


def _replacements(
    sentence: ExplanationSentence, facts: ProductFacts, covered: set[str], room: int
) -> list[ExplanationSentence]:
    """The template sentence of each top-3 point a failed sentence names that nothing says yet, biggest first (the LLM
    may join several strengths into one sentence), at most `room` of them. Adds each point to `covered`."""
    named = {f.key for f in _cited(sentence.claim_ids, facts)}
    out: list[ExplanationSentence] = []
    for point in facts.top:
        if point.key in named and point.key not in covered and len(out) < room:
            covered.add(point.key)
            out.append(ExplanationSentence(text=strength_sentence(point), claim_ids=list(point.claim_ids)))
    return out


def _with(sentence: ExplanationSentence, supported: bool | None) -> ExplanationSentence:
    return ExplanationSentence.model_validate({**sentence.model_dump(), "supported": supported})
